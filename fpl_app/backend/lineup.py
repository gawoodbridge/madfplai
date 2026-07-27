"""
lineup.py
Given a 15-man squad, pick the strongest legal starting XI (and bench),
respecting FPL formation rules (1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD, 10
outfield), optionally honouring a user's preferred formation. Also picks
captain / vice-captain as the two highest-scoring starters.
"""

VALID_FORMATIONS = []
for d in range(3, 6):
    for m in range(2, 6):
        for f in range(1, 4):
            if d + m + f == 10:
                VALID_FORMATIONS.append((d, m, f))


def _top_n(players, n):
    return sorted(players, key=lambda p: p["score"], reverse=True)[:n]


def _score_formation(by_pos, d, m, f):
    gk = _top_n(by_pos["GK"], 1)
    defs = _top_n(by_pos["DEF"], d)
    mids = _top_n(by_pos["MID"], m)
    fwds = _top_n(by_pos["FWD"], f)
    starters = gk + defs + mids + fwds
    total = sum(p["score"] for p in starters)
    return total, starters


def pick_starting_xi(squad, preferred_formation=None):
    """
    squad: list of 15 rated player dicts (from optimizer.build_squad)
    preferred_formation: optional string like "3-4-3" / "4-4-2" / "3-5-2"

    Returns {
      formation: "3-4-3",
      starting: [...11 players, GK first then by position...],
      bench: [...4 players, GK first then by descending score...],
      captain: player, vice_captain: player
    }
    """
    by_pos = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in squad:
        by_pos.setdefault(p["position"], []).append(p)

    chosen_formation = None
    if preferred_formation:
        try:
            d, m, f = (int(x) for x in preferred_formation.split("-"))
        except ValueError:
            d = m = f = None
        if (d, m, f) in VALID_FORMATIONS and d <= len(by_pos["DEF"]) and m <= len(by_pos["MID"]) and f <= len(by_pos["FWD"]):
            chosen_formation = (d, m, f)

    if chosen_formation:
        total, starters = _score_formation(by_pos, *chosen_formation)
    else:
        best = None
        for (d, m, f) in VALID_FORMATIONS:
            if d > len(by_pos["DEF"]) or m > len(by_pos["MID"]) or f > len(by_pos["FWD"]):
                continue
            total, starters = _score_formation(by_pos, d, m, f)
            if best is None or total > best[0]:
                best = (total, starters, (d, m, f))
        total, starters, chosen_formation = best

    starting_ids = {p["id"] for p in starters}
    bench = [p for p in squad if p["id"] not in starting_ids]
    # bench order: outfield bench sorted by score desc, but the reserve GK
    # is conventionally listed last on the bench.
    bench_gk = [p for p in bench if p["position"] == "GK"]
    bench_outfield = sorted([p for p in bench if p["position"] != "GK"], key=lambda p: p["score"], reverse=True)
    bench_ordered = bench_outfield + bench_gk

    starters_sorted = sorted(starters, key=lambda p: (p["position"] != "GK", -p["score"]))
    ranked_by_score = sorted(starters, key=lambda p: p["score"], reverse=True)
    captain = ranked_by_score[0]
    vice_captain = ranked_by_score[1] if len(ranked_by_score) > 1 else None

    return {
        "formation": f"{chosen_formation[0]}-{chosen_formation[1]}-{chosen_formation[2]}",
        "starting": starters_sorted,
        "bench": bench_ordered,
        "captain": captain,
        "vice_captain": vice_captain,
    }
