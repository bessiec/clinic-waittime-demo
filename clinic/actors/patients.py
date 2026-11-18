"""Scripted patient personas (Milestone 1: rules in code, no LLM).
They see only the public page and their own texts, act on that, and log a
short "what I understood" note."""
import random

from ..clock import parse


def understood(view, lang):
    if view.status == "none":   # baseline run: no product, nothing to read
        return ("No hay página de espera; voy a ir y ya." if lang == "es"
                else "There's no wait-time page, so I'll just go in.")
    if view.status == "range":
        if lang == "es":
            return (f"Entendí que la espera es de {view.wait_low} a {view.wait_high} min y que "
                    f"las citas van {view.behind_low}–{view.behind_high} min atrasadas.")
        return (f"I understood the wait is about {view.wait_low}–{view.wait_high} min and scheduled "
                f"visits are {view.behind_low}–{view.behind_high} min behind.")
    if lang == "es":
        return "Entendí que la página no puede decirme la espera ahora y que debo llamar a la clínica."
    return "I understood the page can't tell me a wait right now and I should call the clinic."


class PatientActor:
    def __init__(self, patient, cfg, seed):
        self.p = patient
        self.cfg = cfg
        self.pc = cfg["patients"]
        self.rng = random.Random(f"{seed}:patient:{patient.pid}")
        self.planned = patient.planned_arrival
        self.handoff = False

    # Actions: ("arrive",), ("text", str), ("note", str), ("leave",)
    def step(self, t, view):
        acts = []
        p = self.p
        if p.checked_in_at is None and self.planned is not None and t == self.planned:
            acts.append(("arrive",))
        acts += self.patience(t)
        return acts

    def patience(self, t):
        p = self.p
        ac = self.cfg["arrivals"]
        if p.kind != "walkin" or not p.waiting or p.left_at is not None or self.handoff:
            return []
        waited = t - p.checked_in_at
        if p.told_status == "range" and p.told_high is not None:
            limit = p.told_high + ac["walkin_patience_minutes"]
        else:
            limit = ac["walkin_patience_declined_minutes"]
        return [("leave",)] if waited > limit else []

    def on_reply(self, t, kind, reply, lang):
        return None


class Grace(PatientActor):
    """Scheduled, on time. Checks minutes behind before leaving home."""
    def step(self, t, view):
        acts = super().step(t, view)
        if t == self.p.appt - self.pc["grace_check_before_minutes"]:
            acts.append(("note", "Checked the page before leaving home. " + understood(view, "en") + " Leaving now to arrive on time."))
        return acts


class Maria(PatientActor):
    """Walk-in. Checks the page at 3:30, decides come-now vs go-home-first, then
    texts 'how long now?' every 10 min while waiting."""
    def __init__(self, patient, cfg, seed):
        super().__init__(patient, cfg, seed)
        self.next_check = parse(self.pc["maria_check_time"])
        self.rechecks = 0
        self.next_text = None

    def step(self, t, view):
        acts = super().step(t, view)
        p = self.p
        if self.planned is None and t == self.next_check:
            self.rechecks += 1
            if view.status == "none":   # baseline: nothing to check, she just comes in
                self.planned = t + self.pc["maria_travel_minutes"]
                acts.append(("note", understood(view, "en")))
            elif view.status == "range" and view.wait_high <= self.pc["maria_come_now_max_minutes"]:
                self.planned = t + self.pc["maria_travel_minutes"]
                acts.append(("note", understood(view, "en") + " That's fine, I'm coming now."))
            elif self.rechecks >= self.pc["maria_max_rechecks"]:
                self.planned = t + self.pc["maria_travel_minutes"]
                acts.append(("note", understood(view, "en") + " I've checked enough times; coming in anyway."))
            else:
                self.next_check = t + self.pc["maria_recheck_minutes"]
                acts.append(("note", understood(view, "en") + " I'll do something at home first and check again."))
        if p.waiting and p.left_at is None:
            if self.next_text is None:
                self.next_text = p.checked_in_at + self.pc["maria_text_every_minutes"]
            if t == self.next_text:
                acts.append(("text", "how long now?"))
                self.next_text = t + self.pc["maria_text_every_minutes"]
        return acts

    def on_reply(self, t, kind, reply, lang):
        return "From the text: " + ("they can't say right now." if kind != "range" else "the wait is " + reply.split(".")[0].split(":")[-1].strip() + ".")


class Chen(PatientActor):
    """Scheduled, arrives 30 min early."""
    def step(self, t, view):
        acts = super().step(t, view)
        if t == self.planned:
            acts.append(("note", "Arrived early. " + understood(view, "en") + " I know my visit starts at my appointment time, not now."))
        return acts


class Ana(PatientActor):
    """Scheduled, arrives 15 min late."""
    def step(self, t, view):
        acts = super().step(t, view)
        if t == self.planned:
            acts.append(("note", "Arrived late. " + understood(view, "en") + " I expect to wait longer than my slot."))
        return acts


class Jordan(PatientActor):
    """Scheduled, never arrives."""
    pass


class Rosa(PatientActor):
    """Walk-in, writes only in Spanish."""
    def __init__(self, patient, cfg, seed):
        super().__init__(patient, cfg, seed)
        self.next_text = None

    def step(self, t, view):
        acts = super().step(t, view)
        p = self.p
        if t == self.planned:
            acts.append(("note", understood(view, "es")))
        if p.waiting and p.left_at is None:
            if self.next_text is None:
                self.next_text = p.checked_in_at + 5
            if t == self.next_text:
                acts.append(("text", "Hola, ¿cuánto falta para que me atiendan?"))
                self.next_text = t + self.pc["rosa_text_every_minutes"]
        return acts

    def on_reply(self, t, kind, reply, lang):
        if lang != "es":
            return "No entendí: me respondieron en inglés."
        return "Entendí: " + ("no pueden decir la espera ahora." if kind != "range" else reply.split(".")[0] + ".")


class Tom(PatientActor):
    """Walk-in. After 20 min texts about chest tightness."""
    def __init__(self, patient, cfg, seed):
        super().__init__(patient, cfg, seed)
        self.texted = False

    def step(self, t, view):
        acts = super().step(t, view)
        p = self.p
        if not self.texted and p.checked_in_at is not None and (
                t >= p.checked_in_at + self.pc["tom_text_after_minutes"] or p.roomed_at is not None):
            self.texted = True
            acts.append(("text", "chest feels tight, can I wait?"))
        return acts

    def on_reply(self, t, kind, reply, lang):
        if kind == "urgent":
            self.handoff = True
            return "I understood I should not just wait: call 911 or go to the ER if it's an emergency, and someone from the clinic is coming to talk to me now."
        return "I understood a wait time, which is not what I asked about."


class Filler(PatientActor):
    pass


PERSONAS = {"grace": Grace, "maria": Maria, "chen": Chen, "ana": Ana, "jordan": Jordan,
            "rosa": Rosa, "tom": Tom, "filler": Filler}
