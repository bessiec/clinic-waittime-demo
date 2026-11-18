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
20–30 minutes as soon as the people in the waiting room pass her limit, once or twice
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
`python3 -m web --selftest` checks the panes through the API.
