"""
optimizer.py
Builds an optimal 15-man FPL squad (2 GK / 5 DEF / 5 MID / 3 FWD) within a
budget, honouring the "max 3 players per real club" rule and any
user-required players - using a knapsack-style dynamic program plus a
constraint-repair pass. Pure stdlib, no numpy/scipy.

Money is handled in integer tenths of a million (e.g. 105 = 10.5m) so DP
indices are clean integers.

Algorithm outline
------------------
1. Force-include any user-required players; deduct their cost/slots/club
   counts up front.
2. For each position, run a bounded 0/1 knapsack DP over a filtered
   candidate pool to find, for every (count chosen, cost spent) pair, the
   best achievable total score. This yields one table per position.
3. Combine the four position tables left-to-right with a classic knapsack
   "merge" (for each total budget, best split across the remaining
   groups) to find the overall best combination for the *remaining*
   budget after required players are paid for.
4. Backtrack through the DP tables to recover the actual picks.
5. Repair pass: if any club ended up with more than 3 players, swap the
   weakest offending picks for the best legal alternative in the same
   position and budget envelope.
6. Optional "use full budget" pass: if there's slack left, try upgrading
   the weakest-value picks to spend it, provided a like-for-like
   upgrade improves score.
"""

import copy

MAX_PER_CLUB = 3
SQUAD_REQUIREMENTS = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
CANDIDATE_POOL_SIZE = 45  # per position, trimmed by score to keep DP fast

NEG_INF = float("-inf")


def _to_units(millions):
    return int(round(millions * 10))


def _from_units(units):
    return round(units / 10.0, 1)


def _filter_candidates(players_in_pos, required_ids, pool_size):
    required = [p for p in players_in_pos if p["id"] in required_ids]
    rest = [p for p in players_in_pos if p["id"] not in required_ids]
    rest.sort(key=lambda p: p["score"], reverse=True)
    trimmed = rest[:pool_size]
    return required, trimmed


def _knapsack_exact_count(items, count_needed, budget_units):
    """
    0/1 knapsack: choose exactly `count_needed` items from `items` with
    total cost <= budget_units, maximizing total score.

    Returns dp[c][cost] = best score (NEG_INF if unreachable), and a
    parallel `choice` table to backtrack, where choice[c][cost] is either
    None (not this item) or the item index taken to arrive here.

    Implemented as a rolling table over items, dimensions (count, cost).
    """
    n = len(items)
    max_c = count_needed
    # dp[c][cost] built incrementally; we keep the full history per item
    # so we can backtrack (memory is small: n * max_c * budget_units).
    dp = [[NEG_INF] * (budget_units + 1) for _ in range(max_c + 1)]
    dp[0][0] = 0.0
    # history[i][c][cost] = True if item i was taken to reach dp[c][cost]
    history = []

    for item in items:
        cost = _to_units(item["price"])
        score = item["score"]
        new_dp = [row[:] for row in dp]
        taken = [[False] * (budget_units + 1) for _ in range(max_c + 1)]
        for c in range(max_c - 1, -1, -1):
            for b in range(budget_units - cost, -1, -1):
                if dp[c][b] == NEG_INF:
                    continue
                candidate = dp[c][b] + score
                if candidate > new_dp[c + 1][b + cost]:
                    new_dp[c + 1][b + cost] = candidate
                    taken[c + 1][b + cost] = True
        dp = new_dp
        history.append(taken)

    return dp, history


def _backtrack_exact_count(items, history, dp, count_needed, budget_units):
    """Recover which items were chosen for the best dp[count_needed][cost]."""
    best_cost = None
    best_score = NEG_INF
    for cost in range(budget_units + 1):
        if dp[count_needed][cost] > best_score:
            best_score = dp[count_needed][cost]
            best_cost = cost
    if best_cost is None or best_score == NEG_INF:
        return [], NEG_INF

    chosen = []
    c, b = count_needed, best_cost
    for i in range(len(items) - 1, -1, -1):
        if c == 0:
            break
        if history[i][c][b]:
            item = items[i]
            chosen.append(item)
            b -= _to_units(item["price"])
            c -= 1
    return chosen, best_score


def _build_position_options(players_in_pos, count_needed, required_ids, budget_units):
    """
    Returns list of (cost_units, score, chosen_players) representing the
    best achievable score for spending exactly `cost_units` on this
    position (for every reachable cost), including the required players.
    """
    required, candidates = _filter_candidates(players_in_pos, required_ids, CANDIDATE_POOL_SIZE)
    required_cost = sum(_to_units(p["price"]) for p in required)
    required_score = sum(p["score"] for p in required)
    remaining_needed = count_needed - len(required)
    remaining_budget = budget_units - required_cost

    if remaining_needed < 0 or remaining_budget < 0:
        return []  # infeasible: too many required players or over budget already

    if remaining_needed == 0:
        return [(required_cost, required_score, required)]

    dp, history = _knapsack_exact_count(candidates, remaining_needed, remaining_budget)

    options = []
    for cost in range(remaining_budget + 1):
        if dp[remaining_needed][cost] == NEG_INF:
            continue
        chosen, score = _backtrack_exact_count(candidates, history, dp, remaining_needed, cost)
        if score == NEG_INF:
            continue
        options.append((cost + required_cost, score + required_score, chosen + required))

    if not options:
        return []

    # keep only the pareto-best score for each cost, and make it monotonic
    # (spending more should never look worse) to make later merging clean.
    best_by_cost = {}
    for cost, score, chosen in options:
        if cost not in best_by_cost or score > best_by_cost[cost][0]:
            best_by_cost[cost] = (score, chosen)

    return sorted(((c, s, chosen) for c, (s, chosen) in best_by_cost.items()), key=lambda x: x[0])


def _merge_position_tables(tables, total_budget_units):
    """
    tables: list of per-position option lists [(cost, score, players), ...]
    Returns the best combination across all positions for every possible
    total spend, then picks the best overall <= total_budget_units.
    """
    # running[cost] = (score, [players...])
    running = {0: (0.0, [])}

    for table in tables:
        if not table:
            return None  # a position was infeasible -> whole squad infeasible
        new_running = {}
        for prev_cost, (prev_score, prev_players) in running.items():
            for cost, score, players in table:
                total_cost = prev_cost + cost
                if total_cost > total_budget_units:
                    continue
                total_score = prev_score + score
                if total_cost not in new_running or total_score > new_running[total_cost][0]:
                    new_running[total_cost] = (total_score, prev_players + players)
        running = new_running
        if not running:
            return None

    best_cost = max(running.keys(), key=lambda c: running[c][0])
    score, players = running[best_cost]
    return best_cost, score, players


def _repair_club_limits(squad, all_players_by_pos, budget_units_total, spent_units):
    """
    If any club has > MAX_PER_CLUB players, swap the weakest offender(s)
    out for the best legal replacement in the same position that fits the
    remaining budget. Greedy, deterministic, converges quickly in practice.
    """
    squad = squad[:]
    remaining_budget = budget_units_total - spent_units

    def club_counts(players):
        counts = {}
        for p in players:
            counts[p["team_id"]] = counts.get(p["team_id"], 0) + 1
        return counts

    changed = True
    guard = 0
    while changed and guard < 50:
        changed = False
        guard += 1
        counts = club_counts(squad)
        offending_clubs = [tid for tid, n in counts.items() if n > MAX_PER_CLUB]
        if not offending_clubs:
            break

        for tid in offending_clubs:
            offenders = [p for p in squad if p["team_id"] == tid]
            offenders.sort(key=lambda p: p["score"])  # weakest first
            weakest = offenders[0]
            pos = weakest["position"]
            current_ids = {p["id"] for p in squad}
            pos_budget_available = remaining_budget + _to_units(weakest["price"])

            candidates = [
                p for p in all_players_by_pos[pos]
                if p["id"] not in current_ids
                and counts.get(p["team_id"], 0) < MAX_PER_CLUB
                and _to_units(p["price"]) <= pos_budget_available
            ]
            candidates.sort(key=lambda p: p["score"], reverse=True)
            if not candidates:
                continue  # can't legally fix this one; leave it (rare edge case)

            replacement = candidates[0]
            squad.remove(weakest)
            squad.append(replacement)
            remaining_budget = pos_budget_available - _to_units(replacement["price"])
            changed = True
            break  # recompute counts fresh each loop

    return squad, remaining_budget


def _spend_leftover(squad, all_players_by_pos, remaining_budget_units):
    """
    If use_full_budget is requested and there's slack, greedily upgrade the
    weakest value-for-money pick(s) to soak up leftover budget, as long as
    it strictly improves total score and keeps club/budget legal.
    """
    squad = squad[:]
    guard = 0
    while remaining_budget_units > 0 and guard < 30:
        guard += 1

        def club_counts(players):
            counts = {}
            for p in players:
                counts[p["team_id"]] = counts.get(p["team_id"], 0) + 1
            return counts

        counts = club_counts(squad)
        squad_sorted = sorted(squad, key=lambda p: p["score"])
        upgraded = False

        for weakest in squad_sorted:
            pos = weakest["position"]
            budget_for_slot = remaining_budget_units + _to_units(weakest["price"])
            current_ids = {p["id"] for p in squad}
            counts_without = dict(counts)
            counts_without[weakest["team_id"]] -= 1

            candidates = [
                p for p in all_players_by_pos[pos]
                if p["id"] not in current_ids
                and p["score"] > weakest["score"]
                and _to_units(p["price"]) <= budget_for_slot
                and counts_without.get(p["team_id"], 0) < MAX_PER_CLUB
            ]
            if not candidates:
                continue
            candidates.sort(key=lambda p: (p["score"], p["price"]), reverse=True)
            best = candidates[0]
            squad.remove(weakest)
            squad.append(best)
            remaining_budget_units = budget_for_slot - _to_units(best["price"])
            upgraded = True
            break

        if not upgraded:
            break

    return squad, remaining_budget_units


def build_squad(rated_players, budget_millions, required_player_ids=None,
                 use_full_budget=False):
    """
    Main entry point.

    rated_players: output of ratings.rate_players()
    budget_millions: e.g. 100.0
    required_player_ids: list of player ids the user wants included
    use_full_budget: if True, try to spend leftover money on upgrades

    Returns dict: {squad: [players...15], spent, leftover, feasible, message}
    """
    required_player_ids = set(required_player_ids or [])
    budget_units = _to_units(budget_millions)

    by_pos = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in rated_players:
        if p["position"] in by_pos:
            by_pos[p["position"]].append(p)

    # sanity check: are all required players findable and not over-subscribed
    # per position (e.g. can't require 6 defenders)?
    required_by_pos = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    all_players_by_id = {p["id"]: p for p in rated_players}
    for rid in required_player_ids:
        p = all_players_by_id.get(rid)
        if p is None:
            continue
        required_by_pos[p["position"]].append(rid)

    for pos, need in SQUAD_REQUIREMENTS.items():
        if len(required_by_pos[pos]) > need:
            return {
                "feasible": False,
                "message": f"You required {len(required_by_pos[pos])} {pos}s but a squad only has {need}.",
                "squad": [],
            }

    tables = []
    for pos, need in SQUAD_REQUIREMENTS.items():
        table = _build_position_options(by_pos[pos], need, required_player_ids, budget_units)
        tables.append(table)

    merged = _merge_position_tables(tables, budget_units)
    if merged is None:
        return {
            "feasible": False,
            "message": "No valid 15-man squad fits that budget with those required players. Try a higher budget or fewer required players.",
            "squad": [],
        }

    spent_units, score, squad = merged

    squad, remaining_after_repair = _repair_club_limits(squad, by_pos, budget_units, spent_units)

    if use_full_budget:
        squad, remaining_after_repair = _spend_leftover(squad, by_pos, remaining_after_repair)

    total_spent = budget_units - remaining_after_repair

    return {
        "feasible": True,
        "message": "Squad built successfully.",
        "squad": squad,
        "spent": _from_units(total_spent),
        "leftover": _from_units(remaining_after_repair),
        "budget": budget_millions,
    }
