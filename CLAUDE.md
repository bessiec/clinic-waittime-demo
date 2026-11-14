# Clinic Wait Time Demo — working rules for Claude Code

Read SPEC.md before doing anything. It is the source of truth. If something in this file or a request conflicts with SPEC.md, stop and ask.

## How we work
- Build one milestone from SPEC.md "Build plan" at a time. Propose a short plan first and wait for approval before writing code.
- After each milestone: run the full eval suite, report pass/fail per gate and eval, then explain in plain language what the code does (not what it should do), including anything simpler than the spec describes.
- Never mark a milestone done while a hard gate (G1–G5) fails.
- Keep a running FAILURE_LOG.md: each time an eval catches something, record the date, eval ID, cause, and fix.
- Keep HOW_IT_WORKS.md up to date with 2–3 sentences per component. Bessie edits it; don't overwrite her wording.

## Non-negotiables
- No real patient data, real phone numbers, or real SMS. Everything is synthetic and labeled "simulation, synthetic data" in the UI.
- Actor agents (front desk, patients) and product agents (data quality, audit, patient text, exception) are separate modules with separate prompts. They never share a model call.
- The estimator, guardrail layer, and urgent-symptom check are plain code. The urgent-care message is fixed text; no LLM may generate or rewrite it.
- No agent changes queue order or sends a place-moving message without a logged approval.
- Never show a single number to patients: ranges only, with "updated X min ago".
- No triage or medical advice anywhere.

## Engineering defaults
- Python + SQLite for simulation and evals; a small web front end for the four panes. Ask before adding heavy dependencies.
- Every run takes a seed and is fully replayable. Default to scripted personas; LLM calls only from Milestone 4 on.
- All thresholds live in one config file (values marked [brackets] in SPEC.md), never hard-coded.
- Log model and SMS cost per run for the finance dashboard.
- Put API keys in environment variables, never in code or logs.
