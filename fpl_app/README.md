# FPL Assistant

A self-contained tool that uses live Fantasy Premier League data to build,
maintain and compare FPL squads. Python standard library backend (no pip
installs required), vanilla HTML/CSS/JS frontend, with simple admin-only
login accounts so each person gets their own private squad.

## Running it locally

Requires Python 3.8+. No dependencies to install.

Set at least one login before starting the server — there is no
sign-up screen, so you create accounts yourself via an environment
variable:

```
export FPL_USERS="alice:somepassword,bob:anotherpassword"
cd fpl_app
python3 backend/server.py
```

Then open **http://localhost:8765** in a browser and sign in with one of
those username/password pairs.

## Accounts & login

- There is **no self-registration**. You (the admin) decide who can log
  in by setting the `FPL_USERS` environment variable, formatted as
  `username:password,username2:password2`.
- Each account gets its **own saved squad**, stored under
  `data/<username>/`. Log in with the same account from your phone and
  your laptop and you'll see the *same* team — that's the point. Log in
  with a different account and you get a completely separate squad.
- Sessions are a random token in an HttpOnly cookie, kept in memory on
  the server for 30 days. Restarting the server (e.g. a Render redeploy)
  logs everyone out; they just sign in again.
- Passwords are compared with a constant-time check but are **not**
  hashed at rest — they only ever live in the environment variable you
  set, never written to disk. Fine for a small private tool; don't reuse
  a sensitive password for it.

## Deploying on Render

1. Push this repo (with `Procfile` and/or `render.yaml`) to GitHub/GitLab
   and create a new **Web Service** on Render pointing at it.
2. Render provides `$PORT` automatically — `server.py` already reads it
   (`os.environ.get("PORT", 8765)`), so no change needed.
3. In the service's **Environment** tab, add:
   - `FPL_USERS` = `yourname:yourpassword` (add more comma-separated
     pairs for anyone else you want to give access to)
4. Deploy. Render's health check hits `/`, which is served without
   requiring login (only the `/api/*` data routes are gated), so the
   health check will pass even for a logged-out visitor.
5. **Data persistence:** Render's default disk is ephemeral — a new
   deploy wipes `data/`. For a personal tool this is usually fine (worst
   case you rebuild your squad once), but if you want your squad to
   survive redeploys, add a Render **Persistent Disk** mounted at the
   project's `data/` path.
6. Run this as a **single instance** (don't enable autoscaling/multiple
   instances) — sessions and the local JSON files aren't shared across
   instances.

## How it works

**Weekly prompt.** The app checks the real gameweek deadline from the FPL
API (`events` in `bootstrap-static`). It only asks for your budget/transfer
count once per gameweek — if you've already answered for the current
gameweek, the prompt stays hidden until the next one opens up. You can still
reopen it manually with "Edit this gameweek".

**First time (no saved squad):** you give a budget, optionally lock in
required players and/or a formation, and choose whether to spend the full
budget. The optimizer builds a complete, legal 15-man squad (2 GK / 5 DEF /
5 MID / 3 FWD, max 3 players per real club) that maximises a projected
score within that budget.

**Every gameweek after that:** you say how many transfers you're willing to
make. The app looks at your saved squad, finds the best legal swaps, and
weighs the projected point gain against the -4 hit for any transfer beyond
your free allowance — recommending fewer transfers than requested if the
hit isn't worth it (unless you tick "use all requested transfers anyway").

**Player rating.** Each player gets a composite score from form, points per
game, ICT index, expected-goal-involvement per 90, price value (form per
£m), and upcoming fixture difficulty (average of the next 4 fixtures from
the `fixtures` endpoint) — with a penalty for players flagged as doubtful
or injured.

**Squad optimizer.** A knapsack-style dynamic program: each position (GK/
DEF/MID/FWD) is solved as its own bounded knapsack (best score for every
possible spend, choosing exactly the required count), then the four
position tables are merged to find the best split of the total budget
across all four. A repair pass afterwards fixes any 3-per-club violations,
and (if "use full budget" is on) a final pass upgrades the weakest picks to
soak up leftover cash.

**Starting XI.** From the saved 15, the app tries every legal formation (or
just your preferred one) and picks whichever starting 11 has the highest
combined score. Captain and vice-captain are the two highest-scoring
starters.

**Player comparer.** Search and add any players to a side-by-side stats
table (form, points, expected goals/assists, ICT, fixture difficulty,
overall rating, etc.), with the better value in each row highlighted.

**FPL Help tab (new):**
- *Differentials* — players owned by under 10% of managers (adjustable),
  ranked by rating, for when you want a pick that can gain rank rather than
  just track the crowd.
- *Fixture ticker* — every club's next 5 fixtures at a glance, colour-coded
  from easiest (green) to hardest (red), for spotting good/bad fixture runs
  before you plan transfers.

## Project structure

```
fpl_app/
  backend/
    server.py        # http.server-based API + static file server
    auth.py            # admin-provisioned login accounts + sessions
    fpl_api.py           # fetches + caches bootstrap-static / fixtures
    ratings.py             # composite player rating + fixture ticker data
    optimizer.py             # knapsack-style 15-man squad builder
    lineup.py                  # starting XI / bench / captain selection
    transfers.py                  # transfer suggestions from an existing squad
    data_store.py                   # local per-user JSON persistence
    test_offline.py                   # logic tests against synthetic data
    test_server_e2e.py                  # full HTTP route tests (incl. auth) against synthetic data
  frontend/
    index.html
    style.css
    app.js
  data/                      # created at runtime: <username>/squad.json etc.
  Procfile                   # for Render/Heroku-style start command
  render.yaml                # optional Render service definition
```

## Known simplifications (worth knowing about)

- **Sell price.** Real FPL halves your profit when you sell a player who's
  risen in price. This app treats sell price = current market price, which
  is usually close but can slightly overstate your available budget on
  transfers.
- **Free transfer banking.** The app assumes you bank up to 5 free
  transfers between gameweeks, matching the current FPL rule, but doesn't
  account for chips (Wildcard, Free Hit, Bench Boost, Triple Captain).
- **Rating is a heuristic, not a points predictor.** It's built from public
  stats to rank players sensibly, not a statistical model trained on
  historical outcomes.
- **Data caching.** Bootstrap/fixture data is cached for 30 minutes to
  avoid hammering the API; a full gameweek submission always forces a
  fresh fetch.
- **Accounts.** No self-signup, no password reset flow, no email — this is
  intentionally minimal since only you provision accounts.

## Resetting your squad

Click "Reset squad" in the app (clears just your own account's saved squad
and gameweek history), or delete your folder under `data/<username>/`.
