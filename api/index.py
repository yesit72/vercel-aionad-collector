"""
AI OnAd Rawdata Collector — Flask 단일 앱

Vercel Python 런타임(2026년 기준)은 api/ 폴더 안에 파일을 여러 개 두고 각각을
BaseHTTPRequestHandler `handler`로 만드는 방식을 더 이상 zero-config로 인식하지 않는다.
대신 app.py/index.py/main.py 같은 정해진 파일명 하나에 Flask(또는 FastAPI) 앱을 두고
그 안에서 라우팅하는 것을 표준으로 요구한다 (배포 시 하나의 Vercel Function이 된다).
그래서 예전의 collect.py/login.py/status.py/download.py 네 파일을 이 파일 하나로 합쳤다.

라우트
  POST /api/login     - 비밀번호 확인 -> 세션 쿠키 발급
  GET  /api/status    - 최근 45일 수집 현황 (세션 필요)
  GET  /api/download  - ?date=YYYYMMDD zip 다운로드 (세션 필요)
  GET  /api/collect   - Vercel Cron 전용 (Bearer CRON_SECRET 필요)
  POST /api/collect   - 웹페이지 수동 트리거 (세션 필요). body: {"date": "YYYYMMDD"} 생략 시 D-1

필요한 환경변수 (Vercel 프로젝트 Settings > Environment Variables):
  CMS_USERNAME, CMS_PASSWORD     - aionad.skbroadband.com 계정
  CRON_SECRET                    - Vercel Cron이 자동으로 Authorization 헤더에 실어 보냄
  ADMIN_PASSWORD, SESSION_SECRET - 웹페이지 로그인용
  BLOB_READ_WRITE_TOKEN          - Blob 스토어를 프로젝트에 연결하면 자동 생성됨
"""
import base64
import hashlib
import hmac
import os
import pathlib
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
import vercel_blob
from flask import Flask, Response, jsonify, make_response, request

app = Flask(__name__)

CMS_BASE = "https://aionad.skbroadband.com"
KST = timezone(timedelta(hours=9))
BLOB_PREFIX = "aionad-raw"
SESSION_TTL_SECONDS = 7 * 24 * 3600


# ---------------------------------------------------------------- 세션 인증
def make_session_token(secret: str) -> str:
    exp = int(time.time()) + SESSION_TTL_SECONDS
    sig = hmac.new(secret.encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    raw = f"{exp}.{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def verify_session_token(token: str, secret: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        exp_str, sig = raw.split(".", 1)
        expected = hmac.new(secret.encode(), exp_str.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected) and int(exp_str) > time.time()
    except Exception:
        return False


def is_authenticated() -> bool:
    token = request.cookies.get("session")
    if not token:
        return False
    return verify_session_token(token, os.environ.get("SESSION_SECRET", ""))


# ---------------------------------------------------------------- CMS 로그인 / 다운로드
def cms_login(session: requests.Session) -> None:
    r = session.post(
        f"{CMS_BASE}/account/login-process",
        data={
            "username": os.environ["CMS_USERNAME"],
            "password": os.environ["CMS_PASSWORD"],
        },
        timeout=30,
    )
    if "SUCCESS" not in r.text:
        raise RuntimeError(f"로그인 실패: {r.status_code} {r.text[:200]}")


def download_zip(session: requests.Session, date_str: str) -> Optional[bytes]:
    """None=404(아직 미생성). 401/403이면 PermissionError."""
    body = {
        "metaFileLogPath": f"{date_str[:4]}/{date_str[:6]}",
        "metaFileName": f"{date_str}.zip",
    }
    r = session.post(
        f"{CMS_BASE}/bi-statistics/ai-meta/download-rawdata-zip-file",
        json=body,
        timeout=120,
    )
    if r.status_code == 404:
        return None
    if r.status_code in (401, 403):
        raise PermissionError("세션 만료")
    r.raise_for_status()
    return r.content


def blob_exists(pathname: str) -> bool:
    cursor = None
    while True:
        params = {"prefix": pathname, "limit": "20"}
        if cursor:
            params["cursor"] = cursor
        result = vercel_blob.list(params)
        if any(b["pathname"] == pathname for b in result.get("blobs", [])):
            return True
        if not result.get("hasMore"):
            return False
        cursor = result.get("cursor")


def find_blob(pathname: str):
    cursor = None
    while True:
        params = {"prefix": pathname, "limit": "5"}
        if cursor:
            params["cursor"] = cursor
        result = vercel_blob.list(params)
        for b in result.get("blobs", []):
            if b["pathname"] == pathname:
                return b
        if not result.get("hasMore"):
            return None
        cursor = result.get("cursor")


def list_all_blobs():
    blobs = []
    cursor = None
    while True:
        params = {"prefix": f"{BLOB_PREFIX}/", "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        result = vercel_blob.list(params)
        blobs.extend(result.get("blobs", []))
        if not result.get("hasMore"):
            break
        cursor = result.get("cursor")
    return blobs


def collect(date_str: Optional[str] = None) -> str:
    if not date_str:
        date_str = (datetime.now(KST) - timedelta(days=1)).strftime("%Y%m%d")

    pathname = f"{BLOB_PREFIX}/{date_str[:6]}/{date_str}.zip"

    if blob_exists(pathname):
        return f"{pathname} 이미 존재 -- 스킵"

    session = requests.Session()
    cms_login(session)

    try:
        data = download_zip(session, date_str)
    except PermissionError:
        session = requests.Session()
        cms_login(session)
        data = download_zip(session, date_str)

    if data is None:
        return f"{date_str} 아직 생성 전 (404) -- 나중에 다시 시도"

    vercel_blob.put(pathname, data, {"access": "private", "addRandomSuffix": "false"})
    return f"저장 완료: {pathname} ({len(data):,} bytes)"


# ---------------------------------------------------------------- 라우트
@app.route("/api/login", methods=["POST"])
def login_route():
    payload = request.get_json(silent=True) or {}
    password = payload.get("password", "")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")

    if password and admin_password and hmac.compare_digest(password, admin_password):
        token = make_session_token(os.environ.get("SESSION_SECRET", ""))
        resp = make_response(jsonify({"ok": True}))
        resp.set_cookie(
            "session",
            token,
            httponly=True,
            secure=True,
            samesite="Lax",
            max_age=SESSION_TTL_SECONDS,
            path="/",
        )
        return resp

    return jsonify({"ok": False}), 401


@app.route("/api/status", methods=["GET"])
def status_route():
    if not is_authenticated():
        return "", 401

    blobs_by_name = {b["pathname"].split("/")[-1]: b for b in list_all_blobs()}

    today = datetime.now(KST).date()
    rows = []
    for i in range(45):
        d = today - timedelta(days=i + 1)
        date_str = d.strftime("%Y%m%d")
        b = blobs_by_name.get(f"{date_str}.zip")
        rows.append(
            {
                "date": date_str,
                "status": "완료" if b else "없음",
                "size": b["size"] if b else None,
            }
        )

    return jsonify({"rows": rows})


@app.route("/api/download", methods=["GET"])
def download_route():
    if not is_authenticated():
        return "", 401

    date_str = request.args.get("date", "")
    if not (len(date_str) == 8 and date_str.isdigit()):
        return "date=YYYYMMDD 형식으로 넘겨주세요", 400

    pathname = f"{BLOB_PREFIX}/{date_str[:6]}/{date_str}.zip"
    blob = find_blob(pathname)
    if not blob:
        return "", 404

    tmp_dir = tempfile.mkdtemp()
    try:
        vercel_blob.download_file(
            blob["url"], tmp_dir, {"token": os.environ["BLOB_READ_WRITE_TOKEN"]}
        )
        files = list(pathlib.Path(tmp_dir).rglob("*.zip"))
        if not files:
            return "", 500
        data = files[0].read_bytes()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    resp = Response(data, mimetype="application/zip")
    resp.headers["Content-Disposition"] = f'attachment; filename="{date_str}.zip"'
    return resp


@app.route("/api/collect", methods=["GET", "POST"])
def collect_route():
    if request.method == "GET":
        # Vercel Cron 전용
        expected = f"Bearer {os.environ.get('CRON_SECRET', '')}"
        if request.headers.get("Authorization") != expected:
            return "unauthorized", 401
        date_str = None
    else:
        # 웹페이지 수동 트리거
        if not is_authenticated():
            return "unauthorized", 401
        payload = request.get_json(silent=True) or {}
        date_str = payload.get("date")

    try:
        return collect(date_str), 200
    except Exception as e:  # noqa: BLE001 - 원인 그대로 응답에 남겨 로그에서 바로 보이게
        return f"오류: {e!r}", 500
