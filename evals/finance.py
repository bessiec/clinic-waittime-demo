"""Finance dashboard numbers (SPEC "Finance dashboard"). Aggregates only,
never individual patients. compute() returns raw `counts` (which pool across
seeds by simple addition) and `derive()` turns counts into the rates shown.
Money and staff-time figures are modeled from config [finance], not measured.

E10 recomputes every count independently from the raw tables and requires an
exact match, so what this pane shows is what the logs say."""
from clinic.store import FRONTDESK_EVENT_TYPES
from .common import outcomes, patients, run_end, run_info, truth_times

COUNT_KEYS = [
    "booked", "noshows", "sched_checked_in", "late", "checked_in", "seen",
    "room_minutes", "room_capacity_minutes", "walkins", "walkaways",
    "baseline_walkins", "baseline_walkaways",
    "desk_questions", "presses", "approvals", "messages",
    "model_calls", "model_usd", "sms_usd",
    "cov_num", "cov_den", "fr_num", "fr_den", "views_open", "declined_open",
    "survey_n", "survey_accurate", "survey_satisfied", "survey_reuse",
    "inside_n", "inside_satisfied", "over_n", "over_satisfied", "declined_n", "declined_satisfied",
]


def counts(st, cfg, baseline=None):
    info, pts, end_t = run_info(st), patients(st), run_end(st)
    cc, fc = cfg["clinic"], cfg["finance"]
    ci, rm, co, lv = (truth_times(st, k) for k in ("checkin", "roomed", "checkout", "leave"))
    c = dict.fromkeys(COUNT_KEYS, 0)
    late_grace = cc["late_grace_minutes"]
    for pid, p in pts.items():
        if p["kind"] == "scheduled":
            c["booked"] += 1
            if pid not in ci:
                c["noshows"] += 1
            else:
                c["sched_checked_in"] += 1
                c["late"] += ci[pid] > p["appt"] + late_grace
        else:
            c["walkins"] += pid in ci
            c["walkaways"] += pid in lv
    c["checked_in"] = len(ci)
    c["seen"] = len(co)
    c["room_minutes"] = sum(co.get(pid, end_t) - t for pid, t in rm.items())
    c["room_capacity_minutes"] = cc["rooms"] * (end_t - info["open_t"])
    if baseline is not None:
        bp, bci, blv = patients(baseline), truth_times(baseline, "checkin"), truth_times(baseline, "leave")
        # Same minute as the product run, so a live comparison is fair.
        c["baseline_walkins"] = sum(1 for pid, t in bci.items() if bp[pid]["kind"] == "walkin" and t < end_t)
        c["baseline_walkaways"] = sum(1 for pid, t in blv.items() if bp[pid]["kind"] == "walkin" and t < end_t)
    c["desk_questions"] = st.one("SELECT COUNT(*) n FROM verbal_estimates")["n"]
    q = ",".join("?" * len(FRONTDESK_EVENT_TYPES))
    c["presses"] = st.one(f"SELECT COUNT(*) n FROM events WHERE type IN ({q})", FRONTDESK_EVENT_TYPES)["n"]
    c["approvals"] = st.one("SELECT COUNT(*) n FROM approvals")["n"]
    c["messages"] = st.one("SELECT COUNT(*) n FROM messages")["n"]
    cost = st.one("SELECT * FROM run_costs")
    if cost is not None:
        c["model_calls"], c["model_usd"], c["sms_usd"] = cost["model_calls"], cost["model_usd"], cost["sms_usd"]
    else:  # run still going: same formula the director logs at the end
        c["sms_usd"] = round(c["messages"] * fc["sms_usd_per_message"], 6)
    # Estimate reliability, same definitions as E1 (all patients) and E3 (walk-ins).
    margin = cfg["evals"]["E3_margin_minutes"]
    for o in outcomes(st, cfg):
        if not o["shown"]:
            continue
        waited = o["lower"] if o["censored"] else o["actual"]
        if not o["censored"] or waited > o["hi"]:
            c["cov_den"] += 1
            c["cov_num"] += (not o["censored"]) and o["lo"] <= waited <= o["hi"]
        if o["kind"] == "walkin":
            if waited > o["hi"] + margin:
                c["fr_num"] += 1
                c["fr_den"] += 1
            elif not o["censored"]:
                c["fr_den"] += 1
    for v in st.rows("SELECT status FROM views WHERE t>=? AND t<?", (info["open_t"], info["close_t"])):
        c["views_open"] += 1
        c["declined_open"] += v["status"] != "range"
    for s in st.rows("SELECT * FROM survey"):
        c["survey_n"] += 1
        c["survey_accurate"] += s["accurate"]
        c["survey_satisfied"] += s["satisfied"]
        c["survey_reuse"] += s["reuse"]
        g = group(s, margin)
        if g:
            c[f"{g}_n"] += 1
            c[f"{g}_satisfied"] += s["satisfied"]
    c["model_usd"] = round(c["model_usd"], 6)
    c["sms_usd"] = round(c["sms_usd"], 6)
    return c


def group(s, margin):
    """Survey groups for "does satisfaction track false reassurance?"."""
    if s["told_status"] != "range":
        return "declined"
    if s["told_low"] <= s["waited"] <= s["told_high"]:
        return "inside"
    if s["waited"] > s["told_high"] + margin:
        return "over"
    return None


def add(a, b):
    return {k: round(a.get(k, 0) + b.get(k, 0), 6) for k in COUNT_KEYS}


def _r(n, d):
    return None if not d else n / d


def derive(c, cfg, runs=1):
    """Rates and modeled figures from counts. Survey numbers are hidden (None)
    for any group smaller than [survey_min_group]."""
    fc, k = cfg["finance"], cfg["evals"]["survey_min_group"]
    staff = (c["presses"] * fc["staff_minutes_per_press"] + c["desk_questions"] * fc["staff_minutes_per_question"]
             + c["approvals"] * fc["staff_minutes_per_approval"])
    cost = c["model_usd"] + c["sms_usd"]

    def mean(total, n):
        return round(total / n, 2) if n >= k else None

    return {
        "runs": runs,
        "seen_per_run": round(c["seen"] / runs, 2),
        "room_utilization": _r(c["room_minutes"], c["room_capacity_minutes"]),
        "walkaway_rate": _r(c["walkaways"], c["walkins"]),
        "baseline_walkaway_rate": _r(c["baseline_walkaways"], c["baseline_walkins"]),
        "noshow_rate": _r(c["noshows"], c["booked"]),
        "late_rate": _r(c["late"], c["sched_checked_in"]),
        "desk_questions_per_run": round(c["desk_questions"] / runs, 2),
        "staff_minutes_per_run_modeled": round(staff / runs, 2),
        "coverage": _r(c["cov_num"], c["cov_den"]),
        "false_reassurance": _r(c["fr_num"], c["fr_den"]),
        "decline_rate": _r(c["declined_open"], c["views_open"]),
        "cost_per_day_modeled": round(cost / runs, 4),
        "cost_per_patient_modeled": round(cost / c["checked_in"], 4) if c["checked_in"] else None,
        "survey_n": c["survey_n"],
        "survey_accurate": mean(c["survey_accurate"], c["survey_n"]),
        "survey_satisfied": mean(c["survey_satisfied"], c["survey_n"]),
        "survey_reuse": mean(c["survey_reuse"], c["survey_n"]),
        "satisfied_by_group": {g: {"n": c[f"{g}_n"], "mean": mean(c[f"{g}_satisfied"], c[f"{g}_n"])}
                               for g in ("inside", "over", "declined")},
        "min_group": k,
    }


def compute(st, cfg, baseline=None):
    c = counts(st, cfg, baseline)
    return {"counts": c, "derived": derive(c, cfg)}
