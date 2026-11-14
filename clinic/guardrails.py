"""Guardrail layer: plain code between the estimator and anything a patient
sees. Withholds when data is stale or confidence is low, never shows a single
number, always attaches the fixed urgent-care line and "updated X min ago",
and blocks names and clinical words from the rendered text."""
import re
from dataclasses import dataclass, field

from .urgent import URGENT_TEXT

CLINICAL_TERMS = [
    "chest", "pain", "symptom", "diagnos", "blood", "fever", "cough", "breath",
    "injur", "triage", "medicat", "prescri", "infect", "wound",
    "pecho", "dolor", "síntoma", "sintoma", "fiebre", "sangre", "respir",
    "diagnós", "diagnos", "receta", "herida", "infecc",
]

SAFE_FALLBACK = {
    "en": "We can't give a wait time right now. Please call the clinic for an update.",
    "es": "No podemos dar un tiempo de espera ahora. Por favor llame a la clínica para más información.",
}


@dataclass
class PublicView:
    t: int
    status: str                  # "range" | "declined" | "closed"
    wait_low: int | None = None
    wait_high: int | None = None
    behind_low: int | None = None
    behind_high: int | None = None
    total_low: int | None = None
    total_high: int | None = None
    updated_min_ago: int = 0
    text: dict = field(default_factory=dict)
    reason: str = ""
    labeled_prior: bool = False


def make_blocklist(names: list[str]) -> list[re.Pattern]:
    pats = [re.compile(r"\b" + re.escape(n) + r"\b", re.IGNORECASE) for n in names if n]
    pats += [re.compile(re.escape(w), re.IGNORECASE) for w in CLINICAL_TERMS]
    return pats


def blocked_term(text: str, blocklist) -> str | None:
    for p in blocklist:
        m = p.search(text)
        if m:
            return m.group(0)
    return None


def _wait_phrase(lo, hi, lang):
    if lo == 0:
        return f"under {hi} min" if lang == "en" else f"menos de {hi} min"
    return f"{lo}–{hi} min"


def _behind_phrase(lo, hi, lang):
    if hi <= 5:
        return "running on time" if lang == "en" else "va a tiempo"
    return (f"running {lo}–{hi} min behind" if lang == "en" else f"va con {lo}–{hi} min de retraso")


def render(v: PublicView, lang: str) -> str:
    updated = (f"Updated {v.updated_min_ago} min ago." if lang == "en"
               else f"Actualizado hace {v.updated_min_ago} min.")
    if v.status == "range":
        if lang == "en":
            parts = [f"Walk-in wait: about {_wait_phrase(v.wait_low, v.wait_high, 'en')}.",
                     f"Scheduled visits: {_behind_phrase(v.behind_low, v.behind_high, 'en')}.",
                     f"Total visit: about {v.total_low}–{v.total_high} min."]
            if v.labeled_prior:
                parts.append("Based on typical days, not today's data yet.")
        else:
            parts = [f"Espera sin cita: unos {_wait_phrase(v.wait_low, v.wait_high, 'es')}.",
                     f"Visitas con cita: {_behind_phrase(v.behind_low, v.behind_high, 'es')}.",
                     f"Visita total: unos {v.total_low}–{v.total_high} min."]
            if v.labeled_prior:
                parts.append("Basado en días típicos, no en los datos de hoy.")
    elif v.status == "closed":
        parts = ["The clinic is closed right now." if lang == "en" else "La clínica está cerrada ahora."]
    else:
        parts = [SAFE_FALLBACK[lang]]
    parts.append(updated)
    parts.append(URGENT_TEXT[lang])
    return " ".join(parts)


def guard(est, t: int, last_event_t, cfg, blocklist, open_now=True) -> PublicView:
    gc = cfg["guardrails"]
    age = (t - last_event_t) if last_event_t is not None else None
    v = PublicView(t=t, status="range", updated_min_ago=age if age is not None else 0)

    if not open_now:
        v.status, v.reason = "closed", "closed"
    elif age is None:
        v.status, v.reason = "declined", "no data yet"
    elif age > gc["stale_minutes"]:
        v.status, v.reason = "declined", f"data {age} min old"
    elif est is None or est.confidence < gc["min_confidence"]:
        v.status, v.reason = "declined", "low confidence" if est is not None else "no estimate"
    else:
        v.wait_low, v.wait_high = est.wait_low, est.wait_high
        v.behind_low, v.behind_high = est.behind_low, est.behind_high
        v.total_low, v.total_high = est.total_low, est.total_high
        v.labeled_prior = bool(est.basis.get("labeled_prior"))
        # Never a single number: enforce a minimum width even if the estimator slipped.
        if v.wait_high - v.wait_low < cfg["estimator"]["min_range_width"]:
            v.wait_high = v.wait_low + cfg["estimator"]["min_range_width"]
        if v.behind_high - v.behind_low < cfg["estimator"]["min_range_width"]:
            v.behind_high = v.behind_low + cfg["estimator"]["min_range_width"]
        if v.total_high - v.total_low < cfg["estimator"]["min_range_width"]:
            v.total_high = v.total_low + cfg["estimator"]["min_range_width"]

    v.text = {lang: render(v, lang) for lang in ("en", "es")}
    for lang in ("en", "es"):
        hit = blocked_term(v.text[lang], blocklist)
        if hit:
            v.status, v.reason = "declined", f"blocked term: {hit}"
            v.wait_low = v.wait_high = v.behind_low = v.behind_high = v.total_low = v.total_high = None
            v.text = {l: render(v, l) for l in ("en", "es")}
            break
    return v
