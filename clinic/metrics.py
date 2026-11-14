"""Shared definitions used by both the estimator and the evals."""
import re


def wait_minutes(kind: str, appt, checkin: int, roomed: int) -> int:
    """Wait = check-in to roomed. For a scheduled patient who arrives early the
    clock starts at appointment time (SPEC: what we measure)."""
    if kind == "scheduled" and appt is not None:
        return max(0, roomed - max(appt, checkin))
    return max(0, roomed - checkin)


def behind_minutes(appt, roomed: int) -> int:
    return max(0, roomed - appt)


# A wait figure in patient-facing text: "15–35 min", "under 10 min", "10 min".
MINUTE_FIGURE = re.compile(r"\d+\s*(?:–|-|to|a)?\s*\d*\s*min", re.IGNORECASE)
# The freshness line is not a wait figure and is required on every view (G5).
FRESHNESS_LINE = re.compile(r"(?:Updated \d+ min ago|Actualizado hace \d+ min)\.?", re.IGNORECASE)


def has_wait_figure(text: str) -> bool:
    """True if the text shows a wait/time figure to the patient, ignoring the
    'updated X min ago' freshness line."""
    return bool(MINUTE_FIGURE.search(FRESHNESS_LINE.sub("", text or "")))
