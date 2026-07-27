"""
ratings.py
Turns raw bootstrap-static + fixtures data into a single comparable
"predicted score" per player, plus the individual signals that feed it.

No external libraries - just arithmetic over stdlib data structures.
"""

ELEMENT_TYPE_TO_POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# How many upcoming fixtures to look at for the fixture-difficulty signal.
FIXTURE_LOOKAHEAD = 4

# Weights for the composite rating. Tuned to favour current form + underlying
# attacking output, with fixture difficulty as a swing factor and a small
# penalty for players who barely play.
WEIGHTS = {
    "form": 2.2,
    "points_per_game": 1.4,
    "ict_index": 0.06,
    "expected_involvement": 3.0,
    "value": 0.8,
    "fixture": 1.6,
    "availability_penalty": 1.0,
}


def _safe_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_fixture_difficulty_map(fixtures, teams_by_id, lookahead=FIXTURE_LOOKAHEAD):
    """
    Returns {team_id: average_difficulty_of_next_N_unfinished_fixtures}
    Lower difficulty = easier games = better for attacking returns.
    Falls back to 3.0 (neutral) if a team has no upcoming fixtures found.
    """
    upcoming_by_team = {tid: [] for tid in teams_by_id}

    # sort by kickoff so "next N" is chronological; unfinished fixtures only
    unfinished = [f for f in fixtures if not f.get("finished")]
    unfinished.sort(key=lambda f: (f.get("event") is None, f.get("event") or 9999, f.get("id", 0)))

    for f in unfinished:
        home_id = f.get("team_h")
        away_id = f.get("team_a")
        home_diff = f.get("team_h_difficulty")
        away_diff = f.get("team_a_difficulty")
        if home_id in upcoming_by_team and len(upcoming_by_team[home_id]) < lookahead and home_diff:
            upcoming_by_team[home_id].append(home_diff)
        if away_id in upcoming_by_team and len(upcoming_by_team[away_id]) < lookahead and away_diff:
            upcoming_by_team[away_id].append(away_diff)

    avg_map = {}
    for tid, diffs in upcoming_by_team.items():
        avg_map[tid] = (sum(diffs) / len(diffs)) if diffs else 3.0
    return avg_map


def build_fixture_ticker(fixtures, teams_by_id, lookahead=5):
    """
    Returns {team_id: [ {opponent, opponent_short, is_home, difficulty, event}, ... ]}
    for the next `lookahead` unfinished fixtures per team, chronologically
    ordered. Used for the fixture-difficulty ticker feature.
    """
    ticker = {tid: [] for tid in teams_by_id}

    unfinished = [f for f in fixtures if not f.get("finished")]
    unfinished.sort(key=lambda f: (f.get("event") is None, f.get("event") or 9999, f.get("id", 0)))

    for f in unfinished:
        home_id = f.get("team_h")
        away_id = f.get("team_a")
        home_diff = f.get("team_h_difficulty")
        away_diff = f.get("team_a_difficulty")

        if home_id in ticker and len(ticker[home_id]) < lookahead:
            opp = teams_by_id.get(away_id, {})
            ticker[home_id].append({
                "opponent": opp.get("name", "?"),
                "opponent_short": opp.get("short_name", "?"),
                "is_home": True,
                "difficulty": home_diff or 3,
                "event": f.get("event"),
            })
        if away_id in ticker and len(ticker[away_id]) < lookahead:
            opp = teams_by_id.get(home_id, {})
            ticker[away_id].append({
                "opponent": opp.get("name", "?"),
                "opponent_short": opp.get("short_name", "?"),
                "is_home": False,
                "difficulty": away_diff or 3,
                "event": f.get("event"),
            })

    return ticker


def _expected_involvement(player):
    """
    Combine expected goals + expected assists (season totals from the API,
    which are strings like '4.52') into a per-90-esque signal, scaled down
    so it sits in a similar range to the other components.
    """
    xg = _safe_float(player.get("expected_goals"))
    xa = _safe_float(player.get("expected_assists"))
    minutes = _safe_float(player.get("minutes"))
    if minutes < 45:
        return 0.0
    involvement_per_90 = (xg + xa) / (minutes / 90.0)
    return involvement_per_90


def _availability_penalty(player):
    """
    0 = fully available, larger = more concerning.
    chance_of_playing_next_round: None (fit) or 0-100.
    """
    chance = player.get("chance_of_playing_next_round")
    if chance is None:
        return 0.0
    chance = _safe_float(chance, 100.0)
    return (100.0 - chance) / 100.0  # 0..1


def rate_players(bootstrap, fixtures):
    """
    Returns a list of enriched player dicts, each with:
      id, web_name, team_id, team_short, position, price, form, points_per_game,
      total_points, selected_by_percent, ict_index, expected_goals, expected_assists,
      fixture_difficulty, availability, status, news, score (composite rating)
    """
    teams_by_id = {t["id"]: t for t in bootstrap["teams"]}
    fixture_diff = build_fixture_difficulty_map(fixtures, teams_by_id)

    enriched = []
    for p in bootstrap["elements"]:
        pos = ELEMENT_TYPE_TO_POS.get(p["element_type"], "UNK")
        team = teams_by_id.get(p["team"], {})
        price = _safe_float(p.get("now_cost")) / 10.0
        form = _safe_float(p.get("form"))
        ppg = _safe_float(p.get("points_per_game"))
        ict = _safe_float(p.get("ict_index"))
        involvement = _expected_involvement(p)
        fdr = fixture_diff.get(p["team"], 3.0)
        # invert difficulty (1=easy..5=hard) into a 0..1 "friendliness" score
        fixture_friendliness = (5.0 - fdr) / 4.0
        avail_penalty = _availability_penalty(p)
        value = (form / price) if price > 0 else 0.0

        score = (
            WEIGHTS["form"] * form
            + WEIGHTS["points_per_game"] * ppg
            + WEIGHTS["ict_index"] * ict
            + WEIGHTS["expected_involvement"] * involvement
            + WEIGHTS["value"] * value
            + WEIGHTS["fixture"] * fixture_friendliness
            - WEIGHTS["availability_penalty"] * avail_penalty
        )

        enriched.append({
            "id": p["id"],
            "web_name": p.get("web_name"),
            "full_name": f"{p.get('first_name', '')} {p.get('second_name', '')}".strip(),
            "team_id": p["team"],
            "team_short": team.get("short_name", "?"),
            "team_name": team.get("name", "?"),
            "position": pos,
            "price": round(price, 1),
            "form": form,
            "points_per_game": ppg,
            "total_points": p.get("total_points", 0),
            "selected_by_percent": _safe_float(p.get("selected_by_percent")),
            "ict_index": ict,
            "expected_goals": _safe_float(p.get("expected_goals")),
            "expected_assists": _safe_float(p.get("expected_assists")),
            "goals_scored": p.get("goals_scored", 0),
            "assists": p.get("assists", 0),
            "minutes": p.get("minutes", 0),
            "clean_sheets": p.get("clean_sheets", 0),
            "fixture_difficulty": round(fdr, 2),
            "availability_penalty": round(avail_penalty, 2),
            "status": p.get("status"),  # 'a' available, 'i' injured, 'd' doubtful, 's' suspended, 'u' unavailable
            "news": p.get("news", ""),
            "score": round(score, 3),
        })

    return enriched
