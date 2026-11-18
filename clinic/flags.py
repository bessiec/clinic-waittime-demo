"""Rule-based data-quality flags for the staff console (Milestone 3, plain
code). They read only what the front desk recorded and never change anything:
flags only (SPEC: the data-quality agent "acts alone (flags only)"). The LLM
data-quality agent adds to these in Milestone 5."""
from .clock import fmt
from .estimator import active_waiting


def scan(store, book, t, cfg):
    """Returns a list of {kind, pid, t, text}. pid is None for clinic-wide flags."""
    fc, gc = cfg["flags"], cfg["guardrails"]
    state = store.recorded_state(t)
    out = []

    def flag(kind, pid, when, text):
        out.append({"kind": kind, "pid": pid, "t": when, "text": text})

    if state.last_event_t is not None:
        age = t - state.last_event_t
        if age >= fc["no_update_minutes"]:
            flag("no_update", None, state.last_event_t, f"No front-desk update for {age} min.")
        elif age > gc["stale_minutes"]:
            flag("stale", None, state.last_event_t,
                 f"No front-desk update for {age} min; the patient page is saying \"call the clinic\".")
    for pid, r in state.patients.items():
        if r.roomed is not None and (r.checkin is None or r.roomed < r.checkin):
            flag("roomed_before_checkin", pid, r.roomed, f"Marked roomed at {fmt(r.roomed)} before any check-in.")
        if r.checked_out is not None and r.roomed is None and not r.left:
            flag("checkout_not_roomed", pid, r.checked_out, f"Checked out at {fmt(r.checked_out)} but never marked roomed.")
        if r.checkin_count > 1:
            flag("duplicate_checkin", pid, r.checkin, f"Checked in {r.checkin_count} times.")
        if r.roomed is not None and r.checked_out is None and t - r.roomed >= fc["in_room_minutes"]:
            flag("in_room_long", pid, r.roomed, f"In a room since {fmt(r.roomed)} ({t - r.roomed} min) with no checkout recorded.")
    _, excluded = active_waiting(state, book, store.overrides(t), t, cfg)
    for e in excluded:
        flag("passed_over", e.pid, e.checkin,
             f"Checked in at {fmt(e.checkin)} but people who came later were roomed first; "
             "left out of the estimate (maybe left, or never marked roomed).")
    return out
