// Optional browser check (needs Google Chrome and Node 22+; no packages).
// Start the server first (python3 -m web), then:
//   node web/browsercheck.mjs <empty temp dir for a Chrome profile> <screenshot.png>
// Clicks through the real page: run controls, an approval card, "will run long",
// a scorecard failure link and the suite tab.
import { spawn } from "node:child_process";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333, APP = process.env.APP_URL || "http://127.0.0.1:8000/";
const proc = spawn(CHROME, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`, "--window-size=1600,1400",
  "--user-data-dir=" + process.argv[2], "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let target;
for (let i = 0; i < 50 && !target; i++) {
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"); } catch { await sleep(200); }
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = (m) => { const d = JSON.parse(m.data); if (pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async (expr) => { const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails)); return r.result.result.value; };
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  (" + detail + ")" : ""}`); };
const errors = [];
await send("Runtime.enable");
ws.addEventListener("message", (m) => { const d = JSON.parse(m.data); if (d.method === "Runtime.exceptionThrown") errors.push(d.params.exceptionDetails.text); });
await send("Page.navigate", { url: APP });
await sleep(1500);

// Start a human-approval run of normal_day from the control bar
await js(`document.getElementById("scenario").value="normal_day"; document.getElementById("seed").value="42"; document.getElementById("approver").value="human"; document.getElementById("start").click(); true`);
await sleep(1500);
// Loaded but not started: the panes must render without an error (the error line catches what poll() swallows)
const startErr = await js(`document.getElementById("err").textContent`);
check("Start button loads a run, with no error before the day starts", (await js(`document.getElementById("source").textContent`)).includes("Normal day (patient mix 42)") && (await js(`document.getElementById("source").textContent`)).includes("you approve changes") && !startErr, startErr || "");
// Step until a proposal card appears
let cards = 0;
for (let i = 0; i < 80 && !cards; i++) {
  await js(`document.getElementById("step").click(); true`); await sleep(120);
  cards = await js(`document.querySelectorAll("#inbox .proposal input").length`);
}
check("an approval card with a reason box appears", cards > 0, `${cards} card(s) at ${await js(`document.getElementById("clock").textContent`)}`);
await js(`(() => { const b = document.querySelector("#inbox .proposal"); b.querySelector("input").value = "Late arrival; next opening is free."; b.querySelector('button[data-d="approved"]').click(); return true; })()`);
await sleep(400);
await js(`document.getElementById("step").click(); true`); await sleep(1200);
const decided = await js(`document.getElementById("decided").textContent`);
check("clicking Approve logs a human decision", decided.includes("human") && decided.includes("approved"), decided.slice(0, 90));
// Will run long button
const rl = await js(`(() => { const b = document.querySelector("#inroom [data-rl]"); if (!b) return null; b.click(); return b.dataset.rl; })()`);
await sleep(400); await js(`document.getElementById("step").click(); true`); await sleep(1200);
check("'Will run long' marks the patient", rl === null || (await js(`document.getElementById("inroom").textContent`)).includes("running long"), rl ?? "nobody in a room");
// Speed + play + pause
await js(`document.querySelector('#speeds [data-speed="60x"]').click(); true`); await sleep(300);
const t1 = await js(`document.getElementById("clock").textContent`);
await js(`document.getElementById("play").click(); true`); await sleep(2000);
await js(`document.getElementById("play").click(); true`); await sleep(500);
const t2 = await js(`document.getElementById("clock").textContent`);
check("Play at 60x moves the clock, Pause stops it", t1 !== t2, `${t1} -> ${t2}`);
// To end, then click a scorecard failure link
await js(`document.getElementById("toend").click(); true`); await sleep(2500);
const links = await js(`document.querySelectorAll("#scorecard [data-ref]").length`);
check("final scorecard shows clickable failures", links > 0, `${links} links`);
const linkText = await js(`(() => { const a = document.querySelector("#scorecard [data-ref]"); a.click(); return a.textContent; })()`);
await sleep(2000);
const title = await js(`document.getElementById("detail-title").textContent`);
const focus = await js(`(document.querySelector("#detail .e.focus") || {}).textContent || ""`);
const clock = await js(`document.getElementById("clock").textContent`);
check("clicking a failure opens that patient's log at that minute", title.startsWith("Patient log:") && focus.length > 0 && clock.includes("looking back"),
  `${linkText.slice(0, 50)} -> ${title} | ${focus.slice(0, 60)} | ${clock}`);
const phone = await js(`document.getElementById("page").textContent`);
check("phone shows the page at that minute", phone.includes("Updated"), phone.slice(0, 60));
// Suite tab -> open a failing saved seed
await js(`document.querySelector('[data-tab="suite"]').click(); true`); await sleep(1500);
const srefs = await js(`document.querySelectorAll("#suite [data-sref]").length`);
const st = await js(`(() => { const a = document.querySelector("#suite [data-sref]"); if (!a) return null; a.click(); return a.textContent; })()`);
await sleep(2500);
const src = await js(`document.getElementById("source").textContent`);
check("suite tab: a failure opens the saved run", srefs > 0 && src.startsWith("Saved test day"), `${st?.slice(0, 50)} -> ${src}`);
const shotTitle = await js(`document.getElementById("detail-title").textContent`);
console.log("   after suite click, patient log:", shotTitle);
const shot = await send("Page.captureScreenshot", { format: "png" });
(await import("node:fs")).writeFileSync(process.argv[3], Buffer.from(shot.result.data, "base64"));
// Evals screen: a goal opens its eval card, and the card's example opens that moment in the inspector
await js(`location.hash = "#evals"; true`); await sleep(1500);
await js(`(() => { const b = [...document.querySelectorAll("#promises .goal")].find((x) => x.querySelector(".pill.bad")); b && b.click(); return true; })()`); await sleep(800);
const card = await js(`document.querySelector(".evcard")?.innerText || ""`);
await js(`document.querySelector(".evcard [data-open]")?.click(); true`); await sleep(2500);
const landed = await js(`ui.view === "inspect" && document.getElementById("source").textContent.startsWith("Saved test day")`);
check("Evals: a goal opens its card, and its example opens that moment in the inspector", card.includes("How it’s graded") && landed, card.slice(0, 60).replace(/\n/g, " "));
check("no JavaScript errors", errors.length === 0, errors.join("; "));
console.log(`\nbrowser click test: ${results.filter(Boolean).length}/${results.length} pass`);
ws.close(); proc.kill();
process.exit(results.every(Boolean) ? 0 : 1);
