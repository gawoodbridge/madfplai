"""
auth.py
Minimal stdlib-only username/password authentication with server-side
sessions. Supports admin-provisioned accounts via FPL_USERS and persistent
accounts created via the web UI (saved to data/users.json).
"""

import hmac
import os
import time
import secrets
import json

SESSION_COOKIE = "fpl_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days

# token -> {"username": str, "expires": float}
_sessions = {}

# persistent users file (optional)
PERSISTENT_USERS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "users.json")


def _load_users_from_env():
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


def _load_users_from_file():
    if not os.path.exists(PERSISTENT_USERS_FILE):
        return {}
    try:
        with open(PERSISTENT_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_users_to_file(users):
    os.makedirs(os.path.dirname(PERSISTENT_USERS_FILE), exist_ok=True)
    with open(PERSISTENT_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def _load_all_users():
    users = _load_users_from_env()
    file_users = _load_users_from_file()
    users.update(file_users)
    return users


# Loaded once at process start. Set FPL_USERS before starting the server
# to provision admin users; signup adds to data/users.json for persistence.
USERS = _load_all_users()


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


def add_user(username, password):
    """Add a persistent user. Returns (True, None) on success or (False, msg)."""
    if not username or not password:
        return False, "Username and password are required"
    if username in USERS:
        return False, "Username already exists"
    # minimal validation
    if len(password) < 4:
        return False, "Password too short"
    USERS[username] = password
    try:
        # merge existing file users and write back
        file_users = _load_users_from_file()
        file_users[username] = password
        _save_users_to_file(file_users)
    except Exception as e:
        return False, f"Failed to persist user: {e}"
    return True, None


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
