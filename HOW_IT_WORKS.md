# How it works

Simulation, synthetic data. Two to three sentences per component, in the order
data flows. Thresholds live in `config/thresholds.toml`; nothing below hard-codes one.

## Scenario director (`clinic/director.py`, `clinic/scenarios.py`)
Builds an afternoon half-day from the seed: an appointment book with the four named
scheduled patients on provider 0 and filler patients in the other slots, named and
filler walk-ins, staffing, and any injected events the scenario asks for (a walk-in
surge, a burst of walk-ins at 3 PM, or a provider who doesn't show up). Then it runs
one tick per minute through a fixed pipeline: world moves, front desk records,
estimator runs, guardrails build the page, patients act, proposals get reviewed.
There are seven scenarios; two of them are hold-outs that are never tuned against.
The day can be played whole (evals) or one minute at a time (the web UI); both run
the same tick. A "without the product" mode replays the same people and day with no
page, texts, estimator or exception agent, for the finance pane's walkaway comparison.

## World (`clinic/world.py`)
Ground truth: who really checked in, got roomed, finished, or walked away, with real
visit lengths. Rooms patients whenever a provider is free using the queue rule. It
writes only to the `truth` table, which the estimator never reads and the evals do.

## Queue rule (`clinic/queue.py`)
A pure function from the waiting list to an order: scheduled patients who are due go
first by appointment time, everyone else first come first served. A late scheduled
patient counts as a walk-in until an approved override gives them a new effective
appointment. The G3 grader recomputes this function from the logs to catch any move
that did not go through an approval.

## Event store (`clinic/store.py`)
One SQLite file per run with separate tables for front-desk events, estimates, page
views, texts, verbal guesses, proposals, approvals, queue changes, patient notes, costs
and truth. `recorded_state(t)` rebuilds what the front desk had recorded by minute t,
which is all the product is allowed to know.

## Front-desk personas (`clinic/actors/frontdesk.py`)
Dana, Luis, Kim and Pat each turn true events into status-button presses with their
own delay, batching, skipping or typo rate from config, answer "how long?" in their
own style, and approve or reject proposals with a one-line reason. Kim goes quiet for
20–30 minutes as soon as the people in the waiting room pass her limit, once or twice (catching up in between)
a run. All of this is rules in code for
now; Milestone 4 replaces them with LLM agents held to the same rates.

## Estimator (`clinic/estimator.py`)
Plain code, no ML. It takes the recorded waiting list in queue order, the booked
appointments ahead, providers on duty and how long current visits have run, and plays
the queue forward with a typical visit length to see when a new walk-in would be
roomed. It does this twice, optimistic and pessimistic, and the range spans both plus
a spread. Thin data widens both ends; a "will run long" flag widens only the top. A
patient who checked in but was passed over (someone who should have come after them
was roomed) is left out of the waiting list; a long wait alone doesn't drop anyone.
"Minutes behind" is the same simulation asked about the next booked appointment.

## Guardrail layer (`clinic/guardrails.py`)
Turns an estimate into the public page. It declines whenever the newest front-desk
event is older than the stale limit or confidence is low, enforces a minimum range
width so no single number appears, renders English and Spanish, attaches "Updated X
min ago" and the fixed urgent-care line, and replaces the whole page with a safe
fallback if a name or clinical term shows up.

## Urgent check and texting (`clinic/urgent.py`, `clinic/texting.py`)
A keyword list in both languages decides if a patient text is urgent; the reply is a
string constant that nothing in the repo can rewrite, and a hand-off event is logged
for staff. Otherwise the scripted responder detects the language and sends the
guardrailed page text. In Milestone 5 the patient text agent takes the non-urgent
path only; the urgent path stays code.

## Proposals (`clinic/proposals.py`)
A scripted stand-in for the exception agent. It notices a scheduled patient who
checked in late and files a proposal to re-slot them; the on-duty persona approves
or rejects with a reason, and only an approval row lets the world apply the change
and log it in `queue_changes`.

## Patient personas (`clinic/actors/patients.py`)
Grace, Maria, Mr. Chen, Ana, Jordan, Rosa and Tom plus filler patients. Each sees
only the public page and its own texts, decides when to arrive, text, or walk away,
and logs a "what I understood" note. Walk-ins leave once they have waited past the
top of the range they were shown plus a patience margin.

## Evals (`evals/`)
`python3 -m evals` runs all seven scenarios on 20 seeds in parallel (under a minute),
pools each eval's patients across seeds, and prints a scorecard: gates first, then
tuning scenarios, then hold-outs. A gate fails if any seed of any scenario misses,
hold-outs included; a hold-out's scored eval is reported but never fails the run.
Each suite run is saved under `runs/suites/` with a fingerprint of the config and
code, and compared with the previous run so a pass that turns into a fail is printed
first. `python3 -m evals --quick` is the single-seed, two-scenario check from
Milestone 1. Every failure line carries the minute and patient it came from, so the
observer pane can link to it. E10 recounts every finance number from the raw tables
with separate code and requires an exact match.

## Survey (`clinic/survey.py`)
After each visit, or when a walk-in gives up, the patient answers three 1–5 questions:
was the estimate accurate, how satisfied with the wait, would you use it again. In
this milestone the answers are a formula of what the page showed at check-in versus
the real wait, plus random noise, stored without a patient id. Because it is a
formula, the finance pane's "satisfaction tracks false reassurance" link is built in.

## Data-quality flags (`clinic/flags.py`)
Plain-code checks over what the front desk recorded: roomed before check-in, checked
out without being roomed, checked in twice, in a room unusually long, passed over in
the queue, or no update for a while. They only flag; nothing changes. The LLM
data-quality agent adds to these in Milestone 5.

## Finance (`evals/finance.py`)
Counts per run (patients seen, room-minutes, walkaways with and without the product,
no-shows, desk questions, button presses, approvals, texts, survey answers) that add
up across seeds, and the rates shown on the finance pane. Money and staff time come
from per-unit figures in config and are labeled modeled. Survey averages for any
group under 10 answers are hidden.

## Web UI (`web/`)
`python3 -m web` serves the four panes at http://127.0.0.1:8000 using only the Python
standard library and plain JavaScript. A background ticker steps the run at 1x (one
simulated minute per second), 10x or 60x; each pane is computed from the run's SQLite
file at a chosen minute, so you can scrub back, click a scorecard failure to jump to
that patient and minute, or open a failing seed from the last suite. The patient
phone shows the guardrail text word for word; the staff console shows only what the
desk recorded, and with "approvals by me" your decisions and "will run long" presses
are logged as human actions that Replay feeds back for an identical run.
`python3 -m web --selftest` checks the panes through the API. The observer's Agents tab lists every test-actor persona with its prompt, what it decided in the loaded run, and its fidelity result.

## AI test actors (`clinic/actors/llm.py`, `llm_frontdesk.py`, `llm_patients.py`, `prompts/actors/`)
From Milestone 4 the front desk and the seven named patients can be played by AI persona agents instead of scripted rules. Each persona's habits live in its own prompt file; the agent sees only what that person would see (the desk sees its backlog and waiting room, a patient sees the public page and its own texts) and answers each time with one action in a fixed JSON format. Calls go through the Claude Code CLI on Bessie's subscription (API keys are stripped from its environment, so nothing is billed), every reply is saved in the run's `llm_calls` table, and a failed call falls back to the scripted rule and is logged.

## Persona fidelity (`evals/fidelity.py`, check F1)
These checks grade the actors, not the product: did Kim really go quiet in a crowd (for 20–45 minutes, not the rest of the day), did Mr. Chen arrive about 30 minutes early, did Rosa write only in Spanish. A desk persona's guesses are compared with a fair headcount guess at that minute, so the check measures the habit rather than the day. On scripted runs they pass by construction; on the AI run they show whether an agent drifted toward polite and punctual.

## Demo run (`evals/demo_run.py`, `demo/`)
`python3 -m evals.demo_run` records one day (Kim goes quiet, seed 42) with live AI agents; `--replay` replays the saved replies and grades the day without calling a model. The recording is kept in git so anyone can replay it, and the web UI's "Watch AI demo day" button plays it minute by minute for free. One run is an example, not a rate.
