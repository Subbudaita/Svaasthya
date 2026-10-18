"""Deterministic safety stopgaps around the chat agent.

* ``red_flag`` screens inbound text before any model sees it.
* ``guard`` screens the model's reply and fails closed.

Both are interim: Phase 5 adds the Haiku red-flag classifier, the full output
guard and the adversarial eval suite (docs/PLAN.md §5). They exist now so the
chat agent never runs unguarded.
"""

import re
from dataclasses import dataclass
from functools import cache

import yaml
from swasthya_shared.data import data_dir

_DRUGS = (
    r"dose|dosage|insulin|metformin|medication|medicine|tablet|pill|thyroxine|levothyroxine|"
    r"thyronorm|eltroxin|semaglutide|ozempic|wegovy|rybelsus|mounjaro|tirzepatide|warfarin|"
    r"glimepiride|gliclazide|statin|atorvastatin|rosuvastatin|amlodipine|telmisartan|ppi|"
    r"pantoprazole|omeprazole"
)
MED_CHANGE = re.compile(
    rf"\b(increase|decrease|reduce|raise|lower|stop|skip|double|halve|start|change|adjust|"
    rf"come off|taper)\b[^.\n]{{0,50}}\b({_DRUGS})\b|\b({_DRUGS})\b[^.\n]{{0,30}}\b(increase|"
    rf"decrease|reduce|stop|skip|double|halve|adjust)\b",
    re.I,
)
DOSE = re.compile(r"\b\d+(\.\d+)?\s?(mg|mcg|µg|units?|iu)\b", re.I)
DIAGNOSIS = re.compile(
    r"\byou (have|probably have|likely have|might have|may have|definitely have|are|'re) "
    r"(got )?(an? )?(type ?[12] )?(diabetes|diabetic|pre-?diabet\w*|hypothyroid\w*|"
    r"hyperthyroid\w*|thyroid (disease|problem|disorder)|pcos|pcod|fatty liver|nafld|"
    r"anemi[ac]|anaemi[ac]|depress(ion|ed)|an eating disorder|ibs|celiac|coeliac|"
    r"insulin resistan\w*|sleep apnoea|sleep apnea)\b",
    re.I,
)
CALORIES = re.compile(r"\b\d[\d,]*\s?(k?cals?|kilocalories|calories)\b", re.I)
SENTENCE = re.compile(r"(?<=[.!?])\s+")

REFERRAL = (
    "That one belongs with your doctor — I don't diagnose, and I never change medicines or "
    "doses. What I can do is help you ask it clearly. Tell me what you've noticed and I'll "
    "turn it into a short question to take to your next appointment."
)
FALLBACK = "I couldn't put a safe answer together for that. Could you ask it another way?"


@cache
def _flags() -> tuple[dict[str, list[re.Pattern]], str]:
    raw = yaml.safe_load((data_dir() / "safety/red_flags.yaml").read_text(encoding="utf-8"))
    compiled = {k: [re.compile(p, re.I) for p in v] for k, v in raw["patterns"].items()}
    return compiled, " ".join(raw["reply"].split())


@dataclass(frozen=True)
class RedFlag:
    category: str
    reply: str


def red_flag(text: str) -> RedFlag | None:
    patterns, reply = _flags()
    for category, pats in patterns.items():
        if any(p.search(text) for p in pats):
            return RedFlag(category, reply)
    return None


@dataclass(frozen=True)
class Guarded:
    text: str
    action: str  # passed | referral | redacted_calories | fallback


def guard(reply: str, *, numbers_visible: bool) -> Guarded:
    """Fail closed: anything unexpected becomes the fallback, never the raw reply."""
    try:
        if MED_CHANGE.search(reply) or DOSE.search(reply) or DIAGNOSIS.search(reply):
            return Guarded(REFERRAL, "referral")
        if not numbers_visible and CALORIES.search(reply):
            kept = [s for s in SENTENCE.split(reply) if not CALORIES.search(s)]
            text = " ".join(kept).strip()
            return Guarded(text or FALLBACK, "redacted_calories")
        if not reply.strip():
            return Guarded(FALLBACK, "fallback")
        return Guarded(reply.strip(), "passed")
    except Exception:  # noqa: BLE001 — failing closed is the point
        return Guarded(FALLBACK, "fallback")
