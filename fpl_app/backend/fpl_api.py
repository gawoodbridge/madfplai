"""
fpl_api.py
Fetches and caches data from the official Fantasy Premier League API.
Uses only the Python standard library (urllib) - no requests, no external deps.
"""

import json
import os
import time
import urllib.request
import urllib.error

BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
BOOTSTRAP_CACHE = os.path.join(DATA_DIR, "cache_bootstrap.json")
FIXTURES_CACHE = os.path.join(DATA_DIR, "cache_fixtures.json")

# How long a cache file is considered fresh before we try to refetch (seconds).
# The gameweek-aware logic in server.py decides *when* a refetch actually matters;
# this is just a floor so we don't hammer the API on every request.
CACHE_TTL = 60 * 30  # 30 minutes

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FPL-Assistant/1.0)"
}


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _load_cache(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(path, data):
    _ensure_data_dir()
    payload = {"fetched_at": time.time(), "data": data}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return payload


def get_bootstrap(force_refresh=False):
    """
    Returns the bootstrap-static payload (players, teams, gameweeks/events).
    Falls back to cache if the live API is unreachable.
    """
    cached = _load_cache(BOOTSTRAP_CACHE)
    is_stale = cached is None or (time.time() - cached["fetched_at"] > CACHE_TTL)

    if force_refresh or is_stale:
        try:
            data = _fetch_json(BOOTSTRAP_URL)
            cached = _save_cache(BOOTSTRAP_CACHE, data)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if cached is None:
                raise RuntimeError(
                    f"Could not reach the FPL API and no cached data exists: {e}"
                )
            # fall back silently to whatever we had cached
    return cached["data"], cached["fetched_at"]


def get_fixtures(force_refresh=False):
    cached = _load_cache(FIXTURES_CACHE)
    is_stale = cached is None or (time.time() - cached["fetched_at"] > CACHE_TTL)

    if force_refresh or is_stale:
        try:
            data = _fetch_json(FIXTURES_URL)
            cached = _save_cache(FIXTURES_CACHE, data)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if cached is None:
                raise RuntimeError(
                    f"Could not reach the FPL API and no cached data exists: {e}"
                )
    return cached["data"], cached["fetched_at"]


def current_and_next_events(bootstrap):
    """
    Returns (current_event_dict_or_None, next_event_dict_or_None) from bootstrap['events'].
    """
    events = bootstrap.get("events", [])
    current = next((e for e in events if e.get("is_current")), None)
    nxt = next((e for e in events if e.get("is_next")), None)
    if nxt is None:
        # fall back: first event that hasn't finished
        upcoming = [e for e in events if not e.get("finished")]
        nxt = upcoming[0] if upcoming else None
    return current, nxt
