"""Shared grader plumbing. Every grader is a function over a finished run's
SQLite file and returns a Result."""
import json
from dataclasses import dataclass, field

from clinic.store import Store, FRONTDESK_EVENT_TYPES


@dataclass
class Result:
    id: str
    scenario: str
    score: str
    threshold: str
    passed: bool | None            # None = report-only
    detail: str = ""
    evidence: list = field(default_factory=list)   # log lines that caused a failure
    value: float | None = None     # this seed's score as a number, for the spread
    pool: dict = field(default_factory=dict)       # raw counts or values, pooled across seeds
    seed: int | None = None


def open_run(path):
    return Store(path)


def run_info(st):
    return dict(st.one("SELECT * FROM runs"))


def patients(st):
    return {r["pid"]: dict(r) for r in st.rows("SELECT * FROM patients")}


def truth_times(st, type_):
    """pid -> t of the first truth event of this type."""
    out = {}
    for r in st.rows("SELECT t,pid FROM truth WHERE type=? ORDER BY t,id", (type_,)):
        out.setdefault(r["pid"], r["t"])
    return out


def frontdesk_event_times(st):
    q = ",".join("?" * len(FRONTDESK_EVENT_TYPES))
    return [r["t"] for r in st.rows(f"SELECT t FROM events WHERE type IN ({q}) ORDER BY t,id", FRONTDESK_EVENT_TYPES)]


def data_age(event_times, t):
    """Minutes since the newest front-desk event at or before t (None if none)."""
    last = None
    for et in event_times:
        if et <= t:
            last = et
        else:
            break
    return None if last is None else t - last


def loads(s):
    return json.loads(s) if s else None


def outcomes(st, cfg):
    """One row per patient who checked in: what the page said at that moment
    and what really happened. A patient who walked away or was still waiting
    when the run ended is 'censored': we only know the wait was at least
    `lower` minutes, and the graders use that rather than drop them."""
    from clinic.metrics import wait_minutes
    info = run_info(st)
    pts = patients(st)
    checkin, roomed, leave = truth_times(st, "checkin"), truth_times(st, "roomed"), truth_times(st, "leave")
    rows = []
    for pid, ci in checkin.items():
        p = pts[pid]
        view = st.one("SELECT * FROM views WHERE t=?", (ci,))
        est = st.one("SELECT basis FROM estimates WHERE t=?", (ci,))
        start = max(p["appt"], ci) if p["kind"] == "scheduled" and p["appt"] is not None else ci
        if pid in roomed:
            actual, lower, censored = wait_minutes(p["kind"], p["appt"], ci, roomed[pid]), None, False
        else:
            end = leave.get(pid, info["end_t"])
            actual, lower, censored = None, max(0, end - start), True
        if view is None or view["status"] != "range":
            lo = hi = None
        elif p["kind"] == "walkin":
            lo, hi = view["wait_low"], view["wait_high"]
        else:
            lo, hi = view["behind_low"], view["behind_high"]
        rows.append({"pid": pid, "kind": p["kind"], "appt": p["appt"], "checkin": ci,
                     "shown": lo is not None, "lo": lo, "hi": hi, "actual": actual,
                     "lower": lower, "censored": censored, "left": pid in leave,
                     "basis": loads(est["basis"]) if est else {}})
    return rows
