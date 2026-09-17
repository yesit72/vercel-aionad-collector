"""
?date=YYYYMMDD 로 특정 날짜 zip을 내려받는다. 로그인 세션 필요.
Blob이 private이라 서버에서 한 번 받아서 그대로 스트리밍한다.
"""
import base64
import hashlib
import hmac
import os
import pathlib
import shutil
import tempfile
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import vercel_blob

BLOB_PREFIX = "aionad-raw"


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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_authenticated(self.headers):
            self.send_response(401)
            self.end_headers()
            return

        qs = parse_qs(urlparse(self.path).query)
        date_str = (qs.get("date") or [""])[0]
        if not (len(date_str) == 8 and date_str.isdigit()):
            self.send_response(400)
            self.end_headers()
            self.wfile.write("date=YYYYMMDD 형식으로 넘겨주세요".encode("utf-8"))
            return

        pathname = f"{BLOB_PREFIX}/{date_str[:6]}/{date_str}.zip"
        blob = find_blob(pathname)
        if not blob:
            self.send_response(404)
            self.end_headers()
            return

        tmp_dir = tempfile.mkdtemp()
        try:
            vercel_blob.download_file(
                blob["url"], tmp_dir, {"token": os.environ["BLOB_READ_WRITE_TOKEN"]}
            )
            files = list(pathlib.Path(tmp_dir).rglob("*.zip"))
            if not files:
                self.send_response(500)
                self.end_headers()
                return
            data = files[0].read_bytes()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{date_str}.zip"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
