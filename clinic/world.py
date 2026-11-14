"""Ground truth of the simulated clinic: who is really waiting, in a room, or
gone. Only the scenario director writes here and only the evals read it.
The estimator sees none of this; it sees what the front desk recorded."""
from dataclasses import dataclass

from .queue import QueueEntry, order as queue_order


@dataclass
class Patient:
    pid: str
    name: str
    kind: str                     # "scheduled" | "walkin"
    appt: int | None
    lang: str
    persona: str
    visit_length: int
    planned_arrival: int | None   # None = never arrives (or decides later)
    asks_desk: bool = False
    checked_in_at: int | None = None
    roomed_at: int | None = None
    visit_end_at: int | None = None
    checked_out_at: int | None = None
    left_at: int | None = None
    left_discovered: bool = False
    provider: int | None = None
    told_status: str | None = None   # what the page showed at check-in
    told_high: int | None = None

    @property
    def waiting(self):
        return self.checked_in_at is not None and self.roomed_at is None and not self.left_discovered

    @property
    def in_room(self):
        return self.roomed_at is not None and self.checked_out_at is None


class World:
    def __init__(self, cfg, patients: dict[str, Patient], open_t: int, providers: int | None = None):
        self.cfg = cfg
        self.patients = patients
        cc = cfg["clinic"]
        self.providers = cc["providers"] if providers is None else providers
        self.providers_free_at = [open_t] * self.providers
        self.rooms = cc["rooms"]
        self.overrides: dict[str, int] = {}   # pid -> effective appointment time (approved only)

    def waiting_patients(self):
        return [p for p in self.patients.values() if p.waiting]

    def true_queue_len(self):
        return sum(1 for p in self.patients.values() if p.waiting and p.left_at is None)

    def in_room(self):
        return [p for p in self.patients.values() if p.in_room]

    def entries(self):
        return [QueueEntry(p.pid, p.kind, p.appt, p.checked_in_at, self.overrides.get(p.pid))
                for p in self.waiting_patients()]

    def order(self, t):
        return queue_order(self.entries(), t, self.cfg)

    def advance(self, t):
        ev = []
        for p in self.in_room():
            if p.visit_end_at <= t:
                p.checked_out_at = t
                ev.append({"type": "checkout", "pid": p.pid})
        free = [i for i, f in enumerate(self.providers_free_at) if f <= t]
        while free and len(self.in_room()) < self.rooms:
            roomed_one = False
            for pid in self.order(t):
                p = self.patients[pid]
                if p.left_at is not None:
                    p.left_discovered = True
                    ev.append({"type": "left", "pid": pid})
                    continue
                i = free.pop(0)
                p.roomed_at, p.provider = t, i
                p.visit_end_at = t + p.visit_length
                self.providers_free_at[i] = p.visit_end_at
                ev.append({"type": "roomed", "pid": pid, "data": {"provider": i}})
                roomed_one = True
                break
            if not roomed_one:
                break
        return ev

    def checkin(self, t, pid):
        p = self.patients[pid]
        p.checked_in_at = t
        return {"type": "checkin", "pid": pid, "data": {"kind": p.kind, "appt": p.appt}}

    def leave(self, t, pid):
        self.patients[pid].left_at = t
        return {"type": "leave", "pid": pid}

    def all_settled(self):
        return all(p.checked_out_at is not None or p.left_discovered or p.checked_in_at is None
                   for p in self.patients.values()) and not self.in_room()
