"""
server.py
Runs the FPL Assistant: a stdlib-only HTTP server that serves the static
frontend and a small JSON API backed by live FPL data.

Accounts are admin-provisioned only (see auth.py / FPL_USERS env var) -
there is no sign-up route. Each account gets its own saved squad, so the
same login shows the same team on every device, and different logins
never see each other's data.

Run with:  python3 backend/server.py
Then open: http://localhost:8765  (or whatever $PORT Render assigns)
"""

import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth
import fpl_api
import data_store
import ratings
import optimizer
import lineup
import transfers

# Render (and most PaaS hosts) assign the port via $PORT - fall back to the
# original default for local runs.
PORT = int(os.environ.get("PORT", 8765))
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

# in-process cache of rated players, refreshed at most once every 10 minutes.
# Shared across users since it's just public FPL data, not user-specific.
_rating_cache = {"players": None, "computed_at": 0, "bootstrap": None}
RATING_CACHE_TTL = 600

# Endpoints reachable without a session cookie.
PUBLIC_GET_PATHS = {"/api/session"}
PUBLIC_POST_PATHS = {"/api/login"}


def get_rated_players(force=False):
    now = time.time()
    if force or _rating_cache["players"] is None or (now - _rating_cache["computed_at"] > RATING_CACHE_TTL):
        bootstrap, _ = fpl_api.get_bootstrap(force_refresh=force)
        fixtures, _ = fpl_api.get_fixtures(force_refresh=force)
        _rating_cache["players"] = ratings.rate_players(bootstrap, fixtures)
        _rating_cache["computed_at"] = now
        _rating_cache["bootstrap"] = bootstrap
    return _rating_cache["players"], _rating_cache["bootstrap"]


def players_by_id(rated_players):
    return {p["id"]: p for p in rated_players}


def refresh_squad_scores(squad_picks, rated_players):
    """Re-attach the freshest rating data to a saved squad's player ids."""
    by_id = players_by_id(rated_players)
    refreshed = []
    for pick in squad_picks:
        p = by_id.get(pick["id"])
        if p:
            refreshed.append(p)
    return refreshed


class Handler(BaseHTTPRequestHandler):
    server_version = "FPLAssistant/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ------------------------------------------------------------ utils --

    def _is_secure(self):
        # Render terminates TLS at its edge and forwards this header; also
        # treat any environment with a RENDER var set as "secure" so the
        # cookie always gets marked Secure in production.
        return self.headers.get("X-Forwarded-Proto", "") == "https" or bool(os.environ.get("RENDER"))

    def _current_username(self):
        cookies = auth.parse_cookies(self.headers.get("Cookie"))
        token = cookies.get(auth.SESSION_COOKIE)
        return auth.get_username(token)

    def _send_json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message, status=400):
        self._send_json({"error": message}, status=status)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        safe_path = os.path.normpath(path).lstrip("/")
        full_path = os.path.join(FRONTEND_DIR, safe_path)
        if not full_path.startswith(FRONTEND_DIR) or not os.path.isfile(full_path):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        ext = os.path.splitext(full_path)[1]
        content_type = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
        }.get(ext, "application/octet-stream")

        with open(full_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------ GET ----

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/api/session":
                return self._handle_session()

            if path.startswith("/api/"):
                username = self._current_username()
                if username is None:
                    return self._send_error_json("Not authenticated", 401)
                if path == "/api/status":
                    return self._handle_status(username)
                if path == "/api/players":
                    return self._handle_players(query)
                if path == "/api/teams":
                    return self._handle_teams()
                if path == "/api/squad":
                    return self._handle_get_squad(username)
                if path == "/api/differentials":
                    return self._handle_differentials(query)
                if path == "/api/fixture-ticker":
                    return self._handle_fixture_ticker()
                return self._send_error_json("Unknown endpoint", 404)

            return self._serve_static(path)
        except Exception as e:
            traceback.print_exc()
            self._send_error_json(f"Server error: {e}", 500)

    # ----------------------------------------------------------- POST ----

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            return self._send_error_json("Malformed JSON body", 400)

        try:
            if path == "/api/login":
                return self._handle_login(body)

            username = self._current_username()
            if username is None:
                return self._send_error_json("Not authenticated", 401)

            if path == "/api/logout":
                return self._handle_logout()
            if path == "/api/gameweek":
                return self._handle_gameweek_submit(body, username)
            if path == "/api/compare":
                return self._handle_compare(body)
            if path == "/api/reset":
                return self._handle_reset(username)
            return self._send_error_json("Unknown endpoint", 404)
        except Exception as e:
            traceback.print_exc()
            self._send_error_json(f"Server error: {e}", 500)

    # -------------------------------------------------------- auth api ----

    def _handle_login(self, body):
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        if not auth.verify_login(username, password):
            return self._send_error_json("Invalid username or password", 401)
        token = auth.create_session(username)
        cookie = auth.build_set_cookie(token, secure=self._is_secure())
        self._send_json({"username": username}, extra_headers={"Set-Cookie": cookie})

    def _handle_logout(self):
        cookies = auth.parse_cookies(self.headers.get("Cookie"))
        token = cookies.get(auth.SESSION_COOKIE)
        auth.destroy_session(token)
        cookie = auth.build_clear_cookie(secure=self._is_secure())
        self._send_json({"message": "Logged out."}, extra_headers={"Set-Cookie": cookie})

    def _handle_session(self):
        username = self._current_username()
        self._send_json({"authenticated": username is not None, "username": username})

    # ------------------------------------------------------- handlers ----

    def _handle_status(self, username):
        bootstrap, _ = fpl_api.get_bootstrap()
        current_event, next_event = fpl_api.current_and_next_events(bootstrap)
        target_event = current_event or next_event

        last_answered = data_store.last_answered_gameweek(username)
        needs_answer = target_event is not None and last_answered != target_event["id"]
        squad = data_store.load_squad(username)

        self._send_json({
            "username": username,
            "gameweek": target_event["id"] if target_event else None,
            "deadline_time": target_event["deadline_time"] if target_event else None,
            "gameweek_name": target_event["name"] if target_event else None,
            "needs_weekly_answer": needs_answer,
            "has_squad": squad is not None,
            "last_answered_gameweek": last_answered,
        })

    def _handle_players(self, query):
        rated_players, _ = get_rated_players()
        position = (query.get("position") or [None])[0]
        search = (query.get("search") or [None])[0]
        team = (query.get("team") or [None])[0]
        min_price = (query.get("min_price") or [None])[0]
        max_price = (query.get("max_price") or [None])[0]

        results = rated_players
        if position:
            results = [p for p in results if p["position"] == position.upper()]
        if search:
            s = search.lower()
            results = [p for p in results if s in p["web_name"].lower() or s in p["full_name"].lower()]
        if team:
            results = [p for p in results if p["team_short"].lower() == team.lower()]
        if min_price:
            results = [p for p in results if p["price"] >= float(min_price)]
        if max_price:
            results = [p for p in results if p["price"] <= float(max_price)]

        results = sorted(results, key=lambda p: p["score"], reverse=True)
        self._send_json({"players": results[:500]})

    def _handle_teams(self):
        _, bootstrap = get_rated_players()
        teams = sorted(
            [{"id": t["id"], "short_name": t["short_name"], "name": t["name"]} for t in bootstrap["teams"]],
            key=lambda t: t["name"],
        )
        self._send_json({"teams": teams})

    def _handle_differentials(self, query):
        """
        FPL-help feature: low-ownership, high-score players worth a punt -
        the kind of pick that can separate you from a big chunk of your
        mini-league if it comes off.
        """
        rated_players, _ = get_rated_players()
        max_ownership = float((query.get("max_ownership") or [10.0])[0])
        position = (query.get("position") or [None])[0]

        pool = [p for p in rated_players if p["selected_by_percent"] <= max_ownership]
        if position:
            pool = [p for p in pool if p["position"] == position.upper()]
        pool.sort(key=lambda p: p["score"], reverse=True)

        self._send_json({"players": pool[:30], "max_ownership": max_ownership})

    def _handle_fixture_ticker(self):
        """
        FPL-help feature: next-5-fixture difficulty ticker per club, so you
        can see good/bad fixture swings at a glance when planning transfers.
        """
        _, bootstrap = get_rated_players()
        fixtures, _ = fpl_api.get_fixtures()
        teams_by_id = {t["id"]: t for t in bootstrap["teams"]}
        ticker = ratings.build_fixture_ticker(fixtures, teams_by_id)

        teams_out = []
        for t in sorted(bootstrap["teams"], key=lambda t: t["short_name"]):
            teams_out.append({
                "team_id": t["id"],
                "team_short": t["short_name"],
                "team_name": t["name"],
                "fixtures": ticker.get(t["id"], []),
            })
        self._send_json({"teams": teams_out})

    def _handle_get_squad(self, username):
        squad = data_store.load_squad(username)
        if squad is None:
            return self._send_json({"squad": None})

        rated_players, _ = get_rated_players()
        fresh_squad = refresh_squad_scores(squad["picks"], rated_players)
        if len(fresh_squad) != len(squad["picks"]):
            return self._send_json({
                "squad": None,
                "warning": "Some previously saved players are no longer in the FPL dataset. Please rebuild your squad.",
            })

        lineup_result = lineup.pick_starting_xi(fresh_squad, squad.get("formation"))

        self._send_json({
            "squad": {
                "gameweek": squad["gameweek"],
                "bank": squad["bank"],
                "free_transfers": squad["free_transfers"],
                "picks": fresh_squad,
                "formation": lineup_result["formation"],
                "starting": lineup_result["starting"],
                "bench": lineup_result["bench"],
                "captain": lineup_result["captain"],
                "vice_captain": lineup_result["vice_captain"],
                "updated_at": squad.get("updated_at"),
            }
        })

    def _handle_gameweek_submit(self, body, username):
        budget = float(body.get("budget", 100.0))
        transfers_wanted = int(body.get("transfers_wanted", 0))
        required_ids = [int(i) for i in body.get("required_player_ids", [])]
        formation = body.get("formation") or None
        use_full_budget = bool(body.get("use_full_budget", False))
        use_full_transfers = bool(body.get("use_full_transfers", False))

        rated_players, bootstrap = get_rated_players(force=True)
        current_event, next_event = fpl_api.current_and_next_events(bootstrap)
        target_event = current_event or next_event
        gw_id = target_event["id"] if target_event else 0

        existing_squad = data_store.load_squad(username)
        result_payload = {}

        if existing_squad is None:
            build_result = optimizer.build_squad(
                rated_players, budget_millions=budget,
                required_player_ids=required_ids, use_full_budget=use_full_budget,
            )
            if not build_result["feasible"]:
                return self._send_error_json(build_result["message"], 422)

            squad_players = build_result["squad"]
            lineup_result = lineup.pick_starting_xi(squad_players, formation)

            data_store.save_squad(username, {
                "gameweek": gw_id,
                "bank": build_result["leftover"],
                "free_transfers": 1,
                "formation": lineup_result["formation"],
                "picks": [{"id": p["id"], "position": p["position"], "purchase_price": p["price"]} for p in squad_players],
            })
            result_payload = {
                "mode": "build",
                "spent": build_result["spent"],
                "leftover": build_result["leftover"],
                "squad": squad_players,
                "formation": lineup_result["formation"],
                "starting": lineup_result["starting"],
                "bench": lineup_result["bench"],
                "captain": lineup_result["captain"],
                "vice_captain": lineup_result["vice_captain"],
            }
        else:
            fresh_squad = refresh_squad_scores(existing_squad["picks"], rated_players)
            transfer_result = transfers.suggest_transfers(
                fresh_squad, rated_players,
                bank_millions=existing_squad["bank"],
                free_transfers=existing_squad.get("free_transfers", 1),
                transfers_wanted=transfers_wanted,
                use_full_transfers=use_full_transfers,
            )

            new_squad = fresh_squad[:]
            for swap in transfer_result["swaps"]:
                new_squad = [p for p in new_squad if p["id"] != swap["out"]["id"]] + [swap["in"]]

            lineup_result = lineup.pick_starting_xi(new_squad, formation)

            remaining_free = max(0, existing_squad.get("free_transfers", 1) - transfer_result["recommended_count"])
            next_free_transfers = min(5, remaining_free + 1)  # FPL banks up to 5 free transfers

            data_store.save_squad(username, {
                "gameweek": gw_id,
                "bank": transfer_result["bank_after"],
                "free_transfers": next_free_transfers,
                "formation": lineup_result["formation"],
                "picks": [{"id": p["id"], "position": p["position"], "purchase_price": p["price"]} for p in new_squad],
            })
            result_payload = {
                "mode": "transfer",
                "transfer_summary": transfer_result,
                "squad": new_squad,
                "formation": lineup_result["formation"],
                "starting": lineup_result["starting"],
                "bench": lineup_result["bench"],
                "captain": lineup_result["captain"],
                "vice_captain": lineup_result["vice_captain"],
            }

        data_store.record_gameweek_answer(username, {
            "gameweek": gw_id,
            "budget": budget,
            "transfers_wanted": transfers_wanted,
            "required_player_ids": required_ids,
            "formation": formation,
            "use_full_budget": use_full_budget,
            "use_full_transfers": use_full_transfers,
        })

        self._send_json(result_payload)

    def _handle_compare(self, body):
        ids = [int(i) for i in body.get("ids", [])]
        rated_players, _ = get_rated_players()
        by_id = players_by_id(rated_players)
        found = [by_id[i] for i in ids if i in by_id]
        missing = [i for i in ids if i not in by_id]
        self._send_json({"players": found, "missing_ids": missing})

    def _handle_reset(self, username):
        data_store.reset_user_data(username)
        self._send_json({"message": "Squad and gameweek history cleared."})


def main():
    os.makedirs(data_store.DATA_DIR, exist_ok=True)
    if not auth.USERS:
        print("WARNING: no FPL_USERS environment variable set - nobody will be able to log in.")
        print("Set it like:  FPL_USERS=alice:somepassword,bob:otherpassword")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"FPL Assistant running at http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
