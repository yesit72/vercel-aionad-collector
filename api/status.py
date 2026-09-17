"""
최근 LOOKBACK_DAYS일의 수집 현황을 JSON으로 반환한다. 로그인 세션 필요.
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

import vercel_blob

KST = timezone(timedelta(hours=9))
BLOB_PREFIX = "aionad-raw"
LOOKBACK_DAYS = 45


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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_authenticated(self.headers):
            self.send_response(401)
            self.end_headers()
            return

        blobs_by_name = {}
        for b in list_all_blobs():
            name = b["pathname"].split("/")[-1]  # 20260916.zip
            blobs_by_name[name] = b

        today = datetime.now(KST).date()
        rows = []
        for i in range(LOOKBACK_DAYS):
            d = today - timedelta(days=i + 1)  # 어제부터 과거로
            date_str = d.strftime("%Y%m%d")
            b = blobs_by_name.get(f"{date_str}.zip")
            rows.append(
                {
                    "date": date_str,
                    "status": "완료" if b else "없음",
                    "size": b["size"] if b else None,
                }
            )

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"rows": rows}).encode("utf-8"))
