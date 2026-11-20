"""LLM patient personas (Milestone 4). Actor agents for the seven named
patients; filler patients stay scripted. A patient agent sees only what a real
patient would: the public wait page (verbatim) and its own text thread. It
decides when to come, whether to keep waiting, what to text, and how to answer
the post-visit survey, and it logs a short "what I understood" note.

Habits come from prompts/actors/<persona>.md. When a call fails, the scripted
persona's rule for that moment is used and the failure is logged."""
from ..clock import parse
from .llm_frontdesk import hm

NOTE = {"type": "string", "maxLength": 240}

PLAN_SCHEMA = {"type": "object", "properties": {
    "arrive_at": {"type": ["string", "null"], "maxLength": 12}, "note": NOTE},
    "required": ["arrive_at", "note"], "additionalProperties": False}
DECIDE_SCHEMA = {"type": "object", "properties": {
    "go": {"type": "string", "enum": ["now", "later"]},
    "check_again_in_minutes": {"type": ["integer", "null"], "minimum": 5, "maximum": 120}, "note": NOTE},
    "required": ["go", "check_again_in_minutes", "note"], "additionalProperties": False}
NOTE_SCHEMA = {"type": "object", "properties": {"note": NOTE}, "required": ["note"], "additionalProperties": False}
WAIT_SCHEMA = {"type": "object", "properties": {
    "do": {"type": "string", "enum": ["wait", "text", "leave"]},
    "text": {"type": ["string", "null"], "maxLength": 200}, "note": NOTE},
    "required": ["do", "text", "note"], "additionalProperties": False}
_SCORE = {"type": "integer", "minimum": 1, "maximum": 5}
SURVEY_SCHEMA = {"type": "object", "properties": {"accurate": _SCORE, "satisfied": _SCORE, "reuse": _SCORE},
                 "required": ["accurate", "satisfied", "reuse"], "additionalProperties": False}


def parse_clock(s, near):
    """'2:50 PM', '14:50' or '2:50' -> minutes, the reading closest to `near`."""
    if not s:
        return None
    s = s.strip().upper().replace(".", "")
    pm, am = s.endswith("PM"), s.endswith("AM")
    s = s.replace("PM", "").replace("AM", "").strip()
    try:
        h, m = (int(x) for x in s.split(":"))
    except ValueError:
        return None
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    if not (pm or am):
        return min((h * 60 + m, (h + 12) * 60 + m), key=lambda x: abs(x - near))
    return h * 60 + m


class LLMPatient:
    def __init__(self, scripted, cfg, caller, store):
        self.s = scripted
        self.p = scripted.p
        self.cfg = cfg
        self.caller = caller
        self.store = store
        self.pc = cfg["patients"]
        self.ac = cfg["actors"]
        self.persona = self.p.persona
        self.notes = []
        self.handoff = False
        self.planned = scripted.planned if self.p.kind == "walkin" else None
        self.plan_t = None
        if self.p.kind == "scheduled":
            self.plan_t = max(parse(cfg["clinic"]["open"]), self.p.appt - self.ac["scheduled_plan_lead_minutes"])
        self.next_check = None
        self.rechecks = 0
        if self.persona == "maria":
            self.planned = None
            self.next_check = scripted.next_check

    # helpers ------------------------------------------------------------
    def _page(self, view):
        if view.status == "none":
            return "The clinic has no wait-time page."
        return f"The clinic's wait page says: \"{view.text[self.p.lang]}\""

    def _memory(self):
        mem = self.notes[-self.ac["memory_notes"]:]
        return ("\nYour earlier notes:\n" + "\n".join(f"  {n}" for n in mem)) if mem else ""

    def _call(self, t, role, prompt, schema):
        return self.caller.call(t, self.persona, role, self.persona, prompt, schema)

    def _note(self, t, text):
        text = (text or "").strip()
        if text:
            self.notes.append(f"{hm(t)}: {text}")
        return text

    # the step the director calls every minute -----------------------------
    def step(self, t, view):
        acts = []
        p = self.p
        if self.plan_t is not None and t == self.plan_t:
            acts += self._plan(t, view)
        if self.persona == "maria" and self.planned is None and t == self.next_check:
            acts += self._decide(t, view)
        if p.checked_in_at is None and self.planned is not None and t == self.planned:
            acts.append(("arrive",))
            if p.kind == "walkin":
                out = self._call(t, "arrive", f"Time: {hm(t)}. You just checked in at the counter.\n{self._page(view)}\n"
                                 "Write what you understood about your wait (one sentence).", NOTE_SCHEMA)
                if out and self._note(t, out["note"]):
                    acts.append(("note", out["note"].strip()))
        if (p.kind == "walkin" and p.waiting and p.left_at is None and not self.handoff
                and t > p.checked_in_at and (t - p.checked_in_at) % self.ac["patient_wait_tick_minutes"] == 0):
            acts += self._wait(t, view)
        return acts

    def _plan(self, t, view):
        prompt = (f"Time: {hm(t)}. You are at home. Your appointment is at {hm(self.p.appt)}.\n{self._page(view)}\n"
                  "When will you arrive at the clinic? Give arrive_at as a clock time like \"2:05 PM\", or null if you "
                  "are not going. Note: what you understood from the page, in one sentence.")
        out = self._call(t, "plan", prompt, PLAN_SCHEMA)
        if out is None:
            self.planned = self.s.planned
            return []
        at = parse_clock(out["arrive_at"], self.p.appt) if out["arrive_at"] else None
        self.planned = None if at is None else max(t + 1, at)
        return [("note", n) for n in [self._note(t, out["note"])] if n]

    def _decide(self, t, view):
        self.rechecks += 1
        last = self.rechecks >= self.pc["maria_max_rechecks"]
        prompt = (f"Time: {hm(t)}. You are at home and need to be seen today without an appointment. It takes about "
                  f"{self.pc['maria_travel_minutes']} minutes to get there.\n{self._page(view)}{self._memory()}\n"
                  + ("This is the last time you'll check; you're going today either way.\n" if last else "")
                  + "Do you go now or later? If later, when will you check again (minutes)? Note: what you understood.")
        out = self._call(t, "decide", prompt, DECIDE_SCHEMA)
        acts = []
        if out is None:
            self.planned, self.next_check = self.s.planned, self.s.next_check
            return acts
        if n := self._note(t, out["note"]):
            acts.append(("note", n))
        if out["go"] == "now" or last:
            self.planned = t + self.pc["maria_travel_minutes"]
        else:
            self.next_check = t + (out["check_again_in_minutes"] or self.pc["maria_recheck_minutes"])
        return acts

    def _wait(self, t, view):
        p = self.p
        thread = self.store.thread(p.pid)[-6:]
        convo = "\n".join(f"  {hm(m['t'])} {'you' if m['direction'] == 'in' else 'clinic'}: {m['text']}" for m in thread)
        told = (f"When you checked in the page said a walk-in wait of about {p.told_low}–{p.told_high} min."
                if p.told_status == "range" else "When you checked in the page gave no wait time.")
        prompt = (f"Time: {hm(t)}. You checked in at {hm(p.checked_in_at)} and have been waiting {t - p.checked_in_at} min.\n"
                  f"{told}\n{self._page(view)}\n"
                  + (f"Your texts with the clinic:\n{convo}\n" if convo else "")
                  + self._memory()
                  + "\nWhat do you do now: keep waiting, send the clinic a text (write it), or leave? "
                    "Note: one sentence on how you feel or what you understood.")
        out = self._call(t, "wait", prompt, WAIT_SCHEMA)
        if out is None:
            return self.s.patience(t)
        acts = []
        if n := self._note(t, out["note"]):
            acts.append(("note", n))
        if out["do"] == "text" and (out["text"] or "").strip():
            acts.append(("text", out["text"].strip()))
        elif out["do"] == "leave":
            acts.append(("leave",))
        return acts

    def on_reply(self, t, kind, reply, lang):
        if kind == "urgent":
            self.handoff = True       # a person takes over; the agent stops deciding (plain code, as scripted)
        out = self._call(t, "reply", f"Time: {hm(t)}. The clinic texted you back: \"{reply}\"\n"
                         "What did you understand from it? One sentence.", NOTE_SCHEMA)
        if out is None:
            return self.s.on_reply(t, kind, reply, lang)
        return self._note(t, out["note"]) or None

    def survey(self, t, told_status, lo, hi, waited, completed):
        told = (f"the page said about {lo}–{hi} min" if told_status == "range"
                else "the page gave no wait time" if told_status == "declined" else "there was no wait page")
        prompt = (f"Your visit is over. When you checked in, {told}. You actually waited {waited} min"
                  f"{' and were seen' if completed else ' and left without being seen'}.\n"
                  "Short survey, 1 (worst) to 5 (best): accurate = was the wait estimate accurate? "
                  "satisfied = how satisfied were you with the wait? reuse = would you use this again?")
        out = self._call(t, "survey", prompt, SURVEY_SCHEMA)
        return None if out is None else (out["accurate"], out["satisfied"], out["reuse"])
