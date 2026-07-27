"""
Spins up the real server.py Handler against synthetic data (monkeypatching
fpl_api's network calls, since this sandbox has no internet access) and
exercises the full HTTP request/response cycle for every route, including
login/session/logout and per-user data isolation.
"""
import http.cookiejar
import json
import os
import random
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Must be set before importing auth/server, since auth.USERS is parsed at
# import time from this environment variable.
os.environ["FPL_USERS"] = "alice:alicepass,bob:bobpass"

import fpl_api
import data_store

TEST_DATA_DIR = "/tmp/fpl_test_data"
if os.path.exists(TEST_DATA_DIR):
    shutil.rmtree(TEST_DATA_DIR)
os.makedirs(TEST_DATA_DIR)

# redirect data_store to a scratch dir so we don't touch real /data
data_store.DATA_DIR = TEST_DATA_DIR

random.seed(7)
TEAMS = [{"id": i, "name": f"Team{i}", "short_name": f"T{i}"} for i in range(1, 21)]
COUNTS = {1: 40, 2: 120, 3: 140, 4: 60}
elements = []
pid = 1
for etype, n in COUNTS.items():
    for i in range(n):
        team = random.choice(TEAMS)
        price = random.randint(38, 140)
        minutes = random.choice([0, 800, 1500, 2200, 2800])
        form = round(random.uniform(0, 8), 1) if minutes > 0 else 0.0
        elements.append({
            "id": pid, "web_name": f"Player{pid}", "first_name": "First", "second_name": f"Last{pid}",
            "team": team["id"], "element_type": etype, "now_cost": price, "form": str(form),
            "points_per_game": str(form), "total_points": int(form * 10), "selected_by_percent": "5.0",
            "ict_index": "50.0", "expected_goals": "2.0", "expected_assists": "1.0", "goals_scored": 1,
            "assists": 1, "minutes": minutes, "clean_sheets": 1, "chance_of_playing_next_round": None,
            "status": "a", "news": "",
        })
        pid += 1

FAKE_BOOTSTRAP = {"elements": elements, "teams": TEAMS, "events": [
    {"id": 5, "is_current": True, "is_next": False, "finished": False,
     "deadline_time": "2026-08-01T10:00:00Z", "name": "Gameweek 5"}
]}
FAKE_FIXTURES = []
fid = 1
for gw in range(5, 12):
    shuffled = TEAMS[:]
    random.shuffle(shuffled)
    for i in range(0, 20, 2):
        h, a = shuffled[i], shuffled[i + 1]
        FAKE_FIXTURES.append({"id": fid, "event": gw, "finished": False, "team_h": h["id"], "team_a": a["id"],
                               "team_h_difficulty": random.randint(1, 5), "team_a_difficulty": random.randint(1, 5)})
        fid += 1

fpl_api.get_bootstrap = lambda force_refresh=False: (FAKE_BOOTSTRAP, time.time())
fpl_api.get_fixtures = lambda force_refresh=False: (FAKE_FIXTURES, time.time())

import server as server_mod
from http.server import ThreadingHTTPServer

TEST_PORT = 8799
httpd = ThreadingHTTPServer(("127.0.0.1", TEST_PORT), server_mod.Handler)
thread = threading.Thread(target=httpd.serve_forever, daemon=True)
thread.start()
time.sleep(0.3)

BASE = f"http://127.0.0.1:{TEST_PORT}"


def make_client():
    """Each client gets its own cookie jar, simulating a separate browser/device."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def get(path):
        with opener.open(BASE + path, timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    def post(path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    def get_raw(path):
        with opener.open(BASE + path, timeout=10) as r:
            return r.status, r.read().decode(), dict(r.headers)

    return get, post, get_raw


try:
    print("=== Unauthenticated GET /api/status should 401 ===")
    anon_get, anon_post, _ = make_client()
    try:
        anon_get("/api/status")
        raise AssertionError("expected a 401")
    except urllib.error.HTTPError as e:
        assert e.code == 401
    print("OK")

    print("\n=== GET /api/session before login ===")
    status, body = anon_get("/api/session")
    assert status == 200
    assert body["authenticated"] is False
    print(status, body)

    print("\n=== POST /api/login with bad password ===")
    try:
        anon_post("/api/login", {"username": "alice", "password": "wrong"})
        raise AssertionError("expected a 401")
    except urllib.error.HTTPError as e:
        assert e.code == 401
    print("OK - rejected")

    print("\n=== Two separate clients (alice, bob) log in ===")
    alice_get, alice_post, _ = make_client()
    status, body = alice_post("/api/login", {"username": "alice", "password": "alicepass"})
    assert status == 200 and body["username"] == "alice"

    bob_get, bob_post, _ = make_client()
    status, body = bob_post("/api/login", {"username": "bob", "password": "bobpass"})
    assert status == 200 and body["username"] == "bob"
    print("Both logged in OK")

    print("\n=== GET /api/session after login (alice) ===")
    status, body = alice_get("/api/session")
    assert status == 200 and body["authenticated"] is True and body["username"] == "alice"

    print("\n=== GET /api/status (fresh, per-user) ===")
    status, body = alice_get("/api/status")
    assert status == 200
    assert body["needs_weekly_answer"] is True
    assert body["has_squad"] is False

    print("\n=== GET /api/players?search=Player1 ===")
    status, body = alice_get("/api/players?search=Player1")
    assert status == 200
    assert len(body["players"]) > 0

    print("\n=== alice: POST /api/gameweek (build squad, required player, full budget) ===")
    target_id = body["players"][0]["id"]
    status, alice_build = alice_post("/api/gameweek", {
        "budget": 100.0, "transfers_wanted": 0, "required_player_ids": [target_id],
        "use_full_budget": True, "use_full_transfers": False,
    })
    assert status == 200
    assert alice_build["mode"] == "build"
    assert any(p["id"] == target_id for p in alice_build["squad"])
    assert len(alice_build["squad"]) == 15
    assert len(alice_build["starting"]) == 11
    assert len(alice_build["bench"]) == 4
    print("Alice's squad built. Spent:", alice_build.get("spent"))

    print("\n=== bob: has no squad yet (separate account, separate data) ===")
    status, bob_status = bob_get("/api/status")
    assert status == 200
    assert bob_status["has_squad"] is False, "bob should not see alice's squad"
    print("Confirmed data isolation between accounts")

    print("\n=== GET /api/status (alice, after squad built) ===")
    status, body = alice_get("/api/status")
    assert body["has_squad"] is True
    assert body["needs_weekly_answer"] is False

    print("\n=== alice, second 'device' (fresh cookie jar) still needs to log in ===")
    alice_device2_get, alice_device2_post, _ = make_client()
    try:
        alice_device2_get("/api/squad")
        raise AssertionError("expected 401 before logging in on device 2")
    except urllib.error.HTTPError as e:
        assert e.code == 401
    status, body = alice_device2_post("/api/login", {"username": "alice", "password": "alicepass"})
    assert status == 200
    status, body = alice_device2_get("/api/squad")
    assert status == 200
    assert body["squad"]["formation"]
    assert any(p["id"] == target_id for p in body["squad"]["picks"]), "same account, same squad on a new device"
    print("Confirmed same account sees the same squad from a second device")

    print("\n=== alice: POST /api/gameweek again (should now suggest transfers) ===")
    status, transfer_result = alice_post("/api/gameweek", {
        "budget": 100.0, "transfers_wanted": 2, "use_full_budget": False, "use_full_transfers": False,
    })
    assert status == 200
    assert transfer_result["mode"] == "transfer"
    assert "transfer_summary" in transfer_result

    print("\n=== alice: POST /api/compare ===")
    ids = [p["id"] for p in alice_build["squad"][:3]]
    status, cmp_result = alice_post("/api/compare", {"ids": ids})
    assert status == 200
    assert len(cmp_result["players"]) == 3

    print("\n=== GET /api/differentials ===")
    status, diff_result = alice_get("/api/differentials?max_ownership=10")
    assert status == 200
    assert "players" in diff_result

    print("\n=== GET /api/fixture-ticker ===")
    status, ticker_result = alice_get("/api/fixture-ticker")
    assert status == 200
    assert len(ticker_result["teams"]) == 20
    assert len(ticker_result["teams"][0]["fixtures"]) > 0

    print("\n=== GET / (static index.html, no auth required) ===")
    with urllib.request.urlopen(BASE + "/", timeout=10) as r:
        html = r.read().decode()
        assert r.status == 200
        assert "<html" in html.lower()
    print("index.html served OK, length:", len(html))

    print("\n=== GET /style.css ===")
    with urllib.request.urlopen(BASE + "/style.css", timeout=10) as r:
        assert r.status == 200
        assert r.headers.get("Content-Type") == "text/css"
    print("style.css served OK")

    print("\n=== GET /app.js ===")
    with urllib.request.urlopen(BASE + "/app.js", timeout=10) as r:
        assert r.status == 200
    print("app.js served OK")

    print("\n=== alice: POST /api/logout then GET /api/status should 401 ===")
    status, body = alice_post("/api/logout", {})
    assert status == 200
    try:
        alice_get("/api/status")
        raise AssertionError("expected 401 after logout")
    except urllib.error.HTTPError as e:
        assert e.code == 401
    print("OK - logged out")

    print("\n=== alice logs back in and resets ===")
    status, body = alice_post("/api/login", {"username": "alice", "password": "alicepass"})
    assert status == 200
    status, body = alice_post("/api/reset", {})
    assert status == 200
    status, body = alice_get("/api/status")
    assert body["has_squad"] is False

    print("\nALL SERVER E2E TESTS PASSED")
finally:
    httpd.shutdown()
