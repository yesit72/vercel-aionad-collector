"""
웹페이지 로그인. 비밀번호가 맞으면 서명된 세션 쿠키를 내려준다.

DB 없이 HMAC 서명 토큰만으로 세션을 검증한다 (내부 소수 인원용 도구라 이 정도로 충분).
토큰 구조: base64( "<만료시각>.<HMAC-SHA256(SESSION_SECRET, 만료시각)>" )

필요한 환경변수:
  ADMIN_PASSWORD  - 웹페이지 로그인 비밀번호
  SESSION_SECRET  - 토큰 서명용 임의의 긴 문자열 (CRON_SECRET과는 다른 값 사용 권장)
"""
import base64
import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler

SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7일


def make_session_token(secret: str) -> str:
    exp = int(time.time()) + SESSION_TTL_SECONDS
    sig = hmac.new(secret.encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    raw = f"{exp}.{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw_body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw_body or b"{}")
        except Exception:
            payload = {}

        password = payload.get("password", "")
        admin_password = os.environ.get("ADMIN_PASSWORD", "")

        if password and admin_password and hmac.compare_digest(password, admin_password):
            token = make_session_token(os.environ.get("SESSION_SECRET", ""))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header(
                "Set-Cookie",
                f"session={token}; HttpOnly; Secure; Path=/; Max-Age={SESSION_TTL_SECONDS}; SameSite=Lax",
            )
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
        else:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": false}')
