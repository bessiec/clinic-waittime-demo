"""Scripted front-desk personas (Milestone 1: rules in code, no LLM).
They act only through the status buttons: check-in, roomed, checked out,
"will run long". Their verbal answers are logged separately. Each has its own
seeded random stream so a run replays exactly."""
import random
from dataclasses import dataclass

STATUS_TYPES = ("checkin", "roomed", "checked_out")
_TRUTH_TO_PRESS = {"checkin": "checkin", "roomed": "roomed", "checkout": "checked_out", "left": "checked_out"}


@dataclass
class Press:
    due_t: int
    type: str
    pid: str | None
    data: dict | None = None


class FrontDesk:
    name = "base"

    def __init__(self, cfg, seed):
        self.cfg = cfg
        self.pc = cfg["frontdesk"].get(self.name, {})
        self.rng = random.Random(f"{seed}:frontdesk:{self.name}")
        self.pending: list[Press] = []
        self.run_long_pressed: set[str] = set()

    # --- status buttons ---------------------------------------------------
    def delay(self):
        lo, hi = self.pc.get("delay", [0, 0])
        return self.rng.randint(lo, hi)

    def observe(self, t, truth_events, world):
        for e in truth_events:
            typ = _TRUTH_TO_PRESS.get(e["type"])
            if typ is None:
                continue
            data = {"reason": "left"} if e["type"] == "left" else None
            self.press(t, typ, e["pid"], data, world)

    def press(self, t, typ, pid, data, world):
        self.pending.append(Press(t + self.delay(), typ, pid, data))

    def flush(self, t, world):
        ready = [p for p in self.pending if p.due_t <= t]
        self.pending = [p for p in self.pending if p.due_t > t]
        ready.sort(key=lambda p: p.due_t)
        return ready

    def observe_run_long(self, t, world):
        limit = self.cfg["clinic"]["run_long_visit_minutes"]
        for p in world.in_room():
            if t - p.roomed_at >= limit and p.pid not in self.run_long_pressed:
                self.run_long_pressed.add(p.pid)
                self.press(t, "run_long", p.pid, None, world)

    # --- "how long?" at the desk -----------------------------------------
    def headcount(self, world, walkin_weight=1.0):
        return sum(1.0 if p.kind == "scheduled" else walkin_weight for p in world.waiting_patients())

    def verbal(self, t, pid, view, world):
        """Returns (low, high, point, text). Base: quote the system range."""
        if view.status == "range":
            lo, hi = view.wait_low, view.wait_high
            return lo, hi, (lo + hi) // 2, f"About {lo} to {hi} minutes, it says."
        return None, None, None, "I can't say right now; the page will update when we know."

    # --- approvals ----------------------------------------------------------
    def review(self, t, proposal):
        return "approved", "Late arrival, next opening is free; keeps the day moving."


class Dana(FrontDesk):
    """Accurate: records within a minute, quotes the system range, rare typo."""
    name = "dana"

    def press(self, t, typ, pid, data, world):
        if typ in STATUS_TYPES and self.rng.random() < self.pc["typo_rate"]:
            typ = self.rng.choice([s for s in STATUS_TYPES if s != typ])
            data = (data or {}) | {"typo": True}
        super().press(t, typ, pid, data, world)


class Luis(FrontDesk):
    """Best guess: batches updates, gives his own number from headcount."""
    name = "luis"

    def __init__(self, cfg, seed):
        super().__init__(cfg, seed)
        self.next_flush = None

    def flush(self, t, world):
        lo, hi = self.pc["batch_minutes"]
        if self.next_flush is None:
            self.next_flush = t + self.rng.randint(lo, hi)
        if t < self.next_flush:
            return []
        self.next_flush = t + self.rng.randint(lo, hi)
        ready = sorted(self.pending, key=lambda p: p.due_t)
        self.pending = []
        return ready

    def verbal(self, t, pid, view, world):
        providers = max(1, self.cfg["clinic"]["providers"])
        point = max(1, round(self.headcount(world, self.pc["walkin_discount"]) * self.pc["assumed_minutes_per_patient"] / providers))
        return None, None, point, f"About {point} minutes."

    def review(self, t, proposal):
        return "approved", "Fine, slot them in at the next opening."


class Kim(FrontDesk):
    """Overloaded: late or skipped updates in a surge, goes quiet for 20+ min."""
    name = "kim"

    def __init__(self, cfg, seed):
        super().__init__(cfg, seed)
        lo, hi = self.pc["blackouts_per_run"]
        self.blackouts_planned = self.rng.randint(lo, hi)
        self.blackouts_done = 0
        self.blackout_until = None

    def in_blackout(self, t):
        return self.blackout_until is not None and t < self.blackout_until

    def surge(self, world):
        return world.true_queue_len() > self.pc["surge_queue"]

    def press(self, t, typ, pid, data, world):
        if typ in STATUS_TYPES and self.surge(world) and self.rng.random() < self.pc["surge_skip_rate"]:
            return
        super().press(t, typ, pid, data, world)

    def flush(self, t, world):
        if self.in_blackout(t):
            return []
        # The minute a blackout ends she catches up first; a second blackout can start after that.
        if (self.blackout_until is None or t > self.blackout_until) and self.surge(world) \
                and self.blackouts_done < self.blackouts_planned and self.rng.random() < self.pc["blackout_start_prob"]:
            lo, hi = self.pc["blackout_minutes"]
            self.blackout_until = t + self.rng.randint(lo, hi)
            self.blackouts_done += 1
            return []
        return super().flush(t, world)

    def verbal(self, t, pid, view, world):
        return None, None, None, "Should be soon."

    def review(self, t, proposal):
        if self.in_blackout(t):
            return None
        return "approved", "OK, next opening."


class Pat(FrontDesk):
    """Erratic guesser: on time, but guesses off by 1–50% either way and uses
    'will run long' erratically."""
    name = "pat"

    def verbal(self, t, pid, view, world):
        providers = max(1, self.cfg["clinic"]["providers"])
        base = self.headcount(world) * self.pc["assumed_minutes_per_patient"] / providers
        err = min(self.pc["error_max"], max(self.pc["error_min"], self.rng.expovariate(1 / self.pc["error_mean"])))
        sign = self.rng.choice([-1, 1])
        point = max(1, round(base * (1 + sign * err)))
        return None, None, point, f"Probably {point} minutes."

    def observe_run_long(self, t, world):
        limit = self.cfg["clinic"]["run_long_visit_minutes"]
        for p in world.in_room():
            if t - p.roomed_at >= limit and p.pid not in self.run_long_pressed:
                self.run_long_pressed.add(p.pid)
                if self.rng.random() >= self.pc["missed_run_long_rate"]:
                    self.press(t, "run_long", p.pid, None, world)
        if world.in_room() and self.rng.random() < self.pc["random_run_long_rate"]:
            p = self.rng.choice(world.in_room())
            self.press(t, "run_long", p.pid, {"erratic": True}, world)

    def review(self, t, proposal):
        if self.rng.random() < self.pc["approve_rate"]:
            return "approved", "Sure, go ahead."
        return "rejected", "Rather keep them as a walk-in today."


PERSONAS = {c.name: c for c in (Dana, Luis, Kim, Pat)}
