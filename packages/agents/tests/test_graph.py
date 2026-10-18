"""Supervisor graph, driven by a scripted model client and an in-memory checkpointer."""

import json
from contextlib import asynccontextmanager
from datetime import date

import pytest
from anthropic.types import Message
from langgraph.checkpoint.memory import InMemorySaver
from swasthya_agents.graph import AgentDeps, AgentError, build_graph
from swasthya_agents.safety import guard, red_flag


def msg(content, stop="end_turn"):
    return Message.model_validate(
        {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "x",
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": 500, "output_tokens": 100},
        }
    )


def routed(specialist):
    return msg(
        [
            {
                "type": "tool_use",
                "id": "r",
                "name": "route",
                "input": {"specialist": specialist, "reason": "test"},
            }
        ],
        "tool_use",
    )


def answer(reply, clauses=(), days=(), confidence="medium"):
    return msg(
        [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "reply": reply,
                        "cited_clauses": list(clauses),
                        "cited_days": list(days),
                        "confidence": confidence,
                    }
                ),
            }
        ]
    )


def tool(name, args):
    return msg([{"type": "tool_use", "id": "t1", "name": name, "input": args}], "tool_use")


class Client:
    def __init__(self, *replies):
        self.replies, self.calls, self.messages = list(replies), [], self

    async def create(self, **kw):
        self.calls.append(kw)
        return self.replies.pop(0)


class Box:
    def __init__(self):
        self.asked = []

    async def days(self, start, end):
        self.asked.append((start, end))
        return [{"date": "2026-10-10", "pillars": {"sleep": "missed"}}]

    async def search_facts(self, query):
        return [{"fact": "gas after outside food"}]

    async def food_lookup(self, query):
        return [{"name": "Sambar", "serving": "1 bowl (257 g)", "protein_g": 8.6}]


class Meter:
    def __init__(self):
        self.calls = 0

    @asynccontextmanager
    async def __call__(self, n):
        self.calls += 1

        class M:
            def record(self, i, o):
                pass

        yield M()


CONTEXT = {
    "today": "2026-10-18",
    "numbers_visible": False,
    "program": {
        "phase": "reset",
        "clause_ids": ["sleep.consistent_bedtime", "eating_window.target"],
    },
    "last_days": [
        {"date": "2026-10-17", "pillars": {"sleep": "missed"}, "last_bite": "22:40"},
        {"date": "2026-10-16"},
        {"date": "2026-10-15"},
    ],
}


def turn(text, channel="telegram"):
    return {
        "history": [{"role": "user", "content": text, "channel": channel}],
        "incoming": text,
        "channel": channel,
        "context": CONTEXT,
    }


async def run(client, *turns, thread="u1"):
    graph = build_graph(AgentDeps(client=client, meter=Meter(), toolbox=Box())).compile(
        checkpointer=InMemorySaver()
    )
    out = None
    for t in turns:
        out = await graph.ainvoke(t, {"configurable": {"thread_id": thread}})
    return graph, out


async def test_tired_question_is_routed_grounded_and_cited():
    client = Client(
        routed("insight"),
        answer(
            "You slept short after a 22:40 last bite on the 17th — that's the simplest "
            "explanation. I'm fairly sure.",
            clauses=["sleep.consistent_bedtime", "made.up"],
            days=["2026-10-17", "1999-01-01"],
        ),
    )
    _, out = await run(client, turn("why am I so tired today?"))
    assert out["route"]["specialist"] == "insight"
    assert out["answer"]["cited_clauses"] == ["sleep.consistent_bedtime"]
    assert out["answer"]["cited_days"] == ["2026-10-17"]
    assert set(out["answer"]["dropped_citations"]) == {"made.up", "1999-01-01"}
    router, spec = client.calls
    assert router["model"] == "claude-haiku-4-5" and router["tool_choice"]["name"] == "route"
    assert spec["model"] == "claude-sonnet-5"
    # The last three days reach the model inside the context block...
    assert "22:40" in spec["messages"][-1]["content"][0]["text"]
    # ...and the contract is a cached, stable system prefix.
    assert spec["system"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_tools_retrieve_older_days_and_extend_citable_days():
    box_client = Client(
        routed("insight"),
        tool("get_days", {"start": "2026-10-08", "end": "2026-10-12"}),
        answer("On the 10th you also slept short.", days=["2026-10-10"]),
    )
    _, out = await run(box_client, turn("was last week the same?"))
    assert out["answer"]["cited_days"] == ["2026-10-10"]  # retrieved, so citable
    tool_result = box_client.calls[2]["messages"][-1]["content"][0]
    assert "2026-10-10" in tool_result["content"] and not tool_result["is_error"]


async def test_bad_tool_range_is_an_error_result_not_a_crash():
    client = Client(
        routed("insight"),
        tool("get_days", {"start": "2026-01-01", "end": "2026-10-01"}),
        answer("I can only look a month at a time."),
    )
    await run(client, turn("this year?"))
    assert client.calls[2]["messages"][-1]["content"][0]["is_error"] is True


async def test_history_persists_across_channels_in_one_thread():
    client = Client(
        routed("nutrition_gut"),
        answer("Have dal and two roti."),
        routed("nutrition_gut"),
        answer("Yes — the dal I mentioned at lunch."),
    )
    graph, out = await run(
        client,
        turn("what should I eat now?", "telegram"),
        turn("is that still fine for dinner?", "pwa"),
    )
    second_call = client.calls[3]["messages"]
    assert second_call[0] == {"role": "user", "content": "what should I eat now?"}
    assert second_call[1]["content"] == "Have dal and two roti."
    channels = [h.get("channel") for h in out["history"]]
    assert channels == ["telegram", "telegram", "pwa", "pwa"]


async def test_red_flag_halts_before_any_model_call():
    client = Client()
    _, out = await run(client, turn("I have chest pain and my left arm hurts"))
    assert client.calls == [] and out["escalation"]["category"] == "cardiac"
    assert "112" in out["reply"]


async def test_guard_replaces_medication_advice():
    client = Client(routed("clinical"), answer("You could reduce your metformin to 500 mg."))
    _, out = await run(client, turn("can I take less metformin?"))
    assert out["guard_action"] == "referral" and "doctor" in out["reply"]
    assert "500" not in out["reply"]


async def test_router_nonsense_falls_back_and_refusal_raises():
    client = Client(msg([{"type": "text", "text": "hmm"}]), answer("ok"))
    _, out = await run(client, turn("hello"))
    assert out["route"]["specialist"] == "insight"
    with pytest.raises(AgentError):
        await run(Client(routed("mind"), msg([], "refusal")), turn("x"))


@pytest.mark.parametrize(
    "text,category",
    [
        ("I passed out after lunch", "syncope"),
        ("I want to kill myself", "self_harm"),
        ("my sugar was 52 this morning", "hypoglycaemia"),
        ("vomiting blood", "bleeding"),
    ],
)
def test_red_flags(text, category):
    assert red_flag(text).category == category


@pytest.mark.parametrize(
    "text",
    [
        "my thyroid levels came back",
        "chest day at the gym was great",
        "I feel tired",
        "sugar in my chai",
    ],
)
def test_not_red_flags(text):
    assert red_flag(text) is None


@pytest.mark.parametrize(
    "reply,action",
    [
        ("You have diabetes, honestly.", "referral"),
        ("Stop taking your thyroxine for a week.", "referral"),
        ("Take 1000 IU of vitamin D.", "referral"),
        ("That plate was about 650 kcal. Nice dal though.", "redacted_calories"),
        ("Walk after dinner tonight.", "passed"),
        ("   ", "fallback"),
    ],
)
def test_output_guard(reply, action):
    g = guard(reply, numbers_visible=False)
    assert g.action == action
    if action == "redacted_calories":
        assert g.text == "Nice dal though."


def test_calories_allowed_once_numbers_are_visible():
    assert guard("About 650 kcal.", numbers_visible=True).action == "passed"


def test_specialists_are_seven():
    from swasthya_agents.graph import specialists

    assert sorted(specialists()) == [
        "adherence",
        "circadian",
        "clinical",
        "insight",
        "mind",
        "movement",
        "nutrition_gut",
    ]


def test_dates_are_iso():
    assert date.fromisoformat(CONTEXT["today"])
