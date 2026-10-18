"""Onboarding script: which question is next, and parsing the answer.

The script lives in ``data/onboarding.yaml``. This module never decides a
parameter — it only validates answers to questions about the person's life.
"""

from dataclasses import dataclass
from functools import cache
from typing import Any

import yaml
from swasthya_shared.data import data_dir

from swasthya_engines.clock import fmt, parse_time

_NONE = {"none", "no", "nil", "skip", "nothing", "na", "n/a", "-"}
_YES = {"yes", "y", "yeah", "yep", "haan", "ha", "true"}
_NO = {"no", "n", "nope", "nahi", "false"}


class AnswerError(ValueError):
    """The answer can't be used; the message is shown to the person."""


@dataclass(frozen=True)
class Step:
    id: str
    kind: str
    prompt: str
    options: tuple[str, ...] = ()
    optional: bool = False
    min: float | None = None
    max: float | None = None
    min_items: int = 0
    only_if: dict[str, list[str]] | None = None
    only_if_answered: str | None = None


@dataclass(frozen=True)
class Script:
    version: str
    steps: tuple[Step, ...]

    @classmethod
    @cache
    def load(cls) -> "Script":
        raw = yaml.safe_load((data_dir() / "onboarding.yaml").read_text(encoding="utf-8"))
        steps = tuple(
            Step(
                id=s["id"],
                kind=s["kind"],
                prompt=s["prompt"],
                options=tuple(s.get("options", ())),
                optional=s.get("optional", False),
                min=s.get("min"),
                max=s.get("max"),
                min_items=s.get("min_items", 0),
                only_if=s.get("only_if"),
                only_if_answered=s.get("only_if_answered"),
            )
            for s in raw["steps"]
        )
        return cls(version=str(raw["version"]), steps=steps)

    def applies(self, step: Step, answers: dict[str, Any]) -> bool:
        if step.only_if:
            for key, allowed in step.only_if.items():
                if answers.get(key) not in allowed:
                    return False
        if step.only_if_answered and not answers.get(step.only_if_answered):
            return False
        return True

    def next_step(self, answers: dict[str, Any]) -> Step | None:
        for step in self.steps:
            if step.id not in answers and self.applies(step, answers):
                return step
        return None

    def progress(self, answers: dict[str, Any]) -> tuple[int, int]:
        applicable = [s for s in self.steps if self.applies(s, answers)]
        return sum(1 for s in applicable if s.id in answers), len(applicable)


def parse_answer(step: Step, raw: str) -> Any:
    text = raw.strip()
    low = text.lower()
    if step.optional and low in _NONE:
        return None
    match step.kind:
        case "consent":
            if low in _YES or low == "agree":
                return True
            raise AnswerError("I can only continue once you reply YES.")
        case "yesno":
            if low in _YES:
                return True
            if low in _NO:
                return False
            raise AnswerError("Please answer yes or no.")
        case "choice":
            normalised = low.replace(" ", "_").replace("-", "_")
            if normalised in step.options:
                return normalised
            raise AnswerError("Please pick one of: " + ", ".join(step.options))
        case "number":
            try:
                value = float(text.replace(",", "."))
            except ValueError as exc:
                raise AnswerError("Please send just the number.") from exc
            if (step.min is not None and value < step.min) or (
                step.max is not None and value > step.max
            ):
                raise AnswerError(
                    f"That looks off — expected between {step.min:g} and {step.max:g}."
                )
            return int(value) if value.is_integer() else value
        case "time" | "optional_time":
            if step.kind == "optional_time" and low in _NONE:
                return None
            try:
                return fmt(parse_time(text))
            except ValueError as exc:
                raise AnswerError("Please send a time like 7:30, 7.30am or 22:00.") from exc
        case "list":
            if low in _NONE:
                items: list[str] = []
            else:
                items = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
            if len(items) < step.min_items:
                raise AnswerError(f"Please name at least {step.min_items}.")
            return items
        case "text":
            if not text:
                raise AnswerError("Please write a few words.")
            return text[:1000]
    raise AnswerError(f"unsupported step kind {step.kind}")
