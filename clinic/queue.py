"""Queue ordering rule. Pure function so the G3 grader can recompute it from
the logs and compare against what actually happened.

Rule (SPEC): scheduled patients go first; walk-ins are first come, first served
in the gaps; staff can move anyone up for medical reasons (an approved
override). A late scheduled patient is treated as a walk-in until an approved
override gives them a new effective appointment time."""
from dataclasses import dataclass


@dataclass
class QueueEntry:
    pid: str
    kind: str              # "scheduled" | "walkin"
    appt: int | None
    checkin: int
    override_appt: int | None = None


def effective(e: QueueEntry, cfg):
    """("scheduled", appt) or ("walkin", None) under the queue rule."""
    cc = cfg["clinic"]
    if e.override_appt is not None:
        return "scheduled", e.override_appt
    if e.kind == "scheduled" and e.appt is not None:
        late = e.checkin > e.appt + cc["late_grace_minutes"]
        if not late:
            return "scheduled", e.appt
    return "walkin", None


def order(entries: list[QueueEntry], t: int, cfg) -> list[str]:
    lookahead = cfg["clinic"]["due_lookahead_minutes"]
    due, rest = [], []
    for e in entries:
        kind, appt = effective(e, cfg)
        if kind == "scheduled" and appt <= t + lookahead:
            due.append((appt, e.checkin, e.pid))
        else:
            rest.append((e.checkin, e.pid))
    due.sort()
    rest.sort()
    return [p for _, _, p in due] + [p for _, p in rest]
