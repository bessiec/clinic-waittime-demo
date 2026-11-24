"""LLM front-desk persona (Milestone 4). An actor agent: it plays Dana, Luis,
Kim or Pat. Its only way to touch the product is the same status buttons a real
receptionist has, so the estimator can't tell it from a person.

What the agent sees each time it "looks up": the clock, the waiting room, what
it has seen happen but not yet entered, who is in a room, any system suggestions
waiting for an OK, and its own last few one-line notes. It decides what to enter
now, what to leave for later, what to forget, and which suggestions to answer.

Its habits come from its prompt (prompts/actors/<name>.md), not from code. The
persona-fidelity evals then check whether it actually behaved that way.
When a call fails, the scripted rule for that moment is used and the failure is
logged, so a run always finishes."""
from .frontdesk import Press, _TRUTH_TO_PRESS

BUTTONS = ["checkin", "roomed", "checked_out"]
_SEEN = {"checkin": "checked in at the counter", "roomed": "was called back to an exam room",
         "checkout": "finished the visit and left", "left": "left without being seen (empty seat)"}

LOOK_UP_SCHEMA = {
    "type": "object",
    "properties": {
        "enter": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "button": {"type": "string", "enum": BUTTONS}},
            "required": ["id", "button"], "additionalProperties": False}},
        "forget": {"type": "array", "items": {"type": "integer"}},
        "run_long": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"},
                           "decision": {"type": "string", "enum": ["approved", "rejected"]},
                           "reason": {"type": "string", "maxLength": 160}},
            "required": ["id", "decision", "reason"], "additionalProperties": False}},
        "note": {"type": "string", "maxLength": 200},
    },
    "required": ["enter", "forget", "run_long", "decisions", "note"],
    "additionalProperties": False,
}

HOW_LONG_SCHEMA = {
    "type": "object",
    "properties": {
        "say": {"type": "string", "maxLength": 200},
        "low": {"type": ["integer", "null"], "minimum": 0, "maximum": 600},
        "high": {"type": ["integer", "null"], "minimum": 0, "maximum": 600},
        "point": {"type": ["integer", "null"], "minimum": 0, "maximum": 600},
    },
    "required": ["say", "low", "high", "point"],
    "additionalProperties": False,
}


def hm(t):
    h, m = divmod(t, 60)
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


class LLMDesk:
    def __init__(self, scripted, cfg, caller, names):
        self.s = scripted                 # the scripted persona: name, config, fallback rules
        self.name = scripted.name
        self.cfg = cfg
        self.caller = caller
        self.names = names                # pid -> display name (what the desk sees)
        self.by_name = {v: k for k, v in names.items()}
        self.cadence = cfg["actors"]["desk_cadence"][self.name]
        self.backlog = []                 # things seen but not entered: {id, t, type, pid}
        self.next_id = 1
        self.last_look = None
        self.last_entry_t = None
        self.entries = []                 # minutes when something was entered
        self.notes = []
        self.inbox = {}                   # proposal id -> proposal waiting for an OK
        self.decisions = {}               # proposal id -> (decision, reason)
        self.run_long_pressed = set()

    # --- what the desk notices --------------------------------------------
    def observe(self, t, truth_events, world):
        for e in truth_events:
            if e["type"] in _TRUTH_TO_PRESS:
                self.backlog.append({"id": self.next_id, "t": t, "type": e["type"], "pid": e["pid"]})
                self.next_id += 1

    def observe_run_long(self, t, world):
        pass                              # the agent sees who is in a room and decides itself

    # --- looking up from the counter ----------------------------------------
    def flush(self, t, world):
        if not (self.backlog or self.inbox):
            return []
        if self.last_look is not None and t - self.last_look < self.cadence:
            return []
        self.last_look = t
        out = self.caller.call(t, self.name, "look_up", self.name, self._look_prompt(t, world), LOOK_UP_SCHEMA)
        if out is None:
            return self._fallback(t)
        presses = []
        items = {b["id"]: b for b in self.backlog}
        done = set()
        for en in out["enter"]:
            b = items.get(en["id"])
            if b is None or en["id"] in done:
                continue
            done.add(en["id"])
            typ = _TRUTH_TO_PRESS[b["type"]]
            data = {"reason": "left"} if b["type"] == "left" else None
            if en["button"] != typ:
                data = (data or {}) | {"typo": True}
            presses.append(Press(t, en["button"], b["pid"], data))
        done |= {i for i in out["forget"] if i in items}
        self.backlog = [b for b in self.backlog if b["id"] not in done]
        in_room = {p.pid for p in world.in_room()}
        for nm in out["run_long"]:
            pid = self.by_name.get(nm)
            if pid in in_room:
                erratic = None if t - world.patients[pid].roomed_at >= self.cfg["clinic"]["run_long_visit_minutes"] else {"erratic": True}
                presses.append(Press(t, "run_long", pid, erratic))
                self.run_long_pressed.add(pid)
        for d in out["decisions"]:
            if d["id"] in self.inbox and d["reason"].strip():
                self.decisions[d["id"]] = (d["decision"], d["reason"].strip())
                del self.inbox[d["id"]]
        if out["note"].strip():
            self.notes.append(f"{hm(t)}: {out['note'].strip()}")
        if presses:
            self.last_entry_t = t
            self.entries.append(t)
        return presses

    def _fallback(self, t):
        """Call failed: enter everything now, as a careful receptionist would."""
        presses = [Press(t, _TRUTH_TO_PRESS[b["type"]], b["pid"], {"reason": "left"} if b["type"] == "left" else None)
                   for b in self.backlog]
        self.backlog = []
        if presses:
            self.last_entry_t = t
            self.entries.append(t)
        return presses

    def _look_prompt(self, t, world):
        waiting = world.true_queue_len()
        rooms = world.in_room()
        since = "nothing yet today" if self.last_entry_t is None else f"{hm(self.last_entry_t)} ({t - self.last_entry_t} min ago)"
        recent = sum(1 for x in self.entries if t - x <= 30)
        gap = self.cfg["actors"]["step_away_minutes"]
        marks = self.entries + ([t] if self.entries else [])
        stepped = sum(1 for a, b in zip(marks, marks[1:]) if b - a >= gap)   # her own memory of the day
        lines = [f"Time: {hm(t)}.",
                 f"Waiting room: {waiting} people waiting. {len(rooms)} in exam rooms.",
                 f"You last entered anything in the computer: {since}. Times you entered something in the last 30 min: {recent}.",
                 f"Times today you've stepped away from the computer for {gap}+ min: {stepped}."]
        mem = self.notes[-self.cfg["actors"]["memory_notes"]:]
        if mem:
            lines.append("Your recent notes to yourself:\n" + "\n".join(f"  {n}" for n in mem))
        if self.backlog:
            lines.append("Things you've seen happen but haven't entered yet:")
            for b in self.backlog:
                lines.append(f"  #{b['id']} {self.names[b['pid']]} {_SEEN[b['type']]} ({t - b['t']} min ago)")
        else:
            lines.append("Nothing waiting to be entered.")
        if rooms:
            lines.append("In exam rooms now: " + ", ".join(f"{self.names[p.pid]} ({t - p.roomed_at} min)" for p in rooms)
                         + ". You may press \"will run long\" for any of them.")
        if self.inbox:
            lines.append("System suggestions waiting for your OK:")
            for pid_, pr in self.inbox.items():
                lines.append(f"  #{pid_} about {self.names[pr['pid']]}: {pr['reason']}")
        lines.append("\nDecide what you do right now. \"enter\": items you type in now, with the status button you "
                     "press. \"forget\": items you'll never get to. Anything not listed stays for later. "
                     "\"run_long\": names to mark. \"decisions\": suggestions you answer now (with a short reason). "
                     "\"note\": one short sentence to yourself.")
        return "\n".join(lines)

    # --- "how long?" at the counter -----------------------------------------
    def verbal(self, t, pid, view, world):
        screen = (f"The system shows walk-in wait about {view.wait_low}–{view.wait_high} min"
                  if view.status == "range" else "The system shows no wait estimate right now")
        prompt = (f"Time: {hm(t)}. {self.names[pid]} just checked in and asks how long the wait will be.\n"
                  f"Waiting room: {world.true_queue_len()} people waiting, {len(world.in_room())} in exam rooms.\n"
                  f"{screen}.\nWhat do you say? Put any number you say in point (or low and high for a range); "
                  "use null if you give no number.")
        out = self.caller.call(t, self.name, "how_long", self.name, prompt, HOW_LONG_SCHEMA)
        if out is None:
            return self.s.verbal(t, pid, view, world)
        return out["low"], out["high"], out["point"], out["say"]

    # --- system suggestions ---------------------------------------------------
    def review(self, t, proposal):
        if proposal["id"] in self.decisions:
            return self.decisions.pop(proposal["id"])
        self.inbox.setdefault(proposal["id"], proposal)
        return None
