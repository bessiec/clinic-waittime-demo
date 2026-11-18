"""Scripted post-visit survey (Milestone 3: a formula, no LLM; LLM patient
agents answer in Milestone 4). Three 1–5 answers per patient who checked in:
  accurate  "Was the wait estimate accurate?"
  satisfied "How satisfied were you with the wait?"
  reuse     "Would you use this again?"
Answers come from what the page showed at check-in vs what happened, plus a
set rate of noise (SPEC). Stored anonymously: no patient id.

Honesty note: because this is a formula, any relationship the finance pane
shows between satisfaction and false reassurance is built in here, not
discovered. It shows the dashboard works, not how real patients feel."""
import math
import random

from .metrics import wait_minutes


def _clamp(x):
    return max(1, min(5, int(round(x))))


def score(told_status, lo, hi, waited, completed, cfg):
    """Noise-free answers. `waited` is the actual wait (or how long they had
    waited when they walked away). Returns (accurate, satisfied, reuse)."""
    sc = cfg["survey"]
    overrun = 0
    if told_status != "range" or lo is None:
        accurate = sc["declined_accuracy"]           # page said "call the clinic": neutral
    elif lo <= waited <= hi:
        accurate = 5
    elif waited > hi:
        overrun = waited - hi
        accurate = 5 - math.ceil(overrun / sc["accuracy_step_minutes"])
    else:  # waited less than told: a pleasant miss, half the penalty
        accurate = 5 - math.ceil((lo - waited) / (2 * sc["accuracy_step_minutes"]))
    satisfied = 5 - waited / sc["satisfaction_wait_step_minutes"] - overrun / sc["satisfaction_overrun_step_minutes"]
    if not completed:
        satisfied -= sc["walkaway_penalty"]
    accurate, satisfied = _clamp(accurate), _clamp(satisfied)
    reuse = _clamp((2 * accurate + satisfied) / 3)
    return accurate, satisfied, reuse


class Survey:
    def __init__(self, cfg, seed):
        self.cfg = cfg
        self.rng = random.Random(f"{seed}:survey")

    def answer(self, t, p, view_row, store, completed, end_t):
        """p: world Patient; view_row: the views row at check-in (or None).
        completed: roomed and seen; otherwise they walked away at end_t."""
        if p.checked_in_at is None:
            return None
        if completed:
            waited = wait_minutes(p.kind, p.appt, p.checked_in_at, p.roomed_at)
        else:
            start = max(p.appt, p.checked_in_at) if p.kind == "scheduled" and p.appt is not None else p.checked_in_at
            waited = max(0, end_t - start)
        status = view_row["status"] if view_row else None
        if status == "range":
            lo, hi = ((view_row["wait_low"], view_row["wait_high"]) if p.kind == "walkin"
                      else (view_row["behind_low"], view_row["behind_high"]))
        else:
            lo = hi = None
        ans = list(score(status, lo, hi, waited, completed, self.cfg))
        noisy = False
        for i in range(3):
            if self.rng.random() < self.cfg["survey"]["noise_rate"]:
                ans[i] = self.rng.randint(1, 5)
                noisy = True
        store.add_survey(t, p.kind, status, lo, hi, waited, completed, *ans, noisy)
        return ans
