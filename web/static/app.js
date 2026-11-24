// Four-pane demo UI. Polls /api/state and redraws. No libraries.
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x === null || x === undefined ? "–" : Math.round(x * 100) + "%");
const clock12 = (t) => { if (t === null || t === undefined) return ""; const h = Math.floor(t / 60) % 24, m = t % 60; return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`; };

const URLQ = new URLSearchParams(location.search);
const ui = { pid: URLQ.get("pid") || "maria", lang: URLQ.get("lang") === "es" ? "es" : "en", at: null, selected: null, focusT: null, tab: "live", last: null, metaDone: false, inboxKey: "", detailKey: "", suites: null };

async function api(path, body) {
  const opt = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  if (location.protocol === "file:") throw new Error("This page was opened as a file, so it can't reach the simulation. Run  python3 -m web  in a terminal and open http://127.0.0.1:8000");
  let r;
  try { r = await fetch(path, opt); }
  catch { throw new Error("Can't reach the simulation server. Is  python3 -m web  still running? Then reload http://127.0.0.1:8000"); }
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
async function control(body) {
  try { $("err").textContent = ""; await api("/api/control", body); await poll(true); }
  catch (e) { $("err").textContent = e.message; }
}

// ---------------------------------------------------------------- control bar
function setupMeta(meta) {
  if (ui.metaDone) return;
  const sel = $("scenario");
  sel.innerHTML = Object.entries(meta.scenarios).map(([k, d]) =>
    `<option value="${esc(k)}" title="${esc(d)}">${esc(k.replace(/_/g, " "))}${meta.holdouts.includes(k) ? " (hold-out)" : ""}</option>`).join("");
  $("seed").value = meta.default_seed;
  $("speeds").innerHTML = meta.speeds.map((s) => `<button data-speed="${esc(s)}">${esc(s)}</button>`).join("");
  $("speeds").onclick = (e) => { const s = e.target.dataset.speed; if (s) control({ action: "speed", speed: s }); };
  ui.metaDone = true;
}
$("start").onclick = () => { ui.at = null; ui.selected = null; ui.focusT = null; control({ action: "start", scenario: $("scenario").value, seed: +$("seed").value, approver: $("approver").value }); };
$("play").onclick = () => control({ action: ui.last?.control?.running ? "pause" : "play" });
$("step").onclick = () => { ui.at = null; control({ action: "step" }); };
$("toend").onclick = () => { ui.at = null; control({ action: "to_end" }); };
$("replay").onclick = () => { ui.at = null; control({ action: "replay" }); };
$("demo").onclick = () => { ui.at = null; ui.selected = null; ui.focusT = null; $("approver").value = "persona"; control({ action: "start_demo" }); };
$("live").onclick = () => { ui.at = null; ui.focusT = null; poll(true); };
$("scrub").oninput = (e) => { ui.at = +e.target.value; poll(true); };
$("pid").onchange = (e) => { ui.pid = e.target.value; poll(true); };
document.querySelectorAll("[data-lang]").forEach((b) => (b.onclick = () => { ui.lang = b.dataset.lang; poll(true); }));
document.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

function showTab(tab) {
  ui.tab = tab;
  document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $("tab-live").hidden = tab !== "live";
  $("tab-suite").hidden = tab !== "suite";
  $("tab-agents").hidden = tab !== "agents";
  if (tab === "suite") loadSuites();
  if (tab === "agents") loadAgents(true);
}

function renderControl(s) {
  const c = s.control;
  $("clock").textContent = c ? `${c.time}${c.done ? " · day over" : ""}${c.following ? "" : " · looking back"}` : "no run";
  $("play").disabled = !c || c.mode !== "live" || c.done;
  $("play").textContent = c && c.running ? "Pause" : "Play";
  $("step").disabled = $("toend").disabled = !c || c.mode !== "live" || c.done;
  $("replay").disabled = !c || c.mode !== "live";
  document.querySelectorAll("#speeds button").forEach((b) => b.classList.toggle("on", b.dataset.speed === s.meta.speed));
  const sc = $("scrub");
  if (c && c.now !== null) {
    sc.disabled = false; sc.min = c.open_t; sc.max = c.now; sc.value = c.t;
  } else { sc.disabled = true; }
  $("live").disabled = !c || c.following;
  if (c) {
    const src = c.source;
    const srcKey = JSON.stringify(src);
    if (ui.srcKey !== srcKey) {   // keep the pickers in step with what is loaded
      ui.srcKey = srcKey;
      $("scenario").value = src.scenario; $("seed").value = src.seed;
      if (src.approver === "human" || src.approver === "persona") $("approver").value = src.approver;
    }
    $("source").textContent = c.mode === "review"
      ? `Saved run from suite ${src.suite}: ${src.scenario}, seed ${src.seed} (read only)`
      : `${src.demo ? "AI demo day (AI people, recorded, replayed free): " : "Rule-based people: "}${src.scenario}, seed ${src.seed}, approvals by ${src.approver === "human" ? "you" : "desk persona"}${src.replay ? `, replaying ${src.replayed_actions} logged staff action(s)` : ""}`;
  }
}

// ---------------------------------------------------------------- patient phone
const PHONE_TXT = { en: { title: "Clinic wait times", texts: "Your texts", none: "No texts yet.", sim: "Simulation, synthetic data" },
                    es: { title: "Tiempos de espera", texts: "Sus mensajes", none: "Todavía no hay mensajes.", sim: "Simulación, datos sintéticos" } };
function renderPhone(p) {
  const L = PHONE_TXT[ui.lang];
  $("phone-title").textContent = L.title;
  $("phone-sim").textContent = L.sim;
  $("thread-title").textContent = L.texts;
  document.querySelectorAll("[data-lang]").forEach((b) => b.classList.toggle("on", b.dataset.lang === ui.lang));
  const sel = $("pid");
  const opts = p.choices.map((c) => `<option value="${esc(c.pid)}">${esc(c.name)}</option>`).join("");
  if (sel.dataset.opts !== opts) { sel.innerHTML = opts; sel.dataset.opts = opts; }
  sel.value = ui.pid;
  const page = p.page;
  const el = $("page");
  if (!page) { el.className = "page"; el.innerHTML = `<p class="muted">The page appears at the first simulated minute.</p>`; }
  else {
    el.className = "page " + page.status;
    el.innerHTML = page.main.map((m, i) => `<p class="main${i === 0 ? " first" : ""}">${esc(m)}</p>`).join("")
      + (page.fresh ? `<p class="fresh">${esc(page.fresh)}</p>` : "")
      + (page.urgent ? `<p class="urgent">${esc(page.urgent)}</p>` : "")
      + (page.verbatim ? "" : `<p class="err">Display split changed the text</p>`);
  }
  $("thread").innerHTML = p.thread.length ? p.thread.map((m) =>
    `<div class="bubble ${m.dir}"><span class="when">${esc(m.time)}</span>${esc(m.text)}</div>`).join("") : `<p class="muted">${L.none}</p>`;
  const th = $("thread"); th.scrollTop = th.scrollHeight;
}

// ---------------------------------------------------------------- staff console
function rng(a) { return a && a[0] !== null ? `${a[0]}–${a[1]} min` : "–"; }
function renderStaff(st, c) {
  if (!st) { $("estimate").innerHTML = `<p class="muted">Not started.</p>`; return; }
  const e = st.estimate, pg = st.page;
  const pageState = !pg ? "–" : pg.status === "range"
    ? `<span class="pill ok">showing ranges</span>`
    : `<span class="pill bad">saying "call the clinic"</span> <span class="muted">${esc(pg.reason)}</span>`;
  $("estimate").innerHTML = `<dl class="est">
    <dt>Walk-in wait</dt><dd>${e ? rng(e.wait) : "–"}</dd>
    <dt>Scheduled</dt><dd>${e ? rng(e.behind) + " behind" : "–"}</dd>
    <dt>Total visit</dt><dd>${e ? rng(e.total) : "–"}</dd>
    <dt>Confidence</dt><dd>${e ? e.confidence.toFixed(2) + ` <span class="muted">(${e.samples} recent waits)</span>` : "–"}</dd>
    <dt>Providers on duty</dt><dd>${st.providers ?? "–"}</dd>
    <dt>Patient page</dt><dd>${pageState}${pg ? ` <span class="muted">· last desk update ${pg.updated_min_ago} min ago</span>` : ""}</dd></dl>`;

  // Inbox: only rebuild when the set of proposals or permissions changes, so typed reasons survive polling.
  const key = JSON.stringify([st.inbox.map((i) => i.id), c.can_act, c.human_approver, c.queued_actions]);
  if (key !== ui.inboxKey) {
    ui.inboxKey = key;
    $("inbox").innerHTML = st.inbox.length ? st.inbox.map((i) => `<div class="proposal" data-id="${i.id}">
        <div><b>${esc(i.name)}</b> · ${esc(i.action)} · <span class="muted">${esc(i.time)}, from ${esc(i.source)}</span></div>
        <div class="small">${esc(i.reason)}</div>
        ${c.human_approver && c.can_act ? `<div class="row"><input placeholder="One-line reason (required)" aria-label="Reason">
          <button data-d="approved">Approve</button><button data-d="rejected">Reject</button></div>`
        : `<div class="small muted">${c.human_approver ? "Go live to decide." : "The desk persona decides this one. Choose \"Approvals by: me\" and start a run to decide yourself."}</div>`}
      </div>`).join("") : `<p class="muted small">Nothing waiting for approval.</p>`;
    $("inbox").querySelectorAll(".proposal button").forEach((b) => (b.onclick = () => {
      const box = b.closest(".proposal"); const reason = box.querySelector("input").value.trim();
      if (!reason) { $("err").textContent = "Type a one-line reason first."; return; }
      control({ action: "decide", proposal_id: +box.dataset.id, decision: b.dataset.d, reason });
    }));
  }
  $("decided").innerHTML = st.decided.length ? `<table><tr><th>Decided</th><th>Patient</th><th>By</th><th>Reason</th></tr>${st.decided.map((d) =>
    `<tr><td>${esc(d.decided)}</td><td>${esc(d.name)}</td><td>${esc(d.by)} <span class="pill ${d.decision === "approved" ? "ok" : "bad"}">${esc(d.decision)}</span></td><td class="small">${esc(d.why)}</td></tr>`).join("")}</table>` : "";

  $("flags").innerHTML = st.flags.length ? st.flags.map((f) =>
    `<div class="flag"><b>${esc(f.name || "Clinic")}</b> <span class="muted">${esc(f.kind.replace(/_/g, " "))}</span> ${esc(f.text)}</div>`).join("") : `<p class="muted small">No flags.</p>`;

  const qrow = (q, sched) => `<tr><td class="num">${q.pos}</td><td>${esc(q.name)}${q.late ? ` <span class="pill warn">late, booked ${esc(q.appt)}</span>` : ""}${q.reslotted ? ` <span class="pill ok">re-slotted</span>` : ""}</td>
     <td class="time">${sched ? esc(q.appt) : esc(q.checkin)}</td><td class="num">${q.waiting_min}</td></tr>`;
  $("queue").innerHTML = st.queue.length ? `<table><tr><th class="num" title="Place in the whole line">Place</th><th>Name</th><th>Appt</th><th class="num">Min here</th></tr>${st.queue.map((q) => qrow(q, true)).join("")}</table>` : `<p class="muted small">Empty.</p>`;
  $("walkins").innerHTML = st.walkins.length ? `<table><tr><th class="num" title="Place in the whole line">Place</th><th>Name</th><th>In at</th><th class="num">Min here</th></tr>${st.walkins.map((q) => qrow(q, false)).join("")}</table>` : `<p class="muted small">Empty.</p>`;
  $("inroom").innerHTML = st.in_room.length ? `<table><tr><th>Name</th><th>Since</th><th class="num">Min</th><th></th></tr>${st.in_room.map((r) =>
    `<tr><td>${esc(r.name)}</td><td class="time">${esc(r.roomed)}</td><td class="num">${r.minutes}</td><td>${r.run_long ? `<span class="pill warn">running long</span>`
      : `<button class="small" data-rl="${esc(r.pid)}" ${c.can_act ? "" : "disabled"}>Will run long</button>`}</td></tr>`).join("")}</table>` : `<p class="muted small">No one in a room.</p>`;
  $("inroom").querySelectorAll("[data-rl]").forEach((b) => (b.onclick = () => control({ action: "run_long", pid: b.dataset.rl })));
}

// ---------------------------------------------------------------- observer
function goTo(ref) {
  if (!ref) return;
  if (ui.last?.control?.running) control({ action: "pause" });
  if (ref.t !== null && ref.t !== undefined) { ui.at = ref.t; ui.focusT = ref.t; }
  if (ref.pid) { ui.selected = ref.pid; if (ui.last?.phone?.choices.some((c) => c.pid === ref.pid)) ui.pid = ref.pid; }
  ui.detailKey = "";
  poll(true);
  $("timeline").scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function renderScore(rows, c) {
  $("score-note").textContent = c.done ? "(final)" : "(so far; some checks are judged only at the end)";
  const statusClass = (s) => (s.startsWith("so far: ") ? s.slice(8) : s);
  window._refs = [];
  $("scorecard").innerHTML = `<div class="scrollx"><table class="score"><tr><th>ID</th><th>Score</th><th>Threshold</th><th>Result</th></tr>${rows.map((r) => {
    const ev = r.passed === false && r.evidence.length ? `<tr><td></td><td colspan="3"><ul class="evid">${r.evidence.slice(0, 6).map((e, i) => {
      const ref = r.refs[i]; const n = window._refs.push(ref) - 1;
      return `<li>${ref && (ref.t !== null || ref.pid) ? `<a data-ref="${n}">${esc(e)}</a>` : esc(e)}</li>`; }).join("")}
      ${r.evidence.length > 6 ? `<li class="muted">and ${r.evidence.length - 6} more</li>` : ""}</ul></td></tr>` : "";
    return `<tr><td>${esc(r.id)}</td><td>${esc(r.score)}<div class="muted small">${esc(r.detail)}</div></td><td>${esc(r.threshold)}</td>
      <td class="status ${statusClass(r.status)}">${esc(r.status)}</td></tr>${ev}`; }).join("")}</table></div>`;
  $("scorecard").querySelectorAll("[data-ref]").forEach((a) => (a.onclick = () => goTo(window._refs[+a.dataset.ref])));
}

function renderLegend() {
  $("legend").innerHTML = `<span><i style="background:var(--waiting)"></i>Waiting</span><span><i style="background:var(--inroom)"></i>In a room</span>
    <span><i style="background:var(--told);border:1px solid var(--axis)"></i>Range shown at check-in</span>
    <span style="color:var(--crit-ink)">✕ walked away</span><span>◆ urgent hand-off</span><span>| appointment</span>`;
}
function renderTimeline(o) {
  const lanes = o.lanes.filter((l) => l.checkin !== null || (l.appt !== null && l.appt <= o.t + 30));
  const t0 = o.open_t, t1 = Math.max(o.close_t, o.end_t ?? 0, o.t + 10);
  const box = $("timeline"); const W = Math.max(480, box.clientWidth - 2), LBL = 92, RH = 18, H = 22 + lanes.length * RH + 4;
  const x = (t) => LBL + ((t - t0) / (t1 - t0)) * (W - LBL - 10);
  let s = `<svg width="${W}" height="${H}" role="img" aria-label="Per-patient timeline">`;
  s += `<g class="axis">`;
  for (let t = t0; t <= t1; t += 30) s += `<line x1="${x(t)}" x2="${x(t)}" y1="16" y2="${H}" stroke="var(--line)"/><text x="${x(t) + 2}" y="12">${esc(clock12(t).replace(":00", ""))}</text>`;
  s += `<line x1="${x(o.close_t)}" x2="${x(o.close_t)}" y1="16" y2="${H}" stroke="var(--axis)" stroke-dasharray="3 3"/></g>`;
  const now = o.t;
  lanes.forEach((l, i) => {
    const y = 22 + i * RH, mid = y + RH / 2;
    s += `<g class="row${ui.selected === l.pid ? " sel" : ""}" data-pid="${esc(l.pid)}"><rect class="bg" x="0" y="${y}" width="${W}" height="${RH}" fill="transparent"/>`;
    s += `<text x="4" y="${mid + 4}">${esc(l.name)}${l.kind === "walkin" ? " (W)" : ""}</text>`;
    if (l.appt !== null) s += `<line x1="${x(l.appt)}" x2="${x(l.appt)}" y1="${y + 2}" y2="${y + RH - 2}" stroke="var(--muted)" stroke-width="1.5"/>`;
    if (l.checkin !== null) {
      const start = l.kind === "scheduled" && l.appt !== null ? Math.max(l.appt, l.checkin) : l.checkin;
      if (l.told && l.told.status === "range") s += `<rect x="${x(start + l.told.lo)}" y="${y + 2}" width="${Math.max(2, x(start + l.told.hi) - x(start + l.told.lo))}" height="${RH - 4}" rx="3" fill="var(--told)" stroke="var(--axis)"/>`;
      const wEnd = l.roomed ?? l.left ?? now;
      s += `<rect x="${x(l.checkin)}" y="${mid - 3}" width="${Math.max(2, x(wEnd) - x(l.checkin))}" height="6" rx="2" fill="var(--waiting)"/>`;
      if (l.roomed !== null) s += `<rect x="${x(l.roomed) + 1}" y="${mid - 3}" width="${Math.max(2, x(l.checkout ?? now) - x(l.roomed) - 1)}" height="6" rx="2" fill="var(--inroom)"/>`;
      if (l.left !== null) s += `<text x="${x(l.left) - 4}" y="${mid + 4}" style="fill:var(--crit-ink);font-weight:700">✕</text>`;
      if (l.handoff !== null) s += `<text x="${x(l.handoff) - 4}" y="${mid + 4}" style="fill:var(--ink)">◆</text>`;
      if (l.told && l.told.status !== "range") s += `<text x="${x(l.checkin) + 4}" y="${y + 9}" style="font-size:9px">declined</text>`;
    }
    s += `</g>`;
  });
  if (ui.focusT !== null) s += `<line x1="${x(ui.focusT)}" x2="${x(ui.focusT)}" y1="16" y2="${H}" stroke="var(--crit)" stroke-width="2"/>`;
  s += `<line x1="${x(now)}" x2="${x(now)}" y1="16" y2="${H}" stroke="var(--ink)" stroke-width="1.5"/>`;
  s += `</svg>`;
  const top = box.scrollTop; box.innerHTML = s; box.scrollTop = top;
  const byPid = Object.fromEntries(lanes.map((l) => [l.pid, l]));
  box.querySelectorAll(".row").forEach((g) => {
    g.onclick = () => { ui.selected = g.dataset.pid; ui.detailKey = ""; if (ui.last.phone.choices.some((c) => c.pid === g.dataset.pid)) ui.pid = g.dataset.pid; poll(true); };
    g.onmousemove = (ev) => showTip(ev, byPid[g.dataset.pid]);
    g.onmouseleave = () => ($("tip").hidden = true);
  });
}
function showTip(ev, l) {
  const tip = $("tip");
  const told = !l.told ? "not checked in" : l.told.status === "range" ? `${l.told.lo}–${l.told.hi} min${l.kind === "scheduled" ? " behind" : ""}` : `page declined (${l.told.reason})`;
  const actual = l.actual !== null ? `${l.actual} min` : l.left !== null ? `walked away after ${l.left - l.checkin} min` : l.checkin !== null ? "still waiting" : "–";
  tip.innerHTML = `<b>${esc(l.name)}</b> · ${esc(l.kind)}${l.appt !== null ? `, appt ${clock12(l.appt)}` : ""}<br>Told at check-in: ${esc(told)}<br>Actual wait: ${esc(actual)}`;
  tip.hidden = false; tip.style.left = ev.clientX + 14 + "px"; tip.style.top = ev.clientY + 10 + "px";
}
function renderDecisions(o) {
  $("decisions").innerHTML = o.decisions.length ? o.decisions.slice(0, 80).map((d) =>
    `<div class="e${ui.focusT === d.t ? " focus" : ""}"><span class="w">${esc(d.time)}</span><b>${esc(d.who)}</b>: ${esc(d.what)}<div class="muted">${esc(d.why)}</div></div>`).join("") : `<p class="muted">No decisions yet.</p>`;
}
async function renderDetail(c) {
  if (!ui.selected) return;
  const key = `${ui.selected}|${c.t}|${ui.focusT}`;
  if (key === ui.detailKey) return;
  ui.detailKey = key;
  try {
    const d = await api(`/api/patient?pid=${encodeURIComponent(ui.selected)}${ui.at !== null ? `&at=${ui.at}` : ""}`);
    $("detail-title").textContent = `Patient log: ${d.name} (${d.kind}, ${d.persona})`;
    $("detail").innerHTML = d.log.map((e) => `<div class="e${ui.focusT === e.t ? " focus" : ""}"><span class="w">${esc(e.time)}</span><span class="s">${esc(e.src)}</span>${esc(e.text)}</div>`).join("");
    const f = $("detail").querySelector(".focus"); if (f) f.scrollIntoView({ block: "nearest" });
  } catch (e) { $("detail").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

// ---------------------------------------------------------------- suite tab
async function loadSuites() {
  try { ui.suites = await api("/api/suites"); } catch (e) { $("suite").innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const s = ui.suites[0];
  if (!s) { $("suite").innerHTML = `<p class="muted">No saved suite yet. Run <code>python3 -m evals</code>.</p>`; return; }
  window._srefs = [];
  const rows = s.checks.map((c) => {
    const fails = c.passed === false ? `<ul class="evid">${c.evidence.slice(0, 5).map((e, i) => {
      const r = c.refs[i] || {}; const n = window._srefs.push({ ...r, scenario: c.scenario }) - 1;
      return `<li>${r.kept ? `<a data-sref="${n}">${esc(e)}</a>` : `<a class="dead" title="This seed's run file was pruned">${esc(e)}</a>`}</li>`; }).join("")}</ul>`
      + (c.failing_kept.length ? `<div class="small">Open a failing seed: ${c.failing_kept.map((sd) => { const n = window._srefs.push({ seed: sd, scenario: c.scenario, t: null, pid: null, kept: true }) - 1; return `<a class="linkish" data-sref="${n}">seed ${sd}</a>`; }).join(" ")}</div>` : "") : "";
    return `<tr><td>${esc(c.id)}</td><td>${esc(c.scenario)}${c.group === "hold-out" ? ` <span class="pill">hold-out</span>` : ""}</td><td>${esc(c.score)}${fails}</td><td class="status ${c.passed === null ? "info" : c.passed ? "pass" : "fail"}">${c.passed === null ? "info" : c.passed ? "pass" : "fail"}</td></tr>`;
  }).join("");
  $("suite").innerHTML = `<p>Suite ${esc(s.suite_id)} · ${s.seeds} seeds per scenario. Click a failure to open that seed's saved run at that minute.</p>
    <table class="score"><tr><th>ID</th><th>Scenario</th><th>Score</th><th>Result</th></tr>${rows}</table>`;
  $("suite").querySelectorAll("[data-sref]").forEach((a) => (a.onclick = async () => {
    const r = window._srefs[+a.dataset.sref];
    ui.selected = null; ui.focusT = null;   // a different run: old patient ids mean other people
    $("detail").innerHTML = ""; $("detail-title").textContent = "Patient log";
    await control({ action: "open_saved", suite: s.suite_id, scenario: r.scenario, seed: r.seed });
    showTab("live"); goTo({ t: r.t ?? null, pid: r.pid ?? null });
  }));
}

// ---------------------------------------------------------------- finance
function tile(k, v, sub) { return `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${v}</div><div class="s">${sub}</div></div>`; }
function renderFinance(f, score, c) {
  if (!f) { $("finance").innerHTML = `<p class="muted">Start a run.</p>`; return; }
  const d = f.derived, n = f.counts;
  const sv = (x) => (x === null ? `<span class="muted">hidden</span>` : x.toFixed(2));
  const grp = d.satisfied_by_group;
  const e10 = score.find((r) => r.id === "E10");
  $("finance").innerHTML = `<div class="tiles">
    ${tile("Patients seen", n.seen, `${n.checked_in} checked in`)}
    ${tile("Room use", pct(d.room_utilization), `${n.room_minutes} of ${n.room_capacity_minutes} room-minutes`)}
    ${tile("Walk-ins who left unseen", `${n.walkaways} <span class="muted">vs ${n.baseline_walkaways}</span>`, `with the product vs without (same people, same day): ${pct(d.walkaway_rate)} vs ${pct(d.baseline_walkaway_rate)}`)}
    ${tile("No-shows / late", `${pct(d.noshow_rate)} / ${pct(d.late_rate)}`, `${n.noshows} of ${n.booked} booked; ${n.late} of ${n.sched_checked_in} late`)}
    ${tile("\"How long?\" at the desk", n.desk_questions, `staff time ${d.staff_minutes_per_run_modeled} min, modeled (${n.presses} presses, ${n.approvals} approvals)`)}
    ${tile("Estimate coverage", pct(d.coverage), `${n.cov_num} of ${n.cov_den} waits inside the range`)}
    ${tile("False reassurance", pct(d.false_reassurance), `${n.fr_num} of ${n.fr_den} walk-ins waited 15+ min past the top`)}
    ${tile("Page declined", pct(d.decline_rate), `${n.declined_open} of ${n.views_open} open minutes`)}
    ${tile("Cost per day, modeled", "$" + d.cost_per_day_modeled.toFixed(2), `${n.messages} texts; model $${n.model_usd.toFixed(2)} (${n.model_calls} calls); $${(d.cost_per_patient_modeled ?? 0).toFixed(4)} per patient`)}
    ${tile("Survey: accurate / satisfied / reuse", `${sv(d.survey_accurate)} / ${sv(d.survey_satisfied)} / ${sv(d.survey_reuse)}`, `1–5 scale, ${d.survey_n} answers; groups under ${d.min_group} are hidden`)}
    ${tile("Satisfaction by what they were told", `${sv(grp.inside.mean)} <span class="muted">vs</span> ${sv(grp.over.mean)}`,
        `inside the range (${grp.inside.n}) vs waited 15+ min past it (${grp.over.n}); declined: ${sv(grp.declined.mean)} (${grp.declined.n})`)}
  </div>
  <p class="recon">E10, totals reconcile with the logs: <span class="status ${e10 ? (e10.status.includes("fail") ? "fail" : e10.status === "pending" ? "pending" : "pass") : "pending"}">${e10 ? esc(e10.score) : "–"}</span>
   ${e10 && e10.passed === false ? `<span class="err">${esc(e10.evidence.slice(0, 3).join("; "))}</span>` : ""}</p>
  <p class="note">Survey answers in this milestone come from a scripted formula plus noise, so the link between satisfaction and false reassurance is built in, not discovered. Without the product, walk-ins have no range to judge by and use a fixed patience instead.</p>`;
}
async function renderTrend(c) {
  if (!ui.suites) { try { ui.suites = await api("/api/suites"); } catch (e) { return; } }
  const sc = c.source.scenario;
  const rows = ui.suites.filter((s) => s.finance && s.finance[sc]).slice(0, 8);
  $("trend-note").textContent = `(${sc.replace(/_/g, " ")}, pooled over each suite's seeds)`;
  $("trend").innerHTML = rows.length ? `<div class="scrollx"><table><tr><th>Suite</th><th class="num">Seen / day</th><th class="num">Coverage</th><th class="num">False reassurance</th><th class="num">Declined</th><th class="num">Walkaways with / without</th><th class="num">Satisfied (1–5)</th><th class="num">Cost / day, modeled</th></tr>${rows.map((s) => {
    const d = s.finance[sc].derived;
    return `<tr><td>${esc(s.suite_id)}</td><td class="num">${d.seen_per_run}</td><td class="num">${pct(d.coverage)}</td><td class="num">${pct(d.false_reassurance)}</td><td class="num">${pct(d.decline_rate)}</td><td class="num">${pct(d.walkaway_rate)} / ${pct(d.baseline_walkaway_rate)}</td><td class="num">${d.survey_satisfied ?? "hidden"}</td><td class="num">$${d.cost_per_day_modeled.toFixed(2)}</td></tr>`; }).join("")}</table></div>`
    : `<p class="muted small">No saved suite has finance numbers for this scenario yet. Run <code>python3 -m evals</code>.</p>`;
}

// ---------------------------------------------------------------- loop
let polling = false, timer = null;
async function poll(now) {
  if (polling && !now) return;
  polling = true;
  try {
    const q = `/api/state?pid=${encodeURIComponent(ui.pid)}&lang=${ui.lang}${ui.at !== null ? `&at=${ui.at}` : ""}`;
    const s = await api(q);
    ui.last = s;
    setupMeta(s.meta);
    if (!s.loaded) { renderControl(s); return; }
    if (ui.at !== null && s.control.following) ui.at = null;
    renderControl(s);
    renderPhone(s.phone);
    renderStaff(s.staff, s.control);
    renderScore(s.scorecard, s.control);
    renderTimeline(s.observer);
    renderDecisions(s.observer);
    renderFinance(s.finance, s.scorecard, s.control);
    renderDetail(s.control);
    renderTrend(s.control);
    $("err").textContent = $("err").dataset.sticky ? "" : $("err").textContent; delete $("err").dataset.sticky;
  } catch (e) { $("err").textContent = e.message; $("err").dataset.sticky = "1"; }
  finally {
    polling = false;
    clearTimeout(timer);
    timer = setTimeout(poll, ui.last?.control?.running ? 300 : 1500);
  }
}
renderLegend();
poll(true);

// ---------------------------------------------------------------- agents tab
// Every test-actor persona: its prompt, what it did in this run, and whether it
// behaved the way its habits say (persona fidelity).
ui.agentOpen = null;
let agentsTimer = null;
async function loadAgents(force) {
  if (ui.tab !== "agents") return;
  let a;
  try { a = await api("/api/agents"); } catch (e) { $("agents").innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const pill = (p) => {
    if (!p.in_run) return `<span class="pill">not in this run</span>`;
    const f = p.fidelity;
    if (!f) return `<span class="pill">no data yet</span>`;
    return `<span class="pill ${f.status === "pass" ? "ok" : f.status === "fail" ? "bad" : ""}">${f.status === "pass" ? "✓ behaves as written" : f.status === "fail" ? "✕ drifted" : "untested"}</span>`;
  };
  const card = (p) => `<button class="agent ${ui.agentOpen === p.id ? "open" : ""}" data-agent="${esc(p.id)}">
      <span class="agent-top"><strong>${esc(p.title)}</strong>${pill(p)}</span>
      <span class="small muted">${p.fidelity ? esc(p.fidelity.metric) + (p.fidelity.target ? ` · target ${esc(p.fidelity.target)}` : "") : p.in_run ? "" : "Not on duty in this scenario."}</span>
      ${a.llm && p.in_run ? `<span class="small">${p.calls.n} agent call(s), ${p.calls.ok} valid</span>` : ""}</button>`;
  const open = a.personas.find((p) => p.id === ui.agentOpen);
  const detail = open ? `<div class="agent-detail card">
      <h4>${esc(open.title)} <span class="tag-actor">Simulated person (test actor)</span></h4>
      <p class="small muted">Prompt file: prompts/actors/${esc(open.id)}.md, plus the shared rules. Edit it and record a new run to change how this persona behaves.</p>
      <pre class="prompt">${esc(open.prompt)}</pre>
      ${open.recent.length ? `<h4>Latest decisions in this run</h4>${open.recent.map((c) => `<details><summary>${esc(c.time)} · ${esc(c.role)} ${c.ok ? "" : `<span class="pill bad">fell back: ${esc(c.error || "invalid")}</span>`}</summary>
        <p class="small muted">What it saw</p><pre class="prompt">${esc(c.prompt)}</pre><p class="small muted">What it decided</p><pre class="prompt">${esc(c.reply || "")}</pre></details>`).join("")}`
        : `<p class="small muted">${a.llm ? "No decisions yet at this minute." : "This run uses scripted rules, so there are no agent decisions to show. Press “Watch AI demo day (AI people, recorded)” to see the AI agents."}</p>`}
    </div>` : "";
  $("agents").innerHTML = `<p><strong>Who plays the people:</strong> ${esc(a.mode)}${a.llm ? ` · model: ${esc(a.model)} via Claude Code on a subscription ($0 billed)` : ""}.</p>
    <p class="small muted">Test actors create the day; they are not graded. The product (estimator, safety rules, texts) is plain code and never shares a prompt or a model call with them. Persona fidelity (F1) checks each actor kept its habits, so the test stays realistic.</p>
    <div class="agents-grid">${a.personas.map(card).join("")}</div>${detail}
    <details><summary class="small">Shared rules every test actor follows</summary><pre class="prompt">${esc(a.rules)}</pre></details>`;
  $("agents").querySelectorAll("[data-agent]").forEach((b) => (b.onclick = () => { ui.agentOpen = ui.agentOpen === b.dataset.agent ? null : b.dataset.agent; loadAgents(true); }));
  clearTimeout(agentsTimer);
  if (ui.last?.control?.running) agentsTimer = setTimeout(() => loadAgents(false), 3000);
}
