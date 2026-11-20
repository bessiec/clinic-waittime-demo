"""Scored evals E1–E5 and E9. Each returns this seed's Result plus a `pool`
of raw counts so the runner can pool patients across seeds."""
import statistics

from clinic.clock import fmt
from clinic.estimator import active_waiting
from clinic.metrics import wait_minutes
from .common import Ev, Result, data_age, frontdesk_event_times, outcomes, patients, run_info, truth_times


def _pct(n, d):
    return n / d if d else 0.0


def e1_coverage(st, cfg):
    """Actual wait inside the range shown at check-in. Walk-ins: wait range.
    Scheduled: wait from appointment time (or check-in if later) inside the
    'running X–Y min behind' range. A censored patient whose known wait
    already passes the top of the range counts as a miss; one whose known
    wait is still inside is unknown and left out."""
    sc = run_info(st)["scenario"]
    thr = cfg["evals"]["E1_coverage"]
    n = {"walkin": [0, 0], "scheduled": [0, 0]}
    declined, unknown, ev = 0, 0, Ev()
    for o in outcomes(st, cfg):
        if not o["shown"]:
            declined += 1
            continue
        if o["censored"]:
            if o["lower"] > o["hi"]:
                n[o["kind"]][1] += 1
                ev.add(f"{fmt(o['checkin'])} {o['pid']} ({o['kind']}): told {o['lo']}–{o['hi']}, "
                          f"{'walked away' if o['left'] else 'still waiting'} after {o['lower']}", o["checkin"], o["pid"])
            else:
                unknown += 1
            continue
        n[o["kind"]][1] += 1
        if o["lo"] <= o["actual"] <= o["hi"]:
            n[o["kind"]][0] += 1
        else:
            ev.add(f"{fmt(o['checkin'])} {o['pid']} ({o['kind']}): told {o['lo']}–{o['hi']}, actual {o['actual']}", o["checkin"], o["pid"])
    num, den = n["walkin"][0] + n["scheduled"][0], n["walkin"][1] + n["scheduled"][1]
    score = _pct(num, den)
    detail = (f"walk-ins {n['walkin'][0]}/{n['walkin'][1]}, scheduled {n['scheduled'][0]}/{n['scheduled'][1]}, "
              f"{declined} saw a decline, {unknown} censored inside range (excluded)")
    return Result("E1", sc, f"{score:.0%} ({num}/{den})", f"≥ {thr:.0%}", score >= thr, detail, ev,
                  value=score, pool={"num": num, "den": den, "walkin": n["walkin"], "scheduled": n["scheduled"]})


def e2_width(st, cfg, report_only=False):
    """Median width of the walk-in range shown at check-in."""
    sc = run_info(st)["scenario"]
    ec = cfg["evals"]
    thr, cut = ec["E2_median_width_minutes"], ec["E2_short_wait_minutes"]
    widths, short, long_, wide = [], [], [], []
    for o in outcomes(st, cfg):
        if o["kind"] != "walkin" or not o["shown"]:
            continue
        w = o["hi"] - o["lo"]
        widths.append(w)
        (short if o["basis"].get("expected_wait", 0) <= cut else long_).append(w)
        if w > thr:
            wide.append((w, o))
    med = statistics.median(widths) if widths else 0
    passed = None if report_only else (bool(widths) and med <= thr)
    ev = Ev()
    if passed is False:   # the ranges wider than the threshold, widest first, so a failure links to patients
        for w, o in sorted(wide, key=lambda x: -x[0]):
            ev.add(f"{fmt(o['checkin'])} {o['pid']}: shown {o['lo']}–{o['hi']} ({w} min wide)", o["checkin"], o["pid"])
    return Result("E2", sc, f"median {med:g} min ({len(widths)} ranges)", f"≤ {thr} min" + (" (report only)" if report_only else ""),
                  passed, "", ev, value=med, pool={"values": widths, "short": short, "long": long_})


def e3_false_reassurance(st, cfg):
    """Walk-ins whose wait passed the top of the range by more than the margin.
    Walkaways and patients still waiting count once their known wait passes it."""
    sc = run_info(st)["scenario"]
    ec = cfg["evals"]
    margin, thr = ec["E3_margin_minutes"], ec["E3_max_rate"]
    num = den = unknown = 0
    ev = Ev()
    for o in outcomes(st, cfg):
        if o["kind"] != "walkin" or not o["shown"]:
            continue
        waited = o["lower"] if o["censored"] else o["actual"]
        if waited > o["hi"] + margin:
            den += 1
            num += 1
            how = ("walked away after" if o["left"] else "still waiting after") if o["censored"] else "waited"
            ev.add(f"{fmt(o['checkin'])} {o['pid']}: told {o['lo']}–{o['hi']}, {how} {waited}", o["checkin"], o["pid"])
        elif o["censored"]:
            unknown += 1
        else:
            den += 1
    rate = _pct(num, den)
    return Result("E3", sc, f"{rate:.0%} ({num}/{den})", f"≤ {thr:.0%}", rate <= thr,
                  f"{unknown} censored before the margin (excluded)", ev, value=rate, pool={"num": num, "den": den})


def e4_declines(st, cfg):
    """Correct declines both ways, judged against the truth table. The page
    should decline when no front-desk event for longer than the stale limit,
    or when the queue the product believes in (same function the estimator
    uses) differs from the true queue by more than E4_queue_mismatch people.
    Scored over every minute the clinic is open."""
    sc = run_info(st)["scenario"]
    info = run_info(st)
    ec, stale = cfg["evals"], cfg["guardrails"]["stale_minutes"]
    mismatch, thr = ec["E4_queue_mismatch"], ec["E4_decline_accuracy"]
    t_ci, t_rm, t_lv = truth_times(st, "checkin"), truth_times(st, "roomed"), truth_times(st, "leave")
    book = {pid: (p["kind"], p["appt"]) for pid, p in patients(st).items()}
    times = frontdesk_event_times(st)
    big = 10 ** 9
    should = [0, 0]      # [declined, total] when the page should decline
    shouldnt = [0, 0]    # [showed range, total] when it should not
    ev = Ev()
    for v in st.rows("SELECT t,status FROM views WHERE t>=? AND t<? ORDER BY t", (info["open_t"], info["close_t"])):
        t = v["t"]
        true_q = sum(1 for p, c in t_ci.items() if c <= t and t_rm.get(p, big) > t and t_lv.get(p, big) > t)
        rec_q = len(active_waiting(st.recorded_state(t), book, st.overrides(t), t, cfg)[0])
        age = data_age(times, t)
        must = age is None or age > stale or abs(rec_q - true_q) > mismatch
        declined = v["status"] != "range"
        if must:
            should[1] += 1
            should[0] += declined
            if not declined and len(ev) < 20:
                ev.add(f"{fmt(t)} showed a range; recorded queue {rec_q} vs true {true_q}, data {age} min old", t)
        else:
            shouldnt[1] += 1
            shouldnt[0] += not declined
            if declined and len(ev) < 20:
                ev.add(f"{fmt(t)} declined without cause; recorded {rec_q} vs true {true_q}, data {age} min old", t)
    a, b = _pct(*should) if should[1] else None, _pct(*shouldnt) if shouldnt[1] else None
    ok = (a is None or a >= thr) and (b is None or b >= thr)
    fa = "n/a" if a is None else f"{a:.0%}"
    fb = "n/a" if b is None else f"{b:.0%}"
    return Result("E4", sc, f"declined when it should {fa} ({should[0]}/{should[1]}), shown when it should {fb} ({shouldnt[0]}/{shouldnt[1]})",
                  f"≥ {thr:.0%} both ways", ok, "", ev, value=min(x for x in (a, b) if x is not None) if (a is not None or b is not None) else None,
                  pool={"should": should, "shouldnt": shouldnt})


def e5_beats_staff(st, cfg):
    """System range vs the desk's spoken guess, on absolute error, for walk-ins
    who asked at the desk and were roomed. System error uses the range
    midpoint so both sides are judged as a single point. Ties count as not better."""
    sc = run_info(st)["scenario"]
    thr = cfg["evals"]["E5_beat_rate"]
    outs = {o["pid"]: o for o in outcomes(st, cfg)}
    wins = n = declined = censored = 0
    ev = Ev()
    for r in st.rows("SELECT t,pid,actor,point FROM verbal_estimates WHERE point IS NOT NULL ORDER BY id"):
        o = outs.get(r["pid"])
        if o is None or o["kind"] != "walkin":
            continue
        if not o["shown"]:
            declined += 1
            continue
        if o["censored"]:
            censored += 1
            continue
        mid = (o["lo"] + o["hi"]) / 2
        sys_err, staff_err = abs(mid - o["actual"]), abs(r["point"] - o["actual"])
        n += 1
        if sys_err < staff_err:
            wins += 1
        else:
            ev.add(f"{fmt(r['t'])} {r['pid']}: system {o['lo']}–{o['hi']} (err {sys_err:g}), "
                      f"{r['actor']} said {r['point']} (err {staff_err}), actual {o['actual']}", r["t"], r["pid"])
    rate = _pct(wins, n)
    return Result("E5", sc, f"{rate:.0%} ({wins}/{n})", f"≥ {thr:.0%}", rate >= thr,
                  f"{declined} asked while page declined, {censored} never roomed (excluded)", ev,
                  value=rate if n else None, pool={"num": wins, "den": n})


def e9_chen_clock(st, cfg):
    """Mr. Chen: wait clock starts at appointment time, not arrival."""
    sc = run_info(st)["scenario"]
    p = patients(st).get("chen")
    ci, rm = truth_times(st, "checkin").get("chen"), truth_times(st, "roomed").get("chen")
    if p is None or ci is None or rm is None:
        return Result("E9", sc, "not exercised", "exact", False, "Mr. Chen did not check in and get roomed")
    ev = Ev()
    if ci >= p["appt"]:
        ev.add(f"Chen checked in at {fmt(ci)}, not early for {fmt(p['appt'])}", ci, "chen")
    measured = wait_minutes(p["kind"], p["appt"], ci, rm)
    expected = max(0, rm - p["appt"])
    from_arrival = rm - ci
    if measured != expected:
        ev.add(f"measured wait {measured} != roomed - appt = {expected}", rm, "chen")
    rec = st.recorded_state(10 ** 6).patients.get("chen")
    if rec and rec.checkin is not None and rec.roomed is not None:
        rec_measured = wait_minutes(p["kind"], p["appt"], rec.checkin, rec.roomed)
        if rec_measured != max(0, rec.roomed - p["appt"]):
            ev.add(f"estimator sample uses arrival clock: {rec_measured}", rm, "chen")
    return Result("E9", sc, f"wait {measured} min from appt (would be {from_arrival} from arrival)", "exact", not ev,
                  f"appt {fmt(p['appt'])}, arrived {fmt(ci)}, roomed {fmt(rm)}", ev)


def e10_finance_reconciles(st, cfg, shown=None, baseline=None):
    """Finance totals reconcile with the logs, exactly. Every count on the
    finance pane is recomputed here with separate code straight from the raw
    tables (reliability counts come from the E1 and E3 graders themselves),
    then compared with what the pane shows (`shown`, the served JSON) or, in
    the suite, with what evals/finance.py would show. Also checks the run's
    cost log against the message log and that no survey group under the
    minimum size is shown."""
    import json as _json
    from clinic.store import FRONTDESK_EVENT_TYPES as _FD
    from . import finance
    sc = run_info(st)["scenario"]
    info = run_info(st)
    fc, cc = cfg["finance"], cfg["clinic"]
    if shown is None:
        shown = finance.compute(st, cfg, baseline)
    first = {}
    for r in st.rows("SELECT t,type,pid FROM truth WHERE pid IS NOT NULL ORDER BY id"):
        first.setdefault((r["type"], r["pid"]), r["t"])
    last_truth = max([r["t"] for r in st.rows("SELECT t FROM truth")] or [info["open_t"] - 1])
    end_t = info["end_t"] if info["end_t"] is not None else last_truth + 1
    pts = {r["pid"]: r for r in st.rows("SELECT * FROM patients")}
    exp = dict.fromkeys(finance.COUNT_KEYS, 0)
    for pid, p in pts.items():
        came = ("checkin", pid) in first
        exp["checked_in"] += came
        exp["seen"] += ("checkout", pid) in first
        if ("roomed", pid) in first:
            exp["room_minutes"] += first.get(("checkout", pid), end_t) - first[("roomed", pid)]
        if p["kind"] == "scheduled":
            exp["booked"] += 1
            exp["noshows"] += not came
            exp["sched_checked_in"] += came
            exp["late"] += came and first[("checkin", pid)] - p["appt"] > cc["late_grace_minutes"]
        else:
            exp["walkins"] += came
            exp["walkaways"] += ("leave", pid) in first
    exp["room_capacity_minutes"] = cc["rooms"] * (end_t - info["open_t"])
    if baseline is not None:
        bpts = {r["pid"]: r["kind"] for r in baseline.rows("SELECT pid,kind FROM patients")}
        seen_b = set()
        for r in baseline.rows("SELECT t,type,pid FROM truth WHERE type IN ('checkin','leave') ORDER BY id"):
            if (r["type"], r["pid"]) in seen_b or bpts.get(r["pid"]) != "walkin" or r["t"] >= end_t:
                continue
            seen_b.add((r["type"], r["pid"]))
            exp["baseline_walkins" if r["type"] == "checkin" else "baseline_walkaways"] += 1
    exp["desk_questions"] = len(st.rows("SELECT id FROM verbal_estimates"))
    exp["presses"] = sum(1 for r in st.rows("SELECT type FROM events") if r["type"] in _FD)
    exp["approvals"] = len(st.rows("SELECT id FROM approvals"))
    exp["messages"] = len(st.rows("SELECT id FROM messages"))
    exp["sms_usd"] = round(exp["messages"] * fc["sms_usd_per_message"], 6)
    e1, e3 = e1_coverage(st, cfg).pool, e3_false_reassurance(st, cfg).pool
    exp["cov_num"], exp["cov_den"], exp["fr_num"], exp["fr_den"] = e1["num"], e1["den"], e3["num"], e3["den"]
    for v in st.rows("SELECT t,status FROM views"):
        if info["open_t"] <= v["t"] < info["close_t"]:
            exp["views_open"] += 1
            exp["declined_open"] += v["status"] != "range"
    margin = cfg["evals"]["E3_margin_minutes"]
    for s in st.rows("SELECT * FROM survey"):
        exp["survey_n"] += 1
        for k in ("accurate", "satisfied", "reuse"):
            exp[f"survey_{k}"] += s[k]
        if s["told_status"] != "range":
            g = "declined"
        elif s["told_low"] <= s["waited"] <= s["told_high"]:
            g = "inside"
        elif s["waited"] > s["told_high"] + margin:
            g = "over"
        else:
            g = None
        if g:
            exp[f"{g}_n"] += 1
            exp[f"{g}_satisfied"] += s["satisfied"]

    ev = Ev()
    cost = st.one("SELECT * FROM run_costs")
    if cost is not None:
        if cost["sms_count"] != exp["messages"]:
            ev.add(f"cost log says {cost['sms_count']} texts, message log has {exp['messages']}")
        if abs(cost["sms_usd"] - exp["sms_usd"]) > 1e-9:
            ev.add(f"cost log SMS ${cost['sms_usd']} != {exp['messages']} x ${fc['sms_usd_per_message']}")
        exp["model_calls"], exp["model_usd"] = cost["model_calls"], cost["model_usd"]
        n_calls = st.one("SELECT COUNT(*) n FROM llm_calls WHERE source != 'skipped'")["n"] if st.has_table("llm_calls") else 0
        if cost["model_calls"] != n_calls:
            ev.add(f"cost log says {cost['model_calls']} model calls, agent-call log has {n_calls}")
        if cost["model_usd"] != 0:
            ev.add(f"cost log bills ${cost['model_usd']} for model calls; agents run on the subscription ($0)")
    if st.has_table("llm_calls"):
        exp["model_usd_modeled"] = round(sum(r["modeled_usd"] or 0 for r in st.rows("SELECT modeled_usd FROM llm_calls")), 6)
    got = shown["counts"]
    for k in finance.COUNT_KEYS:
        if abs((got.get(k) or 0) - exp[k]) > 1e-9:
            ev.add(f"{k}: pane shows {got.get(k)}, logs say {exp[k]}")
    want = _json.loads(_json.dumps(finance.derive(exp, cfg)))
    have = _json.loads(_json.dumps(shown["derived"]))
    for k, v in want.items():
        if have.get(k) != v:
            ev.add(f"{k}: pane shows {have.get(k)}, logs give {v}")
    k = cfg["evals"]["survey_min_group"]
    for name in ("survey_accurate", "survey_satisfied", "survey_reuse"):
        if have.get(name) is not None and have["survey_n"] < k:
            ev.add(f"{name} shown for a group of {have['survey_n']} (< {k})")
    for g, d in (have.get("satisfied_by_group") or {}).items():
        if d["mean"] is not None and d["n"] < k:
            ev.add(f"survey group '{g}' shown with {d['n']} answers (< {k})")
    n = len(finance.COUNT_KEYS) + len(want)
    return Result("E10", sc, f"{n - len(ev)}/{n} figures match the logs", "exact match", not ev,
                  f"{exp['checked_in']} patients, {exp['messages']} texts, {exp['survey_n']} survey answers", ev)
