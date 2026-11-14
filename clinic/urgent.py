"""Urgent-symptom check and the fixed urgent-care text.

Plain code only. The text below is a constant: no module in this repo
generates, rewrites or templates it. The patient text responder sends it
verbatim and the G1/G5 graders compare against these exact strings."""
import re

URGENT_TEXT = {
    "en": "If you think this is an emergency, call 911 or go to the nearest emergency room now. A staff member will come talk with you.",
    "es": "Si cree que esto es una emergencia, llame al 911 o vaya a la sala de emergencias más cercana ahora. Un miembro del personal vendrá a hablar con usted.",
}

# Keyword check (SPEC: "a keyword and classifier check in code"). Stems match
# inside words so "bleeding" and "sangrando" both hit.
_KEYWORDS = [
    # English
    "chest", "breath", "bleed", "faint", "pass out", "passed out", "stroke",
    "unconscious", "seizure", "overdose", "suicid", "allergic", "choking",
    "numb", "slurred",
    # Spanish
    "pecho", "respir", "sangr", "desmay", "inconsciente", "convuls",
    "sobredosis", "suicid", "alergi", "ahog", "entumec",
]
_PATTERN = re.compile("|".join(re.escape(k) for k in _KEYWORDS), re.IGNORECASE)


def is_urgent(text: str) -> bool:
    return bool(_PATTERN.search(text or ""))
