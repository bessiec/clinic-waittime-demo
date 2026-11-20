"""Scenario director: simulated clock, appointment book, staffing, injected
events, fixed seed. Drives one tick per minute through the fixed pipeline:
world -> front desk -> store -> estimator -> guardrails -> patients.
run() plays a whole day; start()/step()/finish() let the web UI play it a
minute at a time. Both paths run the identical tick."""
import random
from pathlib import Path

from . import proposals, texting
from .actors.frontdesk import PERSONAS as DESK
from .actors.patients import PERSONAS as PATIENTS
from .actors.llm import make_caller
from .actors.llm_frontdesk import LLMDesk
from .actors.llm_patients import LLMPatient
from .clock import parse
from .config import ROOT
from .estimator import estimate
from .guardrails import PublicView, guard, make_blocklist
from .survey import Survey
from .scenarios import SCENARIOS
from .store import Store
from .world import Patient, World

FILLER_NAMES = [
    "Olivia", "Noah", "Amelia", "Liam", "Sophia", "Ethan", "Isabella", "Mason",
    "Mia", "Logan", "Harper", "Lucas", "Evelyn", "Jackson", "Abigail", "Aiden",
    "Emily", "Carter", "Ella", "Owen", "Scarlett", "Wyatt", "Victoria", "Leo",
    "Aria", "Julian", "Chloe", "Levi", "Penelope", "Hudson", "Layla", "Isaac",
    "Zoey", "Gabriel", "Nora", "Mateo", "Lily", "Elias", "Hannah", "Ezra",
]


class Director:
    def __init__(self, scenario: str, seed: int, cfg: dict, db_path: str | None = None,
                 product: bool = True, approver: str = "persona", human_script: list | None = None,
                 actors: str | None = None, replay_from: str | None = None):
        """product=False is the "without the product" baseline for the finance
        pane: same people, same day, but no public page, no texts, no estimator
        and no exception agent. approver="human" leaves proposals for the person
        running the demo; human_script replays a logged set of human actions."""
        if approver not in ("persona", "human"):
            raise ValueError(approver)
        # Who plays the people: scripted rules, LLM persona agents, or a replay of a
        # recorded LLM run (Milestone 4). Product logic is plain code in every mode.
        self.actors_mode = actors or cfg["actors"]["mode"]
        if self.actors_mode not in ("scripted", "llm", "replay"):
            raise ValueError(self.actors_mode)
        if self.actors_mode == "replay" and not replay_from:
            raise ValueError("replay needs replay_from: a recorded LLM run")
        self.replay_from = replay_from
        self.product = product
        self.approver = approver
        self.human_script = list(human_script or [])
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario}")
        self.scenario = scenario
        self.sc = SCENARIOS[scenario]
        self.seed = seed
        self.cfg = cfg
        self.rng = random.Random(f"{seed}:director")
        self.open_t = parse(cfg["clinic"]["open"])
        self.close_t = parse(cfg["clinic"]["close"])
        self.run_id = f"{scenario}_s{seed}" + ("" if product else "_baseline")
        if db_path is None:
            db_path = str(ROOT / "runs" / f"{self.run_id}.sqlite")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        if Path(db_path).exists():
            Path(db_path).unlink()
        self.db_path = db_path
        self.store = Store(db_path)

    # --- build -------------------------------------------------------------
    def _visit_length(self):
        cc = self.cfg["clinic"]
        return max(5, round(self.rng.gauss(cc["mean_visit_minutes"], cc["visit_sd_minutes"])))

    def build(self):
        cfg, cc, ac, pc = self.cfg, self.cfg["clinic"], self.cfg["arrivals"], self.cfg["patients"]
        # Shuffle up front (same random-number order as before, so existing
        # seeds keep their days); refill only if a busy day runs it dry.
        pool = list(FILLER_NAMES)
        self.rng.shuffle(pool)

        rounds = [0]

        def next_name():
            # A busy day can use more names than the list holds. Refill with
            # the same shuffle as before, but tag reused names ("Owen B") so
            # two different people never share a name on the staff screens.
            if not pool:
                rounds[0] += 1
                pool.extend(FILLER_NAMES)
                self.rng.shuffle(pool)
            name = pool.pop()
            return name if rounds[0] == 0 else f"{name} {chr(ord('A') + rounds[0])}"
        patients: dict[str, Patient] = {}

        def add(pid, name, kind, appt, lang, persona, planned, asks=False):
            patients[pid] = Patient(pid, name, kind, appt, lang, persona, self._visit_length(), planned, asks)

        # Named scheduled personas take slots on provider 0.
        add("grace", "Grace", "scheduled", parse(pc["grace_appt"]), "en", "grace", parse(pc["grace_appt"]))
        add("chen", "Chen", "scheduled", parse(pc["chen_appt"]), "en", "chen", parse(pc["chen_appt"]) - pc["chen_early_minutes"])
        add("ana", "Ana", "scheduled", parse(pc["ana_appt"]), "en", "ana", parse(pc["ana_appt"]) + pc["ana_late_minutes"])
        add("jordan", "Jordan", "scheduled", parse(pc["jordan_appt"]), "en", "jordan", None)
        taken = {p.appt for p in patients.values()}

        # Filler scheduled patients fill the remaining slots.
        n = 0
        for prov in range(cc["providers"]):
            for s in range(self.open_t, self.close_t, cc["slot_minutes"]):
                if prov == 0 and s in taken:
                    continue
                if self.rng.random() >= cc["scheduled_fill_rate"]:
                    continue
                n += 1
                r = self.rng.random()
                if r < ac["filler_noshow_rate"]:
                    planned = None
                elif r < ac["filler_noshow_rate"] + ac["filler_late_rate"]:
                    planned = s + self.rng.randint(*ac["filler_late_minutes"])
                else:
                    planned = max(self.open_t, s + round(self.rng.gauss(0, ac["filler_jitter_sd_minutes"])))
                add(f"s{n:02d}", next_name(), "scheduled", s, "en", "filler", planned)

        # Named walk-ins.
        add("rosa", "Rosa", "walkin", None, "es", "rosa", parse(pc["rosa_arrival"]), asks=False)
        add("tom", "Tom", "walkin", None, "en", "tom", parse(pc["tom_arrival"]), asks=True)
        add("maria", "Maria", "walkin", None, "en", "maria", None, asks=True)

        # Filler walk-ins: Bernoulli per minute at the scenario's rate.
        surge_s, surge_e = parse(ac["surge_start"]), parse(ac["surge_end"])
        w = 0
        for t in range(self.open_t, self.close_t):
            rate = ac["base_walkin_per_hour"]
            if self.sc["surge"] and surge_s <= t < surge_e:
                rate = ac["surge_walkin_per_hour"]
            if self.rng.random() < rate / 60:
                w += 1
                add(f"w{w:02d}", next_name(), "walkin", None, "en", "filler", t,
                    asks=self.rng.random() < ac["walkin_ask_at_desk_rate"])

        # Injected event: a burst of walk-ins (Kim scenario), drawn after the
        # regular arrivals so the other scenarios keep their exact days.
        if self.sc.get("burst"):
            bt = parse(ac["burst_time"])
            for _ in range(ac["burst_walkins"]):
                w += 1
                add(f"w{w:02d}", next_name(), "walkin", None, "en", "filler",
                    bt + self.rng.randrange(ac["burst_spread_minutes"]),
                    asks=self.rng.random() < ac["walkin_ask_at_desk_rate"])

        self.patients = patients
        self.providers_on_duty = cc["providers"] - self.sc.get("providers_absent", 0)
        self.world = World(cfg, patients, self.open_t, self.providers_on_duty)
        self.book = {p.pid: (p.kind, p.appt) for p in patients.values()}
        self.blocklist = make_blocklist([p.name for p in patients.values()] + ["Mr. Chen"])
        self.desk = DESK[self.sc["frontdesk"]](cfg, self.seed)
        self.actors = [PATIENTS[p.persona](p, cfg, self.seed) for p in patients.values()]
        self.caller = make_caller(self.actors_mode, cfg, self.store, self.replay_from)
        if self.caller is not None:
            # Actor agents wrap the scripted personas, which stay as the fallback.
            self.desk = LLMDesk(self.desk, cfg, self.caller, {p.pid: p.name for p in patients.values()})
            self.actors = [LLMPatient(a, cfg, self.caller, self.store) if a.p.persona != "filler" else a
                           for a in self.actors]
        self.agent_by_pid = {a.p.pid: a for a in self.actors if isinstance(a, LLMPatient)}
        self.store.add_run(self.run_id, self.scenario, self.seed, self.desk.name, self.open_t, self.close_t)
        for p in patients.values():
            self.store.add_patient(p.pid, p.name, p.kind, p.appt, p.lang, p.persona)
        self.store.commit()
        return self

    # --- run ---------------------------------------------------------------
    # The loop is split into start / step / finish so the web UI can drive it
    # one minute at a time. run() calls the same three, so evals are unchanged.
    def start(self):
        cfg, st = self.cfg, self.store
        self.end_t = self.close_t + cfg["run"]["overflow_minutes"]
        self.pending_proposals = []
        self.done = False
        self.t = self.open_t          # the next minute to simulate
        self.human_queue = []         # live human actions waiting for the next tick
        self.survey = Survey(cfg, self.seed)
        # The front desk records who actually showed up, not who was scheduled (SPEC X9).
        st.add_event(self.open_t, "staffing", None, self.desk.name, {"providers": self.providers_on_duty})
        return self

    def human(self, kind, **data):
        """Queue a live action from the person running the demo. It is applied
        at the start of the next tick and logged, so the run can be replayed by
        passing the same log back as human_script."""
        if kind not in ("decide", "run_long"):
            raise ValueError(kind)
        if kind == "decide" and not (data.get("reason") or "").strip():
            raise ValueError("an approval needs a one-line reason")
        self.human_queue.append({"t": self.t, "kind": kind, "data": data})

    def _human_actions(self, t):
        acts = [a for a in self.human_script if a["t"] == t] + [a for a in self.human_queue if a["t"] <= t]
        self.human_queue = [a for a in self.human_queue if a["t"] > t]
        for a in acts:
            self.store.add_human_action(t, a["kind"], a["data"])
        return acts

    def _no_product_view(self, t):
        return PublicView(t=t, status="none", text={"en": "", "es": ""}, reason="no product")

    def step(self):
        """Simulate one minute. Returns False once the run has finished."""
        if self.done:
            return False
        cfg, st, world, desk, t = self.cfg, self.store, self.world, self.desk, self.t
        humans = self._human_actions(t)
        human_decisions = {a["data"]["proposal_id"]: a["data"] for a in humans if a["kind"] == "decide"}
        # 1. world moves: checkouts, roomings, walkaways discovered
        truth = world.advance(t)
        for e in truth:
            st.add_truth(t, e["type"], e["pid"], e.get("data"))
            if e["type"] == "checkout":
                self._survey(t, world.patients[e["pid"]], completed=True)
        # 2. front desk records (late, batched, skipped or wrong per persona)
        desk.observe(t, truth, world)
        desk.observe_run_long(t, world)
        for pr in desk.flush(t, world):
            st.add_event(t, pr.type, pr.pid, desk.name, pr.data)
        for a in humans:
            if a["kind"] == "run_long":   # one-tap "will run long" from the staff console
                st.add_event(t, "run_long", a["data"]["pid"], "human", {"source": "staff console"})
        if self.product:
            # 3. estimator reads only the store
            est = estimate(st, self.book, t, cfg)
            st.add_estimate(est)
            # 4. guardrails build the public view
            state = st.recorded_state(t)
            view = guard(est, t, state.last_event_t, cfg, self.blocklist, open_now=True)
            st.add_view(view)
        else:
            state = st.recorded_state(t)
            view = self._no_product_view(t)
        # 5. patients act on the public view and their own texts
        for actor in self.actors:
            p = actor.p
            for act in actor.step(t, view):
                if act[0] == "arrive":
                    e = world.checkin(t, p.pid)
                    st.add_truth(t, e["type"], p.pid, e["data"])
                    p.told_status, p.told_low, p.told_high = view.status, view.wait_low, view.wait_high
                    desk.observe(t, [e], world)
                    if p.asks_desk:
                        lo, hi, pt, text = desk.verbal(t, p.pid, view, world)
                        st.add_verbal(t, p.pid, desk.name, lo, hi, pt, text)
                elif act[0] == "text":
                    if not self.product:
                        continue          # no texting service without the product
                    st.add_message(t, p.pid, "in", p.lang, "patient", act[1])
                    kind, lang, reply = texting.respond(t, p.pid, act[1], p.lang, view, st, cfg, self.blocklist)
                    note = actor.on_reply(t, kind, reply, lang)
                    if note:
                        st.add_note(t, p.pid, note)
                elif act[0] == "note":
                    st.add_note(t, p.pid, act[1])
                elif act[0] == "leave":
                    e = world.leave(t, p.pid)
                    st.add_truth(t, e["type"], p.pid)
                    self._survey(t, p, completed=False)
        # 6. proposals need a logged approval before the queue moves
        if self.product:
            pending = self.pending_proposals
            already = st.proposed_pids() | {pr["pid"] for pr in pending}
            for pr in proposals.scan(t, state, self.book, cfg, already):
                pr["id"] = st.add_proposal(t, pr["pid"], pr["action"], str(pr["detail"]), pr["reason"], "exception_stub")
                pending.append(pr)
            still = []
            for pr in pending:
                if self.approver == "human":
                    h = human_decisions.get(pr["id"])
                    decision, actor_name = ((h["decision"], h["reason"].strip()) if h else None), "human"
                else:
                    decision, actor_name = desk.review(t, pr), desk.name
                if decision is None:
                    still.append(pr)
                    continue
                aid = st.add_approval(t, pr["id"], actor_name, decision[0], decision[1])
                if decision[0] == "approved" and pr["action"] == "reslot":
                    before = world.order(t)
                    from_pos = before.index(pr["pid"]) if pr["pid"] in before else None
                    world.overrides[pr["pid"]] = t
                    after = world.order(t)
                    to_pos = after.index(pr["pid"]) if pr["pid"] in after else None
                    st.add_queue_change(t, pr["pid"], aid, from_pos, to_pos, {"effective_appt": t})
            self.pending_proposals = still
        # 7. truth queue snapshot for G3
        st.add_truth(t, "queue_snapshot", None, world.order(t))
        st.commit()
        self.t = t = t + 1
        if t >= self.end_t or (t >= self.close_t and world.all_settled() and not self._future_arrivals(t)):
            self.finish()
        return not self.done

    def finish(self):
        cfg, st = self.cfg, self.store
        fc = cfg["finance"]
        sms = st.one("SELECT COUNT(*) n FROM messages")["n"]
        calls = st.one("SELECT COUNT(*) n FROM llm_calls WHERE source != 'skipped'")["n"]
        # Agent calls go through the owner's Claude subscription (or a free replay):
        # billed $0. What they would cost at API prices is in llm_calls.modeled_usd.
        st.add_costs(self.run_id, model_calls=calls, model_usd=0.0, sms_count=sms,
                     sms_usd=round(sms * fc["sms_usd_per_message"], 6))
        st.finish_run(self.run_id, self.t)
        self.done = True

    def run(self):
        self.start()
        while self.step():
            pass
        return self.db_path

    def _survey(self, t, p, completed):
        if not self.product:
            return
        view = self.store.one("SELECT * FROM views WHERE t=?", (p.checked_in_at,))
        self.survey.answer(t, p, view, self.store, completed, t, agent=self.agent_by_pid.get(p.pid))

    def _future_arrivals(self, t):
        return any(a.planned is not None and a.planned >= t and a.p.checked_in_at is None for a in self.actors) \
            or any(a.p.persona == "maria" and a.planned is None for a in self.actors)


def run(scenario, seed, cfg, db_path=None, **kw):
    return Director(scenario, seed, cfg, db_path, **kw).build().run()
