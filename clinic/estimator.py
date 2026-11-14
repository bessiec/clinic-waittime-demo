"""Estimator: plain code, no ML. Reads only what the front desk recorded
(the events table), the appointment book, approved queue overrides and the
staffing feed. It never reads the truth table.

Method: a small queue simulation. Take the recorded waiting list in the same
order the clinic uses (clinic/queue.py), the booked appointments due soon,
the providers on duty and how long the current visits have been going, then
play forward with a typical visit length to see when a new walk-in (or a
scheduled patient due now) would be roomed. Recent actual waits calibrate the
visit length and nudge the point estimate; the range is spread around it and
widened for run-long flags and thin data."""
import math
import statistics
from dataclasses import dataclass, field

from .metrics import wait_minutes, behind_minutes
from .queue import QueueEntry, effective, order as queue_order


@dataclass
class Estimate:
    t: int
    wait_low: int
    wait_high: int
    behind_low: int
    behind_high: int
    total_low: int
    total_high: int
    confidence: float
    samples: int
    basis: dict = field(default_factory=dict)


def _round_down(x, step):
    return int(math.floor(max(0, x) / step) * step)


def _round_up(x, step):
    return int(math.ceil(max(0, x) / step) * step)


def _quartiles(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 4], xs[min(n - 1, (3 * n) // 4)]


def _simulate(entries, upcoming, free_at, mean_visit, t, cfg, probe, probe_avail=None):
    """Minutes from the probe's availability until it would be roomed.
    entries: waiting now; upcoming: [(pid, appt)] booked but not checked in;
    free_at: absolute time each provider frees up."""
    start = t if probe_avail is None else probe_avail
    pool = {e.pid: e for e in entries}
    avail = {e.pid: t for e in entries}
    for pid, appt in upcoming:
        if pid == probe.pid:
            continue
        pool[pid] = QueueEntry(pid, "scheduled", appt, max(appt, t))
        avail[pid] = max(appt, t)
    pool[probe.pid] = probe
    avail[probe.pid] = start
    free_at = sorted(free_at)
    for _ in range(len(pool) + 1):
        c = free_at[0]
        ready = [e for e in pool.values() if avail[e.pid] <= c]
        if not ready:
            c = min(avail[p] for p in pool)
            ready = [e for e in pool.values() if avail[e.pid] <= c]
        nxt = queue_order(ready, c, cfg)[0]
        if nxt == probe.pid:
            return max(0, c - start)
        del pool[nxt]
        free_at[0] = c + mean_visit
        free_at.sort()
    return max(0, free_at[0] - start)


def active_waiting(state, book, overrides, t, cfg):
    """Who the product believes is waiting, from front-desk records only.
    SPEC X6: a check-in that is never roomed is excluded. We call a patient
    'passed over' once someone who should have come after them under the queue
    rule was roomed more than a grace period ago (a later walk-in for a walk-in,
    a later appointment for an on-time scheduled patient). A long wait alone does not
    exclude anyone, except past a large backstop. Returns (waiting, excluded)."""
    ec = cfg["estimator"]
    grace = ec["passed_over_grace_minutes"]
    entries, roomed_before = [], []
    for pid, r in state.patients.items():
        kind, appt = book.get(pid, ("walkin", None))
        if r.checkin is None:
            continue
        e = QueueEntry(pid, kind, appt, r.checkin, overrides.get(pid))
        if r.roomed is None and r.checked_out is None:
            entries.append(e)
        elif r.roomed is not None and r.roomed <= t - grace:
            roomed_before.append(e)
    # Compare like with like, by booked type. Recorded times can be late
    # (batched or delayed presses), which can make an on-time scheduled
    # patient look late; so a scheduled patient being roomed never counts as
    # passing over a walk-in, and a late scheduled patient is only ever
    # excluded by the backstop.
    roomed_walkin = [e.checkin for e in roomed_before if e.kind == "walkin"]
    roomed_appt = [e.override_appt if e.override_appt is not None else e.appt
                   for e in roomed_before if e.kind == "scheduled"]
    waiting, excluded = [], []
    for e in entries:
        kind, appt = effective(e, cfg)
        if e.kind == "walkin":
            passed = any(ci > e.checkin for ci in roomed_walkin)
        elif kind == "scheduled":
            passed = any(a is not None and a > appt for a in roomed_appt)
        else:
            passed = False
        if passed or t - e.checkin > ec["abandoned_after_minutes"]:
            excluded.append(e)
        else:
            waiting.append(e)
    return waiting, excluded


def estimate(store, book: dict, t: int, cfg) -> Estimate:
    """book: pid -> (kind, appt). Only scheduled entries have an appt."""
    ec, cc = cfg["estimator"], cfg["clinic"]
    st = store.recorded_state(t)
    providers = st.providers if st.providers is not None else 0
    window = t - ec["recent_window_minutes"]
    step = ec["round_to"]
    overrides = store.overrides(t)

    walkin_waits, sched_lags, visit_lengths = [], [], []
    in_room = []
    for pid, r in st.patients.items():
        kind, appt = book.get(pid, ("walkin", None))
        if r.checkin is not None and r.roomed is not None and r.roomed >= window and r.roomed >= r.checkin:
            if kind == "walkin":
                walkin_waits.append(wait_minutes(kind, appt, r.checkin, r.roomed))
            else:
                sched_lags.append(wait_minutes(kind, appt, r.checkin, r.roomed))
        if r.roomed is not None and r.checked_out is not None and not r.left and r.checked_out >= window and r.checked_out > r.roomed:
            visit_lengths.append(r.checked_out - r.roomed)
        if r.roomed is not None and r.checked_out is None:
            in_room.append((pid, r.roomed))

    # Booked but not checked in. Optimistic view: only those due very soon.
    # Pessimistic view: everyone booked in the lookahead, plus late ones who
    # may still show up (and, by clinic practice, get re-slotted ahead).
    waiting, excluded = active_waiting(st, book, overrides, t, cfg)
    upcoming_lo, upcoming_hi = [], []
    for pid, (kind, appt) in book.items():
        if kind != "scheduled" or pid in st.patients:
            continue
        if t - ec["no_show_grace_minutes"] <= appt <= t + ec["optimistic_lookahead_minutes"]:
            upcoming_lo.append((pid, appt))
        if t - ec["late_arrival_window_minutes"] <= appt <= t + ec["booked_lookahead_minutes"]:
            upcoming_hi.append((pid, appt))
    upcoming = upcoming_hi

    samples = len(walkin_waits) + len(sched_lags)
    thin = len(visit_lengths) < ec["min_samples_today"]
    mean_visit = ec["prior_visit_minutes"] if thin else statistics.median(visit_lengths)
    run_long = any(t - rl <= ec["run_long_active_minutes"] for rl in st.run_long_times)

    basis = {
        "providers": providers, "waiting_recorded": len(waiting), "excluded_passed_over": len(excluded), "upcoming_booked": len(upcoming_hi),
        "in_room": len(in_room), "walkin_wait_samples": len(walkin_waits),
        "sched_lag_samples": len(sched_lags), "visit_samples": len(visit_lengths),
        "mean_visit": mean_visit, "run_long_active": run_long,
        "labeled_prior": samples < ec["min_samples_today"],
    }
    if providers <= 0:
        return Estimate(t, 0, 0, 0, 0, 0, 0, 0.0, samples, basis | {"reason": "no providers on duty"})

    # Provider availability from recorded in-room visits (oldest visits are closest to done).
    busy = sorted(in_room, key=lambda x: x[1])[:providers]
    free_at = [t + max(2, mean_visit - (t - roomed)) for _, roomed in busy]
    free_at += [t] * (providers - len(free_at))
    # A "will run long" flag widens only the top of the range (below). It used
    # to also delay every provider here, which counted the same flag twice.

    widen = ec["low_sample_widen_minutes"] if samples < ec["min_samples_today"] else 0
    if run_long:
        widen += ec["run_long_widen_minutes"]

    def span(probe, probe_avail=None, min_width=ec["min_range_width"]):
        lo = _simulate(waiting, upcoming_lo, free_at, mean_visit, t, cfg, probe, probe_avail)
        hi = _simulate(waiting, upcoming_hi, free_at, mean_visit, t, cfg, probe, probe_avail)
        hi = max(hi, lo)
        low = _round_down(lo * (1 - ec["spread_low"]), step)
        high = _round_up(hi * (1 + ec["spread_high"]) + widen, step)
        if high - low < min_width:
            high = low + min_width
        return lo, hi, low, high

    # --- walk-in wait ---------------------------------------------------
    sim_lo, sim_hi, wait_low, wait_high = span(QueueEntry("~probe", "walkin", None, t))
    if samples < ec["min_samples_today"] and not waiting and not upcoming_hi:
        plo, phi = ec["prior_walkin_wait"]
        wait_low, wait_high = min(wait_low, plo), max(wait_high, phi)
    expected = (sim_lo + sim_hi) / 2

    # --- minutes behind (scheduled): how late will the next booked
    # appointment be roomed, measured from its appointment time -----------
    nxt = min((a for a in upcoming_hi if a[1] >= t - ec["no_show_grace_minutes"]), key=lambda a: a[1], default=None)
    if nxt is not None:
        probe_s, start = QueueEntry(nxt[0], "scheduled", nxt[1], max(nxt[1], t)), max(nxt[1], t)
    else:
        probe_s, start = QueueEntry("~probe_s", "scheduled", t, t), t
    sim_b_lo, sim_b_hi, behind_low, behind_high = span(probe_s, start, ec["min_behind_width"])
    behind = (sim_b_lo + sim_b_hi) / 2

    # --- total visit ------------------------------------------------------
    if visit_lengths and not thin:
        q1, q3 = _quartiles(visit_lengths)
    else:
        q1, q3 = mean_visit - 5, mean_visit + 5
    total_low = wait_low + _round_down(q1, step)
    total_high = wait_high + _round_up(q3, step)

    confidence = min(1.0, 0.4 + 0.2 * min(samples, 3))
    if run_long:
        confidence *= 0.9
    if providers < cc["providers"]:
        confidence *= 0.7
    basis |= {"sim_walkin": [sim_lo, sim_hi], "sim_behind": [sim_b_lo, sim_b_hi],
              "expected_wait": round(expected, 1), "behind": round(behind, 1),
              "upcoming_optimistic": len(upcoming_lo), "upcoming_pessimistic": len(upcoming_hi)}
    return Estimate(t, wait_low, wait_high, behind_low, behind_high, total_low, total_high,
                    round(confidence, 3), samples, basis)
