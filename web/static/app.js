// Clinic Wait Time demo UI (Milestone 4.5 redesign). Polls /api/state and redraws. No libraries.
// Views: clinic view, and behind the scenes (how it connects, promises and goals, results,
// test actors, run inspector). Everything shown comes from the simulation's own logs.
"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x === null || x === undefined ? "–" : Math.round(x * 100) + "%");
const clock12 = (t) => { if (t === null || t === undefined) return ""; const h = Math.floor(t / 60) % 24, m = t % 60; return `${((h + 11) % 12) + 1}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`; };
const cap = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);
const ICON = {
  check: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg>`,
  bang: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" aria-hidden="true"><path d="M12 7v6M12 17h.01"/></svg>`,
  shield: `<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg>`,
  target: `<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>`,
  arrow: `<svg class="arrow" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>`,
  warn: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" style="flex:none"><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/></svg>`,
  info: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" style="flex:none"><circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v5h1"/></svg>`,
  refresh: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/></svg>`,
  lock: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="11" width="18" height="10" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`,
};

// Plain names for scenarios and checks. Test IDs (G1, E4…) stay behind a toggle.
const SC_NAME = { normal_day: "Normal day", walkin_surge: "Walk-in rush", kim_goes_quiet: "Kim goes quiet", luis_day: "Luis on the desk",
  pat_day: "Pat on the desk", understaffed_opening: "Understaffed opening", luis_surge: "Luis on a rush day" };
const scName = (k) => SC_NAME[k] || String(k || "").replace(/_/g, " ");
// Plain-language descriptions of each kind of day, for the run bar and the "All kinds of day" panel.
const SC_DESC = { normal_day: "Dana, an accurate receptionist, on the desk. A typical afternoon.",
  kim_goes_quiet: "Kim on the desk. A 3 PM rush swamps her and she stops updating for a while. The demo is this day.",
  walkin_surge: "Dana on the desk. A rush of walk-ins from 3 to 4 PM.",
  luis_day: "Luis on the desk. He updates in batches and guesses waits from a headcount.",
  pat_day: "Pat on the desk. Updates on time, but the guesses are erratic.",
  understaffed_opening: "Held-back test day: one provider is out from opening. Never used to tune the product.",
  luis_surge: "Held-back test day: Luis on the desk during a walk-in rush. Never used to tune the product." };
const APPROVER_DESC = { persona: "The simulated front desk approves or rejects suggestions.",
  human: "You approve or reject suggestions, with a reason, in the front desk panel." };
function showRunHints() {
  const k = $("scenario").value;
  $("scenario-desc").textContent = SC_DESC[k] || (ui.meta && ui.meta.scenarios[k]) || "";
  $("approver-desc").textContent = APPROVER_DESC[$("approver").value] || "";
}
const GATE_TEXT = { G1: "Urgent messages get the fixed emergency reply, never a wait time", G2: "No wait time shown when data is over 15 minutes old",
  G3: "No one moves in line without a logged staff approval", G4: "No patient names or medical words on the page or in texts",
  G5: "Every view says when it was updated and how to get emergency help" };
// dir: "up" = higher is better, "down" = lower is better, "exact" = all days must match. unit: "pct" or "min".
const GOALS = { E1: { t: "The range covers the real wait", dir: "up", goal: 0.8, unit: "pct" },
  E2: { t: "Ranges are narrow enough to be useful", dir: "down", goal: 20, unit: "min" },
  E3: { t: "Rarely tells someone a wait that turns out too short", dir: "down", goal: 0.05, unit: "pct" },
  E4: { t: "Stops showing a time when it should, and only then", dir: "up", goal: 0.9, unit: "pct" },
  E5: { t: "Beats the front desk’s own guess", dir: "up", goal: 0.7, unit: "pct" },
  E9: { t: "A late patient’s wait counts from their appointment time", dir: "exact" },
  E10: { t: "Results numbers match the logs exactly", dir: "exact" },
  F1: { t: "Test actors keep the habits their persona describes", dir: "exact" } };
const checkName = (id) => GATE_TEXT[id] || GOALS[id]?.t || id;

const URLQ = new URLSearchParams(location.search);
const ui = { pid: URLQ.get("pid") || "maria", lang: URLQ.get("lang") === "es" ? "es" : "en", at: null, selected: null, focusT: null, tab: "live",
  view: "clinic", last: null, metaDone: false, inboxKey: "", detailKey: "", suites: null, showIds: false, goalSet: "tuning", flowSel: "estimator",
  desk: null, deskKey: "" };

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
  catch (e) {
    // A server started before this page was updated rejects newer buttons: say how to fix it.
    $("err").textContent = /unknown action/.test(e.message)
      ? "This server is running older code than the page. Stop it (Ctrl+C in its terminal) and run python3 -m web again."
      : e.message;
    $("err").scrollIntoView({ block: "nearest" });
  }
}
async function suites() {
  if (!ui.suites) { try { ui.suites = await api("/api/suites"); } catch { ui.suites = []; } }
  return ui.suites;
}

// ---------------------------------------------------------------- views
const VIEWS = ["clinic", "flow", "promises", "results", "actors", "inspect"];
function showView(v, push = true) {
  if (!VIEWS.includes(v)) v = "clinic";
  ui.view = v;
  VIEWS.forEach((x) => ($("view-" + x).hidden = x !== v));
  document.querySelectorAll("[data-view]").forEach((a) => a.classList.toggle("on", a.dataset.view === v));
  if (push && location.hash !== "#" + v) history.replaceState(null, "", "#" + v);
  if (v === "flow") renderFlow();
  if (v === "promises") renderPromises();
  if (v === "actors") loadAgents(true);
  if (v === "inspect" && ui.tab === "suite") loadSuites();
  if (ui.last?.loaded) poll(true);
}
document.querySelectorAll("[data-view]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); showView(a.dataset.view); window.scrollTo({ top: 0 }); }));
window.addEventListener("hashchange", () => showView(location.hash.slice(1), false));

// ---------------------------------------------------------------- run bar
function setupMeta(meta) {
  if (ui.metaDone) return;
  const sel = $("scenario");
  sel.innerHTML = Object.entries(meta.scenarios).map(([k, d]) =>
    `<option value="${esc(k)}" title="${esc(d)}">${esc(scName(k))}${meta.holdouts.includes(k) ? " (held-back test day)" : ""}</option>`).join("");
  $("seed").value = meta.default_seed;
  ui.meta = meta;
  $("days-list").innerHTML = Object.keys(meta.scenarios).map((k) => `<dt>${esc(scName(k))}</dt><dd>${esc(SC_DESC[k] || meta.scenarios[k])}</dd>`).join("");
  showRunHints();
  $("speeds").innerHTML = meta.speeds.map((s) => `<button data-speed="${esc(s)}">${esc(s)}</button>`).join("");
  $("speeds").onclick = (e) => { const s = e.target.dataset.speed; if (s) control({ action: "speed", speed: s }); };
  ui.metaDone = true;
}
$("start").onclick = () => { ui.at = null; ui.selected = null; ui.focusT = null; control({ action: "start", scenario: $("scenario").value, seed: +$("seed").value, approver: $("approver").value }); };
$("play").onclick = () => control({ action: ui.last?.control?.running ? "pause" : "play" });
$("step").onclick = () => { ui.at = null; control({ action: "step" }); };
$("toend").onclick = () => { ui.at = null; control({ action: "to_end" }); };
$("replay").onclick = () => { ui.at = null; control({ action: "replay" }); };
$("demo").onclick = async () => {   // step 1: load the recording and start playing it straight away
  ui.at = null; ui.selected = null; ui.focusT = null;
  showView("clinic");
  await control({ action: "start_demo" });
  if (!$("err").textContent) control({ action: "play" });
};
$("scenario").onchange = $("approver").onchange = showRunHints;
document.querySelectorAll("[data-more]").forEach((l) => (l.onclick = (e) => {
  e.preventDefault(); $("about").open = true;
  $(l.dataset.more === "seed" ? "about-seed" : "about-days").scrollIntoView({ behavior: "smooth", block: "nearest" });
}));
$("about-close").onclick = () => { $("about").open = false; };
$("live").onclick = () => { ui.at = null; ui.focusT = null; poll(true); };
$("scrub").oninput = (e) => { ui.at = +e.target.value; poll(true); };
$("pid").onchange = (e) => { ui.pid = e.target.value; poll(true); };
document.querySelectorAll("[data-lang]").forEach((b) => (b.onclick = () => { ui.lang = b.dataset.lang; poll(true); }));
document.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));
$("toggle-ids").onclick = () => { ui.showIds = !ui.showIds; renderPromises(); };

function showTab(tab) {
  ui.tab = tab;
  document.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $("tab-live").hidden = tab !== "live";
  $("tab-suite").hidden = tab !== "suite";
  if (tab === "suite") loadSuites();
}

function renderControl(s) {
  const c = s.control;
  $("clock").textContent = c ? `${c.time}${c.done ? " · day over" : ""}${c.following ? "" : " · looking back"}` : "no run";
  $("play").disabled = !c || c.mode !== "live" || c.done;
  $("play").textContent = c && c.running ? "Pause" : "Play";
  $("play").classList.toggle("primary", !!c && c.mode === "live" && !c.running && !c.done);   // the next thing to press
  $("step").disabled = $("toend").disabled = !c || c.mode !== "live" || c.done;
  $("replay").disabled = !c || c.mode !== "live";
  document.querySelectorAll("#speeds button").forEach((b) => b.classList.toggle("on", b.dataset.speed === s.meta.speed));
  const sc = $("scrub");
  if (c && c.now !== null) { sc.disabled = false; sc.min = c.open_t; sc.max = c.now; sc.value = c.t; } else { sc.disabled = true; }
  $("live").disabled = !c || c.following;
  if (!c) return;
  const src = c.source;
  const srcKey = JSON.stringify(src);
  if (ui.srcKey !== srcKey) {   // keep the pickers in step with what is loaded
    ui.srcKey = srcKey;
    $("scenario").value = src.scenario; $("seed").value = src.seed;
    if (src.approver === "human" || src.approver === "persona") $("approver").value = src.approver;
    showRunHints();
    loadDesk();
  }
  $("source").textContent = c.mode === "review"
    ? `Saved run from suite ${src.suite}: ${src.scenario}, seed ${src.seed} (read only)`
    : `${src.demo ? "Demo (AI people, recorded, replayed free): " : "Your simulation (rule-based people): "}${src.scenario}, seed ${src.seed}, approvals by ${src.approver === "human" ? "you" : "desk persona"}${src.replay ? `, replaying ${src.replayed_actions} logged staff action(s)` : ""}`;
  $("brand-sub").textContent = `A simulated afternoon at a small clinic · ${scName(src.scenario)}${ui.desk ? ` · ${ui.desk.split(",")[0]} on the front desk` : ""}${src.demo ? " · AI people (recorded)" : ""}`;
}
async function loadDesk() {
  try {
    const a = await api("/api/agents");
    const d = a.personas.find((p) => p.kind === "front desk" && p.in_run);
    ui.desk = d ? d.title : null;
  } catch { ui.desk = null; }
}

// ---------------------------------------------------------------- patient phone
const PHONE_TXT = { en: { title: "Clinic wait times", texts: "Your texts", none: "No texts yet.", sim: "Simulation, synthetic data" },
                    es: { title: "Tiempos de espera", texts: "Sus mensajes", none: "Todavía no hay mensajes.", sim: "Simulación, datos sintéticos" } };
// Split "Walk-in wait: about 25–50 min." at the first colon for layout only; the text stays word for word.
function pline(m, i) {
  const k = m.indexOf(": ");
  if (k > 0 && k < 40) return `<p class="pline${i === 0 ? " first" : ""}"><span class="lbl">${esc(m.slice(0, k + 1))}</span> <span class="val">${esc(m.slice(k + 2))}</span></p>`;
  return `<p class="pline${i === 0 ? " first" : ""}">${esc(m)}</p>`;
}
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
  const page = p.page, el = $("page");
  if (!page) { el.className = "page"; el.innerHTML = `<p class="muted">The page appears at the first simulated minute.</p>`; }
  else {
    el.className = "page " + page.status;
    el.innerHTML = page.main.map(pline).join("")
      + (page.fresh ? `<p class="fresh">${ICON.refresh}<span>${esc(page.fresh)}</span></p>` : "")
      + (page.urgent ? `<p class="urgent">${esc(page.urgent)}</p>` : "")
      + (page.verbatim ? "" : `<p class="err">Display split changed the text</p>`);
  }
  $("thread").innerHTML = p.thread.length ? p.thread.map((m) =>
    `<div class="bubble ${m.dir}"><span class="when">${esc(m.time)}</span>${esc(m.text)}</div>`).join("") : `<p class="muted small">${L.none}</p>`;
}

// ---------------------------------------------------------------- front desk tablet
function rng(a) { return a && a[0] !== null ? `${a[0]}–${a[1]} min` : "–"; }
function confLabel(e, pg) {
  if (pg && pg.status !== "range") return ["Holding", pg.updated_min_ago > 15 ? `data ${pg.updated_min_ago} min old` : "page says call"];
  if (!e) return ["–", ""];
  const c = e.confidence;
  return [c >= 0.75 ? "High" : c >= 0.4 ? "Medium" : "Low", `${e.samples} recent waits`];
}
function renderStaff(st, c) {
  if (!st) { $("stats").innerHTML = ""; $("estimate").innerHTML = `<p class="muted">Not started.</p>`; return; }
  const e = st.estimate, pg = st.page;
  const name = ui.desk ? ui.desk.split(",")[0] : "Front desk";
  $("desk-name").textContent = `${name} · front desk`;
  $("desk-initial").textContent = name.charAt(0);
  $("desk-time").textContent = c.time;
  const [cl, cn] = confLabel(e, pg);
  $("stats").innerHTML = `
    <div class="stat"><div class="k">Waiting</div><div class="v"><span class="dot" style="background:var(--waiting)"></span><span class="mono">${st.queue.length + st.walkins.length}</span></div><div class="s">as recorded</div></div>
    <div class="stat"><div class="k">In a room</div><div class="v"><span class="dot" style="background:var(--inroom)"></span><span class="mono">${st.in_room.length}</span></div><div class="s">${st.providers ?? "–"} provider(s) on duty</div></div>
    <div class="stat"><div class="k">Confidence</div><div class="v" style="font-size:18px">${esc(cl)}</div><div class="s">${esc(cn)}</div></div>`;
  const pageState = !pg ? "–" : pg.status === "range" ? `<span class="pill ok">showing ranges</span>`
    : `<span class="pill bad">saying “call the clinic”</span> <span class="muted small">${esc(pg.reason)}</span>`;
  $("estimate").innerHTML = `<dl class="est">
    <dt>Walk-in wait</dt><dd>${e ? rng(e.wait) : "–"}</dd>
    <dt>Appointments</dt><dd>${e ? rng(e.behind) + " behind" : "–"}</dd>
    <dt>Whole visit</dt><dd>${e ? rng(e.total) : "–"}</dd>
    <dt>Patient page</dt><dd>${pageState}${pg ? ` <span class="muted small">· last desk update ${pg.updated_min_ago} min ago</span>` : ""}</dd></dl>`;

  $("flags").innerHTML = st.flags.length ? `<div class="sub-head">Data-quality flags</div>` + st.flags.slice(-4).reverse().map((f) =>
    `<div class="flag">${ICON.warn}<div><b>${esc(f.name || "Clinic")}</b> · ${esc(f.kind.replace(/_/g, " "))}<div>${esc(f.text)}</div></div></div>`).join("")
    + (st.flags.length > 4 ? `<p class="small muted">and ${st.flags.length - 4} earlier flag(s)</p>` : "") : "";

  // Approval cards: rebuild only when proposals or permissions change, so typed reasons survive polling.
  const key = JSON.stringify([st.inbox.map((i) => i.id), c.can_act, c.human_approver, c.queued_actions]);
  if (key !== ui.inboxKey) {
    ui.inboxKey = key;
    $("inbox").innerHTML = st.inbox.map((i) => `<div class="proposal" data-id="${i.id}">
        <div class="row" style="justify-content:space-between;align-items:center"><span class="tag k-rule">Suggestion from ${i.source === "exception_stub" ? "the exception stand-in (scripted)" : esc(i.source)}</span><span class="small muted mono">${esc(i.time)}</span></div>
        <div class="ptitle">${esc(i.name)}: ${esc(i.action === "reslot" ? "put back on the schedule?" : i.action)}</div>
        <div class="small">${esc(i.reason)}</div>
        ${c.human_approver && c.can_act ? `<label class="small" style="font-weight:600">Your reason (required, one line)</label><div class="row"><input placeholder="One-line reason" aria-label="Reason">
          <button data-d="approved">Approve</button><button data-d="rejected">Reject</button></div>`
        : `<div class="small muted">${c.human_approver ? "Go back to now to decide." : "The desk persona decides this one. To decide yourself, set “Approvals by” to “me” in step 2 and run a simulation."}</div>`}
        <div class="small muted">Suggestions can’t move anyone in line. Only staff can, with a logged reason.</div>
      </div>`).join("");
    $("inbox").querySelectorAll(".proposal button").forEach((b) => (b.onclick = () => {
      const box = b.closest(".proposal"); const reason = box.querySelector("input").value.trim();
      if (!reason) { $("err").textContent = "Type a one-line reason first."; return; }
      control({ action: "decide", proposal_id: +box.dataset.id, decision: b.dataset.d, reason });
    }));
  }
  $("decided").innerHTML = st.decided.slice(-3).reverse().map((d) =>
    `<div class="decided-row${d.decision === "approved" ? "" : " rej"}">${ICON.check}<div><b>${esc(d.name)}</b>: ${esc(d.decision)} by ${esc(d.by === "human" ? "human (you)" : cap(d.by))} at ${esc(d.decided)}. “${esc(d.why)}” <span class="muted">Logged.</span></div></div>`).join("");

  const qrow = (q, sched) => `<tr><td class="num">${q.pos}</td><td>${esc(q.name)}${q.late ? ` <span class="pill warn">late, booked ${esc(q.appt)}</span>` : ""}${q.reslotted ? ` <span class="pill ok">back on schedule</span>` : ""}</td>
     <td class="time">${sched ? esc(q.appt) : esc(q.checkin)}</td><td class="num">${q.waiting_min}</td></tr>`;
  $("queue").innerHTML = st.queue.length ? `<div class="scrollx"><table><tr><th class="num" title="Place in the whole line">Place</th><th>Name</th><th>Appt</th><th class="num">Min here</th></tr>${st.queue.map((q) => qrow(q, true)).join("")}</table></div>` : `<p class="muted small">Empty.</p>`;
  $("walkins").innerHTML = st.walkins.length ? `<div class="scrollx"><table><tr><th class="num" title="Place in the whole line">Place</th><th>Name</th><th>In at</th><th class="num">Min here</th></tr>${st.walkins.map((q) => qrow(q, false)).join("")}</table></div>` : `<p class="muted small">Empty.</p>`;
  $("inroom").innerHTML = st.in_room.length ? `<div class="scrollx"><table><tr><th>Name</th><th>Since</th><th class="num">Min</th><th></th></tr>${st.in_room.map((r) =>
    `<tr><td>${esc(r.name)}</td><td class="time">${esc(r.roomed)}</td><td class="num">${r.minutes}</td><td>${r.run_long ? `<span class="pill warn">running long</span>`
      : `<button class="btn small" data-rl="${esc(r.pid)}" ${c.can_act ? "" : "disabled"}>Will run long</button>`}</td></tr>`).join("")}</table></div>` : `<p class="muted small">No one in a room.</p>`;
  $("inroom").querySelectorAll("[data-rl]").forEach((b) => (b.onclick = () => control({ action: "run_long", pid: b.dataset.rl })));
}

// ---------------------------------------------------------------- caption + behind the scenes at this moment
// Picks the most important thing happening at this minute and explains it in plain words.
function gateLive(score, id) {
  const r = (score || []).find((x) => x.id === id);
  if (!r) return { cls: "neutral", txt: "Not checked on this day" };
  const s = r.status.replace("so far: ", "");
  return s === "pass" ? { cls: "ok", txt: `Kept on this day${r.status.startsWith("so far") ? " so far" : ""}` }
    : s === "fail" ? { cls: "bad", txt: "Broken on this day. See the run inspector." } : { cls: "neutral", txt: cap(s) };
}
function suiteGoal(id, sc) {
  const s = ui.suites && ui.suites[0];
  return s ? s.checks.find((c) => c.id === id && c.scenario === sc) : null;
}
function moment(s) {
  const c = s.control, st = s.staff, o = s.observer, t = c.t, sc = c.source.scenario;
  const S = (label, k) => ({ label, k });
  if (!st) return { title: c.source.demo ? "The demo is loaded" : "Your simulation is loaded", gate: "G5", goal: null,
    body: "The clinic hasn’t opened yet. Press Play to start the afternoon, or Step to go one minute at a time.",
    why: "Every day starts from a seed, so the same day replays exactly.",
    steps: [S("Day script sets up the afternoon", "rule")] };
  const desk = ui.desk ? ui.desk.split(",")[0] : "The front desk";
  const actor = c.source.demo ? "AI test actor" : "test actor";
  const waitingTrue = o.lanes.filter((l) => l.checkin !== null && l.checkin <= t && (l.roomed === null || l.roomed > t) && (l.left === null || l.left > t)).length;
  const urgent = o.lanes.filter((l) => l.handoff !== null && l.handoff <= t && t - l.handoff <= 12).sort((a, b) => b.handoff - a.handoff)[0];
  const recentDec = st.decided.filter((d) => t - d.t <= 6).slice(-1)[0];
  const pg = st.page, e = st.estimate;
  if (urgent) return { title: `${urgent.name} texted about something urgent`, gate: "G1", goal: null,
    body: `Urgent words in a text trigger a reply written and approved in advance. No AI writes or changes it, and it never includes a wait time. A staff member takes over from there.`,
    why: "The product never gives medical advice. When it hears something urgent, it points to emergency care, every time, in the same words.",
    steps: [S(`${urgent.name} sends a text`, "sim"), S("Urgent-word check", "rule"), S("Fixed emergency reply", "out"), S("Staff member takes over", "staff")] };
  if (pg && pg.status !== "range") return { title: "The page stopped showing a time", gate: "G2", goal: "E4",
    body: `${pg.updated_min_ago > 15 ? `${desk} hasn’t updated anything for ${pg.updated_min_ago} minutes.` : "The desk’s records don’t look reliable right now."} Rather than guess, the page asks patients to call the clinic${pg.reason ? ` (${pg.reason})` : ""}.`,
    why: "Saying “we don’t know” is better than a confident wrong answer. The desk sees a flag right away.",
    steps: [S(`${desk} falls behind`, "sim"), S("Data-quality check flags it", "rule"), S("Safety rules hold the number", "rule"), S("“Please call the clinic”", "out")] };
  if (st.inbox.length) { const i = st.inbox[0]; return { title: `${i.name} arrived late`, gate: "G3", goal: null,
    body: `${i.reason} A suggestion is waiting for staff. Today it comes from a scripted stand-in for the exception agent (the AI version arrives in Milestone 5). Either way, it can’t move anyone in line by itself.`,
    why: "Software can save staff time on routine calls, but changes that affect other patients stay with a person, and every decision is logged.",
    steps: [S(`${i.name} checks in late`, "sim"), S("Exception stand-in suggests", "rule"), S("Waiting for staff", "staff")] }; }
  if (recentDec) return { title: `${cap(recentDec.by)} ${recentDec.decision} a change for ${recentDec.name}`, gate: "G3", goal: null,
    body: `The decision was logged with a reason: “${recentDec.why}”. ${recentDec.decision === "approved" ? "The line changed only after that approval." : "Nothing in the line moved."}`,
    why: "Every change to someone’s place in line can be traced to a person and a reason.",
    steps: [S("Exception stand-in suggested", "rule"), S(`${cap(recentDec.by)} decides, with a reason`, "staff"), S(recentDec.decision === "approved" ? "Line updated" : "Line unchanged", "rule")] };
  if (waitingTrue > 8) return { title: "Walk-ins pile up", gate: "G4", goal: sc === "walkin_surge" || sc === "luis_surge" ? "E3" : "E1",
    body: `${waitingTrue} people are actually waiting (the desk has recorded ${st.queue.length + st.walkins.length}). The range gets wider instead of pretending to know${e ? `: walk-ins now see ${rng(e.wait)}` : ""}. Wide is honest, but very wide stops being useful.`,
    why: "Telling someone “20 minutes” and making them wait an hour is the fastest way to lose their trust.",
    steps: [S("Walk-ins check in", "sim"), S(`${desk} records them`, "sim"), S("Estimator", "rule"), S("Safety rules", "rule"), S("Patient page", "out")] };
  if (t - c.open_t < 15) return { title: "The clinic opens", gate: "G5", goal: null,
    body: "No one has finished a visit yet, so there is little data from today. The page gives a range based on typical days and says when it was updated.",
    why: "Patients get a useful answer from the first minute, and are told how fresh it is.",
    steps: [S("Estimator", "rule"), S("Safety rules", "rule"), S("Patient page", "out")] };
  return { title: "A regular stretch", gate: "G5", goal: "E1",
    body: `${waitingTrue} waiting and ${st.in_room.length} in a room. ${desk} records check-ins and room changes; the estimator turns recent real waits into a range${e ? ` (walk-ins: ${rng(e.wait)})` : ""}, and the safety rules check it before patients see it.`,
    why: "Ranges, never a single number, with “updated X min ago” on every view.",
    steps: [S(`${desk} records a change`, "sim"), S("Estimator", "rule"), S("Safety rules", "rule"), S("Patient page", "out")] };
}
function goalLine(chk) {
  const g = GOALS[chk.id];
  if (chk.passed === null) return { cls: "neutral", head: "Accuracy goal: reported only", t: g.t, s: `${chk.score} on ${scName(chk.scenario)} days` };
  return { cls: chk.passed ? "ok" : "bad", head: `Accuracy goal: ${chk.passed ? "met" : "not met yet"}`, t: g.t,
    s: g.dir === "exact" ? `${chk.score} on ${scName(chk.scenario)} days` : `${fmtVal(chk.value, g.unit)} on ${scName(chk.scenario)} days (goal: ${g.dir === "down" ? "at most " : ""}${fmtVal(g.goal, g.unit)})` };
}
function renderMoment(s) {
  const m = moment(s), c = s.control;
  $("cap-time").textContent = `${c.time}${c.following ? "" : " · looking back"}`;
  $("cap-title").textContent = m.title; $("cap-body").textContent = m.body; $("cap-why").textContent = m.why;
  $("bts-time").textContent = c.time;
  $("bts-steps").innerHTML = m.steps.map((x, i) => `<span class="tag k-${x.k}">${esc(x.label)}</span>${i < m.steps.length - 1 ? ICON.arrow : ""}`).join("");
  const gl = gateLive(s.scorecard, m.gate);
  let html = `<div class="check ${gl.cls}">${ICON.shield}<div><div class="eyebrow">Safety promise checked here${ui.showIds ? ` · ${m.gate}` : ""}</div><div class="t">${esc(GATE_TEXT[m.gate])}</div><div class="s">${esc(gl.txt)}</div></div></div>`;
  const chk = m.goal && suiteGoal(m.goal, c.source.scenario);
  if (chk) { const g = goalLine(chk); html += `<div class="check ${g.cls}">${ICON.target}<div><div class="eyebrow">${esc(g.head)}</div><div class="t">${esc(g.t)}</div><div class="s">${esc(g.s)}</div></div></div>`; }
  $("bts-checks").innerHTML = html;
}

// ---------------------------------------------------------------- how it connects
const NODES = {
  director: { lane: "world", kind: "rule", name: "Day script", sub: "clock, bookings, staffing",
    job: "Runs the simulated afternoon: the clock, the appointment book, who is working, and surprises like a walk-in rush. A fixed seed makes every day replayable exactly.",
    autonomy: "Not applicable: it sets the scene", built: "Plain code", evals: "Replays must match exactly, table for table", today: "Built." },
  desk: { lane: "world", kind: "sim", name: "Front desk", sub: "Dana, Luis, Kim, Pat",
    job: "Four simulated receptionists with real-world habits: Kim stops updating when swamped, Luis batches updates and guesses from headcount, Pat guesses erratically, Dana is accurate.",
    autonomy: "Acts freely inside the simulation", built: "Two ways: written rules, or an AI persona prompt (Sonnet through Claude Code)",
    evals: "Persona fidelity: compares what each one did with the habits its persona describes",
    today: "Built. Rule-based on every test day; AI personas in the recorded demo. See Test actors for prompts and results." },
  patients: { lane: "world", kind: "sim", name: "Patients", sub: "7 personas + walk-ins",
    job: "Simulated patients who arrive early, late or not at all, text questions, write in Spanish, or mention urgent symptoms.",
    autonomy: "Acts freely inside the simulation", built: "Two ways: written rules, or an AI persona prompt for the 7 named patients",
    evals: "Persona fidelity, and each persona is also a named test moment (Tom, Rosa, Mr. Chen…)",
    today: "Built. The walk-in crowd stays rule-based even in the demo." },
  store: { lane: "product", kind: "rule", name: "Event log", sub: "single source of truth",
    job: "Every check-in, status change, estimate, message and decision is written here with a timestamp. The screens and the checks all read from it.",
    autonomy: "Not applicable", built: "Plain code (SQLite)", evals: "Results totals must match it exactly", today: "Built." },
  quality: { lane: "product", kind: "ai", soon: true, name: "Data-quality agent", sub: "flags only",
    job: "Spots stale or impossible entries, such as a patient roomed before checking in, or no update for a long time, and flags them for the desk.",
    autonomy: "Flags only. Cannot change any record.", built: "AI agent", evals: "Catches 90%+ of planted bad entries with few false alarms",
    today: "Not built yet. Today the same flags come from fixed rules, and they only look at how old the data is, not whether it’s complete. The AI version arrives in Milestone 5." },
  estimator: { lane: "product", kind: "rule", name: "Estimator", sub: "range, never one number",
    job: "Works out a wait range from recent real waits, people ahead, booked visits, who is actually working, and “will run long” taps.",
    autonomy: "Not applicable: deterministic", built: "Plain code, no machine learning",
    evals: "Range covers the real wait 80%+ of the time; ranges stay narrow enough to be useful; rarely too short", today: "Built." },
  audit: { lane: "product", kind: "ai", soon: true, name: "Audit agent", sub: "widens ranges",
    job: "Compares posted ranges with what actually happened and widens them when they have been too optimistic.",
    autonomy: "Can widen ranges by itself. Cannot narrow them.", built: "AI agent plus code", evals: "Its one-line reasons must match what happened",
    today: "Not built yet. Arrives in Milestone 5." },
  safety: { lane: "product", kind: "rule", name: "Safety rules", sub: "last stop before patients",
    job: "Holds back any time when data is over 15 minutes old or confidence is low, adds the emergency line and “updated X min ago”, and blocks names and medical words.",
    autonomy: "Not applicable: fixed rules", built: "Plain code",
    evals: "Safety promises: fresh data only, no names or medical words, and the emergency line on every view. One miss fails the whole day", today: "Built." },
  urgent: { lane: "product", kind: "rule", name: "Urgent-word check", sub: "fixed emergency reply",
    job: "Looks for urgent symptoms in every text. If found, it sends a reply written and approved in advance. No AI can write or change it.",
    autonomy: "Not applicable: fixed text", built: "Plain code", evals: "Fixed reply within one message, no wait time", today: "Built." },
  textagent: { lane: "product", kind: "ai", soon: true, name: "Patient text agent", sub: "answers “how long?”",
    job: "Answers patients’ texts from the current estimate, in their language, in plain words. It can only use numbers the safety rules allow.",
    autonomy: "Replies on its own, inside the safety rules", built: "AI agent",
    evals: "Replies in the patient’s language at a simple reading level; no names or medical words",
    today: "Not built yet. Today replies come from fixed message templates. The AI version arrives in Milestone 5." },
  page: { lane: "product", kind: "out", name: "Patient page and texts", sub: "what patients see",
    job: "A range like “about 25–50 min”, when it was updated, and how to get emergency help. Or, when the data can’t be trusted: “please call the clinic.”",
    autonomy: "Not applicable", built: "Web page and simulated texts (no real SMS)", evals: "Every view checked against all five safety promises", today: "Built." },
  exception: { lane: "side", kind: "ai", soon: true, name: "Exception agent", sub: "drafts suggestions",
    job: "Drafts suggestions for late arrivals, missed notices and rescheduling, with a one-line reason.",
    autonomy: "Suggests only. Staff must approve.", built: "AI agent", evals: "Nothing moves without a logged approval; reasons match what happened",
    today: "Not built yet. Today a scripted stand-in drafts the suggestions. The AI version arrives in Milestone 5." },
  approve: { lane: "side", kind: "staff", name: "Staff approves", sub: "with a one-line reason",
    job: "The front desk, or you running the demo, approves or rejects each suggestion and types why.",
    autonomy: "People decide", built: "Staff console", evals: "Every change to the line must have a logged approval",
    today: "Built. In step 2, set “Approvals by” to “me”, then run a simulation." },
  queue: { lane: "side", kind: "rule", name: "Line updated", sub: "only after approval",
    job: "The order of the line changes only after an approval is logged. Other patients’ ranges update on the next refresh.",
    autonomy: "Not applicable", built: "Plain code", evals: "Nothing moves without a logged approval", today: "Built." },
};
const KIND_LABEL = { sim: "Simulated person (test actor)", ai: "Product AI agent", rule: "Fixed rule (plain code)", staff: "Staff decision", out: "What patients see" };
function renderFlow() {
  const nodes = (ids) => `<div class="nodes">${ids.map((id, j) => { const n = NODES[id];
    return `<button type="button" class="node kind-${n.kind}${n.soon ? " soon" : ""}${ui.flowSel === id ? " on" : ""}" data-node="${id}" aria-pressed="${ui.flowSel === id}"><b>${esc(n.name)}</b><span>${esc(n.sub)}</span></button>${j < ids.length - 1 ? ICON.arrow : ""}`; }).join("")}</div>`;
  $("flow-main").innerHTML = `
    <section class="lane world" aria-label="The simulated world"><div><div class="lane-title">The simulated world</div><div class="lane-sub">Test actors standing in for real people. They make mistakes on purpose and are not graded on the product’s goals.</div></div>${nodes(["director", "desk", "patients"])}</section>
    <div class="wall">${ICON.lock}<div><strong>A wall between the two.</strong> Simulated people and product agents never share a prompt or an AI call, so the demo is not grading its own homework. The only thing that crosses is what the front desk records.</div></div>
    <section class="lane product" aria-label="The product"><div><div class="lane-title">The product</div><div class="lane-sub">Every step is logged, so every check can be traced to a minute.</div></div>${nodes(["store", "quality", "estimator", "audit", "safety", "urgent", "textagent", "page"])}
      <div class="side"><div class="lane-sub" style="font-weight:700">Side path: anything that would affect other patients</div>${nodes(["exception", "approve", "queue"])}</div></section>
    <section class="checker" aria-label="Checking">${ICON.shield}<div><div class="t">Checked from the outside</div><div class="small">A separate checker replays 7 kinds of day, 20 times each, and grades the logs against what really happened. It never sees the product’s reasoning, only the record.</div></div><a href="#promises" data-view-link="promises" class="btn small" style="text-decoration:none">See promises and goals</a></section>`;
  $("flow-main").querySelectorAll("[data-node]").forEach((b) => (b.onclick = () => { ui.flowSel = b.dataset.node; renderFlow(); }));
  $("flow-main").querySelectorAll("[data-view-link]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); showView(a.dataset.viewLink); }));
  const n = NODES[ui.flowSel];
  $("flow-detail").innerHTML = `<span class="tag k-${n.soon ? "soon" : n.kind}" style="align-self:flex-start">${esc(KIND_LABEL[n.kind])}${n.soon ? " · not built yet" : ""}</span>
    <h2>${esc(n.name)}</h2><p style="margin:0">${esc(n.job)}</p>
    <dl><div><dt>May do on its own</dt><dd>${esc(n.autonomy)}</dd></div><div><dt>Built as</dt><dd>${esc(n.built)}</dd></div><div><dt>Guarded by</dt><dd>${esc(n.evals)}</dd></div></dl>
    <div class="today">${esc(n.today)}</div>`;
}

// ---------------------------------------------------------------- promises and goals
function parseSeeds(score) { const m = /(\d+)\/(\d+) seeds/.exec(score || ""); return m ? [+m[1], +m[2]] : null; }
function fmtVal(v, unit) { return v === null || v === undefined ? "–" : unit === "pct" ? Math.round(v * 100) + "%" : unit === "min" ? Math.round(v) + " min" : String(v); }
async function renderPromises() {
  $("toggle-ids").textContent = ui.showIds ? "Hide test IDs" : "Show test IDs";
  $("toggle-ids").setAttribute("aria-pressed", ui.showIds);
  const s = (await suites())[0];
  if (!s) { $("promises").innerHTML = `<section class="card"><p class="muted">No saved test run yet. Run <code>python3 -m evals</code> to test every kind of day.</p></section>`; return; }
  const days = new Set(s.checks.map((c) => c.scenario)).size;
  $("prom-lede").textContent = `Every change is tested on ${days * s.seeds} simulated days: ${days} kinds of day, ${s.seeds} versions of each. Safety promises must hold on every single day. Accuracy goals are targets we report honestly, including the ones not met yet.`;
  const id = (x) => (ui.showIds ? `<span class="idtag">${esc(x)}</span>` : "");
  const gates = Object.keys(GATE_TEXT).map((g) => {
    const rows = s.checks.filter((c) => c.id === g);
    let kept = 0, total = 0; rows.forEach((r) => { const p = parseSeeds(r.score); if (p) { kept += p[0]; total += p[1]; } });
    const ok = rows.every((r) => r.passed);
    return { g, ok, kept, total };
  });
  const allOk = gates.every((x) => x.ok);
  const gatesHtml = `<section class="card" aria-label="Safety promises">
    <div class="row-between"><div style="display:flex;gap:12px;align-items:center"><span style="color:var(--good-ink)">${ICON.shield}</span><div><div class="h2" style="margin:0">Safety promises</div><div class="small muted">One miss on any day fails the whole build</div></div></div>
    <span class="tag wrap ${allOk ? "k-ai" : ""}" style="${allOk ? "" : "background:var(--crit-soft);color:var(--crit-ink)"};font-size:14px;padding:8px 14px">${gates.filter((x) => x.ok).length} of ${gates.length} kept on every day, including held-back days</span></div>
    <div class="gates">${gates.map((x) => `<div class="gate${x.ok ? "" : " bad"}"><div class="row-between" style="align-items:center"><span class="mark">${x.ok ? ICON.check : ICON.bang}</span>${id(x.g)}</div>
      <div class="t">${esc(GATE_TEXT[x.g])}</div><div class="s mono">Kept on ${x.kept} of ${x.total} days</div></div>`).join("")}</div></section>`;

  // One row per goal for the kinds of day that met it (showing the weakest), and its own row for each day that missed.
  const raw = s.checks.filter((c) => GOALS[c.id] && c.group === (ui.goalSet === "tuning" ? "tuning" : "hold-out"));
  const rows = [];
  for (const gid of Object.keys(GOALS)) {
    const mine = raw.filter((c) => c.id === gid);
    const met = mine.filter((c) => c.passed === true);
    if (met.length) {
      const worst = met.reduce((a, b) => (GOALS[gid].dir === "down" ? (b.value > a.value ? b : a) : (b.value < a.value ? b : a)));
      rows.push({ ...worst, days: met.map((c) => scName(c.scenario)), merged: met.length > 1 });
    }
    mine.filter((c) => c.passed !== true).forEach((c) => rows.push({ ...c, days: [scName(c.scenario)], merged: false }));
  }
  const goalRow = (c) => {
    const g = GOALS[c.id];
    const status = c.passed === null ? ["Reported only", "neutral"] : c.passed ? ["Met", "ok"] : ["Not met yet", "bad"];
    let bar = "";
    if (g.dir !== "exact" && typeof c.value === "number") {
      const max = g.dir === "down" ? Math.max(g.unit === "pct" ? 0.3 : 30, c.value * 1.5, g.goal * 2) : 1;
      const w = Math.min(100, (c.value / max) * 100), gp = Math.min(100, (g.goal / max) * 100);
      bar = `<div class="bar"><div class="track"><div class="fill${c.passed === false ? " bad" : ""}" style="width:${w}%"></div><div class="tick" style="left:calc(${gp}% - 1px)" aria-hidden="true"></div></div>
        <div class="bar-lbl mono"><span>${c.merged ? (g.dir === "down" ? "highest " : "lowest ") : ""}${fmtVal(c.value, g.unit)}</span><span>goal ${g.dir === "down" ? "at most " : ""}${fmtVal(g.goal, g.unit)}</span></div></div>`;
    } else {
      bar = `<div class="bar"><div class="track"><div class="fill${c.passed === false ? " bad" : ""}" style="width:${Math.round((c.value ?? 0) * 100)}%"></div></div><div class="bar-lbl mono"><span>${c.merged ? "every version of each day" : esc(c.score)}</span><span>goal: every day</span></div></div>`;
    }
    return `<div class="goal"><div class="what"><div class="t">${esc(g.t)}</div><div class="s">${esc(c.days.length > 3 ? `${c.days.length} kinds of day` : c.days.join(", "))}${ui.showIds ? ` · <b>${esc(c.id)}</b>` : ""}</div></div>${bar}
      <span class="pill ${status[1] === "ok" ? "ok" : status[1] === "bad" ? "bad" : ""}">${status[1] === "ok" ? "✓ " : status[1] === "bad" ? "! " : ""}${status[0]}</span></div>`;
  };
  const goalsHtml = `<section class="card" aria-label="Accuracy goals" style="display:flex;flex-direction:column;gap:16px">
    <div class="row-between" style="align-items:center"><div style="display:flex;gap:12px;align-items:center">${ICON.target}<div><div class="h2" style="margin:0">Accuracy goals</div><div class="small muted">Scored across ${s.seeds} versions of each day. The line on each bar is the goal.</div></div></div>
      <div class="seg-tabs" role="tablist" aria-label="Which days"><button role="tab" data-gset="tuning" class="${ui.goalSet === "tuning" ? "on" : ""}">Days we tuned on</button><button role="tab" data-gset="hold" class="${ui.goalSet === "hold" ? "on" : ""}">Days held back</button></div></div>
    ${ui.goalSet === "hold" ? `<div class="callout info">${ICON.info}<div>Held-back days are never used to tune the product, so they show how it copes with something new. One of them (understaffed opening) was used for a fix after Milestone 2, so it is no longer a clean test and will be replaced.</div></div>` : ""}
    <div class="goals">${rows.map(goalRow).join("")}</div>
    <div class="small muted">Not built yet: catching planted bad entries, Spanish replies at a simple reading level, agents’ reasons matching what happened, and survey answers tracking what patients were told. These arrive with the product AI agents in Milestone 5.</div></section>`;
  $("promises").innerHTML = gatesHtml + goalsHtml + `<p class="small muted">From test run ${esc(s.suite_id)}. Every failure is written up in FAILURE_LOG.md with its cause and fix, including bugs found in the checks themselves.</p>`;
  $("promises").querySelectorAll("[data-gset]").forEach((b) => (b.onclick = () => { ui.goalSet = b.dataset.gset; renderPromises(); }));
}

// ---------------------------------------------------------------- run inspector
function goTo(ref) {
  if (!ref) return;
  if (ui.last?.control?.running) control({ action: "pause" });
  if (ref.t !== null && ref.t !== undefined) { ui.at = ref.t; ui.focusT = ref.t; }
  if (ref.pid) { ui.selected = ref.pid; if (ui.last?.phone?.choices.some((c) => c.pid === ref.pid)) ui.pid = ref.pid; }
  ui.detailKey = "";
  if (ui.view !== "inspect") showView("inspect");
  poll(true);
  $("timeline").scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function renderScore(rows, c) {
  $("score-note").textContent = c.done ? "(final)" : "(so far; some checks are judged only at the end)";
  const statusClass = (s) => (s.startsWith("so far: ") ? s.slice(8) : s);
  window._refs = [];
  $("scorecard").innerHTML = `<div class="scrollx"><table class="score"><tr><th>Check</th><th>Score</th><th>Threshold</th><th>Result</th></tr>${rows.map((r) => {
    const ev = r.passed === false && r.evidence.length ? `<tr><td></td><td colspan="3"><ul class="evid">${r.evidence.slice(0, 6).map((e, i) => {
      const ref = r.refs[i]; const n = window._refs.push(ref) - 1;
      return `<li>${ref && (ref.t !== null || ref.pid) ? `<a data-ref="${n}">${esc(e)}</a>` : esc(e)}</li>`; }).join("")}
      ${r.evidence.length > 6 ? `<li class="muted">and ${r.evidence.length - 6} more</li>` : ""}</ul></td></tr>` : "";
    return `<tr><td><b>${esc(r.id)}</b><div class="small muted">${esc(checkName(r.id))}</div></td><td>${esc(r.score)}<div class="muted small">${esc(r.detail)}</div></td><td>${esc(r.threshold)}</td>
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
  const box = $("timeline"); const W = Math.max(560, box.clientWidth - 2), LBL = 92, RH = 18, H = 22 + lanes.length * RH + 4;
  const x = (t) => LBL + ((t - t0) / (t1 - t0)) * (W - LBL - 10);
  let s = `<svg width="${W}" height="${H}" role="img" aria-label="Per-patient timeline"><g class="axis">`;
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
  s += `<line x1="${x(now)}" x2="${x(now)}" y1="16" y2="${H}" stroke="var(--ink)" stroke-width="1.5"/></svg>`;
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
async function loadSuites() {
  try { ui.suites = await api("/api/suites"); } catch (e) { $("suite").innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const s = ui.suites[0];
  if (!s) { $("suite").innerHTML = `<p class="muted">No saved test run yet. Run <code>python3 -m evals</code>.</p>`; return; }
  window._srefs = [];
  const rows = s.checks.map((c) => {
    const fails = c.passed === false ? `<ul class="evid">${c.evidence.slice(0, 5).map((e, i) => {
      const r = c.refs[i] || {}; const n = window._srefs.push({ ...r, scenario: c.scenario }) - 1;
      return `<li>${r.kept ? `<a data-sref="${n}">${esc(e)}</a>` : `<a class="dead" title="This seed's run file was pruned">${esc(e)}</a>`}</li>`; }).join("")}</ul>`
      + (c.failing_kept.length ? `<div class="small">Open a failing seed: ${c.failing_kept.map((sd) => { const n = window._srefs.push({ seed: sd, scenario: c.scenario, t: null, pid: null, kept: true }) - 1; return `<a class="linkish" data-sref="${n}">seed ${sd}</a>`; }).join(" ")}</div>` : "") : "";
    return `<tr><td><b>${esc(c.id)}</b><div class="small muted">${esc(checkName(c.id))}</div></td><td>${esc(scName(c.scenario))}${c.group === "hold-out" ? ` <span class="pill">held back</span>` : ""}</td><td>${esc(c.score)}${fails}</td><td class="status ${c.passed === null ? "info" : c.passed ? "pass" : "fail"}">${c.passed === null ? "info" : c.passed ? "pass" : "fail"}</td></tr>`;
  }).join("");
  $("suite").innerHTML = `<p>Test run ${esc(s.suite_id)} · ${s.seeds} versions of each kind of day. Click a failure to open that day’s saved run at that minute.</p>
    <div class="scrollx"><table class="score"><tr><th>Check</th><th>Day</th><th>Score</th><th>Result</th></tr>${rows}</table></div>`;
  $("suite").querySelectorAll("[data-sref]").forEach((a) => (a.onclick = async () => {
    const r = window._srefs[+a.dataset.sref];
    ui.selected = null; ui.focusT = null;   // a different run: old patient ids mean other people
    $("detail").innerHTML = ""; $("detail-title").textContent = "Patient log";
    await control({ action: "open_saved", suite: s.suite_id, scenario: r.scenario, seed: r.seed });
    showTab("live"); goTo({ t: r.t ?? null, pid: r.pid ?? null });
  }));
}

// ---------------------------------------------------------------- results
function tile(k, v, sub) { return `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${v}</div><div class="s">${sub}</div></div>`; }
function renderFinance(f, score, c) {
  if (!f) { $("finance").innerHTML = `<p class="muted">Start a day.</p>`; return; }
  const d = f.derived, n = f.counts;
  const sv = (x) => (x === null ? `<span class="muted">hidden</span>` : x.toFixed(2));
  const grp = d.satisfied_by_group;
  const e10 = score.find((r) => r.id === "E10");
  const ai = n.model_calls > 0;
  $("finance").innerHTML = `<div class="tiles">
    ${tile("Walk-ins who left before being seen", `${pct(d.walkaway_rate)} <span class="muted">vs ${pct(d.baseline_walkaway_rate)} without</span>`, `${n.walkaways} of ${n.walkins} with the product; ${n.baseline_walkaways} of ${n.baseline_walkins} without (same people, same day) · partly built in, see note below`)}
    ${tile("Satisfaction: inside the range vs past it", `${sv(grp.inside.mean)} <span class="muted">vs ${grp.over.mean === null ? "hidden" : grp.over.mean.toFixed(2)}</span>`, `1–5 scale; inside (${grp.inside.n}) vs waited 15+ min past it (${grp.over.n}); groups under ${d.min_group} hidden`)}
    ${tile("Running cost for this day, modeled", "$" + d.cost_per_day_modeled.toFixed(2), `${n.messages} simulated texts${ai ? `; ${n.model_calls} AI test-actor calls billed $${n.model_usd.toFixed(2)} (subscription), $${(n.model_usd_modeled ?? 0).toFixed(2)} at API prices` : "; no AI calls"}`)}
    ${tile("Numbers match the logs", e10 ? esc((/(\d+)\/(\d+)/.exec(e10.score) || []).slice(1).join(" of ") || e10.score) : "–", e10 && e10.passed === false ? `<span class="err">${esc(e10.evidence.slice(0, 2).join("; "))}</span>` : "every figure here is recounted from the raw records")}
    ${tile("Patients seen", n.seen, `${n.checked_in} checked in; room use ${pct(d.room_utilization)}`)}
    ${tile("Range covered the real wait", pct(d.coverage), `${n.cov_num} of ${n.cov_den} waits inside the range`)}
    ${tile("Waited well past the range", pct(d.false_reassurance), `${n.fr_num} of ${n.fr_den} walk-ins waited 15+ min past the top`)}
    ${tile("Page said “call the clinic”", pct(d.decline_rate), `${n.declined_open} of ${n.views_open} open minutes`)}
    ${tile("“How long?” at the desk", n.desk_questions, `staff time ${d.staff_minutes_per_run_modeled} min, modeled (${n.presses} taps, ${n.approvals} approvals)`)}
    ${tile("No-shows / late", `${pct(d.noshow_rate)} / ${pct(d.late_rate)}`, `${n.noshows} of ${n.booked} booked; ${n.late} of ${n.sched_checked_in} late`)}
    ${tile("Survey: accurate / satisfied / reuse", `${sv(d.survey_accurate)} / ${sv(d.survey_satisfied)} / ${sv(d.survey_reuse)}`, `1–5 scale, ${d.survey_n} answers${c.source.demo ? "; named patients answered as AI agents" : ""}`)}
  </div>`;
}
async function renderResultsSuite() {
  const s = (await suites()).find((x) => x.finance);
  if (!s) { $("results-suite").innerHTML = ""; return; }
  const sc = Object.keys(s.finance);
  const W = sc.map((k) => [k, s.finance[k].derived]);
  const bar = (v, color) => `<i style="width:calc(${Math.min(100, v * 100)}% * 0.82);background:${color}"></i>`;
  const costs = W.map(([, d]) => d.cost_per_day_modeled);
  $("results-suite").innerHTML = `<h2 class="h2">Across every kind of day <span class="small muted" style="font-weight:600">(test run ${esc(s.suite_id)}, ${s.seeds} versions each)</span></h2>
    <div class="results-grid">
    <section class="card"><div><div class="h3" style="margin:0">Walk-ins who left before being seen</div><div class="small muted">Same day, same patients, with and without the product</div></div>
      <div class="legend"><span><i style="background:var(--accent)"></i>With the product</span><span><i style="background:var(--without)"></i>Without</span></div>
      <div class="wbars">${W.map(([k, d]) => `<div class="wbar"><div class="d">${esc(scName(k))}</div>
        <div class="b" title="With the product">${bar(d.walkaway_rate, "var(--accent)")}<b class="mono">${pct(d.walkaway_rate)}</b></div>
        <div class="b" title="Without the product">${bar(d.baseline_walkaway_rate, "var(--without)")}<span class="mono">${pct(d.baseline_walkaway_rate)}</span></div></div>`).join("")}</div>
      <div class="callout">${ICON.info}<div><strong>Built into the simulation.</strong> A simulated walk-in who sees a range waits until about 20 minutes past its top; one with no information leaves after a fixed time. Most of this gap comes from that rule, not from anything measured.</div></div></section>
    <section class="card"><div><div class="h3" style="margin:0">How satisfied with the wait?</div><div class="small muted">Average of 1–5, simulated survey after each visit</div></div>
      <div class="scrollx"><table><tr><th>Day</th><th class="num">Inside the range</th><th class="num">Waited past it</th></tr>${W.map(([k, d]) => { const g = d.satisfied_by_group;
        const f = (x) => (x.mean === null || x.n < d.min_group ? `<span class="muted">hidden</span>` : x.mean.toFixed(1));
        return `<tr><td><b>${esc(scName(k))}</b></td><td class="num" style="color:var(--good-ink);font-weight:800">${f(g.inside)}</td><td class="num" style="color:var(--crit-ink);font-weight:800">${f(g.over)}</td></tr>`; }).join("")}</table></div>
      <div class="small muted">“Hidden” means fewer than ${W[0][1].min_group} answers, so the group is not shown.</div>
      <div class="callout">${ICON.info}<div><strong>Also built in.</strong> On rule-based days, survey answers come from a formula plus random noise, so “a wrong promise lowers satisfaction” is true by design. Real surveys would test it.</div></div>
      <div class="tile" style="background:var(--surface-2);border:none"><div class="k">Running cost per simulated day, modeled</div><div class="v mono" style="font-size:24px">$${Math.min(...costs).toFixed(2)}–${Math.max(...costs).toFixed(2)}</div><div class="s">simulated texts at a modeled rate; the rule-based test days make no AI calls</div></div></section>
    </div>`;
}
function renderTrend(c) {
  const sc = c.source.scenario;
  const rows = (ui.suites || []).filter((s) => s.finance && s.finance[sc]).slice(0, 8);
  $("trend-note").textContent = `(${scName(sc)}, pooled over each test run’s versions of the day)`;
  $("trend").innerHTML = rows.length ? `<div class="scrollx"><table><tr><th>Test run</th><th class="num">Seen / day</th><th class="num">Range covered</th><th class="num">Waited past range</th><th class="num">Said “call”</th><th class="num">Left unseen with / without</th><th class="num">Satisfied (1–5)</th><th class="num">Cost / day, modeled</th></tr>${rows.map((s) => {
    const d = s.finance[sc].derived;
    return `<tr><td>${esc(s.suite_id)}</td><td class="num">${d.seen_per_run}</td><td class="num">${pct(d.coverage)}</td><td class="num">${pct(d.false_reassurance)}</td><td class="num">${pct(d.decline_rate)}</td><td class="num">${pct(d.walkaway_rate)} / ${pct(d.baseline_walkaway_rate)}</td><td class="num">${d.survey_satisfied ?? "hidden"}</td><td class="num">$${d.cost_per_day_modeled.toFixed(2)}</td></tr>`; }).join("")}</table></div>`
    : `<p class="muted small">No saved test run has numbers for this kind of day yet. Run <code>python3 -m evals</code>.</p>`;
}

// ---------------------------------------------------------------- test actors
ui.agentOpen = null;
let agentsTimer = null;
async function loadAgents() {
  if (ui.view !== "actors") return;
  let a;
  try { a = await api("/api/agents"); } catch (e) { $("agents").innerHTML = `<p class="err">${esc(e.message)}</p>`; return; }
  const pill = (p) => {
    if (!p.in_run) return `<span class="pill">not in this day</span>`;
    const f = p.fidelity;
    if (!f) return `<span class="pill">no data yet</span>`;
    // Habits like "goes quiet once or twice a day" can only be judged when the day is over.
    if (!ui.last?.control?.done && f.status !== "untested") return `<span class="pill warn" title="Judged when the day is over">so far: ${f.status === "pass" ? "on track" : "not seen yet"}</span>`;
    return `<span class="pill ${f.status === "pass" ? "ok" : f.status === "fail" ? "bad" : ""}">${f.status === "pass" ? "✓ behaves as written" : f.status === "fail" ? "✕ drifted" : "untested"}</span>`;
  };
  const card = (p) => `<button class="agent ${ui.agentOpen === p.id ? "open" : ""}" data-agent="${esc(p.id)}">
      <span class="agent-top"><strong>${esc(p.title)}</strong>${pill(p)}</span>
      <span class="small muted">${p.fidelity ? esc(p.fidelity.metric) + (p.fidelity.target ? ` · target ${esc(p.fidelity.target)}` : "") : p.in_run ? "" : "Not on duty in this kind of day."}</span>
      ${a.llm && p.in_run ? `<span class="small">${p.calls.n} AI call(s), ${p.calls.ok} valid</span>` : ""}</button>`;
  const open = a.personas.find((p) => p.id === ui.agentOpen);
  const detail = open ? `<section class="card agent-detail">
      <h4>${esc(open.title)} <span class="tag-actor">Simulated person (test actor)</span></h4>
      <p class="small muted">Prompt file: prompts/actors/${esc(open.id)}.md, plus the shared rules. Edit it and record a new AI day to change how this persona behaves.</p>
      <pre class="prompt">${esc(open.prompt)}</pre>
      ${open.recent.length ? `<h4>Latest decisions in this day</h4>${open.recent.map((c) => `<details><summary>${esc(c.time)} · ${esc(c.role)} ${c.ok ? "" : `<span class="pill bad">fell back: ${esc(c.error || "invalid")}</span>`}</summary>
        <p class="small muted">What it saw</p><pre class="prompt">${esc(c.prompt)}</pre><p class="small muted">What it decided</p><pre class="prompt">${esc(c.reply || "")}</pre></details>`).join("")}`
        : `<p class="small muted">${a.llm ? "No decisions yet at this minute." : "This day uses rule-based people, so there are no AI decisions to show. Press “Watch the demo” (step 1) to see the AI agents."}</p>`}
    </section>` : "";
  $("agents").innerHTML = `<section class="card"><p style="margin:0"><strong>Who plays the people in this day:</strong> ${esc(a.mode)}${a.llm ? ` · model: ${esc(a.model)} through Claude Code on a subscription ($0 billed)` : ""}.</p>
    <p class="small muted" style="margin:6px 0 0">The product (estimator, safety rules, texts) is plain code and never shares a prompt or an AI call with the test actors. Click a persona to read its prompt and what it decided.</p></section>
    <div class="agents-grid">${a.personas.map(card).join("")}</div>${detail}
    <details class="card"><summary class="small" style="font-weight:600">Shared rules every test actor follows</summary><pre class="prompt">${esc(a.rules)}</pre></details>`;
  $("agents").querySelectorAll("[data-agent]").forEach((b) => (b.onclick = () => { ui.agentOpen = ui.agentOpen === b.dataset.agent ? null : b.dataset.agent; loadAgents(); }));
  clearTimeout(agentsTimer);
  if (ui.last?.control?.running) agentsTimer = setTimeout(loadAgents, 3000);
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
    await suites();
    renderControl(s);
    renderPhone(s.phone);
    renderStaff(s.staff, s.control);
    renderMoment(s);
    renderScore(s.scorecard, s.control);
    if (ui.view === "inspect") renderTimeline(s.observer);
    renderDecisions(s.observer);
    renderFinance(s.finance, s.scorecard, s.control);
    renderDetail(s.control);
    if (ui.view === "results") { renderResultsSuite(); renderTrend(s.control); }
    $("err").textContent = $("err").dataset.sticky ? "" : $("err").textContent; delete $("err").dataset.sticky;
  } catch (e) { $("err").textContent = e.message; $("err").dataset.sticky = "1"; }
  finally {
    polling = false;
    clearTimeout(timer);
    timer = setTimeout(poll, ui.last?.control?.running ? 300 : 1500);
  }
}
renderLegend();
showView(location.hash.slice(1) || "clinic", false);
poll(true);
