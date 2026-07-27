"""
auth.py
Minimal stdlib-only username/password authentication with server-side
sessions. No external libraries, no database.

There is deliberately no sign-up route. Accounts are provisioned by the
admin (you) via the FPL_USERS environment variable, e.g. in the Render
dashboard under your service's "Environment" tab:

    FPL_USERS=alice:hunter2,bob:another-password

Each comma-separated pair is username:password. Whoever holds this
process's environment controls who can log in - there is no way for a
visitor to create their own account from the app itself.

Sessions are held in memory (a dict keyed by a random token, sent to the
browser as an HttpOnly cookie). That means: restarting the server logs
everyone out, and if you ever scale this service to more than once
instance, sessions won't be shared between instances. For a small
personal tool run as a single Render web service this is fine.
"""

import hmac
import os
import time
import secrets

SESSION_COOKIE = "fpl_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days

# token -> {"username": str, "expires": float}
_sessions = {}


def _load_users():
    raw = os.environ.get("FPL_USERS", "")
    users = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        username, password = pair.split(":", 1)
        username = username.strip()
        if username:
            users[username] = password
    return users


# Loaded once at process start. Set FPL_USERS before starting the server;
# changing it requires a restart (a Render redeploy, or just re-running
# python3 backend/server.py locally).
USERS = _load_users()


def verify_login(username, password):
    """Constant-time-ish credential check. Returns True/False."""
    if not username or not password:
        return False
    expected = USERS.get(username)
    if expected is None:
        # Still do a comparison so a missing username doesn't respond
        # measurably faster than a wrong password.
        hmac.compare_digest("no-such-user", password)
        return False
    return hmac.compare_digest(expected, password)


def create_session(username):
    token = secrets.token_urlsafe(32)
    _sessions[token] = {"username": username, "expires": time.time() + SESSION_TTL_SECONDS}
    return token


def get_username(token):
    if not token:
        return None
    entry = _sessions.get(token)
    if not entry:
        return None
    if entry["expires"] < time.time():
        _sessions.pop(token, None)
        return None
    return entry["username"]


def destroy_session(token):
    if token:
        _sessions.pop(token, None)


def parse_cookies(cookie_header):
    cookies = {}
    if not cookie_header:
        return cookies
    for part in cookie_header.split(";"):
        if "=" in part:
            key, value = part.strip().split("=", 1)
            cookies[key] = value
    return cookies


def build_set_cookie(token, secure=True):
    parts = [
        f"{SESSION_COOKIE}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={SESSION_TTL_SECONDS}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def build_clear_cookie(secure=True):
    parts = [f"{SESSION_COOKIE}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)
