"""Anytime chat (F14): one thread per person across Telegram and the PWA.

Each turn:
1. builds the "right now" context — one read across the four memory stores
   (episodic: today + last 3 days; semantic: recent facts; procedural: the
   contract; causal: standing hypotheses);
2. runs the supervisor graph with a Postgres checkpointer on a connection
   bound to this person (RLS on the checkpoint tables, thread_id = user id);
3. records the exchange, an audit row citing contract clauses and days, and
   an escalation if coaching was halted.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import anthropic
import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from sqlalchemy import func, or_, select
from swasthya_agents.graph import AgentDeps, AgentError, build_graph
from swasthya_agents.safety import red_flag
from swasthya_engines.clock import fmt, minutes
from swasthya_engines.plate import describe_portion
from swasthya_engines.program_contract import window_of

from swasthya_api.budget import TokenBudget, TokenCapExceeded
from swasthya_api.db.models import (
    AuditLog,
    Conversation,
    DailyLog,
    Escalation,
    HealthProfile,
    Hypothesis,
    Meal,
    MemoryFact,
    Message,
    PillarDay,
    ProgramContract,
    RitualRun,
    User,
)
from swasthya_api.db.session import user_session
from swasthya_api.rituals import _portion
from swasthya_api.services import FeatureUnavailable, Services

log = logging.getLogger(__name__)
CONTEXT_DAYS = 3
RECENT_FACTS = 8


@dataclass(frozen=True)
class ChatReply:
    text: str
    specialist: str | None
    cited_clauses: list[str]
    cited_days: list[str]
    confidence: str | None
    escalated: bool
    guard_action: str | None


class PersonToolbox:
    """The agent's read-only view of one person's data, always through RLS."""

    def __init__(self, services: Services, user_id: uuid.UUID, tz: ZoneInfo, numbers_visible: bool):
        self.svc, self.user_id, self.tz, self.numbers_visible = (
            services,
            user_id,
            tz,
            numbers_visible,
        )

    async def days(self, start: date, end: date) -> list[dict[str, Any]]:
        lo = datetime.combine(start, time(), self.tz)
        hi = datetime.combine(end + timedelta(days=1), time(), self.tz)
        async with user_session(self.svc.engine, self.user_id) as s:
            meals = (
                await s.scalars(
                    select(Meal)
                    .where(Meal.eaten_at >= lo, Meal.eaten_at < hi)
                    .order_by(Meal.eaten_at)
                )
            ).all()
            pillars = (
                await s.execute(
                    select(PillarDay.day, PillarDay.pillar, PillarDay.result).where(
                        PillarDay.day >= start, PillarDay.day <= end
                    )
                )
            ).all()
            logs = (
                await s.scalars(
                    select(DailyLog).where(DailyLog.log_date >= start, DailyLog.log_date <= end)
                )
            ).all()
            answers = (
                await s.execute(
                    select(RitualRun.local_date, RitualRun.payload, RitualRun.answer).where(
                        RitualRun.ritual == "evening_checkin",
                        RitualRun.local_date >= start,
                        RitualRun.local_date <= end,
                        RitualRun.answer.is_not(None),
                    )
                )
            ).all()
        out: dict[date, dict[str, Any]] = {}
        d = start
        while d <= end:
            out[d] = {
                "date": d.isoformat(),
                "weekday": f"{d:%A}",
                "meals": [],
                "pillars": {},
                "checkin": {},
            }
            d += timedelta(days=1)
        for m in meals:
            local = m.eaten_at.astimezone(self.tz)
            entry = {
                "time": fmt(local.time()),
                "how_logged": m.log_method,
                "items": [describe_portion(_portion(i)) for i in m.items],
                "roles": sorted({i["role"] for i in m.items}),
            }
            if self.numbers_visible and m.nutrients:
                entry["nutrients"] = m.nutrients
            out[local.date()]["meals"].append(entry)
        for day, pillar, result in pillars:
            out[day]["pillars"][pillar] = result
        for row in logs:
            out[row.log_date]["checkin"] = row.pillar_reports or {}
            if row.energy:
                out[row.log_date]["energy_1_to_5"] = row.energy
        for day, payload, answer in answers:
            out[day]["evening_question"] = payload.get("question")
            out[day]["evening_answer"] = answer
        for v in out.values():
            if v["meals"]:
                v["first_bite"], v["last_bite"] = v["meals"][0]["time"], v["meals"][-1]["time"]
        return list(out.values())

    async def search_facts(self, query: str) -> list[dict[str, Any]]:
        async with user_session(self.svc.engine, self.user_id) as s:
            tsq = func.websearch_to_tsquery("english", query)
            rows = (
                await s.execute(
                    select(MemoryFact.category, MemoryFact.fact, MemoryFact.created_at)
                    .where(
                        MemoryFact.superseded_at.is_(None),
                        or_(
                            MemoryFact.fact_tsv.op("@@")(tsq),
                            MemoryFact.fact.ilike(f"%{query[:60]}%"),
                        ),
                    )
                    .order_by(
                        func.ts_rank(MemoryFact.fact_tsv, tsq).desc(), MemoryFact.created_at.desc()
                    )
                    .limit(RECENT_FACTS)
                )
            ).all()
        return [{"category": c, "fact": f, "noted_on": t.date().isoformat()} for c, f, t in rows]

    async def food_lookup(self, query: str) -> list[dict[str, Any]]:
        table = self.svc.require_food_table()
        keys = ["protein_g", "fibre_g", "carb_g", "fat_g"]
        if self.numbers_visible:
            keys.insert(0, "energy_kcal")
        out = []
        for food in table.search(query, limit=3):
            grams = food.serving_grams
            per = (
                {
                    k: round(v * grams / 100, 1)
                    for k, v in food.per_100g.items()
                    if k in keys and v is not None
                }
                if grams
                else {}
            )
            out.append(
                {
                    "name": food.name,
                    "code": food.code,
                    "serving": f"1 {food.serving_unit} ({grams:g} g)" if grams else None,
                    "per_serving": per,
                    "source": table.dataset,
                }
            )
        return out


class ChatService:
    def __init__(self, services: Services) -> None:
        self.svc = services

    async def _context(
        self, user_id: uuid.UUID, now: datetime
    ) -> tuple[dict[str, Any], ZoneInfo, bool]:
        async with user_session(self.svc.engine, user_id) as s:
            user = await s.get(User, user_id)
            tz = ZoneInfo(user.timezone)
            contract = await s.scalar(
                select(ProgramContract)
                .where(ProgramContract.superseded_at.is_(None))
                .order_by(ProgramContract.version.desc())
                .limit(1)
            )
            profile = await s.scalar(select(HealthProfile))
            facts = (
                await s.execute(
                    select(MemoryFact.category, MemoryFact.fact)
                    .where(MemoryFact.superseded_at.is_(None))
                    .order_by(MemoryFact.created_at.desc())
                    .limit(RECENT_FACTS)
                )
            ).all()
            hypotheses = (
                await s.execute(
                    select(Hypothesis.statement, Hypothesis.prediction, Hypothesis.status).where(
                        Hypothesis.status.in_(("proposed", "supported", "confirmed"))
                    )
                )
            ).all()
        local = now.astimezone(tz)
        numbers_visible = bool(contract and contract.body.get("numbers_visible"))
        program: dict[str, Any] = {"clause_ids": []}
        window = None
        if contract:
            body = contract.body
            window = window_of(body)
            program = {
                "phase": contract.phase,
                "version": contract.version,
                "goal": body.get("goal"),
                "clause_ids": [c["id"] for c in body["clauses"]],
                "clauses": [{"id": c["id"], "text": c["text"]} for c in body["clauses"]],
                "prohibitions": body.get("prohibitions", []),
                "flags": body.get("flags", []),
                "parameters": {
                    k: body["parameters"].get(k)
                    for k in (
                        "eating_window",
                        "fasting_days",
                        "sleep_time",
                        "protein_target_g",
                        "diet_pattern",
                    )
                },
            }
        if profile:
            program["profile"] = {
                "diet_pattern": profile.diet_pattern,
                "conditions": profile.conditions,
                "medication_context": profile.medication_context,
                "lifestyle": profile.lifestyle,
            }
        toolbox = PersonToolbox(self.svc, user_id, tz, numbers_visible)
        days = await toolbox.days(local.date() - timedelta(days=CONTEXT_DAYS), local.date())
        today = days[-1]
        today_so_far: dict[str, Any] = {"meals": today["meals"], "checkin": today["checkin"]}
        if window:
            close = minutes(window.close)
            now_m = minutes(local.time())
            today_so_far["window"] = {
                "open": fmt(window.open),
                "close": fmt(window.close),
                "minutes_to_close": close - now_m if now_m <= close else 0,
            }
        context = {
            "today": local.date().isoformat(),
            "weekday": f"{local:%A}",
            "now_local": fmt(local.time()),
            "phase": contract.phase if contract else "not_onboarded",
            "program_day": (local.date() - user.program_started_on).days + 1
            if user.program_started_on
            else None,
            "numbers_visible": numbers_visible,
            "program": program,
            "today_so_far": today_so_far,
            "last_days": list(reversed(days[:-1])),
            "facts": [{"category": c, "fact": f} for c, f in facts],
            "hypotheses": [
                {"statement": st, "prediction": p, "status": stt} for st, p, stt in hypotheses
            ],
        }
        if not contract:
            context["note"] = "No program yet. Suggest /onboard when relevant."
        return context, tz, numbers_visible

    def _libpq_url(self) -> str:
        return str(self.svc.settings.database_url).replace(
            "postgresql+psycopg://", "postgresql://", 1
        )

    async def reply(
        self, user_id: uuid.UUID, text: str, *, channel: str, now: datetime | None = None
    ) -> ChatReply:
        now = now or datetime.now(UTC)
        # Red flags are answered without any model, so check them before requiring one.
        client = self.svc.anthropic if red_flag(text) else self.svc.require_anthropic()
        context, tz, _ = await self._context(user_id, now)
        deps = AgentDeps(
            client=client,
            meter=TokenBudget(self.svc.engine, user_id, self.svc.settings.daily_token_cap).metered,
            toolbox=PersonToolbox(self.svc, user_id, tz, context["numbers_visible"]),
            effort=self.svc.settings.plate_agent_effort,
        )
        turn = {
            "history": [
                {"role": "user", "content": text, "channel": channel, "at": now.isoformat()}
            ],
            "incoming": text,
            "channel": channel,
            "context": context,
        }
        # A connection bound to this person: RLS keys the checkpoint rows on it.
        async with await psycopg.AsyncConnection.connect(
            self._libpq_url(), autocommit=True, prepare_threshold=0, row_factory=dict_row
        ) as conn:
            await conn.execute("SELECT set_config('app.user_id', %s, false)", (str(user_id),))
            graph = build_graph(deps).compile(checkpointer=AsyncPostgresSaver(conn))
            out = await graph.ainvoke(turn, {"configurable": {"thread_id": str(user_id)}})

        answer = out.get("answer") or {}
        result = ChatReply(
            text=out["reply"],
            specialist=(out.get("route") or {}).get("specialist"),
            cited_clauses=answer.get("cited_clauses", []),
            cited_days=answer.get("cited_days", []),
            confidence=answer.get("confidence"),
            escalated=bool(out.get("escalation")),
            guard_action=out.get("guard_action"),
        )
        await self._record(user_id, text, result, out, channel, now, context)
        return result

    async def _record(self, user_id, text, result: ChatReply, out, channel, now, context) -> None:
        async with user_session(self.svc.engine, user_id) as s:
            conv = await s.scalar(select(Conversation).order_by(Conversation.created_at).limit(1))
            if conv is None:
                conv = Conversation(user_id=user_id, title="Swasthya")
                s.add(conv)
                await s.flush()
            conv.last_message_at = now
            inbound = Message(
                user_id=user_id, conversation_id=conv.id, channel=channel, role="user", content=text
            )
            s.add(inbound)
            s.add(
                Message(
                    user_id=user_id,
                    conversation_id=conv.id,
                    channel=channel,
                    role="assistant",
                    content=result.text,
                )
            )
            await s.flush()
            if result.escalated:
                s.add(
                    Escalation(
                        user_id=user_id,
                        trigger_source=out["escalation"]["source"],
                        category=out["escalation"]["category"],
                        message_id=inbound.id,
                        details={"text": text[:500]},
                    )
                )
            s.add(
                AuditLog(
                    user_id=user_id,
                    actor=f"agent:{result.specialist or 'safety'}",
                    action="chat.reply",
                    contract_version=context["program"].get("version"),
                    contract_clause=",".join(result.cited_clauses) or None,
                    evidence={
                        "cited_days": result.cited_days,
                        "confidence": result.confidence,
                        "guard": result.guard_action,
                        "dropped_citations": (out.get("answer") or {}).get("dropped_citations", []),
                    },
                    payload={"route_reason": (out.get("route") or {}).get("reason")},
                )
            )


async def safe_reply(services: Services, user_id: uuid.UUID, text: str, channel: str) -> str:
    """Chat for channels: known failures become honest sentences."""
    try:
        return (await ChatService(services).reply(user_id, text, channel=channel)).text
    except TokenCapExceeded:
        return "I've hit today's usage limit, so I can't think this through until tomorrow."
    except FeatureUnavailable:
        return "Chat isn't set up on the server yet (no model key)."
    except (AgentError, anthropic.APIError):
        log.exception("chat failed")
        return "I couldn't answer that just now. Could you try again in a minute?"
