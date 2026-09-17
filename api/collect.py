"""
AI OnAd 일일 원시 로그 수집 -> Vercel Blob 저장

두 가지 방식으로 호출된다.
  GET  /api/collect   - Vercel Cron 전용. Authorization: Bearer <CRON_SECRET> 필요.
  POST /api/collect   - 웹페이지의 "지금 수집" 버튼. 로그인 세션 쿠키 필요.
                         body에 {"date": "YYYYMMDD"} 를 주면 그 날짜를, 없으면 D-1을 수집한다.

필요한 환경변수 (Vercel 프로젝트 Settings > Environment Variables):
  CMS_USERNAME, CMS_PASSWORD  - aionad.skbroadband.com 계정
  CRON_SECRET                 - Vercel Cron 인증용 (자동으로 Authorization 헤더에 실려온다)
  ADMIN_PASSWORD, SESSION_SECRET - 웹페이지 로그인용 (api/login.py 참고)
  BLOB_READ_WRITE_TOKEN       - Blob 스토어를 프로젝트에 연결하면 자동 생성됨
"""
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from typing import Optional

import requests
import vercel_blob

CMS_BASE = "https://aionad.skbroadband.com"
KST = timezone(timedelta(hours=9))
BLOB_PREFIX = "aionad-raw"


# ---------- 세션 인증 (login.py / status.py / download.py와 동일 로직 — 파일마다 복사) ----------
def verify_session_token(token: str, secret: str) -> bool:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        exp_str, sig = raw.split(".", 1)
        expected = hmac.new(secret.encode(), exp_str.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected) and int(exp_str) > time.time()
    except Exception:
        return False


def is_authenticated(headers) -> bool:
    raw = headers.get("Cookie") or headers.get("cookie") or ""
    c = SimpleCookie()
    c.load(raw)
    token = c["session"].value if "session" in c else None
    if not token:
        return False
    return verify_session_token(token, os.environ.get("SESSION_SECRET", ""))


# ---------- CMS 로그인 / 다운로드 ----------
def login(session: requests.Session) -> None:
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
    """
    반환값:
      bytes  - zip 본문
      None   - 404 (그 날짜 RawData가 아직 생성되지 않음. 실패 아님)
    예외:
      PermissionError - 401/403 (세션 만료)
    """
    body = {
        "metaFileLogPath": f"{date_str[:4]}/{date_str[:6]}",  # YYYY/YYYYMM
        "metaFileName": f"{date_str}.zip",                    # YYYYMMDD.zip
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
    """
    vercel_blob.head()는 pathname이 아니라 blob url을 인자로 받으므로 쓸 수 없다.
    대신 prefix로 list()해서 정확히 일치하는 pathname이 있는지 확인한다.
    """
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


def collect(date_str: Optional[str] = None) -> str:
    if not date_str:
        date_str = (datetime.now(KST) - timedelta(days=1)).strftime("%Y%m%d")

    pathname = f"{BLOB_PREFIX}/{date_str[:6]}/{date_str}.zip"

    if blob_exists(pathname):
        return f"{pathname} 이미 존재 -- 스킵"

    session = requests.Session()
    login(session)

    try:
        data = download_zip(session, date_str)
    except PermissionError:
        # 방어적 재시도 1회
        session = requests.Session()
        login(session)
        data = download_zip(session, date_str)

    if data is None:
        return f"{date_str} 아직 생성 전 (404) -- 나중에 다시 시도"

    vercel_blob.put(
        pathname,
        data,
        {"access": "private", "addRandomSuffix": "false"},
    )
    return f"저장 완료: {pathname} ({len(data):,} bytes)"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Vercel Cron 전용
        expected = f"Bearer {os.environ.get('CRON_SECRET', '')}"
        if self.headers.get("authorization") != expected:
            self._reply(401, "unauthorized")
            return
        self._run(None)

    def do_POST(self):
        # 웹페이지 수동 트리거 — 로그인 세션 필요
        if not is_authenticated(self.headers):
            self._reply(401, "unauthorized")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw_body = self.rfile.read(length) if length else b""
        date_str = None
        if raw_body:
            try:
                date_str = json.loads(raw_body).get("date")
            except Exception:
                pass
        self._run(date_str)

    def _run(self, date_str):
        try:
            msg = collect(date_str)
            code = 200
        except Exception as e:  # noqa: BLE001 - 원인 그대로 응답에 남겨 로그에서 바로 보이게
            msg = f"오류: {e!r}"
            code = 500
        self._reply(code, msg)

    def _reply(self, code: int, msg: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode("utf-8"))
