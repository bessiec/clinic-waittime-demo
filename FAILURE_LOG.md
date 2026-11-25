# Failure log

One entry each time an eval catches something. Date · eval ID · cause · fix.

## 2026-11-10 — Milestone 1 build

**G2 · grader false positive.** The "no number shown" regex matched the required
"Updated 16 min ago" line on declined pages, so every honest decline during Kim's
blackout was counted as a leak (14/14). The product was right; the grader was wrong.
Fix: `has_wait_figure()` in `clinic/metrics.py` strips the freshness line before
scanning for a wait figure. G1 and G2 both use it.

**E1 · scheduled patients scored on the wrong clock.** The first grader compared the
"running X–Y min behind" range to roomed-minus-appointment, which charged late
arrivals for their own lateness (Ana: told 0–20, "actual" 23 of which 15 was her).
Fix: E1 uses the shared wait definition from `clinic/metrics.py` (clock starts at
appointment time, or at check-in if later), the same function the estimator uses.

**E1 · walk-in ranges too optimistic (59% → 71%).** The estimator counted booked
appointments only 30 min ahead and used a headcount formula, so a walk-in facing a
40-min wait was jumped by scheduled patients it never counted. Trace on seed 42:
w02 told 20–50, waited 53 while three scheduled patients and one approved re-slot
went first. Fix: the estimator now runs a small queue simulation that reuses the
clinic's own ordering rule (`clinic/queue.py`), looks 90 min ahead, and takes its
range from two runs: optimistic (only appointments due in 10 min) and pessimistic
(every booked patient shows, late ones included).

**E1 · "behind" estimate double counted (scheduled coverage 57%).** The scheduled
probe was added on top of the booked patient at the same slot, so the clinic looked
one visit later than it was (told 10–30, actual 1, ten times over ten seeds). Fix:
the probe *is* the next booked patient; "behind" is how late that appointment will
be roomed, measured from its appointment time. Scheduled coverage 57% → 89%.

**E1 · behind range floor too tight (79% on seed 42, one patient short).** Across
ten seeds scheduled lags reach 13 min at p90; a 0–10 floor missed them (s02 told
0–10, actual 11). Fix: `min_behind_width = 15` in config. Measured on ten seeds
before applying: total coverage 84% → 87%, seed 42 79% → 86%.

**Open · E1 on seed 8 is 63%.** Misses go both ways (told 5–15, waited 31; told
20–65, waited 6). Not tuned against. Milestone 2's multi-seed run should report the
spread; the audit agent in Milestone 5 is the designed fix for width, not more
hand-tuning here.

## 2026-11-13 — Milestone 2 build

**G2 · gate not exercised on 6 of 20 seeds (kim_goes_quiet).** The product leaked
nothing; the test never ran. On those seeds the surge never put more than 8 people in
the waiting room, so Kim's habit never triggered. On one more seed it passed 8 for two
minutes and Kim's 30%-per-minute chance of going quiet didn't fire. That chance was my
invention, not the spec's. Fix: the scenario injects a burst of 12 walk-ins at 3 PM
(the director's "injected events" job), and Kim goes quiet as soon as the room passes
her limit (`blackout_start_prob = 1.0`, matching the spec's wording). Checked on 100
seeds: 100 pass. A heavier surge rate was tried first and abandoned; it just moved the
miss to another seed.

**Runner · filler names ran out (crash).** 40 synthetic names weren't enough for a busy
day. Fix: the name pool refills. The first version of the fix changed the order of
random draws, and the regression view caught it at once: every scenario's scores moved
although none had changed. Fix to the fix: the pool is shuffled at the same point as
before, so existing seeds keep their exact days.

**Runner · old suites kept 45 MB each.** Pruning kept every seed that failed any check,
and scored evals fail on most seeds. Fix: old suites keep only seeds that failed a gate
plus the worst seed of each failing eval (about 5 MB).

**Open · E5 luis_day 23%, pat_day 41% (threshold 70%).** The system's range loses to
the desk's headcount guess. Three causes found, none fixed in Milestone 2:
(1) a "will run long" flag adds 10 minutes twice, once inside the queue simulation
and again on top of the range, and Pat presses it at random; (2) the pessimistic
simulation assumes every booked patient shows, so a walk-in is jumped by each one (Tom,
pat_day seed 1: told 25–120, waited 6); (3) E5 scores the midpoint of a lopsided range.
Also worth knowing: in this simple world, headcount × 15 ÷ providers is a strong guess,
and the desk personas count the real waiting room.

**Open · E4 kim_goes_quiet 38% (threshold 90%).** The guardrail only checks how old the
newest front-desk event is. Kim keeps pressing some buttons while skipping others, so
the data looks fresh while the recorded queue is off by up to 11 people (seed 1, 15:04:
recorded 2, true 13, data 2 min old). Part of this is E4's definition: 12 people
arriving in 5 minutes with Kim's normal 1–3 minute delay already exceeds the
3-person mismatch, so even accurate staff would trip it briefly.

**Open · hold-outs (reported, not tuned).** understaffed_opening: E1 71%, E3 24%, E4
5%. The estimator drops anyone waiting over 120 minutes as "never roomed" (the X6 rule),
and with one provider real waits pass 4 hours, so it forgets real patients; ranges
reach 25–235. luis_surge: E4 26%, E5 53%, E1 77%. Luis's batching hides how stale his
data is, because a batch lands every 10–15 minutes and the age check never trips.

**Open · E2 on seed 42 alone is 35 min** (9 ranges); pooled across 20 seeds it is 20.
`--quick` reports it as a fail; the suite does not.

## 2026-11-13 — fixes Bessie approved after Milestone 2

**E5 · "will run long" counted twice (fixed).** The flag now widens only the top of the
range; it no longer also delays every provider in the queue simulation. pat_day E5
41% → 49%. E1 up 1–2 points on every tuning scenario. Still failing; the remaining
causes (worst case assumes every booked patient shows; E5 scores a lopsided range by its
midpoint) are not fixed.

**Hold-out · 2-hour exclusion forgot real patients (fixed; hold-out now spent).** The
X6 rule now drops a waiting patient only when they were passed over: someone who should
have come after them under the queue rule was roomed more than 10 minutes ago. A
6-hour backstop remains. understaffed_opening E4 5% → 100%, E3 24% → 18%, E1 71% →
74%. Bessie accepted tuning against this hold-out, so **understaffed_opening is no
longer a clean hold-out**; replace it before Milestone 6.

**Estimator · first "passed over" rule dropped real walk-ins (caught by E4 and E3 on
luis_surge, fixed).** Luis records check-ins in batches, so an on-time scheduled patient
could look late, count as a walk-in, get roomed on time, and make every earlier walk-in
look skipped (seed 2, 16:40: 7 people really waiting were dropped). Fix: compare like
with like by booked type; a late scheduled patient is only dropped by the backstop.
luis_surge E4 12% → 90%, E3 7% → 5%. The E4 grader now uses the estimator's own
waiting-list function, so the two can't disagree about who is waiting.

**E4 · mismatch limit loosened from 3 to 5 people (Bessie's call).** kim_goes_quiet
still fails at 53%: Kim's skipped presses leave the recorded queue more than 5 people
off while the data still looks fresh. The guardrail's freshness check only looks at the
newest entry; that gap is still open.

## 2026-11-17 — Milestone 3 build (four panes)

**Refactor check · splitting the run loop changed nothing (passed).** Before any UI
work, all 140 saved runs from the last Milestone 2 suite were replayed with the new
start/step/finish loop and compared table by table: identical. The suite's regression
view agrees: no score moved.

**UI self-test "every failure links to its timeline" · E2 had no links (fixed).** A
failing E2 (median range too wide) listed no patients, so there was nothing to click.
E2 now lists each range wider than the threshold with its patient and minute.

**UI self-test · saved suites from before this milestone crashed the server
(fixed).** They lack the new survey table. The server now refuses them with a message
to run `python3 -m evals`; new suites open fine.

**Browser check · stale patient selection after opening a saved run (fixed).** The
patient log kept the previous run's selection, and patient ids like "w02" mean a
different person in another run. Opening a run now clears the selection.

**By eye (screenshot) · page wider than a phone screen at 375 px (fixed).** A grid
column wouldn't shrink below its content. The phone frame itself was already 375 px
wide on desktop.

**By eye · two different patients both named "Owen" (fixed).** Busy Kim days use more
than the 40 filler names. Reused names now get a letter ("Owen B"); the random draws
are unchanged, so no score moved.

**Open · walkaways "with vs without the product" is partly built in.** A walk-in who
saw a range waits until 20 min past its top; one with no information leaves after a
fixed 45 min. So the product's lower walkaway rate (e.g. walkin_surge 2% vs 35%)
mostly reflects that patience rule, not evidence. Milestone 4's LLM patients should
decide this themselves.

**Open · survey relationship is built in.** Satisfaction is a formula that penalizes
waiting past the range, so "satisfaction tracks false reassurance" on the finance pane
is true by construction until Milestone 4.

**Unchanged · tuning evals still failing:** kim_goes_quiet E4 53%, luis_day E5 23%,
pat_day E5 49%. Hold-outs unchanged.

## 2026-11-19 — Milestone 4 build (AI test actors)

**F1 · desk-guess checks graded the day, not the persona (fixed before any agent ran).** The first
Luis and Pat checks compared their "how long?" guesses with the patient's actual wait. On scripted
runs, which follow the habits exactly, Luis failed (only 40% of guesses below the actual wait) and Pat
failed (100% of guesses more than 25% off), because the actual wait mostly reflects the simulated day.
Fix: compare each guess with a fair headcount guess at that minute (people waiting x 15 min / providers),
so the check measures the habit. Scripted Luis guesses at 67% of fair; SPEC says about 30% low.

**F1 · Tom's timing window (fixed).** The check wanted his chest text 10–35 min after check-in; on
pat_day seed 1 he was called back after 6 minutes and texted then, which is in character. Window is now 0–35.

**F1 pat_day · 11/20 seeds (caught by the suite, fixed).** Pat answers only 3–6 "how long?" questions
a day, so "5–60% of guesses are big misses" swung on one guess: 0 of 4 failed the floor, 2 of 3 failed the
cap. A share needs a sample. Fix: fewer than 10 guesses reports Pat's guessing as untested rather than
pass or fail (config `min_guesses`). All 140 scripted runs now pass F1.

**Calls skipped after the budget counted as model calls (fixed).** The 6-call smoke test logged 266 model
calls in run_costs because every skipped attempt was a row. Skipped attempts are now marked and excluded,
in the director and in E10 alike.

**2026-11-20 · F1 kim on the AI demo run (kim_goes_quiet seed 42, 175 agent calls).** The AI Kim drifted two
ways. (1) Too punctual on calm stretches: median entry 0 min after the event (SPEC: late; scripted Kim
2 min); 46 of 71 entries were same-minute. (2) Too far into the blackout: once the room passed 8 at
15:03 she stopped entering anything for 170 minutes, to the end of the day, and forgot about half of
what happened (71 entries for 143 events). Her prompt says 20–30 minutes; her notes repeat "computer can
wait" every 2 minutes, although each prompt told her how long it had been. F1 caught (1). It did not
catch (2): the check counts quiet stretches of 20+ minutes (SPEC [20+]) with no upper limit, so a 170-minute
stretch counted as one acceptable blackout. Gates all passed (G2 exercised: 154 stale views, 0 leaks).
Approved 2026-11-20: add an upper limit (45 min) to the check, sharpen Kim's prompt on both habits, re-record.
Fix (2026-11-20): (a) F1 now caps a quiet stretch at 45 min (`kim_quiet_max_minutes`, PROPOSED, not in
SPEC) and counts a stretch that runs to closing time. (b) A quiet stretch is now measured from the first
event she left waiting, not from her last press, so calm minutes with nothing to enter don't count.
(c) The cap then caught the *scripted* Kim on 7/20 seeds (stretches of 47–59 min): when her first
blackout ended with the room still full, her second blackout started the same minute, before she
entered anything, so two blackouts ran together. SPEC says she goes quiet "1–2 times", i.e. separately.
Scripted Kim now catches up on the minute a blackout ends. (d) Kim's prompt now says she is 2–5 min
late even when calm, and catches up after 20–30 min away even if the room is still full. Re-recorded
the demo; result below.
Side effect on the scripted suite (2026-11-20): E4 kim_goes_quiet "declined when it should" moved 53% → 42%
(283/671 minutes). With Kim catching up between blackouts there are fewer stale minutes (671 vs 827) and
the ones left are shorter, which the data-quality check is slower to notice. Already failing; tuning is
Milestone 5 work. All gates still pass; F1 passes on every scripted run.

**2026-11-20 · F1 kim on the re-recorded AI demo (2026-11-20, 191 agent calls, $0 billed).** Both earlier drifts
are fixed: median entry 4 min late (was 0), longest quiet stretch 26 min (was 170). New drift, the other
way: 4 quiet stretches of 20–26 min (SPEC [1–2] per run). She steps away, catches up, and steps away
again each time the room refills. Cause: her prompt says "once or twice" but nothing tells her how many
times she has already done it; each call only sees the last 30 minutes. Proposed: add one line she
would know herself ("times today you've stepped away from the computer for 20+ min: N"), re-record once.
Also seen: E4 on this run 60% (was 87%). At 15:03–15:06 the page showed a range from data 1–4 min old
while the recorded queue said 4 and the true queue was 11–15. The data was fresh but incomplete
(Kim skipped entries), and the data-quality check only looks at age. Product-side, Milestone 5.
Fix (2026-11-24): every desk agent's prompt now includes "Times today you've stepped away from the
computer for 20+ min: N", counted from its own entries (`step_away_minutes`), and Kim's persona says she
doesn't step away again once it's 2. Third recording (192 calls, $0 billed, $1.90 modeled): F1 8/8, Kim
has 1 quiet stretch of 24 min, entries a median 3 min late, 100% vague answers. Gates pass.

**Open · E4 on the third AI recording: 30% declined when it should (13/44).** Product-side, not an actor
problem. At 15:03–15:06 the page showed a range while the recorded queue said 4 and the true queue was
11–15. The data was 1–4 min old, but Kim had skipped entries, so it was incomplete. The data-quality
check only looks at data age. The better-behaved Kim makes this worse: she keeps entering a few things,
so the data never looks old. To fix in Milestone 5 (data-quality agent).

**2026-11-24 · Web UI (caught by Bessie, not by a test) · "Cannot read properties of null (reading 'decided')".**
Cause: the Milestone 4.5 caption code read the front-desk data before the day started, when the server
sends none. The click test missed it because the page catches the error and prints it in the error line
instead of throwing. Fix: a "loaded, press Play" caption until the day starts, and the click test's start
check now also requires an empty error line (it fails on the old code with this exact message).
