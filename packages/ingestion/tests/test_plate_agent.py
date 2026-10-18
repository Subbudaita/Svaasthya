"""The agent loop, driven by a scripted client.

These tests exercise our code — loop control, tool execution, metering, code
validation, failure handling — not the model's perception quality. Perception
quality is measured against real photos by evals/vision.
"""

import io
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message
from PIL import Image
from swasthya_engines.nutrition import FoodTable
from swasthya_ingestion.images import InvalidImageError, prepare_image
from swasthya_ingestion.plate_agent import (
    MAX_TURNS,
    PerceptionError,
    PerceptionRefused,
    perceive_plate,
)
from swasthya_shared.models import ModelTier

FIXTURE = Path(__file__).parents[2] / "engines" / "tests" / "fixtures" / "foods.json"


def message(content: list[dict[str, Any]], stop_reason: str, usage=(1000, 200)) -> Message:
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": ModelTier.SONNET,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": usage[0], "output_tokens": usage[1]},
        }
    )


def tool_call(queries: list[str]) -> Message:
    return message(
        [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "search_foods",
                "input": {"queries": queries},
            }
        ],
        "tool_use",
    )


def final(perception: dict[str, Any]) -> Message:
    return message([{"type": "text", "text": json.dumps(perception)}], "end_turn")


class ScriptedClient:
    def __init__(self, replies: list[Message]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    async def create(self, **kwargs: Any) -> Message:
        self.calls.append(kwargs)
        return self.replies.pop(0)


class Meter:
    def __init__(self) -> None:
        self.reserved: list[int] = []
        self.recorded: list[int] = []

    @asynccontextmanager
    async def __call__(self, max_tokens: int):
        self.reserved.append(max_tokens)
        outer = self

        class _M:
            def record(self, i: int, o: int) -> None:
                outer.recorded.append(i + o)

        yield _M()


@pytest.fixture
def table() -> FoodTable:
    return FoodTable.from_dict(json.loads(FIXTURE.read_text()))


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), (200, 150, 100)).save(buf, format="JPEG")
    return buf.getvalue()


ITEM = {
    "name": "chapati",
    "food_code": "T001",
    "quantity": 2,
    "unit": "chapati",
    "estimated_grams": 70,
    "role": "grain",
    "confidence": "high",
}


async def test_tool_loop_then_answer(table):
    client = ScriptedClient(
        [
            tool_call(["chapati", "moong dal"]),
            final({"is_food": True, "items": [ITEM], "note": None}),
        ]
    )
    meter = Meter()
    result = await perceive_plate(
        client=client, table=table, meter=meter, image=prepare_image(jpeg())
    )
    assert result.turns == 2
    assert result.perception.items[0].food_code == "T001"
    # Every call was metered before it ran and settled after.
    assert len(meter.reserved) == len(meter.recorded) == 2
    assert result.input_tokens + result.output_tokens == sum(meter.recorded)
    # The tool result returned real table candidates for each query.
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    payload = json.loads(tool_result["content"])
    assert [r["query"] for r in payload] == ["chapati", "moong dal"]
    assert payload[0]["candidates"][0]["code"] == "T001"
    # Exact model ID and stable, cacheable system prompt.
    assert all(c["model"] == "claude-sonnet-5" for c in client.calls)
    assert client.calls[0]["system"] == client.calls[1]["system"]


async def test_invented_code_is_dropped(table):
    bad = {**ITEM, "food_code": "NOPE42"}
    client = ScriptedClient([final({"is_food": True, "items": [bad], "note": None})])
    result = await perceive_plate(client=client, table=table, meter=Meter(), text="2 rotis")
    assert result.perception.items[0].food_code is None
    assert result.dropped_codes == ["NOPE42"]


async def test_refusal_raises(table):
    client = ScriptedClient([message([], "refusal")])
    with pytest.raises(PerceptionRefused):
        await perceive_plate(client=client, table=table, meter=Meter(), text="lunch")


async def test_truncation_raises(table):
    client = ScriptedClient([message([{"type": "text", "text": '{"is_fo'}], "max_tokens")])
    with pytest.raises(PerceptionError):
        await perceive_plate(client=client, table=table, meter=Meter(), text="lunch")


async def test_schema_violation_raises(table):
    client = ScriptedClient([message([{"type": "text", "text": '{"items": 3}'}], "end_turn")])
    with pytest.raises(PerceptionError):
        await perceive_plate(client=client, table=table, meter=Meter(), text="lunch")


async def test_loop_is_bounded(table):
    client = ScriptedClient([tool_call(["dal"]) for _ in range(MAX_TURNS + 2)])
    meter = Meter()
    with pytest.raises(PerceptionError):
        await perceive_plate(client=client, table=table, meter=meter, text="dal")
    assert len(client.calls) == MAX_TURNS  # a runaway loop stops at the cap


async def test_needs_some_input(table):
    with pytest.raises(ValueError):
        await perceive_plate(client=ScriptedClient([]), table=table, meter=Meter(), text="  ")


def test_image_is_downscaled_to_jpeg():
    prepared = prepare_image(jpeg())
    with Image.open(io.BytesIO(prepared.data)) as img:
        assert max(img.size) <= 1568
    assert prepared.media_type == "image/jpeg"


def test_garbage_image_rejected():
    with pytest.raises(InvalidImageError):
        prepare_image(b"not an image")
