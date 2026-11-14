"""Scripted stand-in for the patient text agent (Milestone 1: no LLM).
Order of checks is fixed: urgent keywords first, then length/language
fallback, then the guardrailed public view in the patient's language."""
import re

from .urgent import URGENT_TEXT, is_urgent
from .guardrails import SAFE_FALLBACK, blocked_term

_SPANISH = re.compile(r"\b(cuánto|cuanto|falta|hola|espera|tiempo|ahora|por favor|gracias|cuándo|cuando|puedo|esperar)\b", re.IGNORECASE)


def detect_language(text: str, default="en") -> str:
    return "es" if _SPANISH.search(text or "") else default


def respond(t, pid, text, patient_lang, view, store, cfg, blocklist):
    """Write the reply to the store and return (kind, lang, reply_text)."""
    lang = patient_lang or detect_language(text)
    if is_urgent(text):
        reply = URGENT_TEXT[lang]
        kind = "urgent"
        store.add_event(t, "handoff", pid, "texting", {"reason": "urgent keywords in patient text"})
    elif len(text) > cfg["guardrails"]["max_text_chars"]:
        reply = f"{SAFE_FALLBACK[lang]} {URGENT_TEXT[lang]}"
        kind = "fallback"
    else:
        reply = view.text[lang]
        kind = "range" if view.status == "range" else "decline"
        hit = blocked_term(reply, blocklist)
        if hit:
            reply = f"{SAFE_FALLBACK[lang]} {URGENT_TEXT[lang]}"
            kind = "fallback"
            store.add_event(t, "guardrail_block", pid, "texting", {"term": hit})
    store.add_message(t, pid, "out", lang, kind, reply)
    return kind, lang, reply
