# Clinic Wait Time Demo

A working prototype of a wait-time page for a small walk-in clinic, built to show three things together:

- **A real wait-time estimate.** Patients see a range ("about 25–50 min", never a single number) with "updated X min ago". When the front desk's data is stale or unreliable, the page says "please call the clinic" instead of guessing.
- **AI agents with clear limits.** AI plays the front desk staff and patients so the product can be tested against realistic behavior. AI product agents (coming next) flag bad data, answer patient texts and draft messages, each with a fixed level of autonomy.
- **Evals.** Every promise the product makes is an automated check, run on 140 simulated afternoons. Safety promises must hold on every single day; accuracy goals are reported honestly, including the ones not met yet.

**Everything here is a simulation with synthetic data.** No real patients, phone numbers or text messages, and no medical advice or triage anywhere.

## Try it

Needs Python 3 (built and tested on 3.14). No packages to install.

```sh
python3 -m web
```

Open http://127.0.0.1:8000 and press **Watch the demo**. It plays a recorded afternoon where AI plays Kim, a receptionist who stops updating the system when swamped, and the patients. The recording replays for free with no AI calls. Then try **Run your own simulation** with a different kind of day or patient mix.

## What you'll see

- **Clinic view:** the patient's phone, the front desk's screen and a plain-language caption of what's happening and why, minute by minute.
- **How it connects:** each part of the system, whether it's AI or plain code, and what it's allowed to do.
- **Evals:** the five safety promises and the accuracy goals from the latest test run. Click any one to see how it's tested, a real example you can open at that minute, and its score over time. Below that, "What the evals caught": real problems found while building this.
- **Results:** wait accuracy, patient walk-aways, a simulated satisfaction survey and modeled costs.
- **Test actors:** the AI personas' prompts and whether they behaved like their descriptions.
- **Run inspector** (from the Evals screen): every patient's timeline and the full logs for one day.

## Run the evals

```sh
python3 -m evals            # 7 kinds of day x 20 patient mixes, about a minute
python3 -m evals --quick    # one patient mix per kind of day
python3 -m web --selftest   # checks every screen through the web API
```

Every run starts from a fixed number, so the same day replays exactly and any failure can be reproduced.

## How it's built

- **Safety-critical parts are plain code, not AI:** the wait estimate, the safety rules on every message, and the urgent-symptom check. An urgent message ("chest pain") always gets the same pre-approved reply pointing to emergency care; no AI writes or changes it.
- **No one's place in line changes without a logged staff approval.**
- **AI test actors and AI product agents are kept separate,** with their own prompts and model calls, so the product is never graded by the same AI that runs it.
- Python standard library, SQLite and plain JavaScript. All thresholds live in [config/thresholds.toml](config/thresholds.toml).

More detail: [SPEC.md](SPEC.md) (what's being built and the full list of evals), [HOW_IT_WORKS.md](HOW_IT_WORKS.md) (each component in a few sentences), [FAILURE_LOG.md](FAILURE_LOG.md) (every problem the evals caught and how it was fixed).

## Status

Built: the simulated clinic, wait estimate and safety rules, the eval suite, the screens, and AI test actors.
Next: AI product agents.
