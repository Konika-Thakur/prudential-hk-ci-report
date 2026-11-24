"""Password-protected report/chat entry point. API keys and the password stay in server-side environment variables."""
import hashlib
import hmac
import io
import json
import os
import re
import sys
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import Handler, REPORT_HTML  # noqa: E402

COOKIE = "report_access"
COOKIE_DAYS = 30


def _login_page() -> str:
    page = (ROOT / "private" / "login.template.html").read_text(encoding="utf-8")
    report = REPORT_HTML.read_text(encoding="utf-8")
    for cls, slot in (("logo-on-light", "{{LOGO_LIGHT}}"), ("logo-on-dark", "{{LOGO_DARK}}")):
        m = re.search(rf'<img class="logo {cls}" src="(data:image/png;base64,[A-Za-z0-9+/=]+)"', report)
        page = page.replace(slot, m.group(1) if m else "")
    return page


LOGIN_PAGE = _login_page()


def _username() -> str:
    return (os.environ.get("REPORT_USERNAME") or "").strip()


def _password() -> str:
    return (os.environ.get("REPORT_PASSWORD") or "").strip()


def _token() -> str:
    # Derived from the credentials, so changing either signs everyone out.
    key = f"{_username()}\n{_password()}".encode()
    return hmac.new(key, b"prudential-hk-ci-report-access-v1", hashlib.sha256).hexdigest()


class handler(Handler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Robots-Tag', 'noindex, nofollow')
        super().end_headers()

    def authed(self) -> bool:
        if not (_username() and _password()):
            return False  # fail closed if the credentials are not configured
        jar = SimpleCookie(self.headers.get('Cookie', ''))
        return COOKIE in jar and hmac.compare_digest(jar[COOKIE].value, _token())

    def login_page(self, error: str = "", code: int = 200):
        html = LOGIN_PAGE.replace("{{ERROR}}", f'<p class="err" role="alert">{error}</p>' if error else "")
        self.send_body(html.encode(), "text/html; charset=utf-8", code)

    def do_GET(self):
        self.path = self.path.split('?', 1)[0]
        if self.path in ('/', '/index.html'):
            return super().do_GET() if self.authed() else self.login_page()
        if self.path == '/api/status':
            return super().do_GET() if self.authed() else self.send_json({'error': 'Sign in required'}, 401)
        m = re.fullmatch(r'/narration/pru/([a-z0-9-]{1,40})\.mp3', self.path)
        if m:
            if not self.authed():
                return self.send_json({'error': 'Sign in required'}, 401)
            clip = ROOT / 'private' / 'narration' / 'pru' / f'{m.group(1)}.mp3'
            return self.send_body(clip.read_bytes(), 'audio/mpeg') if clip.is_file() else self.send_json({'error': 'Not found'}, 404)
        m = re.fullmatch(r'/media/([A-Za-z0-9_.-]{1,80}\.jpg)', self.path)
        if m:
            if not self.authed():
                return self.send_json({'error': 'Sign in required'}, 401)
            img = ROOT / 'private' / 'media' / m.group(1)
            return self.send_body(img.read_bytes(), 'image/jpeg') if img.is_file() else self.send_json({'error': 'Not found'}, 404)
        if self.path == '/api/logout':
            self.send_response(303)
            self.send_header('Set-Cookie', f'{COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax')
            self.send_header('Location', '/')
            self.end_headers()
            return
        return self.send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        if self.path == '/api/login':
            return self.handle_login()
        if self.path != '/api/ask':
            return self.send_json({'error': 'Not found'}, 404)
        if not self.authed():
            return self.send_json({'error': 'Sign in required'}, 401)
        if not os.environ.get('GEMINI_API_KEY'):
            return self.send_json({'error': 'AI service is not configured.'}, 503)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4_000_000:  # Vercel caps request bodies at 4.5 MB
                return self.send_json({'error': 'Request too large or empty'}, 413)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
            question = data.get('question')
            history = data.get('history', [])
            if not isinstance(question, str) or not 0 < len(question.strip()) <= 8000:
                raise ValueError()
            if not isinstance(history, list):
                raise ValueError()
            for turn in history:
                if (not isinstance(turn, dict) or turn.get('role') not in ('user', 'model')
                        or not isinstance(turn.get('text'), str)):
                    raise ValueError()
            self.rfile = io.BytesIO(json.dumps(data).encode())
            self.headers.replace_header('Content-Length', str(len(self.rfile.getvalue())))
        except (ValueError, TypeError, UnicodeError):
            return self.send_json({'error': 'Invalid request'}, 400)
        super().do_POST()

    def handle_login(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2000:
                raise ValueError()
            form = parse_qs(self.rfile.read(size).decode())
            user = (form.get('username') or [''])[0].strip()
            given = (form.get('password') or [''])[0]
        except (ValueError, UnicodeError):
            user, given = '', ''
        # Compare both every time so a wrong username takes as long as a wrong password.
        user_ok = hmac.compare_digest(user.lower().encode(), _username().lower().encode())
        pass_ok = hmac.compare_digest(given.encode(), _password().encode())
        if _username() and _password() and user_ok and pass_ok:
            self.send_response(303)
            self.send_header('Set-Cookie', f'{COOKIE}={_token()}; Path=/; Max-Age={COOKIE_DAYS * 86400}; HttpOnly; Secure; SameSite=Lax')
            self.send_header('Location', '/')
            self.end_headers()
            return
        time.sleep(1)  # slow down password guessing
        self.login_page("Incorrect username or password. Please try again.", 401)

    def do_OPTIONS(self):
        self.send_response(405)
        self.end_headers()
