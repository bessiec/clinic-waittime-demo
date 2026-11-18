"""What each pane may see, computed from one run's store at minute t.

  phone     public page + one patient's own texts (views, messages only)
  staff     what the front desk recorded (events), estimate, flags, inbox
  observer  everything, truth included: the demo audience sees what really happened
  finance   aggregates only, never individual patients

Every function takes (store, t) so the same code serves a live run, a paused
one being scrubbed, and a saved run opened from a suite."""
import json
import re

from clinic import flags as flagmod
from clinic.clock import fmt_12h
from clinic.estimator import active_waiting
from clinic.metrics import FRESHNESS_LINE, wait_minutes
from clinic.queue import effective, order as queue_order
from clinic.urgent import URGENT_TEXT

NAMED = ["grace", "maria", "chen", "ana", "jordan", "rosa", "tom"]


def _loads(s):
    return json.loads(s) if s else None


def book(store):
    return {r["pid"]: (r["kind"], r["appt"]) for r in store.rows("SELECT pid,kind,appt FROM patients")}


def names(store):
    return {r["pid"]: r["name"] for r in store.rows("SELECT pid,name FROM patients")}


# --- patient phone -----------------------------------------------------------
def split_view_text(text, lang):
    """Split the guardrail text into display lines without changing a word:
    main lines, the freshness line, and the fixed urgent-care text."""
    urgent = URGENT_TEXT[lang]
    body = text
    has_urgent = urgent in body
    if has_urgent:
        body = body.replace(urgent, "")
    fresh = FRESHNESS_LINE.search(body)
    fresh_line = fresh.group(0) if fresh else None
    if fresh:
        body = body.replace(fresh_line, "")
    main = [s.strip() for s in re.split(r"(?<=\.)\s+", body.strip()) if s.strip()]
    rebuilt = " ".join(main + ([fresh_line] if fresh_line else []) + ([urgent] if has_urgent else []))
    return {"main": main, "fresh": fresh_line, "urgent": urgent if has_urgent else None,
            "verbatim": rebuilt == text}


def phone(store, t, pid, lang):
    v = store.one("SELECT * FROM views WHERE t<=? ORDER BY t DESC LIMIT 1", (t,)) if t is not None else None
    page = None
    if v is not None:
        text = v[f"text_{lang}"]
        page = {"t": v["t"], "status": v["status"], "text": text, **split_view_text(text, lang)}
    thread = []
    if pid:
        for m in store.rows("SELECT t,direction,lang,kind,text FROM messages WHERE pid=? AND t<=? ORDER BY id", (pid, t if t is not None else -1)):
            thread.append({"t": m["t"], "time": fmt_12h(m["t"]), "dir": m["direction"], "lang": m["lang"],
                           "kind": m["kind"], "text": m["text"]})
    nm = names(store)
    return {"page": page, "thread": thread, "lang": lang, "pid": pid,
            "choices": [{"pid": p, "name": nm[p]} for p in NAMED if p in nm]}


# --- staff console ----------------------------------------------------------
def staff(store, t, cfg):
    if t is None:
        return None
    b, nm = book(store), names(store)
    state = store.recorded_state(t)
    overrides = store.overrides(t)
    waiting, excluded = active_waiting(state, b, overrides, t, cfg)
    order = queue_order(waiting, t, cfg)
    by_pid = {e.pid: e for e in waiting}
    queue = []
    for i, pid in enumerate(order):
        e = by_pid[pid]
        booked, appt = b.get(pid, ("walkin", None))
        kind, _ = effective(e, cfg)   # a late scheduled patient waits as a walk-in until re-slotted
        queue.append({"pos": i + 1, "pid": pid, "name": nm.get(pid, pid), "kind": kind,
                      "late": booked == "scheduled" and kind == "walkin",
                      "appt": fmt_12h(appt) if appt is not None else None,
                      "checkin": fmt_12h(e.checkin), "waiting_min": t - e.checkin,
                      "reslotted": pid in overrides})
    in_room = []
    for pid, r in state.patients.items():
        if r.roomed is not None and r.checked_out is None:
            in_room.append({"pid": pid, "name": nm.get(pid, pid), "roomed": fmt_12h(r.roomed),
                            "minutes": t - r.roomed, "run_long": r.run_long_at is not None})
    in_room.sort(key=lambda x: -x["minutes"])
    est = store.one("SELECT * FROM estimates WHERE t=?", (t,))
    view = store.one("SELECT status,reason,updated_min_ago FROM views WHERE t=?", (t,))
    inbox, decided = [], []
    for p in store.rows("SELECT * FROM proposals WHERE t<=? ORDER BY id", (t,)):
        a = store.one("SELECT * FROM approvals WHERE proposal_id=? AND t<=?", (p["id"], t))
        item = {"id": p["id"], "t": p["t"], "time": fmt_12h(p["t"]), "pid": p["pid"], "name": nm.get(p["pid"], p["pid"]),
                "action": p["action"], "reason": p["reason"], "source": p["source"]}
        if a is None:
            inbox.append(item)
        else:
            decided.append(item | {"decision": a["decision"], "by": a["actor"], "why": a["reason"], "decided": fmt_12h(a["t"])})
    return {
        "queue": [q for q in queue if q["kind"] == "scheduled"],
        "walkins": [q for q in queue if q["kind"] == "walkin"],
        "in_room": in_room,
        "providers": state.providers,
        "estimate": None if est is None else {
            "wait": [est["wait_low"], est["wait_high"]], "behind": [est["behind_low"], est["behind_high"]],
            "total": [est["total_low"], est["total_high"]], "confidence": est["confidence"], "samples": est["samples"]},
        "page": None if view is None else {"status": view["status"], "reason": view["reason"], "updated_min_ago": view["updated_min_ago"]},
        "flags": [f | {"name": nm.get(f["pid"]) if f["pid"] else None, "time": fmt_12h(f["t"]) if f["t"] is not None else None}
                  for f in flagmod.scan(store, b, t, cfg)],
        "inbox": inbox,
        "decided": decided[-8:][::-1],
    }


# --- observer ---------------------------------------------------------------
def observer(store, t):
    info = dict(store.one("SELECT * FROM runs"))
    nm = names(store)
    if t is None:
        t = info["open_t"] - 1
    first = {}
    for r in store.rows("SELECT t,type,pid FROM truth WHERE pid IS NOT NULL AND t<=? ORDER BY id", (t,)):
        first.setdefault((r["type"], r["pid"]), r["t"])
    handoffs = {r["pid"]: r["t"] for r in store.rows("SELECT t,pid FROM events WHERE type='handoff' AND t<=?", (t,))}
    lanes = []
    for p in store.rows("SELECT * FROM patients ORDER BY COALESCE(appt, 100000), pid"):
        pid = p["pid"]
        ci = first.get(("checkin", pid))
        lane = {"pid": pid, "name": p["name"], "kind": p["kind"], "persona": p["persona"],
                "appt": p["appt"], "checkin": ci, "roomed": first.get(("roomed", pid)),
                "checkout": first.get(("checkout", pid)), "left": first.get(("leave", pid)),
                "handoff": handoffs.get(pid), "told": None, "actual": None}
        if ci is not None:
            v = store.one("SELECT status,reason,wait_low,wait_high,behind_low,behind_high FROM views WHERE t=?", (ci,))
            if v is not None:
                if v["status"] == "range":
                    lo, hi = (v["wait_low"], v["wait_high"]) if p["kind"] == "walkin" else (v["behind_low"], v["behind_high"])
                    lane["told"] = {"status": "range", "lo": lo, "hi": hi}
                else:
                    lane["told"] = {"status": v["status"], "reason": v["reason"]}
            if lane["roomed"] is not None:
                lane["actual"] = wait_minutes(p["kind"], p["appt"], ci, lane["roomed"])
        lanes.append(lane)
    return {"lanes": lanes, "decisions": decisions(store, t, nm), "open_t": info["open_t"],
            "close_t": info["close_t"], "end_t": info["end_t"], "t": t}


def decisions(store, t, nm):
    """Product decisions with their one-line reasons, newest first."""
    out = []
    for p in store.rows("SELECT * FROM proposals WHERE t<=?", (t,)):
        out.append({"t": p["t"], "pid": p["pid"], "who": "exception agent (scripted)", "what": f"proposed {p['action']} for {nm.get(p['pid'])}", "why": p["reason"]})
    for a in store.rows("SELECT a.*, p.pid FROM approvals a JOIN proposals p ON p.id=a.proposal_id WHERE a.t<=?", (t,)):
        out.append({"t": a["t"], "pid": a["pid"], "who": a["actor"], "what": f"{a['decision']} the proposal for {nm.get(a['pid'])}", "why": a["reason"]})
    for q in store.rows("SELECT * FROM queue_changes WHERE t<=?", (t,)):
        out.append({"t": q["t"], "pid": q["pid"], "who": "queue", "what": f"{nm.get(q['pid'])} moved from place {q['from_pos'] + 1 if q['from_pos'] is not None else '?'} to {q['to_pos'] + 1 if q['to_pos'] is not None else '?'}",
                    "why": f"approval #{q['approval_id']}"})
    for e in store.rows("SELECT * FROM events WHERE type IN ('handoff','guardrail_block') AND t<=?", (t,)):
        d = _loads(e["data"]) or {}
        out.append({"t": e["t"], "pid": e["pid"], "who": e["actor"], "what": "urgent-care reply + hand-off to staff" if e["type"] == "handoff" else "blocked a reply",
                    "why": d.get("reason") or f"blocked term: {d.get('term')}"})
    prev = None
    for v in store.rows("SELECT t,status,reason FROM views WHERE t<=? ORDER BY t", (t,)):
        key = (v["status"], v["reason"] if v["status"] != "range" else "")
        if v["status"] == "declined" and v["reason"].startswith("data "):
            key = ("declined", "stale")
        if key != prev:
            if v["status"] == "range":
                out.append({"t": v["t"], "pid": None, "who": "guardrail", "what": "showing ranges", "why": "data fresh and confidence ok"})
            else:
                out.append({"t": v["t"], "pid": None, "who": "guardrail", "what": "declined: \"call the clinic\"", "why": v["reason"]})
            prev = key
    for d in out:
        d["time"] = fmt_12h(d["t"])
    out.sort(key=lambda d: (-d["t"], d["who"]))
    return out


def patient_log(store, pid, t):
    """Everything about one patient up to t, oldest first, with its source."""
    nm = names(store)
    p = store.one("SELECT * FROM patients WHERE pid=?", (pid,))
    if p is None:
        return None
    log = []

    def add(when, src, text):
        log.append({"t": when, "time": fmt_12h(when), "src": src, "text": text})

    if p["appt"] is not None:
        add(p["appt"], "book", f"Appointment at {fmt_12h(p['appt'])}")
    for r in store.rows("SELECT * FROM truth WHERE pid=? AND t<=? AND type!='queue_snapshot' ORDER BY id", (pid, t)):
        label = {"checkin": "Arrived and checked in", "roomed": "Taken to a room", "checkout": "Visit finished",
                 "leave": "Walked away without being seen", "left": "Desk noticed they had left"}.get(r["type"], r["type"])
        add(r["t"], "truth", label)
    for r in store.rows("SELECT * FROM events WHERE pid=? AND t<=? ORDER BY id", (pid, t)):
        d = _loads(r["data"]) or {}
        extra = " (typo)" if d.get("typo") else (" (erratic)" if d.get("erratic") else "")
        add(r["t"], "desk" if r["actor"] not in ("texting",) else "product", f"{r['actor']} pressed \"{r['type']}\"{extra}")
    ci = store.one("SELECT t FROM truth WHERE pid=? AND type='checkin'", (pid,))
    if ci and ci["t"] <= t:
        v = store.one("SELECT text_en FROM views WHERE t=?", (ci["t"],))
        if v:
            add(ci["t"], "page", "Page at check-in: " + v["text_en"])
    for r in store.rows("SELECT * FROM verbal_estimates WHERE pid=? AND t<=?", (pid, t)):
        add(r["t"], "desk", f"Asked \"how long?\" at the desk; {r['actor']} said: \"{r['text']}\"")
    for r in store.rows("SELECT * FROM messages WHERE pid=? AND t<=? ORDER BY id", (pid, t)):
        add(r["t"], "text", ("Patient texted: " if r["direction"] == "in" else f"Reply ({r['kind']}): ") + r["text"])
    for r in store.rows("SELECT * FROM notes WHERE pid=? AND t<=?", (pid, t)):
        add(r["t"], "note", "What I understood: " + r["text"])
    for r in store.rows("SELECT * FROM proposals WHERE pid=? AND t<=?", (pid, t)):
        add(r["t"], "agent", f"Proposal ({r['action']}): {r['reason']}")
        a = store.one("SELECT * FROM approvals WHERE proposal_id=? AND t<=?", (r["id"], t))
        if a:
            add(a["t"], "approval", f"{a['actor']} {a['decision']}: {a['reason']}")
    order = {"book": 0, "truth": 1, "desk": 2, "page": 3, "agent": 4, "approval": 5, "text": 6, "product": 7, "note": 8}
    log.sort(key=lambda x: (x["t"], order.get(x["src"], 9)))
    return {"pid": pid, "name": p["name"], "kind": p["kind"], "persona": p["persona"], "log": log}
