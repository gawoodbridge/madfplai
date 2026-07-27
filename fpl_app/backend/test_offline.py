"""
Offline sanity test using synthetic bootstrap/fixtures data shaped like the
real FPL API, since this sandbox has no network access. Exercises ratings,
optimizer (with required players + club limit + full budget), lineup
selection, and the transfer suggester.
"""
import random
import ratings
import optimizer
import lineup
import transfers

random.seed(42)

TEAMS = [{"id": i, "name": f"Team{i}", "short_name": f"T{i}"} for i in range(1, 21)]

POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
COUNTS = {1: 40, 2: 120, 3: 140, 4: 60}  # roughly realistic pool sizes

elements = []
pid = 1
for etype, n in COUNTS.items():
    for i in range(n):
        team = random.choice(TEAMS)
        price = random.randint(38, 140)  # tenths -> 3.8 to 14.0
        minutes = random.choice([0, 200, 800, 1500, 2200, 2800])
        form = round(random.uniform(0, 8), 1) if minutes > 0 else 0.0
        elements.append({
            "id": pid,
            "web_name": f"Player{pid}",
            "first_name": "First",
            "second_name": f"Last{pid}",
            "team": team["id"],
            "element_type": etype,
            "now_cost": price,
            "form": str(form),
            "points_per_game": str(round(form * random.uniform(0.8, 1.3), 1)),
            "total_points": int(form * 10),
            "selected_by_percent": str(round(random.uniform(0, 40), 1)),
            "ict_index": str(round(random.uniform(0, 200), 1)),
            "expected_goals": str(round(random.uniform(0, 15), 2)),
            "expected_assists": str(round(random.uniform(0, 10), 2)),
            "goals_scored": random.randint(0, 20),
            "assists": random.randint(0, 15),
            "minutes": minutes,
            "clean_sheets": random.randint(0, 15),
            "chance_of_playing_next_round": random.choice([None, None, None, 75, 50, 25]),
            "status": "a",
            "news": "",
        })
        pid += 1

bootstrap = {"elements": elements, "teams": TEAMS, "events": [
    {"id": 5, "is_current": True, "is_next": False, "finished": False, "deadline_time": "2026-08-01T10:00:00Z", "name": "Gameweek 5"}
]}

fixtures = []
fid = 1
for gw in range(5, 12):
    shuffled = TEAMS[:]
    random.shuffle(shuffled)
    for i in range(0, 20, 2):
        home, away = shuffled[i], shuffled[i + 1]
        fixtures.append({
            "id": fid, "event": gw, "finished": False,
            "team_h": home["id"], "team_a": away["id"],
            "team_h_difficulty": random.randint(1, 5),
            "team_a_difficulty": random.randint(1, 5),
        })
        fid += 1

print("=== Rating players ===")
rated = ratings.rate_players(bootstrap, fixtures)
print(f"Rated {len(rated)} players")
assert len(rated) == len(elements)
by_pos_count = {}
for p in rated:
    by_pos_count[p["position"]] = by_pos_count.get(p["position"], 0) + 1
print("By position:", by_pos_count)

print("\n=== Building squad (budget 100.0, no required, no full-budget) ===")
result = optimizer.build_squad(rated, budget_millions=100.0, use_full_budget=False)
print("feasible:", result["feasible"], "| message:", result["message"])
assert result["feasible"]
squad = result["squad"]
assert len(squad) == 15, f"expected 15 players, got {len(squad)}"
pos_counts = {}
club_counts = {}
for p in squad:
    pos_counts[p["position"]] = pos_counts.get(p["position"], 0) + 1
    club_counts[p["team_id"]] = club_counts.get(p["team_id"], 0) + 1
print("Position counts:", pos_counts)
print("Max per club:", max(club_counts.values()))
assert pos_counts == {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
assert max(club_counts.values()) <= 3
total_price = sum(p["price"] for p in squad)
print(f"Total spent: {total_price} (reported spent={result['spent']}, leftover={result['leftover']})")
assert total_price <= 100.0 + 1e-6
assert abs(total_price - result["spent"]) < 0.1

print("\n=== Building squad with required players + full budget ===")
required_ids = [squad[0]["id"], squad[1]["id"]]
result2 = optimizer.build_squad(rated, budget_millions=100.0, required_player_ids=required_ids, use_full_budget=True)
assert result2["feasible"]
squad2 = result2["squad"]
squad2_ids = {p["id"] for p in squad2}
assert all(rid in squad2_ids for rid in required_ids), "required players missing from squad"
print(f"Required players present. Spent: {result2['spent']}, leftover: {result2['leftover']}")
assert result2["leftover"] < result["leftover"] + 5  # sanity: full-budget shouldn't blow past total

print("\n=== Lineup selection (auto formation) ===")
lu = lineup.pick_starting_xi(squad)
print("Formation:", lu["formation"], "| Starting XI size:", len(lu["starting"]), "| Bench size:", len(lu["bench"]))
assert len(lu["starting"]) == 11
assert len(lu["bench"]) == 4
gk_starters = [p for p in lu["starting"] if p["position"] == "GK"]
assert len(gk_starters) == 1
print("Captain:", lu["captain"]["web_name"], lu["captain"]["score"])
print("Vice:", lu["vice_captain"]["web_name"], lu["vice_captain"]["score"])
assert lu["captain"]["score"] >= lu["vice_captain"]["score"]

print("\n=== Lineup selection (preferred formation 3-5-2) ===")
lu2 = lineup.pick_starting_xi(squad, preferred_formation="3-5-2")
print("Formation:", lu2["formation"])
assert lu2["formation"] == "3-5-2"

print("\n=== Transfer suggestions ===")
tr = transfers.suggest_transfers(squad, rated, bank_millions=result["leftover"], free_transfers=1, transfers_wanted=2)
print("Recommended:", tr["recommended_count"], "| net gain:", tr["net_gain"], "| message:", tr["message"])
for sw in tr["swaps"]:
    print(f"  OUT {sw['out']['web_name']} ({sw['out']['score']})  ->  IN {sw['in']['web_name']} ({sw['in']['score']})  delta={round(sw['score_delta'],2)}")

print("\nALL OFFLINE TESTS PASSED")
