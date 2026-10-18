"""Plate perception agent — a hand-written Claude tool-use loop.

Input: a plate photo and/or a typed description. Output: a list of perceived
items, each with an INDB food code (or none), a portion, a role on the plate,
and a confidence. The model may call one tool, ``search_foods``, to look codes
up. It never computes nutrients; ``swasthya_engines.plate`` does that from the
table afterwards.

The loop is deliberately written by hand (no framework): it is short, every
step is visible, and every model call is metered against the user's daily
token cap before it is made.
"""

import json
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import anthropic
from pydantic import BaseModel, Field, ValidationError
from swasthya_engines.nutrition import FoodTable
from swasthya_shared.models import ModelTier

from swasthya_ingestion.images import PreparedImage

Role = Literal[
    "grain", "protein", "vegetable", "dairy", "fruit", "fried", "sweet", "beverage", "other"
]
Confidence = Literal["high", "medium", "low"]

MAX_TURNS = 4
MAX_TOKENS = 4_000
# Upper bound on input tokens for one call: system + tools + image + tool results.
INPUT_TOKEN_ESTIMATE = 8_000
MAX_QUERIES_PER_CALL = 12
CANDIDATES_PER_QUERY = 8


class MeterLike(Protocol):
    def record(self, input_tokens: int, output_tokens: int) -> None: ...


# ``TokenBudget.metered`` satisfies this; evals pass an unmetered stand-in.
MeterFactory = Callable[[int], AbstractAsyncContextManager[MeterLike]]


class PerceivedItem(BaseModel):
    name: str = Field(description="What the item is, in plain words, e.g. 'chapati', 'moong dal'")
    food_code: str | None
    quantity: float
    unit: str
    estimated_grams: float | None
    role: Role
    confidence: Confidence


class Perception(BaseModel):
    is_food: bool
    items: list[PerceivedItem]
    note: str | None = None


class PerceptionError(Exception):
    """The agent could not produce a usable perception."""


class PerceptionRefused(PerceptionError):
    pass


@dataclass
class PerceptionResult:
    perception: Perception
    model: str
    turns: int
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    dropped_codes: list[str] = field(default_factory=list)


SYSTEM_PROMPT = """\
You identify what is on a person's plate, for an Indian health-coaching app. \
You see a photo, a typed description, or both. Your output is perception only: \
items, portions and roles. You never state calories or nutrients; the app \
computes those from the Indian Nutrient Databank (INDB).

How to work:
1. List every distinct item you can see or that the person names: breads, rice, \
dals, sabzis, curries, curd, salad, chutney, pickle, drinks.
2. Call search_foods once with all item names together. Use common Indian names \
and English names (e.g. "rajma" and "kidney bean curry"). If an item has no good \
candidate, you may search again with other names; avoid more than two searches.
3. For each item pick the food_code whose dish really matches what is on the \
plate. If none truly matches, use null; never pick a different dish just because \
it contains the ingredient (plain curd is not "curd rice"; a banana is not \
"banana cake"). INDB holds cooked recipes; plain staples like plain curd, milk \
or whole fruit are often absent, and null is the correct answer for them.
4. Portion: if the item is countable in its INDB serving unit (search results \
show it, e.g. "chapati", "idli", "dosa"), give quantity in that unit. Otherwise \
use a household unit (katori, bowl, cup, glass, tablespoon, piece) or grams if \
the person stated grams. Always also give estimated_grams: your best visual \
estimate of the edible weight, or null if you truly cannot tell.
5. Role: the item's main role on the plate: grain, protein (dals, pulses, \
paneer, egg, meat, fish, soya), vegetable, dairy, fruit, fried, sweet, beverage \
or other.
6. Confidence: high when the item is clearly visible or stated; medium when the \
dish is ambiguous (e.g. which dal); low when you are guessing.

If the input is not a meal, set is_food to false and items to []. Put any \
important caveat (e.g. "gravy hides the vegetables") in note; otherwise null."""

SEARCH_TOOL: dict[str, Any] = {
    "name": "search_foods",
    "description": (
        "Search the Indian Nutrient Databank (cooked Indian recipes) by dish name. "
        "Pass every item name in one call. Returns, per query, candidate foods with "
        "their code, full name and INDB serving unit/weight."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Dish names to look up, e.g. ['chapati', 'moong dal']",
            }
        },
        "required": ["queries"],
        "additionalProperties": False,
    },
}

_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_NULLABLE_NUMBER = {"anyOf": [{"type": "number"}, {"type": "null"}]}

PERCEPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_food": {"type": "boolean"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "food_code": _NULLABLE_STRING,
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "estimated_grams": _NULLABLE_NUMBER,
                    "role": {"type": "string", "enum": list(Role.__args__)},
                    "confidence": {"type": "string", "enum": list(Confidence.__args__)},
                },
                "required": [
                    "name",
                    "food_code",
                    "quantity",
                    "unit",
                    "estimated_grams",
                    "role",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "note": _NULLABLE_STRING,
    },
    "required": ["is_food", "items", "note"],
    "additionalProperties": False,
}


def run_search(table: FoodTable, queries: list[str]) -> list[dict[str, Any]]:
    """The tool's implementation: deterministic lookup, no model involved."""
    results = []
    for query in queries[:MAX_QUERIES_PER_CALL]:
        candidates = [
            {
                "code": f.code,
                "name": f.name,
                "serving_unit": f.serving_unit,
                "serving_grams": f.serving_grams,
            }
            for f in table.search(query, limit=CANDIDATES_PER_QUERY)
        ]
        results.append({"query": query, "candidates": candidates})
    return results


def _user_content(image: PreparedImage | None, text: str | None) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if image is not None:
        content.append(image.as_content_block())
    if text:
        content.append({"type": "text", "text": f"The person says: {text}"})
    if image is not None and not text:
        content.append({"type": "text", "text": "What is on this plate?"})
    return content


async def perceive_plate(
    *,
    client: anthropic.AsyncAnthropic,
    table: FoodTable,
    meter: MeterFactory,
    image: PreparedImage | None = None,
    text: str | None = None,
    model: str = ModelTier.SONNET,
    effort: str = "low",
) -> PerceptionResult:
    if image is None and not (text and text.strip()):
        raise ValueError("need an image or a description")

    started = time.monotonic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": _user_content(image, text)}]
    result = PerceptionResult(
        perception=Perception(is_food=False, items=[]), model=model, turns=0, latency_ms=0
    )

    for _ in range(MAX_TURNS):
        result.turns += 1
        async with meter(MAX_TOKENS + INPUT_TOKEN_ESTIMATE) as m:
            response = await client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                # Stable prefix (tools → system) so repeated calls hit the prompt cache.
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
                ],
                tools=[SEARCH_TOOL],
                output_config={
                    "effort": effort,
                    "format": {"type": "json_schema", "schema": PERCEPTION_SCHEMA},
                },
                messages=messages,
            )
            usage = response.usage
            spent_in = (
                usage.input_tokens
                + (usage.cache_creation_input_tokens or 0)
                + (usage.cache_read_input_tokens or 0)
            )
            m.record(spent_in, usage.output_tokens)
            result.input_tokens += spent_in
            result.output_tokens += usage.output_tokens

        if response.stop_reason == "refusal":
            raise PerceptionRefused("the model declined this input")
        if response.stop_reason == "max_tokens":
            raise PerceptionError("perception was cut off (max_tokens)")

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "tool_use" and tool_uses:
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in tool_uses:
                if block.name != "search_foods":
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Unknown tool {block.name!r}.",
                            "is_error": True,
                        }
                    )
                    continue
                queries = [str(q) for q in block.input.get("queries", [])]
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(run_search(table, queries), ensure_ascii=False),
                    }
                )
            messages.append({"role": "user", "content": tool_results})
            continue

        text_block = next((b for b in response.content if b.type == "text"), None)
        if text_block is None:
            raise PerceptionError(f"no answer (stop_reason={response.stop_reason})")
        try:
            perception = Perception.model_validate_json(text_block.text)
        except ValidationError as exc:
            raise PerceptionError("answer did not match the perception schema") from exc

        # The model may only cite codes that exist; anything else becomes "unmatched".
        for item in perception.items:
            if item.food_code is not None and table.get(item.food_code) is None:
                result.dropped_codes.append(item.food_code)
                item.food_code = None
        result.perception = perception
        result.latency_ms = int((time.monotonic() - started) * 1000)
        return result

    raise PerceptionError(f"no answer after {MAX_TURNS} turns")
