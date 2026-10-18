"""The supervisor graph (docs/PLAN.md §5, F14).

    START → precheck ─┬─ red flag ────────────────────────────→ finalize → END
                      └─ route (Haiku) → specialist (Sonnet) → guard ↗

* precheck — deterministic red-flag screen; coaching halts on a match.
* route    — Haiku picks one of seven specialists (forced, strict tool).
* specialist — Sonnet with retrieval tools over the person's own data, a
  structured answer that cites contract clauses and days, metered per call.
* guard    — deterministic output guard; fails closed.

State lives in a LangGraph checkpoint keyed by ``thread_id`` = the person's
id, so Telegram at lunch and the PWA at night are one conversation.
"""

import json
import operator
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Protocol, TypedDict

import anthropic
import yaml
from langgraph.graph import END, START, StateGraph
from swasthya_shared.models import ModelTier

from swasthya_agents.safety import guard, red_flag

PROMPTS = Path(__file__).parent / "prompts"
HISTORY_TURNS = 20  # messages kept in the model's view; the checkpoint keeps all
MAX_TOOL_TURNS = 4
ROUTER_MAX_TOKENS = 300
SPECIALIST_MAX_TOKENS = 3_000
INPUT_TOKEN_ESTIMATE = 12_000


class MeterLike(Protocol):
    def record(self, input_tokens: int, output_tokens: int) -> None: ...


MeterFactory = Callable[[int], AbstractAsyncContextManager[MeterLike]]


class Toolbox(Protocol):
    """Read-only access to the person's own data, implemented by the API under RLS."""

    async def days(self, start: date, end: date) -> list[dict[str, Any]]: ...

    async def search_facts(self, query: str) -> list[dict[str, Any]]: ...

    async def food_lookup(self, query: str) -> list[dict[str, Any]]: ...


@dataclass
class AgentDeps:
    client: anthropic.AsyncAnthropic
    meter: MeterFactory
    toolbox: Toolbox
    router_model: str = ModelTier.HAIKU
    specialist_model: str = ModelTier.SONNET
    effort: str = "low"


class TurnState(TypedDict, total=False):
    # Persisted across turns (appended); everything else is per turn.
    history: Annotated[list[dict[str, Any]], operator.add]
    incoming: str
    channel: str
    context: dict[str, Any]
    route: dict[str, Any]
    answer: dict[str, Any]
    reply: str
    guard_action: str
    escalation: dict[str, Any] | None


@cache
def specialists() -> dict[str, dict[str, str]]:
    return yaml.safe_load((PROMPTS / "specialists.yaml").read_text(encoding="utf-8"))


@cache
def base_prompt() -> str:
    return (PROMPTS / "base.md").read_text(encoding="utf-8")


ROUTE_TOOL = {
    "name": "route",
    "description": "Choose the one specialist best placed to answer the person's latest message.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "specialist": {"type": "string", "enum": sorted(specialists())},
            "reason": {"type": "string"},
        },
        "required": ["specialist", "reason"],
        "additionalProperties": False,
    },
}

TOOLS = [
    {
        "name": "get_days",
        "description": (
            "The person's own day-by-day record between two local dates (inclusive, max 31 "
            "days): meals with times and roles, the six pillar results, check-in answers, "
            "energy. Resolve phrases like 'last Tuesday' using today's date from the context."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "YYYY-MM-DD"},
                "end": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_facts",
        "description": (
            "Search durable facts about this person: corrections, answers to evening "
            "questions, preferences, triggers."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "food_lookup",
        "description": (
            "Look up an Indian dish in the Indian Nutrient Databank: serving size and "
            "nutrients per serving. Use this for any quantity you state."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "cited_clauses": {"type": "array", "items": {"type": "string"}},
        "cited_days": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low", "unknown"]},
    },
    "required": ["reply", "cited_clauses", "cited_days", "confidence"],
    "additionalProperties": False,
}


class AgentError(Exception):
    pass


async def _call(deps: AgentDeps, max_tokens: int, **kwargs: Any):
    async with deps.meter(max_tokens + INPUT_TOKEN_ESTIMATE) as m:
        response = await deps.client.messages.create(max_tokens=max_tokens, **kwargs)
        u = response.usage
        m.record(
            u.input_tokens
            + (u.cache_creation_input_tokens or 0)
            + (u.cache_read_input_tokens or 0),
            u.output_tokens,
        )
    if response.stop_reason == "refusal":
        raise AgentError("model declined")
    if response.stop_reason == "max_tokens":
        raise AgentError("answer cut off")
    return response


def _conversation(state: TurnState) -> list[dict[str, Any]]:
    """Prior turns as plain text messages, oldest first, user-first."""
    prior = [h for h in state.get("history", [])[:-1] if h["role"] in ("user", "assistant")]
    prior = prior[-HISTORY_TURNS:]
    while prior and prior[0]["role"] != "user":
        prior = prior[1:]
    return [{"role": h["role"], "content": h["content"]} for h in prior]


def build_graph(deps: AgentDeps) -> StateGraph:
    async def precheck(state: TurnState) -> dict[str, Any]:
        flag = red_flag(state["incoming"])
        if flag is None:
            return {"escalation": None}
        return {
            "escalation": {"category": flag.category, "source": "red_flag_stopgap"},
            "reply": flag.reply,
            "guard_action": "escalated",
            "route": {"specialist": None, "reason": "red flag"},
            "answer": {"cited_clauses": [], "cited_days": [], "confidence": "high"},
        }

    async def route(state: TurnState) -> dict[str, Any]:
        menu = "\n".join(f"- {k}: {v['when']}" for k, v in sorted(specialists().items()))
        response = await _call(
            deps,
            ROUTER_MAX_TOKENS,
            model=deps.router_model,
            system=f"Route the person's latest message to one specialist.\n{menu}",
            tools=[ROUTE_TOOL],
            tool_choice={"type": "tool", "name": "route"},
            messages=[*_conversation(state)[-6:], {"role": "user", "content": state["incoming"]}],
        )
        call = next((b for b in response.content if b.type == "tool_use"), None)
        choice = (call.input if call else {}) or {}
        if choice.get("specialist") not in specialists():
            choice = {"specialist": "insight", "reason": "router fallback"}
        return {"route": choice}

    async def specialist(state: TurnState) -> dict[str, Any]:
        key = state["route"]["specialist"]
        spec = specialists()[key]
        ctx = state["context"]
        system = [
            {"type": "text", "text": base_prompt()},
            {
                "type": "text",
                "text": f"Specialist: {spec['title']}. {spec['focus']}",
            },
            {
                # Stable per person until the contract changes: the cache prefix.
                "type": "text",
                "text": "Program Contract and profile:\n"
                + json.dumps(ctx.get("program", {}), ensure_ascii=False, sort_keys=True),
                "cache_control": {"type": "ephemeral"},
            },
        ]
        volatile = {k: v for k, v in ctx.items() if k != "program"}
        messages: list[dict[str, Any]] = [
            *_conversation(state),
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "<context>\n"
                        + json.dumps(volatile, ensure_ascii=False, default=str)
                        + "\n</context>",
                    },
                    {"type": "text", "text": state["incoming"]},
                ],
            },
        ]
        seen_days = {d["date"] for d in ctx.get("last_days", [])} | {ctx.get("today")}
        for _ in range(MAX_TOOL_TURNS):
            response = await _call(
                deps,
                SPECIALIST_MAX_TOKENS,
                model=deps.specialist_model,
                system=system,
                tools=TOOLS,
                output_config={
                    "effort": deps.effort,
                    "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
                },
                messages=messages,
            )
            uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "tool_use" and uses:
                messages.append({"role": "assistant", "content": response.content})
                results = []
                for b in uses:
                    content, is_error = await _run_tool(deps.toolbox, b.name, b.input, seen_days)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": b.id,
                            "content": content,
                            "is_error": is_error,
                        }
                    )
                messages.append({"role": "user", "content": results})
                continue
            text = next((b.text for b in response.content if b.type == "text"), None)
            if text is None:
                raise AgentError("no answer")
            answer = json.loads(text)
            clause_ids = set(ctx.get("program", {}).get("clause_ids", []))
            answer["dropped_citations"] = sorted(
                {c for c in answer["cited_clauses"] if c not in clause_ids}
                | {d for d in answer["cited_days"] if d not in seen_days}
            )
            answer["cited_clauses"] = [c for c in answer["cited_clauses"] if c in clause_ids]
            answer["cited_days"] = [d for d in answer["cited_days"] if d in seen_days]
            return {"answer": answer, "reply": answer["reply"]}
        raise AgentError(f"no answer after {MAX_TOOL_TURNS} tool turns")

    async def output_guard(state: TurnState) -> dict[str, Any]:
        g = guard(state["reply"], numbers_visible=state["context"].get("numbers_visible", False))
        return {"reply": g.text, "guard_action": g.action}

    async def finalize(state: TurnState) -> dict[str, Any]:
        return {
            "history": [
                {
                    "role": "assistant",
                    "content": state["reply"],
                    "channel": state.get("channel"),
                    "specialist": state.get("route", {}).get("specialist"),
                }
            ]
        }

    g = StateGraph(TurnState)
    g.add_node("precheck", precheck)
    g.add_node("route", route)
    g.add_node("specialist", specialist)
    g.add_node("guard", output_guard)
    g.add_node("finalize", finalize)
    g.add_edge(START, "precheck")
    g.add_conditional_edges(
        "precheck",
        lambda s: "finalize" if s.get("escalation") else "route",
        {"finalize": "finalize", "route": "route"},
    )
    g.add_edge("route", "specialist")
    g.add_edge("specialist", "guard")
    g.add_edge("guard", "finalize")
    g.add_edge("finalize", END)
    return g


async def _run_tool(
    toolbox: Toolbox, name: str, args: dict[str, Any], seen_days: set
) -> tuple[str, bool]:
    try:
        if name == "get_days":
            start, end = date.fromisoformat(args["start"]), date.fromisoformat(args["end"])
            if end < start or (end - start).days > 31:
                return "Range must be 0-31 days with end >= start.", True
            days = await toolbox.days(start, end)
            seen_days.update(d["date"] for d in days)
            return json.dumps(days, ensure_ascii=False, default=str), False
        if name == "search_facts":
            return json.dumps(
                await toolbox.search_facts(args["query"]), ensure_ascii=False, default=str
            ), False
        if name == "food_lookup":
            return json.dumps(await toolbox.food_lookup(args["query"]), ensure_ascii=False), False
    except (ValueError, KeyError) as exc:
        return f"Bad arguments: {exc}", True
    return f"Unknown tool {name!r}.", True
