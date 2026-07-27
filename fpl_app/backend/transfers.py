"""
transfers.py
Given an existing 15-man squad, suggests the best set of transfers within
a requested transfer count, weighing point hits (-4 per transfer beyond
the free allowance) against the projected score gain of each swap.

Simplification: sell price is treated as equal to current market price
(FPL's real sell-on-profit-halving rule needs purchase-price history we
don't track precisely; this is noted to the user in the API response).
"""

MAX_PER_CLUB = 3
POINT_HIT_PER_EXTRA_TRANSFER = 4


def _to_units(millions):
    return int(round(millions * 10))


def _from_units(units):
    return round(units / 10.0, 1)


def _club_counts(players):
    counts = {}
    for p in players:
        counts[p["team_id"]] = counts.get(p["team_id"], 0) + 1
    return counts


def _best_single_swaps(current_squad, all_players_by_pos, bank_units):
    """
    For every player currently in the squad, find the best legal
    replacement (same position, not already owned, respects club limit
    once the incumbent is removed, affordable using bank + incumbent's
    sale value) and the resulting score delta.

    Returns a list of candidate swaps sorted by score delta descending:
    {out: player, in: player, cost_delta_units, score_delta}
    """
    owned_ids = {p["id"] for p in current_squad}
    counts = _club_counts(current_squad)

    candidates = []
    for incumbent in current_squad:
        pos = incumbent["position"]
        sale_value = _to_units(incumbent["price"])
        budget_for_slot = bank_units + sale_value
        counts_without = dict(counts)
        counts_without[incumbent["team_id"]] -= 1

        for alt in all_players_by_pos.get(pos, []):
            if alt["id"] in owned_ids:
                continue
            if _to_units(alt["price"]) > budget_for_slot:
                continue
            if counts_without.get(alt["team_id"], 0) >= MAX_PER_CLUB:
                continue
            score_delta = alt["score"] - incumbent["score"]
            if score_delta <= 0:
                continue
            candidates.append({
                "out": incumbent,
                "in": alt,
                "cost_delta_units": _to_units(alt["price"]) - sale_value,
                "score_delta": score_delta,
            })

    candidates.sort(key=lambda c: c["score_delta"], reverse=True)
    return candidates


def suggest_transfers(current_squad, all_rated_players, bank_millions,
                       free_transfers, transfers_wanted, use_full_transfers=False):
    """
    Returns {
      recommended_count, swaps: [...], total_score_gain, point_hit,
      net_gain, bank_after, message
    }
    """
    by_pos = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in all_rated_players:
        if p["position"] in by_pos:
            by_pos[p["position"]].append(p)

    bank_units = _to_units(bank_millions)
    max_k = max(transfers_wanted, 0)
    if max_k == 0:
        return {
            "recommended_count": 0,
            "swaps": [],
            "total_score_gain": 0.0,
            "point_hit": 0,
            "net_gain": 0.0,
            "bank_after": bank_millions,
            "message": "No transfers requested this gameweek.",
        }

    # Greedily build up to max_k non-conflicting swaps, tracking bank as we go.
    working_squad = current_squad[:]
    working_bank = bank_units
    chosen_swaps = []
    cumulative_gain = 0.0
    gain_after_k = []  # (k, cumulative_gain, bank_after) snapshots

    for k in range(1, max_k + 1):
        pool = _best_single_swaps(working_squad, by_pos, working_bank)
        if not pool:
            break
        best = pool[0]
        working_squad = [p for p in working_squad if p["id"] != best["out"]["id"]] + [best["in"]]
        working_bank -= best["cost_delta_units"]
        cumulative_gain += best["score_delta"]
        chosen_swaps.append(best)
        gain_after_k.append((k, cumulative_gain, working_bank))

    if not gain_after_k:
        return {
            "recommended_count": 0,
            "swaps": [],
            "total_score_gain": 0.0,
            "point_hit": 0,
            "net_gain": 0.0,
            "bank_after": bank_millions,
            "message": "No beneficial transfers found - your squad already looks well optimised.",
        }

    # Decide how many of the queued swaps are actually worth taking, weighing
    # the -4 hit for anything beyond free_transfers, unless the user insists
    # on using the full amount they asked for.
    best_k, best_net = 0, 0.0
    for k, gain, _bank in gain_after_k:
        hit = POINT_HIT_PER_EXTRA_TRANSFER * max(0, k - free_transfers)
        net = gain - hit
        if net > best_net or (use_full_transfers and k == max_k and net >= best_net - 1e-9):
            best_net = net
            best_k = k

    if use_full_transfers and gain_after_k:
        best_k = gain_after_k[-1][0]

    final_gain = gain_after_k[best_k - 1][1] if best_k > 0 else 0.0
    final_bank = gain_after_k[best_k - 1][2] if best_k > 0 else bank_units
    final_hit = POINT_HIT_PER_EXTRA_TRANSFER * max(0, best_k - free_transfers)

    message = "Suggested transfers ready."
    if best_k < max_k and not use_full_transfers:
        message = (f"Only {best_k} of your requested {max_k} transfer(s) are worth making "
                   f"once point hits are considered.")
    if best_k == 0:
        message = "No transfer is worth the point hit right now - sitting tight is recommended."

    return {
        "recommended_count": best_k,
        "swaps": chosen_swaps[:best_k],
        "total_score_gain": round(final_gain, 2),
        "point_hit": final_hit,
        "net_gain": round(final_gain - final_hit, 2),
        "bank_after": _from_units(final_bank),
        "message": message,
    }
