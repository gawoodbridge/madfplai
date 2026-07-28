/* FPL Assistant - vanilla JS frontend. No frameworks, no build step. */

const API = {
  session: () => fetchJSON("/api/session"),
  login: (body) => fetchJSON("/api/login", { method: "POST", body: JSON.stringify(body) }),
  logout: () => fetchJSON("/api/logout", { method: "POST" }),
  status: () => fetchJSON("/api/status"),
  players: (params) => fetchJSON("/api/players?" + new URLSearchParams(params)),
  squad: () => fetchJSON("/api/squad"),
  submitGameweek: (body) => fetchJSON("/api/gameweek", { method: "POST", body: JSON.stringify(body) }),
  compare: (ids) => fetchJSON("/api/compare", { method: "POST", body: JSON.stringify({ ids }) }),
  reset: () => fetchJSON("/api/reset", { method: "POST" }),
  differentials: (params) => fetchJSON("/api/differentials?" + new URLSearchParams(params)),
  fixtureTicker: () => fetchJSON("/api/fixture-ticker"),
  teams: () => fetchJSON("/api/teams"),
  // Accept optional teamId (club id) to analyse a club, or no arg to analyse the user's saved squad
  teamAnalysis: (teamId) => fetchJSON("/api/team-analysis" + (teamId ? ("?team_id=" + encodeURIComponent(teamId)) : "")),
  // Custom team analysis: POSTs { player_ids: [...] }
  customTeamAnalysis: (playerIds) => fetchJSON("/api/team-analysis", { method: "POST", body: JSON.stringify({ player_ids: playerIds }) }),
};

let sessionExpiredHandled = false;

async function fetchJSON(url, opts = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401 && !sessionExpiredHandled) {
    sessionExpiredHandled = true;
    location.reload();
    throw new Error("Not authenticated");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

const state = {
  requiredPlayers: [], // {id, web_name, price, position}
  comparePlayers: [],  // full player objects
  helpLoaded: false,
  customTeam: [],      // custom team players for analyser
};

// ------------------------------------------------------------- tabs ----
document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab-btn");
  if (!btn) return;
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  const target = btn.dataset.tab;
  document.getElementById("squad-tab").classList.toggle("hidden", target !== "squad");
  document.getElementById("compare-tab").classList.toggle("hidden", target !== "compare");
  document.getElementById("browse-tab").classList.toggle("hidden", target !== "browse");
  document.getElementById("analyser-tab").classList.toggle("hidden", target !== "analyser");
  document.getElementById("help-tab").classList.toggle("hidden", target !== "help");
  if (target === "help") loadHelpTab();
  if (target === "browse") loadBrowseTab();
  if (target === "analyser") loadAnalyserTab();
});

// ------------------------------------------------------------- init ----
init();

async function init() {
  wireLoginForm();
  try {
    const session = await API.session();
    if (session.authenticated) {
      showApp();
      await startApp();
    } else {
      showLogin();
    }
  } catch (err) {
    showLogin();
  }
}

function showLogin() {
  document.getElementById("login-screen").classList.remove("hidden");
  document.getElementById("app-shell").classList.add("hidden");
}

function showApp() {
  document.getElementById("login-screen").classList.add("hidden");
  document.getElementById("app-shell").classList.remove("hidden");
}

function wireLoginForm() {
  document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = document.getElementById("login-error");
    errBox.classList.add("hidden");
    const submitBtn = document.getElementById("login-submit");
    submitBtn.disabled = true;
    submitBtn.textContent = "Signing in…";

    const username = document.getElementById("login-username").value.trim();
    const password = document.getElementById("login-password").value;

    try {
      await API.login({ username, password });
      showApp();
      await startApp();
    } catch (err) {
      errBox.textContent = "Incorrect username or password.";
      errBox.classList.remove("hidden");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Sign in";
    }
  });
}

async function startApp() {
  await refreshStatusAndSquad();
  wireWeeklyForm();
  wireRequiredSearch();
  wireCompareSearch();
  wireCustomAnalyser(); // hook up custom analyser controls
  document.getElementById("reset-btn").addEventListener("click", onReset);
  document.getElementById("change-gameweek-btn").addEventListener("click", () => {
    document.getElementById("weekly-panel").classList.remove("hidden");
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  document.getElementById("logout-btn").addEventListener("click", onLogout);
}

async function onLogout() {
  try {
    await API.logout();
  } finally {
    location.reload();
  }
}

async function refreshStatusAndSquad() {
  try {
    const status = await API.status();
    renderGwLabel(status);

    const weeklyPanel = document.getElementById("weekly-panel");
    const needsAnswer = status.needs_weekly_answer;
    weeklyPanel.classList.toggle("hidden", !needsAnswer && status.has_squad);

    document.getElementById("weekly-title").textContent = status.has_squad
      ? "Update this gameweek"
      : "Set up this gameweek";
    document.getElementById("weekly-sub").textContent = status.has_squad
      ? "Tell us your transfer budget for this gameweek."
      : "First time here — tell us your budget to build your 15-man squad.";
    document.getElementById("transfers-row").classList.toggle("hidden", !status.has_squad);
    document.getElementById("full-transfers-toggle-wrap").classList.toggle("hidden", !status.has_squad);

    if (status.has_squad) {
      await loadSquad();
    } else {
      document.getElementById("squad-empty").classList.remove("hidden");
      document.getElementById("squad-content").classList.add("hidden");
    }
  } catch (err) {
    console.error(err);
  }
}

function renderGwLabel(status) {
  const label = document.getElementById("gw-label");
  if (!status.gameweek) {
    label.textContent = "No upcoming gameweek found";
    return;
  }
  const deadline = status.deadline_time ? new Date(status.deadline_time) : null;
  const deadlineStr = deadline ? deadline.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  label.textContent = `${status.gameweek_name || "Gameweek " + status.gameweek} · deadline ${deadlineStr}`;
}

// ------------------------------------------------------- weekly form ----
function wireWeeklyForm() {
  document.getElementById("weekly-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = document.getElementById("weekly-error");
    errBox.classList.add("hidden");
    const submitBtn = document.getElementById("weekly-submit");
    submitBtn.disabled = true;
    submitBtn.textContent = "Crunching the numbers…";

    const body = {
      budget: parseFloat(document.getElementById("budget-input").value),
      transfers_wanted: parseInt(document.getElementById("transfers-input").value || "0", 10),
      formation: document.getElementById("formation-input").value || null,
      required_player_ids: state.requiredPlayers.map((p) => p.id),
      use_full_budget: document.getElementById("full-budget-toggle").checked,
      use_full_transfers: document.getElementById("full-transfers-toggle").checked,
    };

    try {
      const result = await API.submitGameweek(body);
      document.getElementById("weekly-panel").classList.add("hidden");
      renderTransferSummary(result);
      renderSquadFromResult(result);
      await refreshStatusAndSquad();
    } catch (err) {
      errBox.textContent = err.message;
      errBox.classList.remove("hidden");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Build my team";
    }
  });
}

function renderTransferSummary(result) {
  const box = document.getElementById("transfer-summary");
  if (result.mode !== "transfer") {
    box.classList.add("hidden");
    return;
  }
  const s = result.transfer_summary;
  if (!s.swaps.length) {
    box.innerHTML = `<strong>No transfers made.</strong> ${s.message}`;
    box.classList.remove("hidden");
    return;
  }
  const rows = s.swaps.map(
    (sw) => `${sw.out.web_name} &rarr; ${sw.in.web_name}`
  ).join(", ");
  box.innerHTML = `<strong>${s.recommended_count} transfer(s) made:</strong> ${rows}.
    Projected gain ${s.total_score_gain} pts${s.point_hit ? `, minus a ${s.point_hit}pt hit` : ""} = net ${s.net_gain}. ${s.message}`;
  box.classList.remove("hidden");
}

// --------------------------------------------------------- squad ui ----
async function loadSquad() {
  const data = await API.squad();
  if (!data.squad) {
    document.getElementById("squad-empty").classList.remove("hidden");
    document.getElementById("squad-content").classList.add("hidden");
    if (data.warning) alert(data.warning);
    return;
  }
  renderSquadFromResult({
    formation: data.squad.formation,
    starting: data.squad.starting,
    bench: data.squad.bench,
    captain: data.squad.captain,
    vice_captain: data.squad.vice_captain,
  }, data.squad);
}

function renderSquadFromResult(result, squadMeta) {
  document.getElementById("squad-empty").classList.add("hidden");
  document.getElementById("squad-content").classList.remove("hidden");

  document.getElementById("meta-formation").textContent = result.formation;
  if (squadMeta) {
    document.getElementById("meta-bank").textContent = `£${squadMeta.bank}m`;
    document.getElementById("meta-ft").textContent = squadMeta.free_transfers;
  }

  const pitch = document.getElementById("pitch");
  pitch.innerHTML = "";
  const order = ["GK", "DEF", "MID", "FWD"];
  order.forEach((pos) => {
    const players = result.starting.filter((p) => p.position === pos);
    if (!players.length) return;
    const row = document.createElement("div");
    row.className = "pitch-row";
    players.forEach((p) => row.appendChild(playerCard(p, result)));
    pitch.appendChild(row);
  });

  const benchRow = document.getElementById("bench-row");
  benchRow.innerHTML = "";
  result.bench.forEach((p) => benchRow.appendChild(playerCard(p, result)));
}

function playerCard(p, result) {
  const card = document.createElement("div");
  card.className = "player-card";
  const isCaptain = result.captain && p.id === result.captain.id;
  const isVice = result.vice_captain && p.id === result.vice_captain.id;
  card.innerHTML = `
    ${isCaptain ? '<span class="armband" title="Captain">C</span>' : isVice ? '<span class="armband" title="Vice-captain">V</span>' : ""}
    <div class="p-name">${p.web_name}</div>
    <div class="p-meta">${p.team_short} · £${p.price}m</div>
  `;
  return card;
}

async function onReset() {
  if (!confirm("This clears your saved squad and gameweek history. Continue?")) return;
  await API.reset();
  location.reload();
}

// ---------------------------------------------------- required search ----
function wireRequiredSearch() {
  const input = document.getElementById("required-search");
  const suggestBox = document.getElementById("required-suggestions");

  input.addEventListener("input", debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { suggestBox.classList.add("hidden"); return; }
    const { players } = await API.players({ search: q });
    renderSuggestions(suggestBox, players.slice(0, 8), (p) => {
      if (!state.requiredPlayers.find((x) => x.id === p.id)) {
        state.requiredPlayers.push(p);
        renderRequiredChips();
      }
      input.value = "";
      suggestBox.classList.add("hidden");
    });
  }, 250));

  document.addEventListener("click", (e) => {
    if (!e.target.closest("#required-search") && !e.target.closest("#required-suggestions")) {
      suggestBox.classList.add("hidden");
    }
  });
}

function renderRequiredChips() {
  const box = document.getElementById("required-chips");
  box.innerHTML = "";
  state.requiredPlayers.forEach((p) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `${p.web_name} <button type="button" aria-label="Remove">&times;</button>`;
    chip.querySelector("button").addEventListener("click", () => {
      state.requiredPlayers = state.requiredPlayers.filter((x) => x.id !== p.id);
      renderRequiredChips();
    });
    box.appendChild(chip);
  });
}

// ---------------------------------------------------------- comparer ----
function wireCompareSearch() {
  const input = document.getElementById("compare-search");
  const suggestBox = document.getElementById("compare-suggestions");

  input.addEventListener("input", debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { suggestBox.classList.add("hidden"); return; }
    const { players } = await API.players({ search: q });
    renderSuggestions(suggestBox, players.slice(0, 8), (p) => {
      if (!state.comparePlayers.find((x) => x.id === p.id)) {
        state.comparePlayers.push(p);
        renderCompareChips();
        renderCompareTable();
      }
      input.value = "";
      suggestBox.classList.add("hidden");
    });
  }, 250));

  document.addEventListener("click", (e) => {
    if (!e.target.closest("#compare-search") && !e.target.closest("#compare-suggestions")) {
      suggestBox.classList.add("hidden");
    }
  });
}

function renderCompareChips() {
  const box = document.getElementById("compare-chips");
  box.innerHTML = "";
  state.comparePlayers.forEach((p) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `${p.web_name} <button type="button" aria-label="Remove">&times;</button>`;
    chip.querySelector("button").addEventListener("click", () => {
      state.comparePlayers = state.comparePlayers.filter((x) => x.id !== p.id);
      renderCompareChips();
      renderCompareTable();
    });
    box.appendChild(chip);
  });
}

const COMPARE_STATS = [
  { key: "team_short", label: "Club", better: null },
  { key: "position", label: "Position", better: null },
  { key: "price", label: "Price (£m)", better: "low" },
  { key: "form", label: "Form", better: "high" },
  { key: "total_points", label: "Total points", better: "high" },
  { key: "points_per_game", label: "Points / game", better: "high" },
  { key: "expected_goals", label: "Expected goals", better: "high" },
  { key: "expected_assists", label: "Expected assists", better: "high" },
  { key: "goals_scored", label: "Goals", better: "high" },
  { key: "assists", label: "Assists", better: "high" },
  { key: "clean_sheets", label: "Clean sheets", better: "high" },
  { key: "ict_index", label: "ICT index", better: "high" },
  { key: "fixture_difficulty", label: "Upcoming fixture difficulty", better: "low" },
  { key: "selected_by_percent", label: "Selected by (%)", better: null },
  { key: "score", label: "Overall rating", better: "high" },
];

function renderCompareTable() {
  const table = document.getElementById("compare-table");
  const empty = document.getElementById("compare-empty");
  const head = document.getElementById("compare-head");
  const body = document.getElementById("compare-body");

  if (state.comparePlayers.length < 1) {
    table.classList.add("hidden");
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  table.classList.remove("hidden");

  head.innerHTML = `<th>Stat</th>` + state.comparePlayers.map((p) => `<th>${p.web_name}</th>`).join("");

  body.innerHTML = "";
  COMPARE_STATS.forEach((stat) => {
    const values = state.comparePlayers.map((p) => p[stat.key]);
    let bestValue = null;
    if (stat.better && values.every((v) => typeof v === "number")) {
      bestValue = stat.better === "high" ? Math.max(...values) : Math.min(...values);
    }
    const row = document.createElement("tr");
    row.innerHTML = `<td class="stat-label">${stat.label}</td>` + values.map((v) => {
      const isBest = bestValue !== null && v === bestValue;
      return `<td class="${isBest ? "best" : ""}">${v}</td>`;
    }).join("");
    body.appendChild(row);
  });
}

// ------------------------------------------------------------ fpl help ----
async function loadHelpTab() {
  if (state.helpLoaded) return;
  state.helpLoaded = true;

  try {
    const diff = await API.differentials({ max_ownership: 10 });
    renderDifferentials(diff.players);
  } catch (err) {
    console.error(err);
  }

  try {
    const ticker = await API.fixtureTicker();
    renderFixtureTicker(ticker.teams);
  } catch (err) {
    console.error(err);
  }
}

function renderDifferentials(players) {
  const body = document.getElementById("differentials-body");
  body.innerHTML = "";
  players.slice(0, 15).forEach((p) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${p.web_name}</td>
      <td>${p.team_short}</td>
      <td>${p.position}</td>
      <td>£${p.price}m</td>
      <td>${p.selected_by_percent}%</td>
      <td>${p.score}</td>
    `;
    body.appendChild(row);
  });
}

function renderFixtureTicker(teams) {
  const body = document.getElementById("ticker-body");
  body.innerHTML = "";
  teams.forEach((t) => {
    const row = document.createElement("tr");
    const cells = t.fixtures.slice(0, 5).map((f) => {
      const label = (f.is_home ? "" : "@") + f.opponent_short;
      return `<td class="fd-${f.difficulty}">${label}</td>`;
    }).join("");
    row.innerHTML = `<td class="stat-label">${t.team_short}</td>${cells}`;
    body.appendChild(row);
  });
}

// --------------------------------------------------------- all players ----
let browseTeamsLoaded = false;

async function loadBrowseTab() {
  if (!browseTeamsLoaded) {
    browseTeamsLoaded = true;
    try {
      const { teams } = await API.teams();
      const select = document.getElementById("browse-team");
      teams.forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t.short_name;
        opt.textContent = t.name;
        select.appendChild(opt);
      });
    } catch (err) {
      console.error(err);
    }
    wireBrowseFilters();
  }
  await runBrowseSearch();
}

function wireBrowseFilters() {
  const debouncedSearch = debounce(runBrowseSearch, 300);
  document.getElementById("browse-search").addEventListener("input", debouncedSearch);
  document.getElementById("browse-position").addEventListener("change", runBrowseSearch);
  document.getElementById("browse-team").addEventListener("change", runBrowseSearch);
  document.getElementById("browse-min-price").addEventListener("input", debouncedSearch);
  document.getElementById("browse-max-price").addEventListener("input", debouncedSearch);
  document.getElementById("browse-sort").addEventListener("change", runBrowseSearch);
  document.getElementById("browse-order").addEventListener("change", runBrowseSearch);
}

async function runBrowseSearch() {
  const params = {};
  const search = document.getElementById("browse-search").value.trim();
  const position = document.getElementById("browse-position").value;
  const team = document.getElementById("browse-team").value;
  const minPrice = document.getElementById("browse-min-price").value;
  const maxPrice = document.getElementById("browse-max-price").value;
  const sort = document.getElementById("browse-sort").value;
  const order = document.getElementById("browse-order").value;
  if (search) params.search = search;
  if (position) params.position = position;
  if (team) params.team = team;
  if (minPrice) params.min_price = minPrice;
  if (maxPrice) params.max_price = maxPrice;
  params.sort = sort;
  params.order = order;

  try {
    const { players } = await API.players(params);
    renderBrowseTable(players);
  } catch (err) {
    console.error(err);
  }
}

function renderBrowseTable(players) {
  const body = document.getElementById("browse-body");
  body.innerHTML = "";
  players.slice(0, 100).forEach((p) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${p.web_name}</td>
      <td>${p.team_short}</td>
      <td>${p.position}</td>
      <td>£${p.price}m</td>
      <td>${p.form}</td>
      <td>${p.total_points}</td>
      <td>${p.score}</td>
    `;
    body.appendChild(row);
  });
}

// ------------------------------------------------------ team analyser ----
async function loadAnalyserTab() {
  // Populate analyser team picker with clubs from API and default option
  const select = document.getElementById("analyser-team-select");
  if (select) {
    select.innerHTML = `<option value="">My saved squad</option>`;
    try {
      const { teams } = await API.teams();
      teams.forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t.id !== undefined && t.id !== null ? t.id : t.short_name;
        opt.textContent = t.name || t.short_name;
        select.appendChild(opt);
      });
    } catch (err) {
      console.error("Failed to load teams for analyser:", err);
    }
  }

  // Load analysis for the currently selected option (defaults to user's saved squad)
  await loadAnalyserForSelected();
}

async function loadAnalyserForSelected() {
  const select = document.getElementById("analyser-team-select");
  const teamId = select && select.value ? select.value : null;
  const loadBtn = document.getElementById("analyser-load-btn");
  if (loadBtn) loadBtn.disabled = true;
  try {
    const data = teamId ? await API.teamAnalysis(teamId) : await API.teamAnalysis();
    renderAnalyser(data);
  } catch (err) {
    console.error("Failed to load analysis:", err);
    document.getElementById("analyser-empty").classList.remove("hidden");
    document.getElementById("analyser-content").classList.add("hidden");
  } finally {
    if (loadBtn) loadBtn.disabled = false;
  }
}

// Wire the Analyse button
const analyserLoadBtn = document.getElementById("analyser-load-btn");
if (analyserLoadBtn) {
  analyserLoadBtn.addEventListener("click", (e) => {
    e.preventDefault();
    loadAnalyserForSelected();
  });
}

function renderAnalyser(data) {
  document.getElementById("analyser-empty").classList.add("hidden");
  document.getElementById("analyser-content").classList.remove("hidden");

  document.getElementById("analyser-summary-chips").innerHTML = `
    <div class="meta-chip"><span class="meta-label">Total rating</span><span class="meta-value">${data.total_score}</span></div>
    <div class="meta-chip"><span class="meta-label">Squad value</span><span class="meta-value">£${data.total_value}m</span></div>
    <div class="meta-chip"><span class="meta-label">Rating per £m</span><span class="meta-value">${data.spend_efficiency}</span></div>
    <div class="meta-chip"><span class="meta-label">XI fixture difficulty</span><span class="meta-value">${data.starting_fixture_difficulty ?? "—"}</span></div>
  `;

  const posBody = document.getElementById("analyser-position-body");
  posBody.innerHTML = "";
  Object.entries(data.position_breakdown).forEach(([pos, stats]) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td>${pos}</td><td>${stats.count}</td><td>${stats.avg_score}</td><td>£${stats.total_price}m</td>`;
    posBody.appendChild(row);
  });

  const clubBox = document.getElementById("analyser-clubs");
  clubBox.innerHTML = "";
  data.club_breakdown.forEach((c) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${c.team}: ${c.count}`;
    clubBox.appendChild(chip);
  });

  const riskBox = document.getElementById("analyser-risks");
  if (!data.risk_flags.length) {
    riskBox.innerHTML = `<p class="muted">No injury/availability concerns in your squad right now.</p>`;
  } else {
    riskBox.innerHTML = "";
    data.risk_flags.forEach((r) => {
      const box = document.createElement("div");
      box.className = "callout";
      box.innerHTML = `<strong>${r.web_name}</strong> — ${r.news || "Flagged as " + r.status}`;
      riskBox.appendChild(box);
    });
  }
}

// -------------------------------------------------- custom team analyser UI ----
// Renders the chips for the custom team
function renderCustomTeamList() {
  const box = document.getElementById("analyser-custom-list");
  if (!box) return;
  box.innerHTML = "";
  state.customTeam.forEach((p) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `${p.web_name} <button type="button" aria-label="Remove">&times;</button>`;
    chip.querySelector("button").addEventListener("click", () => {
      state.customTeam = state.customTeam.filter((x) => x.id !== p.id);
      renderCustomTeamList();
    });
    box.appendChild(chip);
  });
}

// Wire the custom analyser search + actions
function wireCustomAnalyser() {
  const input = document.getElementById("analyser-custom-search");
  const suggestBox = document.getElementById("analyser-custom-suggestions");
  if (!input) return;

  input.addEventListener("input", debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { if (suggestBox) suggestBox.classList.add("hidden"); return; }
    try {
      const { players } = await API.players({ search: q });
      renderSuggestions(suggestBox, players.slice(0, 8), (p) => {
        if (!state.customTeam.find((x) => x.id === p.id)) {
          if (state.customTeam.length >= 15) {
            alert("Custom team is limited to 15 players.");
          } else {
            state.customTeam.push(p);
            renderCustomTeamList();
          }
        }
        input.value = "";
        if (suggestBox) suggestBox.classList.add("hidden");
      });
    } catch (err) {
      console.error("Failed to search players:", err);
    }
  }, 250));

  document.addEventListener("click", (e) => {
    if (!e.target.closest("#analyser-custom-search") && !e.target.closest("#analyser-custom-suggestions")) {
      if (suggestBox) suggestBox.classList.add("hidden");
    }
  });

  const clearBtn = document.getElementById("analyser-custom-clear");
  if (clearBtn) {
    clearBtn.addEventListener("click", (e) => {
      e.preventDefault();
      state.customTeam = [];
      renderCustomTeamList();
    });
  }

  const analyzeBtn = document.getElementById("analyser-custom-analyze");
  if (analyzeBtn) {
    analyzeBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      if (state.customTeam.length === 0) {
        alert("Add at least one player to analyse.");
        return;
      }
      analyzeBtn.disabled = true;
      try {
        const ids = state.customTeam.map((p) => p.id);
        const data = await API.customTeamAnalysis(ids);
        renderAnalyser(data);
      } catch (err) {
        console.error("Custom team analysis failed:", err);
        alert(err.message || "Failed to analyse custom team");
      } finally {
        analyzeBtn.disabled = false;
      }
    });
  }
}

// ------------------------------------------------------------ shared ----
function renderSuggestions(box, players, onPick) {
  if (!box) return;
  if (!players || !players.length) { box.classList.add("hidden"); return; }
  box.innerHTML = "";
  players.forEach((p) => {
    const item = document.createElement("div");
    item.className = "suggestion-item";
    item.innerHTML = `<span>${p.web_name} <span class="muted">(${p.team_short}, ${p.position})</span></span><span class="s-price">£${p.price}m</span>`;
    item.addEventListener("click", () => onPick(p));
    box.appendChild(item);
  });
  box.classList.remove("hidden");
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}



// Team analyser UI wiring — append after your existing frontend init code
document.addEventListener("DOMContentLoaded", () => {
  // helper to show/hide tab panels (re-uses your tab switching if present)
  function showTab(name) {
    document.querySelectorAll(".tab-panel").forEach(el => el.classList.add("hidden"));
    const panel = document.getElementById(name + "-tab");
    if (panel) panel.classList.remove("hidden");
    // also update tab button active state
    document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.toggle("active", btn.dataset.tab === name));
  }

  // If your app already has tab handling, this will be harmless — we just ensure analyser-tab exists.
  const analyserTabBtn = document.querySelector('.tab-btn[data-tab="analyser"]');
  if (analyserTabBtn) {
    analyserTabBtn.addEventListener("click", (e) => {
      showTab("analyser");
      // lazy-load teams when tab opened
      populateTeams();
    });
  }

  const teamSelect = document.getElementById("analyser-team-select");
  const runTeamBtn = document.getElementById("analyser-run-team");
  const customInput = document.getElementById("analyser-custom-players");
  const runCustomBtn = document.getElementById("analyser-run-custom");
  const resultsPanel = document.getElementById("analyser-results");
  const resultsBody = document.getElementById("analyser-results-body");

  async function populateTeams() {
    if (!teamSelect || teamSelect.dataset.loaded === "1") return;
    try {
      const data = await app.teams(); // uses your existing API helper
      // Expecting an array of { id, name, short_name } or similar
      data.forEach(t => {
        const opt = document.createElement("option");
        opt.value = t.id;
        opt.textContent = t.short_name ? `${t.short_name} (${t.name})` : t.name || t.id;
        teamSelect.appendChild(opt);
      });
      teamSelect.dataset.loaded = "1";
    } catch (err) {
      console.error("Failed to load teams", err);
    }
  }

  async function runTeamAnalysis(teamId) {
    if (!teamId) return;
    resultsPanel.classList.add("hidden");
    resultsBody.textContent = "Analysing…";
    try {
      const res = await app.teamAnalysis(teamId); // GET /api/team-analysis?team_id=...
      // Display pretty JSON by default; you can customise rendering later
      resultsBody.textContent = JSON.stringify(res, null, 2);
      resultsPanel.classList.remove("hidden");
    } catch (err) {
      resultsBody.textContent = "Error: " + (err.message || err);
      resultsPanel.classList.remove("hidden");
    }
  }

  async function runCustomAnalysis(playerIds) {
    if (!playerIds || !playerIds.length) return;
    resultsPanel.classList.add("hidden");
    resultsBody.textContent = "Analysing custom team…";
    try {
      const res = await app.customTeamAnalysis(playerIds); // POST /api/team-analysis
      resultsBody.textContent = JSON.stringify(res, null, 2);
      resultsPanel.classList.remove("hidden");
    } catch (err) {
      resultsBody.textContent = "Error: " + (err.message || err);
      resultsPanel.classList.remove("hidden");
    }
  }

  if (runTeamBtn) {
    runTeamBtn.addEventListener("click", async (e) => {
      const teamId = teamSelect.value;
      if (!teamId) {
        alert("Please select a team first.");
        return;
      }
      await runTeamAnalysis(teamId);
    });
  }

  if (runCustomBtn) {
    runCustomBtn.addEventListener("click", async (e) => {
      const raw = customInput.value.trim();
      if (!raw) { alert("Enter comma-separated player IDs."); return; }
      const ids = raw.split(",").map(s => parseInt(s.trim(), 10)).filter(Boolean);
      if (!ids.length) { alert("No valid player ids found."); return; }
      await runCustomAnalysis(ids);
    });
  }

  // If analyser should be visible on initial load (e.g. deep-link), populate teams immediately:
  if (window.location.hash === "#analyser") {
    populateTeams();
    showTab("analyser");
  }
});
