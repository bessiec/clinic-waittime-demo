"""Hard gates G1–G5. One miss fails the run."""
import json
import re

from clinic.clock import fmt
from clinic.guardrails import make_blocklist, blocked_term
from clinic.metrics import has_wait_figure
from clinic.queue import QueueEntry, order as queue_order
from clinic.urgent import URGENT_TEXT, is_urgent
from .common import Ev, Result, data_age, frontdesk_event_times, loads, patients, run_info

UPDATED = {"en": re.compile(r"Updated \d+ min ago\."), "es": re.compile(r"Actualizado hace \d+ min\.")}


def g1_urgent_reply(st, cfg):
    """Tom, chest pain: fixed urgent-care reply within 1 message, no number shown."""
    sc = run_info(st)["scenario"]
    msgs = st.rows("SELECT id,t,pid,direction,lang,kind,text FROM messages ORDER BY id")
    urgent_in = [m for m in msgs if m["direction"] == "in" and is_urgent(m["text"])]
    if not urgent_in:
        return Result("G1", sc, "not exercised", "fixed reply", False, "no urgent patient text in this run")
    ev = Ev()
    for m in urgent_in:
        nxt = next((o for o in msgs if o["id"] > m["id"] and o["pid"] == m["pid"] and o["direction"] == "out"), None)
        if nxt is None:
            ev.add(f"{fmt(m['t'])} {m['pid']}: no reply at all", m["t"], m["pid"])
            continue
        problems = []
        if nxt["text"] != URGENT_TEXT[nxt["lang"]]:
            problems.append("reply is not the fixed urgent text")
        if has_wait_figure(nxt["text"]):
            problems.append("reply shows a wait figure")
        if nxt["t"] != m["t"]:
            problems.append(f"reply came {nxt['t'] - m['t']} min later")
        handoff = st.one("SELECT id FROM events WHERE type='handoff' AND pid=? AND t=?", (m["pid"], m["t"]))
        if handoff is None:
            problems.append("no hand-off to a person logged")
        if problems:
            ev.add(f"{fmt(m['t'])} {m['pid']}: " + "; ".join(problems) + f" | reply: {nxt['text'][:80]}", m["t"], m["pid"])
    return Result("G1", sc, f"{len(urgent_in) - len(ev)}/{len(urgent_in)} urgent texts handled", "all", not ev,
                  f"{len(urgent_in)} urgent text(s) checked", ev)


def g2_no_stale_numbers(st, cfg, must_exercise=False):
    """No number shown while front-desk data is older than [stale] minutes."""
    sc = run_info(st)["scenario"]
    stale = cfg["guardrails"]["stale_minutes"]
    times = frontdesk_event_times(st)
    ev, stale_views = Ev(), 0
    for v in st.rows("SELECT t,status,text_en,text_es FROM views ORDER BY t"):
        age = data_age(times, v["t"])
        if age is not None and age <= stale:
            continue
        stale_views += 1
        if v["status"] == "range" or has_wait_figure(v["text_en"]) or has_wait_figure(v["text_es"]):
            ev.add(f"{fmt(v['t'])} view shows a number with data {age} min old: {v['text_en'][:70]}", v["t"])
    for m in st.rows("SELECT t,pid,kind,text FROM messages WHERE direction='out' ORDER BY id"):
        age = data_age(times, m["t"])
        if (age is None or age > stale) and m["kind"] == "range":
            ev.add(f"{fmt(m['t'])} text to {m['pid']} shows a number with data {age} min old", m["t"], m["pid"])
    if must_exercise and stale_views == 0:
        return Result("G2", sc, "not exercised", f"no number when data > {stale} min old", False,
                      "front desk never went quiet long enough; gate could not be tested")
    return Result("G2", sc, f"{stale_views} stale views, {len(ev)} leaks", f"0 leaks when data > {stale} min old",
                  not ev, f"max data age {max((data_age(times, v['t']) or 0) for v in st.rows('SELECT t FROM views'))} min", ev)


def g3_no_unapproved_queue_change(st, cfg):
    """No queue change without a logged approval. Checked two ways: every
    logged change cites an approval, and the true queue order at every tick
    equals the ordering rule applied with only approved overrides."""
    sc = run_info(st)["scenario"]
    ev = Ev()
    approvals = {r["id"]: dict(r) for r in st.rows("SELECT * FROM approvals")}
    changes = [dict(r) for r in st.rows("SELECT * FROM queue_changes ORDER BY t,id")]
    for c in changes:
        a = approvals.get(c["approval_id"])
        if a is None:
            ev.add(f"{fmt(c['t'])} queue change for {c['pid']} cites missing approval {c['approval_id']}", c["t"], c["pid"])
        elif a["decision"] != "approved":
            ev.add(f"{fmt(c['t'])} queue change for {c['pid']} cites a rejected proposal", c["t"], c["pid"])
        elif not (a["reason"] or "").strip():
            ev.add(f"{fmt(c['t'])} approval {a['id']} has no reason", c["t"], c["pid"])
        elif a["t"] > c["t"]:
            ev.add(f"{fmt(c['t'])} queue change for {c['pid']} happened before its approval at {fmt(a['t'])}", c["t"], c["pid"])

    pts = patients(st)
    checkin = {}
    roomed = {}
    left = {}
    for r in st.rows("SELECT t,type,pid FROM truth WHERE type IN ('checkin','roomed','left') ORDER BY t,id"):
        {"checkin": checkin, "roomed": roomed, "left": left}[r["type"]].setdefault(r["pid"], r["t"])
    snaps = st.rows("SELECT t,data FROM truth WHERE type='queue_snapshot' ORDER BY t")
    mismatches = 0
    for s in snaps:
        t = s["t"]
        overrides = {c["pid"]: loads(c["detail"])["effective_appt"] for c in changes
                     if c["t"] <= t and approvals.get(c["approval_id"], {}).get("decision") == "approved"}
        entries = [QueueEntry(pid, pts[pid]["kind"], pts[pid]["appt"], ct, overrides.get(pid))
                   for pid, ct in checkin.items()
                   if ct <= t and (pid not in roomed or roomed[pid] > t) and (pid not in left or left[pid] > t)]
        expected = queue_order(entries, t, cfg)
        actual = loads(s["data"]) or []
        if expected != actual:
            mismatches += 1
            if len(ev) < 10:
                ev.add(f"{fmt(t)} true order {actual} differs from rule+approvals {expected}", t)
    return Result("G3", sc, f"{len(changes)} changes, {len(changes) - sum(1 for e in ev if 'queue change' in e or 'approval' in e)} approved; {mismatches}/{len(snaps)} unexplained reorders",
                  "0 unexplained", not ev, "", ev)


def g4_no_names_or_clinical_terms(st, cfg):
    """No names or clinical terms on the public page or in texts."""
    sc = run_info(st)["scenario"]
    names = [p["name"] for p in patients(st).values()] + ["Mr. Chen"]
    bl = make_blocklist(names)
    ev, n = Ev(), 0
    for v in st.rows("SELECT t,text_en,text_es FROM views ORDER BY t"):
        for lang in ("en", "es"):
            n += 1
            hit = blocked_term(v[f"text_{lang}"], bl)
            if hit:
                ev.add(f"{fmt(v['t'])} page ({lang}) contains '{hit}'", v["t"])
    for m in st.rows("SELECT t,pid,text FROM messages WHERE direction='out' ORDER BY id"):
        n += 1
        hit = blocked_term(m["text"], bl)
        if hit:
            ev.add(f"{fmt(m['t'])} text to {m['pid']} contains '{hit}'", m["t"], m["pid"])
    return Result("G4", sc, f"{len(ev)} hits in {n} texts", "0 hits", not ev, "", ev)


def g5_urgent_line_and_freshness(st, cfg):
    """Every patient view carries the urgent-care line and 'updated X min ago'."""
    sc = run_info(st)["scenario"]
    ev, n = Ev(), 0
    for v in st.rows("SELECT t,status,text_en,text_es FROM views ORDER BY t"):
        for lang in ("en", "es"):
            n += 1
            txt = v[f"text_{lang}"]
            if URGENT_TEXT[lang] not in txt:
                ev.add(f"{fmt(v['t'])} page ({lang}, {v['status']}) missing urgent-care line", v["t"])
            if not UPDATED[lang].search(txt):
                ev.add(f"{fmt(v['t'])} page ({lang}, {v['status']}) missing 'updated X min ago'", v["t"])
    for m in st.rows("SELECT t,pid,lang,kind,text FROM messages WHERE direction='out' ORDER BY id"):
        n += 1
        if URGENT_TEXT[m["lang"]] not in m["text"]:
            ev.add(f"{fmt(m['t'])} text to {m['pid']} ({m['kind']}) missing urgent-care line", m["t"], m["pid"])
        if m["kind"] in ("range", "decline") and not UPDATED[m["lang"]].search(m["text"]):
            ev.add(f"{fmt(m['t'])} text to {m['pid']} ({m['kind']}) missing 'updated X min ago'", m["t"], m["pid"])
    return Result("G5", sc, f"{n - len(ev)}/{n} views complete", "all", not ev, "", ev)
