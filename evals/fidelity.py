"""Persona fidelity (Milestone 4): did each test actor behave the way its SPEC
habits say? These catch agents drifting toward polite and punctual. They grade
the actors, not the product, and read only the logs.

personas() returns one row per persona (pass / fail / untested) for the Agents
view; f1() rolls them into one scorecard row. Tolerances live in config
[fidelity] and are proposed, not from SPEC. On scripted runs these pass by
construction, which checks the checks themselves."""
import json
import statistics

from clinic.texting import detect_language
from clinic.urgent import is_urgent
from clinic.clock import fmt
from .common import Ev, Result, patients, run_info, truth_times

STATUS = ("checkin", "roomed", "checked_out")
_TRUTH_FOR = {"checkin": ("checkin",), "roomed": ("roomed",), "checked_out": ("checkout", "left")}


def _row(persona, kind, status, metric, target, ev=None):
    return {"persona": persona, "kind": kind, "status": status, "metric": metric, "target": target,
            "evidence": list(ev or []), "refs": list(getattr(ev, "refs", []))}


def _presses(st, desk):
    return st.rows("SELECT t,type,pid,data FROM events WHERE actor=? AND type IN (?,?,?) ORDER BY t,id", (desk, *STATUS))


def _delays(st, desk):
    """(press t, truth t, pid, typo) for each status press matched to the truth event it records."""
    truth = {}
    for r in st.rows("SELECT t,type,pid FROM truth WHERE type IN ('checkin','roomed','checkout','left') ORDER BY t,id"):
        truth.setdefault((r["type"], r["pid"]), r["t"])
    out = []
    for e in _presses(st, desk):
        typo = bool(e["data"] and '"typo"' in e["data"])
        cands = [truth[(k, e["pid"])] for k in ("checkin", "roomed", "checkout", "left") if (k, e["pid"]) in truth]
        want = [truth[(k, e["pid"])] for k in _TRUTH_FOR[e["type"]] if (k, e["pid"]) in truth]
        tt = (want or cands or [None])[0]
        if tt is not None:
            out.append((e["t"], tt, e["pid"], typo))
    return out


def _queue_at(st):
    q = {}
    for r in st.rows("SELECT t,data FROM truth WHERE type='queue_snapshot'"):
        q[r["t"]] = r["data"].count('"') // 2 if r["data"] else 0
    return q


def _waits(st):
    ci, rm = truth_times(st, "checkin"), truth_times(st, "roomed")
    return {pid: rm[pid] - t for pid, t in ci.items() if pid in rm}


def _guesses(st, cfg):
    """(guess, fair headcount guess, t, pid). The fair guess is what counting the
    room honestly would give at that minute: people waiting x minutes per patient
    / providers. Comparing to it grades the persona's habit, not the day."""
    q = _queue_at(st)
    staffing = st.one("SELECT data FROM events WHERE type='staffing'")
    providers = max(1, json.loads(staffing["data"])["providers"] if staffing and staffing["data"] else cfg["clinic"]["providers"])
    per = cfg["frontdesk"]["luis"]["assumed_minutes_per_patient"]
    out = []
    for v in st.rows("SELECT t,pid,point FROM verbal_estimates WHERE actor=?", (run_info(st)["frontdesk"],)):
        fair = q.get(v["t"], 0) * per / providers
        if v["point"] is not None and fair > 0:
            out.append((v["point"], fair, v["t"], v["pid"]))
    return out


def desk_row(st, cfg):
    desk = run_info(st)["frontdesk"]
    fc = cfg["fidelity"]
    ev = Ev()
    d = _delays(st, desk)
    if not d:
        return _row(desk, "front desk", "untested", "no status presses", "")
    if desk == "dana":
        on_time = sum(1 for p, t, _, _ in d if p - t <= 1) / len(d)
        typos = sum(1 for x in d if x[3]) / len(d)
        for p, t, pid, _ in d:
            if p - t > 1:
                ev.add(f"{fmt(t)} {pid}: entered {p - t} min late", p, pid)
        ok = on_time >= fc["dana_on_time_rate"] and typos <= fc["dana_typo_max"]
        return _row(desk, "front desk", "pass" if ok else "fail", f"{on_time:.0%} within 1 min, {typos:.0%} wrong button",
                    f"≥{fc['dana_on_time_rate']:.0%} on time, ≤{fc['dana_typo_max']:.0%} wrong", ev)
    if desk == "kim":
        q = _queue_at(st)
        times = sorted({p for p, _, _, _ in d})
        quiet = []
        for a, b in zip(times, times[1:]):
            busy = [m for m in range(a, b) if q.get(m, 0) > cfg["frontdesk"]["kim"]["surge_queue"]]
            if b - a >= fc["kim_quiet_minutes"] and busy:
                quiet.append((a, b))
                ev.add(f"quiet {fmt(a)}–{fmt(b)} ({b - a} min) while the room had > {cfg['frontdesk']['kim']['surge_queue']}", a)
        verbal = st.rows("SELECT point,low FROM verbal_estimates WHERE actor='kim'")
        vague = sum(1 for v in verbal if v["point"] is None and v["low"] is None) / len(verbal) if verbal else None
        late = statistics.median(p - t for p, t, _, _ in d)
        lo, hi = fc["kim_quiet_periods"]
        ok = lo <= len(quiet) <= hi and (vague is None or vague >= fc["kim_vague_rate"]) and late >= 1
        vtxt = "n/a" if vague is None else f"{vague:.0%}"
        return _row(desk, "front desk", "pass" if ok else "fail",
                    f"{len(quiet)} quiet stretch(es) of {fc['kim_quiet_minutes']}+ min in a crowd; median entry {late:g} min late; {vtxt} vague answers",
                    f"{lo}–{hi} quiet stretches, entries late, ≥{fc['kim_vague_rate']:.0%} vague", ev)
    if desk == "luis":
        times = sorted({p for p, _, _, _ in d})
        gaps = [b - a for a, b in zip(times, times[1:])]
        med = statistics.median(gaps) if gaps else 0
        g = _guesses(st, cfg)
        ratio = statistics.median(pt / f for pt, f, _, _ in g) if g else None
        lo, hi = fc["luis_batch_gap"]
        ok = lo <= med <= hi and (ratio is None or ratio <= fc["luis_low_ratio_max"])
        rtxt = "no guesses" if ratio is None else f"guesses at {ratio:.0%} of a fair headcount guess"
        return _row(desk, "front desk", "pass" if ok else "fail", f"median {med:g} min between batches; {rtxt}",
                    f"{lo}–{hi} min batches, guesses ≤{fc['luis_low_ratio_max']:.0%} of fair", ev)
    if desk == "pat":
        g = _guesses(st, cfg)
        if not g:
            return _row(desk, "front desk", "untested", "no guesses to compare", "")
        big = sum(1 for pt, f, _, _ in g if abs(pt - f) / f > 0.25) / len(g)
        lo, hi = fc["pat_large_miss_rate"]
        # A share is a rate: with a handful of guesses either bound is noise
        # (one miss in 3 guesses is 33%), so below a minimum the habit is untested.
        if len(g) < fc["min_guesses"]:
            return _row(desk, "front desk", "untested", f"only {len(g)} guesses; {fc['min_guesses']} needed to judge a rate",
                        f"{lo:.0%}–{hi:.0%} big misses")
        ok = lo <= big <= hi
        return _row(desk, "front desk", "pass" if ok else "fail",
                    f"{big:.0%} of {len(g)} guesses more than 25% off a fair headcount guess", f"{lo:.0%}–{hi:.0%}", ev)
    return _row(desk, "front desk", "untested", "unknown persona", "")


def patient_rows(st, cfg):
    fc = cfg["fidelity"]
    pts, ci = patients(st), truth_times(st, "checkin")
    notes = {r["pid"] for r in st.rows("SELECT DISTINCT pid FROM notes")}
    msgs = st.rows("SELECT t,pid,text FROM messages WHERE direction='in' ORDER BY t,id")
    rows = []

    def arrival(pid, label, lo, hi, sign):
        if pid not in ci:
            return _row(label, "patient", "fail", "never arrived", f"{lo}–{hi} min")
        off = sign * (ci[pid] - pts[pid]["appt"])
        ok = lo <= off <= hi
        ev = Ev()
        if not ok:
            ev.add(f"{fmt(ci[pid])} {pid}: arrived {abs(ci[pid] - pts[pid]['appt'])} min "
                   f"{'early' if ci[pid] < pts[pid]['appt'] else 'late'}", ci[pid], pid)
        return _row(label, "patient", "pass" if ok else "fail",
                    f"arrived {abs(off)} min {'early' if ci[pid] < pts[pid]['appt'] else 'late' if ci[pid] > pts[pid]['appt'] else 'on time'}",
                    f"{lo}–{hi} min" + (" early" if sign < 0 else " late" if label == "ana" else ""), ev)

    rows.append(arrival("grace", "grace", *fc["grace_on_time"], 1))
    rows.append(arrival("chen", "chen", *fc["chen_early"], -1))
    rows.append(arrival("ana", "ana", *fc["ana_late"], 1))
    rows.append(_row("jordan", "patient", "pass" if "jordan" not in ci else "fail",
                     "never came" if "jordan" not in ci else f"arrived at {fmt(ci['jordan'])}", "no-show"))
    rosa = [m for m in msgs if m["pid"] == "rosa"]
    if not rosa:
        rows.append(_row("rosa", "patient", "untested", "sent no texts", "Spanish only"))
    else:
        bad = Ev()
        for m in rosa:
            if detect_language(m["text"], default="en") != "es":
                bad.add(f"{fmt(m['t'])} rosa: \"{m['text'][:60]}\"", m["t"], "rosa")
        rows.append(_row("rosa", "patient", "fail" if bad else "pass", f"{len(rosa) - len(bad)}/{len(rosa)} texts in Spanish",
                         "Spanish only", bad))
    tom = [m for m in msgs if m["pid"] == "tom"]
    if "tom" not in ci or not tom:
        rows.append(_row("tom", "patient", "untested" if "tom" not in ci else "fail", "no text sent", "chest text"))
    else:
        first = tom[0]
        after = first["t"] - ci["tom"]
        lo, hi = fc["tom_text_window"]
        ok = lo <= after <= hi and is_urgent(first["text"])
        ev = Ev()
        if not ok:
            ev.add(f"{fmt(first['t'])} tom: \"{first['text'][:60]}\" ({after} min after check-in)", first["t"], "tom")
        rows.append(_row("tom", "patient", "pass" if ok else "fail",
                         f"texted {after} min after check-in{'' if is_urgent(first['text']) else ', no urgent symptom words'}",
                         f"{lo}–{hi} min, mentions chest", ev))
    maria = [m["t"] for m in msgs if m["pid"] == "maria"]
    if len(maria) < 2:
        rows.append(_row("maria", "patient", "untested", f"{len(maria)} text(s): too few to judge timing", "every ~10 min"))
    else:
        gap = statistics.median(b - a for a, b in zip(maria, maria[1:]))
        lo, hi = fc["maria_text_gap"]
        rows.append(_row("maria", "patient", "pass" if lo <= gap <= hi else "fail", f"texted every {gap:g} min (median)",
                         f"{lo}–{hi} min"))
    for r in rows:   # every named patient who came should say what they understood
        pid = r["persona"]
        if pid in ci and pid not in notes and r["status"] == "pass":
            r["status"] = "fail"
            r["metric"] += "; no 'what I understood' note"
    return rows


def personas(st, cfg):
    return [desk_row(st, cfg)] + patient_rows(st, cfg)


def agent_health(st):
    if not st.has_table("llm_calls"):
        return None
    r = st.one("SELECT COUNT(*) n, COALESCE(SUM(ok),0) ok FROM llm_calls")
    return None if not r["n"] else {"calls": r["n"], "ok": r["ok"]}


def f1_persona_fidelity(st, cfg):
    """One scorecard row: every persona this run exercised behaves as written."""
    sc = run_info(st)["scenario"]
    rows = personas(st, cfg)
    tested = [r for r in rows if r["status"] != "untested"]
    bad = [r for r in tested if r["status"] == "fail"]
    ev = Ev()
    for r in bad:
        ref = next((x for x in r["refs"] if x), {})
        ev.add(f"{r['persona']}: {r['metric']} (wanted {r['target']})", ref.get("t"), ref.get("pid"))
    untested = [r["persona"] for r in rows if r["status"] == "untested"]
    h = agent_health(st)
    detail = (f"untested: {', '.join(untested)}" if untested else "all personas exercised")
    if h:
        detail += f"; agent calls {h['ok']}/{h['calls']} valid (the rest fell back to scripted rules)"
    return Result("F1", sc, f"{len(tested) - len(bad)}/{len(tested)} personas behave as written", "all exercised",
                  not bad, detail, ev)
