"""Local web server for the four panes. Standard library only.

  python3 -m web            serve on http://127.0.0.1:8000 (config [ui])
  python3 -m web --selftest play a full run through the API and check every pane

One Session holds one run. A background ticker steps the director at the
chosen speed; request handlers read the store under the same lock. Panes are
computed from the store at a minute t (live, scrubbed, or a saved run)."""
import json
import threading
import time
import traceback
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from clinic import config
from clinic.clock import fmt_12h
from clinic.config import ROOT
from clinic.director import Director
from clinic.scenarios import SCENARIOS
from clinic.store import Store
from clinic.actors.llm import PROMPTS
from evals import fidelity
from evals import finance as finmod
from evals.demo_run import paths as demo_paths
from evals.common import open_run
from evals.runner import EXACT, GRADERS, PLAN, SUITES
from evals.scored import e10_finance_reconciles
from . import panes

STATIC = Path(__file__).parent / "static"
LIVE = ROOT / "runs" / "live"
LABEL = "Simulation, synthetic data"


class Session:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.director = None
        self.store = None
        self.baseline = None
        self.mode = None            # "live" | "review"
        self.source = None          # what is loaded, for the control bar
        self.running = False
        self.speed = cfg["ui"]["default_speed"]
        self._score = (None, None, 0.0)   # (key, rows, wall time)
        self._fin = (None, None)
        threading.Thread(target=self._ticker, daemon=True).start()

    # --- loading ------------------------------------------------------------
    def start(self, scenario, seed, approver="persona", replay=False, demo=False):
        """demo=True plays the recorded LLM-agent day (Milestone 4) minute by minute.
        It replays the saved agent replies, so watching it calls no model and costs nothing."""
        with self.lock:
            script = []
            if replay:
                if self.mode != "live" or self.director is None:
                    raise ValueError("replay needs a live run")
                script = [{"t": r["t"], "kind": r["kind"], "data": json.loads(r["data"])}
                          for r in self.store.rows("SELECT * FROM human_actions ORDER BY id")]
                scenario, seed, approver = self.director.scenario, self.director.seed, self.director.approver
                demo = bool(self.source and self.source.get("demo"))
            actor_kw = {}
            if demo:
                rec, _ = demo_paths(self.cfg)
                if not rec.exists():
                    raise ValueError("no recorded AI demo day yet; run python3 -m evals.demo_run")
                scenario, seed = self.cfg["actors"]["demo_scenario"], self.cfg["actors"]["demo_seed"]
                actor_kw = {"actors": "replay", "replay_from": str(rec)}
                if approver == "human":
                    raise ValueError("the AI demo day replays Kim's recorded approvals; use approvals by desk persona")
            if scenario not in SCENARIOS:
                raise ValueError(f"unknown scenario {scenario}")
            self.running = False
            LIVE.mkdir(parents=True, exist_ok=True)
            base_path = LIVE / f"{scenario}_s{seed}_baseline.sqlite"
            Director(scenario, seed, self.cfg, str(base_path), product=False).build().run()
            self.baseline = open_run(str(base_path))
            self.director = Director(scenario, seed, self.cfg, str(LIVE / f"{scenario}_s{seed}.sqlite"),
                                     approver=approver, human_script=script, **actor_kw).build().start()
            self.store = self.director.store
            self.mode = "live"
            self.source = {"scenario": scenario, "seed": seed, "approver": approver, "replay": bool(script),
                           "replayed_actions": len(script), "demo": demo}
            self._score = (None, None, 0.0)
            self._fin = (None, None)

    def open_saved(self, suite, scenario, seed):
        path = SUITES / suite / f"{scenario}_s{seed}.sqlite"
        if not path.exists():
            raise ValueError(f"run {scenario} seed {seed} was not kept for suite {suite}")
        probe = Store(str(path))
        if not probe.one("SELECT name FROM sqlite_master WHERE name='survey'"):
            probe.conn.close()
            raise ValueError(f"suite {suite} was saved before Milestone 3 (no survey or cost data); "
                             "run python3 -m evals to make a new one")
        with self.lock:
            self.running = False
            self.director = None
            self.store = probe
            bpath = SUITES / suite / f"{scenario}_s{seed}_baseline.sqlite"
            if not bpath.exists():
                LIVE.mkdir(parents=True, exist_ok=True)
                bpath = LIVE / f"{scenario}_s{seed}_baseline.sqlite"
                Director(scenario, seed, self.cfg, str(bpath), product=False).build().run()
            self.baseline = open_run(str(bpath))
            self.mode = "review"
            self.source = {"scenario": scenario, "seed": seed, "suite": suite,
                           "approver": "saved run", "replay": False}
            self._score = (None, None, 0.0)
            self._fin = (None, None)

    # --- clock ----------------------------------------------------------------
    def _ticker(self):
        while True:
            delay = self.cfg["ui"]["speed_seconds"].get(self.speed, 1.0)
            with self.lock:
                if self.running and self.director is not None:
                    if not self.director.step():
                        self.running = False
            time.sleep(delay)

    def now(self):
        """Last simulated minute (None before the first step)."""
        if self.store is None:
            return None
        if self.director is not None:
            return self.director.t - 1 if self.director.t > self.director.open_t else None
        info = self.store.one("SELECT end_t FROM runs")
        return info["end_t"] - 1

    def done(self):
        if self.director is not None:
            return self.director.done
        return self.store is not None

    def step(self, n=1):
        with self.lock:
            for _ in range(n):
                if self.director is None or not self.director.step():
                    break

    def run_to_end(self):
        with self.lock:
            while self.director is not None and self.director.step():
                pass

    # --- graded views -----------------------------------------------------------
    def finance(self):
        key = (self.now(), self.done())
        if self._fin[0] != key:
            self._fin = (key, finmod.compute(self.store, self.cfg, self.baseline))
        return self._fin[1]

    def scorecard(self, force=False):
        key = (self.now(), self.done())
        k, rows, wall = self._score
        if not force and k is not None and (k == key or (time.time() - wall < self.cfg["ui"]["score_refresh_seconds"] and not key[1])):
            return rows
        sc = self.source["scenario"]
        live = not self.done()
        rows = []
        for cid in PLAN[sc]:
            if cid == "F1" and live:
                # Persona fidelity judges a whole day; the Agents tab shows it per persona as it goes.
                rows.append({"id": "F1", "key": "F1", "scenario": sc, "score": "judged at the end of the day",
                             "threshold": "all exercised", "passed": None, "detail": "", "evidence": [], "refs": [],
                             "status": "pending", "exact": True})
                continue
            if cid == "E10":
                r = e10_finance_reconciles(self.store, self.cfg, shown=self.finance(), baseline=self.baseline)
            else:
                r = GRADERS[cid](self.store, self.cfg)
            d = asdict(r)
            d["key"] = cid
            if r.passed is None:
                d["status"] = "info"
            elif live and r.id.startswith("G") and r.passed is False and r.score != "not exercised":
                d["status"] = "fail"            # a leak already shown cannot be undone
            elif live and (r.score == "not exercised" or r.id in ("E9", "E10")):
                d["status"] = "pending"         # judged only once the run ends
            elif live:
                d["status"] = "so far: " + ("pass" if r.passed else "fail")
            else:
                d["status"] = "pass" if r.passed else "fail"
            d["exact"] = r.id in EXACT
            rows.append(d)
        rows.sort(key=lambda d: (not d["id"].startswith("G"), d["id"]))
        self._score = (key, rows, time.time())
        return rows

    def agents(self):
        """The Agents view: every test-actor persona, its prompt, what it did in the
        loaded run and whether it behaved as written."""
        with self.lock:
            st = self.store
            has_calls = st is not None and st.has_table("llm_calls") and st.one("SELECT COUNT(*) n FROM llm_calls")["n"] > 0
            fid = {r["persona"]: r for r in fidelity.personas(st, self.cfg)} if st is not None and self.now() is not None else {}
            desk = st.one("SELECT frontdesk FROM runs")["frontdesk"] if st is not None else None
            out = []
            for pid, kind in [("dana", "front desk"), ("luis", "front desk"), ("kim", "front desk"), ("pat", "front desk"),
                              ("grace", "patient"), ("maria", "patient"), ("chen", "patient"), ("ana", "patient"),
                              ("jordan", "patient"), ("rosa", "patient"), ("tom", "patient")]:
                in_run = st is not None and (pid == desk if kind == "front desk" else True)
                calls = []
                stats = {"n": 0, "ok": 0, "usd": 0.0}
                if has_calls and in_run:
                    r = st.one("SELECT COUNT(*) n, COALESCE(SUM(ok),0) ok, COALESCE(SUM(modeled_usd),0) usd FROM llm_calls "
                               "WHERE actor=? AND source != 'skipped' AND t <= ?", (pid, self.now() if self.now() is not None else -1))
                    stats = {"n": r["n"], "ok": r["ok"], "usd": round(r["usd"], 4)}
                    calls = [{"time": fmt_12h(c["t"]), "role": c["role"], "prompt": c["prompt"], "reply": c["response"],
                              "ok": bool(c["ok"]), "error": c["error"]}
                             for c in st.rows("SELECT * FROM llm_calls WHERE actor=? AND source != 'skipped' AND t <= ? "
                                              "ORDER BY seq DESC LIMIT 8", (pid, self.now() if self.now() is not None else -1))]
                prompt = (PROMPTS / f"{pid}.md").read_text()
                row = fid.get(pid) if in_run else None
                out.append({"id": pid, "kind": kind, "title": prompt.splitlines()[0].lstrip("# ").strip(), "prompt": prompt,
                            "in_run": in_run, "fidelity": row, "calls": stats, "recent": calls})
            return {"mode": ("LLM persona agents (recorded, replayed free)" if has_calls else
                             "scripted rules (no model calls)" if st is not None else "no run loaded"),
                    "llm": has_calls, "model": self.cfg["actors"]["model"],
                    "rules": (PROMPTS / "_rules.md").read_text(), "personas": out}

    # --- payloads -----------------------------------------------------------------
    def state(self, pid, lang, at):
        with self.lock:
            meta = {"label": LABEL, "scenarios": {k: v["description"] for k, v in SCENARIOS.items()},
                    "holdouts": [k for k, v in SCENARIOS.items() if v["holdout"]],
                    "speeds": list(self.cfg["ui"]["speed_seconds"]), "speed": self.speed,
                    "default_seed": self.cfg["run"]["seed"]}
            if self.store is None:
                return {"meta": meta, "loaded": False}
            now = self.now()
            info = dict(self.store.one("SELECT * FROM runs"))
            t = now if at is None or now is None else max(min(at, now), info["open_t"])
            ctrl = {"mode": self.mode, "source": self.source, "running": self.running, "done": self.done(),
                    "now": now, "t": t, "time": fmt_12h(t) if t is not None else "not started",
                    "open_t": info["open_t"], "close_t": info["close_t"],
                    "following": at is None or at == now,
                    "can_act": self.mode == "live" and not self.done() and (at is None or at == now),
                    "human_approver": bool(self.director and self.director.approver == "human"),
                    "queued_actions": len(self.director.human_queue) if self.director else 0}
            return {"meta": meta, "loaded": True, "control": ctrl,
                    "phone": panes.phone(self.store, t, pid, lang),
                    "staff": panes.staff(self.store, t, self.cfg),
                    "observer": panes.observer(self.store, t),
                    "scorecard": self.scorecard() if now is not None else [],
                    "finance": self.finance() if now is not None else None}


def suites_summary(limit=6):
    """Saved suites for the observer pane's "suite results" tab and the finance trend."""
    if not SUITES.exists():
        return []
    out = []
    for d in sorted((d for d in SUITES.iterdir() if (d / "results.json").exists()), reverse=True)[:limit]:
        res = json.loads((d / "results.json").read_text())
        kept = {f.name for f in d.glob("*.sqlite")}
        for c in res["checks"]:
            c["refs"] = [r | {"kept": f"{c['scenario']}_s{r.get('seed')}.sqlite" in kept} for r in c.get("refs", [])]
            c["failing_kept"] = [s for s in c["failing_seeds"] if f"{c['scenario']}_s{s}.sqlite" in kept]
        out.append({"suite_id": res["suite_id"], "created": res["created"], "seeds": len(res["seeds"]),
                    "checks": res["checks"], "finance": res.get("finance")})
    return out


class Handler(BaseHTTPRequestHandler):
    session: Session = None

    def log_message(self, fmt, *args):   # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text", "application/json")) else ""))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        s = self.session
        try:
            if u.path == "/api/state":
                at = int(q["at"]) if q.get("at") not in (None, "") else None
                return self._send(200, s.state(q.get("pid") or None, q.get("lang", "en"), at))
            if u.path == "/api/patient":
                with s.lock:
                    if s.store is None:
                        return self._send(404, {"error": "no run loaded"})
                    now = s.now()
                    at = int(q["at"]) if q.get("at") not in (None, "") else now
                    return self._send(200, panes.patient_log(s.store, q["pid"], min(at, now) if now is not None else -1))
            if u.path == "/api/suites":
                return self._send(200, suites_summary())
            if u.path == "/api/agents":
                return self._send(200, s.agents())
            path = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (STATIC / path).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._send(404, {"error": "not found"})
            ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(f.suffix, "application/octet-stream")
            return self._send(200, f.read_bytes(), ctype)
        except Exception as e:  # show the error in the UI instead of hanging
            traceback.print_exc()
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        s = self.session
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if u.path != "/api/control":
                return self._send(404, {"error": "not found"})
            a = body.get("action")
            if a == "start":
                s.start(body["scenario"], int(body["seed"]), body.get("approver", "persona"))
            elif a == "start_demo":
                s.start(None, None, "persona", demo=True)
            elif a == "replay":
                s.start(None, None, replay=True)
            elif a == "open_saved":
                s.open_saved(body["suite"], body["scenario"], int(body["seed"]))
            elif a == "play":
                with s.lock:
                    s.running = s.mode == "live" and not s.done()
            elif a == "pause":
                with s.lock:
                    s.running = False
            elif a == "step":
                with s.lock:
                    s.running = False
                s.step(int(body.get("n", 1)))
            elif a == "to_end":
                with s.lock:
                    s.running = False
                s.run_to_end()
            elif a == "speed":
                if body["speed"] not in s.cfg["ui"]["speed_seconds"]:
                    raise ValueError("unknown speed")
                s.speed = body["speed"]
            elif a in ("decide", "run_long"):
                with s.lock:
                    if s.mode != "live" or s.director is None or s.director.done:
                        raise ValueError("only a live run takes staff actions")
                    if a == "decide":
                        if s.director.approver != "human":
                            raise ValueError("this run's approvals are made by the desk persona")
                        if body.get("decision") not in ("approved", "rejected"):
                            raise ValueError("decision must be approved or rejected")
                        s.director.human("decide", proposal_id=int(body["proposal_id"]), decision=body["decision"],
                                         reason=body.get("reason", ""))
                    else:
                        s.director.human("run_long", pid=body["pid"])
            else:
                raise ValueError(f"unknown action {a}")
            return self._send(200, {"ok": True})
        except (ValueError, KeyError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": str(e)})


def make_server(cfg, host=None, port=None):
    Handler.session = Session(cfg)
    return ThreadingHTTPServer((host or cfg["ui"]["host"], cfg["ui"]["port"] if port is None else port), Handler)
