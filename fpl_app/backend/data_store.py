"""
data_store.py
Small JSON-file persistence layer. No database, no external libraries.

Data is scoped per user: everything lives under
    data/<safe-username>/{squad,gameweek_log,settings}.json
so two different logins never see each other's squad, and the same
login sees the same squad no matter which device it's opened from.
"""

import json
import os
import re
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_username(username):
    cleaned = _SAFE_RE.sub("_", username or "default").strip("_")
    return cleaned or "default"


def _user_dir(username):
    d = os.path.join(DATA_DIR, _safe_username(username))
    os.makedirs(d, exist_ok=True)
    return d


def _squad_file(username):
    return os.path.join(_user_dir(username), "squad.json")


def _gameweek_log_file(username):
    return os.path.join(_user_dir(username), "gameweek_log.json")


def _settings_file(username):
    return os.path.join(_user_dir(username), "settings.json")


def _read(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------- squad ----

def load_squad(username):
    """
    Squad shape:
    {
      "gameweek": 3,
      "bank": 2.3,                # money in the bank, in millions
      "free_transfers": 1,
      "picks": [ { "id": 123, "position": "GK", "purchase_price": 4.5 }, ... ]  # 15 players
      "formation": "3-4-3",
      "updated_at": 169999999.0
    }
    """
    return _read(_squad_file(username), None)


def save_squad(username, squad):
    squad["updated_at"] = time.time()
    _write(_squad_file(username), squad)
    return squad


# --------------------------------------------------------- gameweek log ----

def load_gameweek_log(username):
    return _read(_gameweek_log_file(username), [])


def record_gameweek_answer(username, entry):
    """
    entry: {gameweek, budget, transfers_wanted, required_player_ids, formation,
            use_full_budget, use_full_transfers, timestamp}
    """
    log = load_gameweek_log(username)
    entry["timestamp"] = time.time()
    # replace any existing entry for the same gameweek
    log = [e for e in log if e.get("gameweek") != entry.get("gameweek")]
    log.append(entry)
    _write(_gameweek_log_file(username), log)
    return log


def last_answered_gameweek(username):
    log = load_gameweek_log(username)
    if not log:
        return None
    return max(e["gameweek"] for e in log)


# ------------------------------------------------------------ settings ----

def load_settings(username):
    return _read(_settings_file(username), {})


def save_settings(username, settings):
    _write(_settings_file(username), settings)
    return settings


# --------------------------------------------------------------- reset ----

def reset_user_data(username):
    for path in (_squad_file(username), _gameweek_log_file(username)):
        if os.path.exists(path):
            os.remove(path)
