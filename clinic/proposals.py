"""Scripted stand-in for the exception agent (Milestone 1: no LLM). It reads
the store, notices a scheduled patient who checked in late, and files a
proposal. It never touches the queue; only an approval does."""
from .clock import fmt


def scan(t, state, book, cfg, already: set):
    out = []
    grace = cfg["clinic"]["late_grace_minutes"]
    for pid, (kind, appt) in book.items():
        if kind != "scheduled" or pid in already:
            continue
        r = state.patients.get(pid)
        if r is None or r.checkin is None or r.roomed is not None or r.checked_out is not None:
            continue
        late = r.checkin - appt
        if late > grace:
            out.append({
                "pid": pid, "action": "reslot",
                "detail": {"effective_appt": t},
                "reason": f"Checked in {late} min after the {fmt(appt)} appointment; now waiting as a walk-in. Propose re-slotting as scheduled at the next opening.",
            })
    return out
