"""API-level self-test for the four panes: python3 -m web --selftest

Starts the server on a free local port, plays runs through the same HTTP
calls the page makes, and checks what each pane returns. It cannot judge
layout or readability; look at the page for that."""
import copy
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request

from clinic.clock import parse
from clinic.guardrails import blocked_term, make_blocklist
from clinic.metrics import has_wait_figure
from clinic.urgent import URGENT_TEXT
from evals.common import open_run
from evals.runner import grade
from evals.scored import e10_finance_reconciles
from .server import LABEL, STATIC, Handler, make_server, suites_summary

TABLES = ["truth", "events", "estimates", "views", "messages", "verbal_estimates", "proposals",
          "approvals", "queue_changes", "notes", "survey", "human_actions"]


class Check:
    def __init__(self):
        self.rows = []

    def __call__(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        return ok


def main(cfg):
    srv = make_server(cfg, port=0)
    LIVE = Handler.session.live   # this server's own run folder
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    ok = Check()

    def get(path):
        return json.load(urllib.request.urlopen(base + path))

    def post(body, expect=200):
        req = urllib.request.Request(base + "/api/control", json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            r = urllib.request.urlopen(req)
            code, data = r.status, json.load(r)
        except urllib.error.HTTPError as e:
            code, data = e.code, json.load(e)
        if code != expect:
            raise AssertionError(f"{body} -> {code} {data}")
        return data

    print("Clinic wait-time demo UI self-test (simulation, synthetic data)")
    html = (STATIC / "index.html").read_text()
    ok("label on every pane and the phone", html.count(LABEL) >= 6, f"{html.count(LABEL)} labels in the page")

    # --- 1. persona run, played by the ticker then stepped to the end ----------------
    sc, seed = "normal_day", cfg["run"]["seed"]
    post({"action": "start", "scenario": sc, "seed": seed, "approver": "persona"})
    post({"action": "speed", "speed": "60x"})
    post({"action": "speed", "speed": "2x"}, expect=400)
    post({"action": "play"})
    time.sleep(1.5)
    post({"action": "pause"})
    t_played = get("/api/state")["control"]["now"]
    open_t = parse(cfg["clinic"]["open"])
    ok("Play advances the clock at 60x", t_played is not None and t_played >= open_t + 20,
       f"{t_played - open_t if t_played else 0} simulated minutes in 1.5 s")
    post({"action": "decide", "proposal_id": 1, "decision": "approved", "reason": "x"}, expect=400)
    post({"action": "to_end"})
    s = get("/api/state?pid=tom")
    c = s["control"]
    ok("run plays to the end", c["done"] and c["now"] is not None, s["control"]["time"])

    # every minute of the phone, both languages
    st = open_run(str(LIVE / f"{sc}_s{seed}.sqlite"))
    names = [r["name"] for r in st.rows("SELECT name FROM patients")] + ["Mr. Chen"]
    bl = make_blocklist(names)
    bad = []
    for t in range(c["open_t"], c["now"] + 1):
        for lang in ("en", "es"):
            pg = get(f"/api/state?lang={lang}&at={t}")["phone"]["page"] if t % 15 == 0 else None
            if pg is None:
                continue
            if not pg["verbatim"]:
                bad.append(f"{t} {lang} split changed text")
            if pg["urgent"] != URGENT_TEXT[lang]:
                bad.append(f"{t} {lang} urgent line missing")
            if not pg["fresh"]:
                bad.append(f"{t} {lang} freshness missing")
            if blocked_term(pg["text"], bl):
                bad.append(f"{t} {lang} blocked term")
            if pg["status"] != "range" and has_wait_figure(pg["text"]):
                bad.append(f"{t} {lang} number while declined")
    ok("phone page: verbatim, urgent line, freshness, no names, no number when declined", not bad, "; ".join(bad[:3]) or "every 15 min, EN and ES")
    tom = s["phone"]["thread"]
    ok("Tom's thread gets the fixed urgent reply", any(m["dir"] == "out" and m["text"] == URGENT_TEXT["en"] for m in tom))
    ok("staff console: estimate, queue lists and flags present", s["staff"] is not None and "flags" in s["staff"])

    # scorecard equals the suite grader on the same run
    ui = {r["id"]: (r["passed"], r["score"]) for r in s["scorecard"]}
    ev = {r.id: (r.passed, r.score) for r in grade(str(LIVE / f"{sc}_s{seed}.sqlite"), sc, cfg, seed,
                                                     str(LIVE / f"{sc}_s{seed}_baseline.sqlite"))}
    ok("scorecard matches the eval runner exactly", ui == ev, "" if ui == ev else str(set(ui.items()) ^ set(ev.items()))[:200])
    ok("final scorecard has no 'pending' rows", all(r["status"] in ("pass", "fail", "info") for r in s["scorecard"]))

    # click-through: every failure ref points at a real patient log entry or a real minute
    dead = []
    n_refs = 0
    for r in s["scorecard"]:
        if r["passed"] is not False:
            continue
        # E10 is about the finance pane, not a patient; "not exercised" means nothing happened to link to.
        if r["id"] != "E10" and r["score"] != "not exercised" and not any(r["refs"]):
            dead.append(f"{r['id']} fails with no link")
        for i, ref in enumerate(r["refs"]):
            if not ref:
                continue
            n_refs += 1
            if not (c["open_t"] <= ref["t"] <= c["now"]):
                dead.append(f"{r['id']} ref minute {ref['t']} outside run")
            if ref.get("pid"):
                log = get(f"/api/patient?pid={ref['pid']}")
                if not any(e["t"] == ref["t"] for e in log["log"]):
                    dead.append(f"{r['id']} {ref['pid']} has no log entry at {ref['t']}")
    failing = [r["id"] for r in s["scorecard"] if r["passed"] is False]
    ok("every failure links to its timeline", not dead,
       "; ".join(dead[:3]) or f"{n_refs} links checked on failing {failing}")

    # finance reconciles, and E10 is not vacuous
    fin = s["finance"]
    base_st = open_run(str(LIVE / f"{sc}_s{seed}_baseline.sqlite"))
    r10 = e10_finance_reconciles(st, cfg, shown=fin, baseline=base_st)
    ok("E10: finance pane totals reconcile with the logs", r10.passed, r10.score)
    tampered = copy.deepcopy(fin)
    tampered["counts"]["walkaways"] += 1
    tampered["derived"]["survey_satisfied"] = 4.99
    r10b = e10_finance_reconciles(st, cfg, shown=tampered, baseline=base_st)
    ok("E10 catches a tampered finance number", r10b.passed is False, f"{len(r10b.evidence)} mismatches found")
    small = copy.deepcopy(fin)
    small["derived"]["satisfied_by_group"]["over"]["mean"] = 3.0
    r10c = e10_finance_reconciles(st, cfg, shown=small, baseline=base_st)
    ok("E10 catches a survey group under the minimum being shown",
       fin["derived"]["satisfied_by_group"]["over"]["n"] >= cfg["evals"]["survey_min_group"] or r10c.passed is False)

    # --- 2. human approvals, one-tap run long, replay ---------------------------------
    post({"action": "start", "scenario": "normal_day", "seed": seed, "approver": "human"})
    decided = 0
    pressed = False
    for _ in range(400):
        post({"action": "step"})
        s = get("/api/state")
        if s["control"]["done"]:
            break
        for item in s["staff"]["inbox"]:
            post({"action": "decide", "proposal_id": item["id"], "decision": "approved", "reason": ""}, expect=400)
            post({"action": "decide", "proposal_id": item["id"], "decision": "approved" if decided % 2 == 0 else "rejected",
                  "reason": "Late arrival; next opening is free." if decided % 2 == 0 else "Keep as a walk-in today."})
            decided += 1
        if not pressed and s["staff"]["in_room"]:
            post({"action": "run_long", "pid": s["staff"]["in_room"][0]["pid"]})
            pressed = True
    post({"action": "to_end"})
    h1 = LIVE / f"normal_day_s{seed}.sqlite"
    db = sqlite3.connect(h1)
    appr = db.execute("SELECT actor, decision, reason FROM approvals").fetchall()
    qc = db.execute("SELECT COUNT(*) FROM queue_changes").fetchone()[0]
    rl = db.execute("SELECT COUNT(*) FROM events WHERE type='run_long' AND actor='human'").fetchone()[0]
    ok("human approvals are logged with a reason; only approvals move the queue",
       decided > 0 and all(a[0] == "human" and a[2] for a in appr) and qc == sum(1 for a in appr if a[1] == "approved"),
       f"{len(appr)} decisions, {qc} queue changes")
    ok("one-tap 'will run long' is logged as a human press", rl == 1)
    s = get("/api/state")
    g3 = next(r for r in s["scorecard"] if r["id"] == "G3")
    ok("G3 passes with human approvals", g3["passed"], g3["score"])
    before = {t: db.execute(f"SELECT * FROM {t}").fetchall() for t in TABLES}
    db.close()
    post({"action": "replay"})
    post({"action": "to_end"})
    db = sqlite3.connect(h1)
    after = {t: db.execute(f"SELECT * FROM {t}").fetchall() for t in TABLES}
    db.close()
    diff = [t for t in TABLES if before[t] != after[t]]
    ok("replay with the logged staff actions is identical", not diff, ", ".join(diff))

    # --- 3. a saved suite run opens read-only, at a failure --------------------------
    suites = [x for x in suites_summary(limit=10) if x.get("finance")][:1]
    target = None
    for ch in (suites[0]["checks"] if suites else []):
        if ch.get("failing_kept"):
            target = (suites[0]["suite_id"], ch["scenario"], ch["failing_kept"][0])
            break
    if target:
        post({"action": "open_saved", "suite": target[0], "scenario": target[1], "seed": target[2]})
        s = get("/api/state")
        ok("a failing seed from the last suite opens in the panes", s["control"]["mode"] == "review" and s["control"]["done"],
           f"{target[1]} seed {target[2]}")
        post({"action": "play"})
        ok("saved runs are read only", get("/api/state")["control"]["running"] is False)
        post({"action": "run_long", "pid": "x"}, expect=400)
    else:
        ok("a failing seed from the last suite opens in the panes", True, "no failing seed kept in the last suite; skipped")

    srv.shutdown()
    failed = [r for r in ok.rows if not r[1]]
    print(f"\nself-test: {len(ok.rows) - len(failed)}/{len(ok.rows)} checks pass")
    return 1 if failed else 0
