"""SQLite event store: the single source of truth for everything the product
sees (events, messages, estimates, views, approvals) plus a separate `truth`
table that only the scenario director writes and only the evals read."""
import json
import sqlite3
from dataclasses import dataclass, field

SCHEMA = """
CREATE TABLE runs(run_id TEXT PRIMARY KEY, scenario TEXT, seed INTEGER, frontdesk TEXT,
                  open_t INTEGER, close_t INTEGER, end_t INTEGER);
CREATE TABLE patients(pid TEXT PRIMARY KEY, name TEXT, kind TEXT, appt INTEGER, lang TEXT, persona TEXT);
CREATE TABLE truth(id INTEGER PRIMARY KEY, t INTEGER, type TEXT, pid TEXT, data TEXT);
CREATE TABLE events(id INTEGER PRIMARY KEY, t INTEGER, type TEXT, pid TEXT, actor TEXT, data TEXT);
CREATE TABLE estimates(id INTEGER PRIMARY KEY, t INTEGER, wait_low INTEGER, wait_high INTEGER,
                       behind_low INTEGER, behind_high INTEGER, total_low INTEGER, total_high INTEGER,
                       confidence REAL, samples INTEGER, basis TEXT);
CREATE TABLE views(id INTEGER PRIMARY KEY, t INTEGER, status TEXT, wait_low INTEGER, wait_high INTEGER,
                   behind_low INTEGER, behind_high INTEGER, total_low INTEGER, total_high INTEGER,
                   updated_min_ago INTEGER, text_en TEXT, text_es TEXT, reason TEXT);
CREATE TABLE messages(id INTEGER PRIMARY KEY, t INTEGER, pid TEXT, direction TEXT, lang TEXT, kind TEXT, text TEXT);
CREATE TABLE verbal_estimates(id INTEGER PRIMARY KEY, t INTEGER, pid TEXT, actor TEXT,
                              low INTEGER, high INTEGER, point INTEGER, text TEXT);
CREATE TABLE proposals(id INTEGER PRIMARY KEY, t INTEGER, pid TEXT, action TEXT, detail TEXT, reason TEXT, source TEXT);
CREATE TABLE approvals(id INTEGER PRIMARY KEY, t INTEGER, proposal_id INTEGER, actor TEXT, decision TEXT, reason TEXT);
CREATE TABLE queue_changes(id INTEGER PRIMARY KEY, t INTEGER, pid TEXT, approval_id INTEGER,
                           from_pos INTEGER, to_pos INTEGER, detail TEXT);
CREATE TABLE notes(id INTEGER PRIMARY KEY, t INTEGER, pid TEXT, text TEXT);
CREATE TABLE run_costs(run_id TEXT, model_calls INTEGER, model_usd REAL, sms_count INTEGER, sms_usd REAL);
CREATE TABLE survey(id INTEGER PRIMARY KEY, t INTEGER, kind TEXT, told_status TEXT, told_low INTEGER, told_high INTEGER,
                    waited INTEGER, completed INTEGER, accurate INTEGER, satisfied INTEGER, reuse INTEGER, noisy INTEGER);
CREATE TABLE human_actions(id INTEGER PRIMARY KEY, t INTEGER, kind TEXT, data TEXT);
CREATE TABLE llm_calls(seq INTEGER PRIMARY KEY, t INTEGER, actor TEXT, role TEXT, prompt_file TEXT,
                       prompt_sha TEXT, prompt TEXT, response TEXT, ok INTEGER, error TEXT, model TEXT,
                       ms INTEGER, input_tokens INTEGER, output_tokens INTEGER, modeled_usd REAL, source TEXT);
"""

# Status-button events written by the front desk. Anything else in `events`
# (handoff, guardrail_block) is a product-side log line, not front-desk data.
FRONTDESK_EVENT_TYPES = ("checkin", "roomed", "checked_out", "run_long", "staffing")


@dataclass
class Recorded:
    """What the store says about one patient, from front-desk presses only."""
    pid: str
    checkin: int | None = None
    roomed: int | None = None
    checked_out: int | None = None
    left: bool = False
    checkin_count: int = 0
    run_long_at: int | None = None


@dataclass
class RecordedState:
    t: int
    patients: dict = field(default_factory=dict)
    providers: int | None = None
    last_event_t: int | None = None
    run_long_times: list = field(default_factory=list)


class Store:
    def __init__(self, path: str):
        self.path = path
        # check_same_thread=False: the web server reads from request threads while
        # one ticker thread writes; every access there is serialized by a lock.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if not self.conn.execute("SELECT name FROM sqlite_master WHERE name='runs'").fetchone():
            self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- writes -------------------------------------------------------
    def _ins(self, sql, params):
        cur = self.conn.execute(sql, params)
        return cur.lastrowid

    def add_run(self, run_id, scenario, seed, frontdesk, open_t, close_t):
        self._ins("INSERT INTO runs VALUES(?,?,?,?,?,?,NULL)", (run_id, scenario, seed, frontdesk, open_t, close_t))

    def finish_run(self, run_id, end_t):
        self.conn.execute("UPDATE runs SET end_t=? WHERE run_id=?", (end_t, run_id))
        self.conn.commit()

    def add_patient(self, pid, name, kind, appt, lang, persona):
        self._ins("INSERT INTO patients VALUES(?,?,?,?,?,?)", (pid, name, kind, appt, lang, persona))

    def add_truth(self, t, type_, pid=None, data=None):
        return self._ins("INSERT INTO truth(t,type,pid,data) VALUES(?,?,?,?)", (t, type_, pid, json.dumps(data) if data is not None else None))

    def add_event(self, t, type_, pid, actor, data=None):
        return self._ins("INSERT INTO events(t,type,pid,actor,data) VALUES(?,?,?,?,?)", (t, type_, pid, actor, json.dumps(data) if data is not None else None))

    def add_estimate(self, e):
        return self._ins("INSERT INTO estimates(t,wait_low,wait_high,behind_low,behind_high,total_low,total_high,confidence,samples,basis) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (e.t, e.wait_low, e.wait_high, e.behind_low, e.behind_high, e.total_low, e.total_high, e.confidence, e.samples, json.dumps(e.basis)))

    def add_view(self, v):
        return self._ins("INSERT INTO views(t,status,wait_low,wait_high,behind_low,behind_high,total_low,total_high,updated_min_ago,text_en,text_es,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (v.t, v.status, v.wait_low, v.wait_high, v.behind_low, v.behind_high, v.total_low, v.total_high, v.updated_min_ago, v.text["en"], v.text["es"], v.reason))

    def add_message(self, t, pid, direction, lang, kind, text):
        return self._ins("INSERT INTO messages(t,pid,direction,lang,kind,text) VALUES(?,?,?,?,?,?)", (t, pid, direction, lang, kind, text))

    def add_verbal(self, t, pid, actor, low, high, point, text):
        return self._ins("INSERT INTO verbal_estimates(t,pid,actor,low,high,point,text) VALUES(?,?,?,?,?,?,?)", (t, pid, actor, low, high, point, text))

    def add_proposal(self, t, pid, action, detail, reason, source):
        return self._ins("INSERT INTO proposals(t,pid,action,detail,reason,source) VALUES(?,?,?,?,?,?)", (t, pid, action, detail, reason, source))

    def add_approval(self, t, proposal_id, actor, decision, reason):
        return self._ins("INSERT INTO approvals(t,proposal_id,actor,decision,reason) VALUES(?,?,?,?,?)", (t, proposal_id, actor, decision, reason))

    def add_queue_change(self, t, pid, approval_id, from_pos, to_pos, detail):
        return self._ins("INSERT INTO queue_changes(t,pid,approval_id,from_pos,to_pos,detail) VALUES(?,?,?,?,?,?)", (t, pid, approval_id, from_pos, to_pos, json.dumps(detail)))

    def add_note(self, t, pid, text):
        return self._ins("INSERT INTO notes(t,pid,text) VALUES(?,?,?)", (t, pid, text))

    def add_costs(self, run_id, model_calls=0, model_usd=0.0, sms_count=0, sms_usd=0.0):
        self._ins("INSERT INTO run_costs VALUES(?,?,?,?,?)", (run_id, model_calls, model_usd, sms_count, sms_usd))

    def add_survey(self, t, kind, told_status, told_low, told_high, waited, completed, accurate, satisfied, reuse, noisy):
        """Anonymous: no patient id is stored, only what they were told and how long they waited."""
        return self._ins("INSERT INTO survey(t,kind,told_status,told_low,told_high,waited,completed,accurate,satisfied,reuse,noisy) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (t, kind, told_status, told_low, told_high, waited, int(completed), accurate, satisfied, reuse, int(noisy)))

    def add_llm_call(self, row: dict):
        """One persona-agent call (Milestone 4). The full prompt and reply are kept so a
        run can be replayed exactly and inspected in the Agents view."""
        cols = ("seq", "t", "actor", "role", "prompt_file", "prompt_sha", "prompt", "response", "ok", "error",
                "model", "ms", "input_tokens", "output_tokens", "modeled_usd", "source")
        self._ins(f"INSERT INTO llm_calls({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                  tuple(row.get(c) for c in cols))

    def has_table(self, name):
        return self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def add_human_action(self, t, kind, data):
        return self._ins("INSERT INTO human_actions(t,kind,data) VALUES(?,?,?)", (t, kind, json.dumps(data)))

    def commit(self):
        self.conn.commit()

    # --- reads --------------------------------------------------------
    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        return self.conn.execute(sql, params).fetchone()

    def recorded_state(self, t: int) -> RecordedState:
        """Rebuild what the front desk has recorded up to time t (inclusive)."""
        st = RecordedState(t=t)
        for r in self.rows("SELECT t,type,pid,data FROM events WHERE t<=? ORDER BY t,id", (t,)):
            typ, pid = r["type"], r["pid"]
            if typ not in FRONTDESK_EVENT_TYPES:
                continue
            st.last_event_t = r["t"]
            if typ == "staffing":
                st.providers = json.loads(r["data"])["providers"]
                continue
            rec = st.patients.setdefault(pid, Recorded(pid))
            if typ == "checkin":
                rec.checkin_count += 1
                if rec.checkin is None:
                    rec.checkin = r["t"]
            elif typ == "roomed":
                if rec.roomed is None:
                    rec.roomed = r["t"]
            elif typ == "checked_out":
                if rec.checked_out is None:
                    rec.checked_out = r["t"]
                    data = json.loads(r["data"]) if r["data"] else {}
                    rec.left = data.get("reason") == "left"
            elif typ == "run_long":
                rec.run_long_at = r["t"]
                st.run_long_times.append(r["t"])
        return st

    def thread(self, pid):
        return self.rows("SELECT t,direction,lang,kind,text FROM messages WHERE pid=? ORDER BY id", (pid,))

    def overrides(self, t):
        """Approved queue overrides in effect at t: pid -> effective appointment time."""
        out = {}
        for r in self.rows("SELECT q.pid, q.detail FROM queue_changes q JOIN approvals a ON a.id=q.approval_id "
                           "WHERE q.t<=? AND a.decision='approved' ORDER BY q.t,q.id", (t,)):
            out[r["pid"]] = json.loads(r["detail"])["effective_appt"]
        return out

    def proposed_pids(self):
        return {r["pid"] for r in self.rows("SELECT pid FROM proposals")}
