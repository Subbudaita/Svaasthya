"""Rituals (F4) and the behaviour engine's daily close-out (F10).

``tick(now)`` runs every minute. For each person with an accepted program and
a linked Telegram account it decides, in their local time, whether the 07:00
brief or 21:30 check-in is due. ``ritual_runs`` has one row per ritual per
local day, so a tick that runs twice sends once; a ritual still undelivered
``catch_up_minutes`` after its time is recorded as missed (a logged incident).

The morning brief first closes out yesterday: each of the six tracks is
scored, a miss is silently covered by a grace day if one is left this month,
milestones pay the person's own rewards retroactively — and the brief is
where all of that is revealed.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from swasthya_engines import behaviour as be
from swasthya_engines.clock import minutes
from swasthya_engines.eating_window import kept
from swasthya_engines.meals import fingerprint, slot_for
from swasthya_engines.plate import describe_portion
from swasthya_engines.program_contract import window_of
from swasthya_engines.rituals import (
    BriefFacts,
    OwnBreakfast,
    checkin_prompt,
    choose_breakfast,
    choose_question,
    compose_brief,
    default_time,
    pillar_labels,
    schedule,
)

from swasthya_api.db.models import (
    AuditLog,
    ConsistencyTrack,
    DailyLog,
    GraceDay,
    HealthProfile,
    Meal,
    MemoryFact,
    PillarDay,
    ProgramContract,
    Reward,
    RitualRun,
    User,
)
from swasthya_api.db.session import user_session
from swasthya_api.gateway import Button, TelegramGateway
from swasthya_api.services import Services

log = logging.getLogger(__name__)

RITUALS = {"morning_brief": "morning_brief_at", "evening_checkin": "evening_checkin_at"}
PROTEIN_ROLES = {"protein", "dairy"}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class Candidate:
    user_id: uuid.UUID
    tz: ZoneInfo
    started_on: date
    times: dict[str, time]
    chat_id: int


def _is_fasting_day(fasting_days: list[str], day: date) -> bool:
    names = {d.strip().lower() for d in fasting_days}
    return WEEKDAYS[day.weekday()] in names or f"{WEEKDAYS[day.weekday()]}s" in names


def _has_protein(items: list[dict[str, Any]]) -> bool:
    return any(i.get("role") in PROTEIN_ROLES for i in items)


class RitualService:
    def __init__(self, services: Services, gateway: TelegramGateway) -> None:
        self.svc = services
        self.gw = gateway

    # --- Scheduling ----------------------------------------------------------

    async def candidates(self) -> list[Candidate]:
        async with self.svc.engine.begin() as conn:
            rows = (await conn.execute(text("SELECT * FROM app.ritual_candidates()"))).all()
        return [
            Candidate(
                user_id=r.user_id,
                tz=ZoneInfo(r.timezone),
                started_on=r.program_started_on,
                times={
                    "morning_brief": r.morning_brief_at or default_time("morning_brief"),
                    "evening_checkin": r.evening_checkin_at or default_time("evening_checkin"),
                },
                chat_id=int(r.telegram_id),
            )
            for r in rows
        ]

    async def tick(self, now: datetime | None = None) -> list[tuple[uuid.UUID, str, str]]:
        """Deliver every ritual that is due. Returns (user, ritual, status) for each action."""
        now = now or datetime.now(UTC)
        catch_up = timedelta(minutes=schedule()["catch_up_minutes"])
        done = []
        for c in await self.candidates():
            local = now.astimezone(c.tz)
            for ritual, at in c.times.items():
                if local.date() <= c.started_on:
                    continue  # rituals begin the day after onboarding
                due = datetime.combine(local.date(), at, c.tz)
                if local < due:
                    continue
                status = "sent" if local < due + catch_up else "missed"
                try:
                    claimed = await self._claim(c.user_id, ritual, local.date(), status, now)
                except Exception:
                    log.exception("claiming %s for %s failed", ritual, c.user_id)
                    continue
                if not claimed:
                    continue
                if status == "missed":
                    log.error(
                        "ritual incident: %s for %s on %s not delivered in time",
                        ritual,
                        c.user_id,
                        local.date(),
                    )
                    done.append((c.user_id, ritual, status))
                    continue
                try:
                    if ritual == "morning_brief":
                        await self.morning_brief(c, local.date(), now)
                    else:
                        await self.evening_checkin(c, local.date(), now)
                    done.append((c.user_id, ritual, "sent"))
                except Exception:
                    log.exception("delivering %s to %s failed", ritual, c.user_id)
                    await self._set_status(c.user_id, ritual, local.date(), "failed")
                    done.append((c.user_id, ritual, "failed"))
        return done

    async def _claim(self, user_id, ritual, day, status, now) -> bool:
        """Insert the run row first; the unique key makes delivery exactly-once."""
        async with user_session(self.svc.engine, user_id) as session:
            result = await session.execute(
                insert(RitualRun)
                .values(
                    user_id=user_id,
                    ritual=ritual,
                    local_date=day,
                    status=status,
                    sent_at=now if status == "sent" else None,
                )
                .on_conflict_do_nothing(constraint="ritual_runs_user_day_key")
                .returning(RitualRun.id)
            )
            return result.scalar() is not None

    async def _set_status(self, user_id, ritual, day, status, payload=None) -> None:
        async with user_session(self.svc.engine, user_id) as session:
            run = await session.scalar(
                select(RitualRun).where(RitualRun.ritual == ritual, RitualRun.local_date == day)
            )
            run.status = status
            if payload is not None:
                run.payload = payload

    # --- Shared reads ------------------------------------------------------------

    async def _contract(self, session) -> ProgramContract:
        return await session.scalar(
            select(ProgramContract)
            .where(ProgramContract.superseded_at.is_(None))
            .order_by(ProgramContract.version.desc())
            .limit(1)
        )

    async def _meals_on(self, session, day: date, tz: ZoneInfo) -> list[Meal]:
        start = datetime.combine(day, time(), tz)
        return list(
            (
                await session.scalars(
                    select(Meal)
                    .where(Meal.eaten_at >= start, Meal.eaten_at < start + timedelta(days=1))
                    .order_by(Meal.eaten_at)
                )
            ).all()
        )

    # --- Close out a day: score, grace, rewards ------------------------------------

    async def close_day(
        self, user_id: uuid.UUID, day: date, tz: ZoneInfo, now: datetime
    ) -> list[str]:
        """Score ``day`` on all six tracks. Returns recognition texts for rewards paid."""
        recognitions: list[str] = []
        async with user_session(self.svc.engine, user_id) as session:
            contract = await self._contract(session)
            body = contract.body
            user = await session.get(User, user_id)
            program_day = (day - user.program_started_on).days + 1
            meals = await self._meals_on(session, day, tz)
            log_row = await session.scalar(select(DailyLog).where(DailyLog.log_date == day))
            window = window_of(body)
            window_kept = None
            if window and meals:
                first = meals[0].eaten_at.astimezone(tz).time()
                last = meals[-1].eaten_at.astimezone(tz).time()
                window_kept = kept(window, first, last)
            results = be.score_day(
                be.DayInputs(
                    reports=(log_row.pillar_reports if log_row else {}) or {},
                    window_kept=window_kept,
                    meals_logged=len(meals),
                    meals_with_protein=sum(_has_protein(m.items) for m in meals),
                    is_fasting_day=_is_fasting_day(
                        body["parameters"].get("fasting_days") or [], day
                    ),
                    observation=program_day <= body.get("observation_days", 0),
                )
            )
            tracks = {t.pillar: t for t in await session.scalars(select(ConsistencyTrack))}
            month_start = day.replace(day=1)
            grace_counts = dict(
                (
                    await session.execute(
                        select(GraceDay.track_id, func.count())
                        .where(GraceDay.applied_on >= month_start, GraceDay.applied_on <= day)
                        .group_by(GraceDay.track_id)
                    )
                ).all()
            )
            available = list(
                await session.scalars(
                    select(Reward).where(Reward.status == "available").order_by(Reward.created_at)
                )
            )
            for pillar, result in results.items():
                track = tracks.get(pillar)
                if track is None:
                    continue
                upd = be.apply_day(
                    be.TrackState(
                        pillar,
                        track.current_run_days,
                        track.longest_run_days,
                        track.last_counted_on,
                    ),
                    day,
                    result,
                    grace_used_this_month=grace_counts.get(track.id, 0),
                )
                if upd.state.last_counted_on == track.last_counted_on:
                    continue  # already closed out
                track.current_run_days = upd.state.current_run_days
                track.longest_run_days = upd.state.longest_run_days
                track.last_counted_on = day
                track.identity_statement = be.identity(pillar)
                track.updated_at = now
                session.add(
                    PillarDay(user_id=user_id, pillar=pillar, day=day, result=upd.result.value)
                )
                if upd.grace_applied:
                    session.add(
                        GraceDay(user_id=user_id, track_id=track.id, pillar=pillar, applied_on=day)
                    )
                if upd.milestone:
                    reward = be.pick_reward(
                        [be.Reward(r.id, r.text, r.kind) for r in available], phase=contract.phase
                    )
                    if reward is not None:
                        paid = next(r for r in available if r.id == reward.id)
                        paid.status, paid.paid_at = "paid", now
                        paid.paid_for = {
                            "pillar": pillar,
                            "run_days": upd.milestone,
                            "day": day.isoformat(),
                        }
                        available.remove(paid)
                        recognitions.append(be.recognition(pillar, upd.milestone, reward.text))
        return recognitions

    # --- The 07:00 brief -------------------------------------------------------------

    async def morning_brief(self, c: Candidate, day: date, now: datetime) -> None:
        yesterday = day - timedelta(days=1)
        recognitions = (
            await self.close_day(c.user_id, yesterday, c.tz, now)
            if yesterday >= c.started_on
            else []
        )
        labels = dict(pillar_labels())
        async with user_session(self.svc.engine, c.user_id) as session:
            contract = await self._contract(session)
            body = contract.body
            user = await session.get(User, c.user_id)
            profile = await session.scalar(select(HealthProfile))
            unrevealed = list(
                await session.scalars(select(GraceDay).where(GraceDay.revealed_at.is_(None)))
            )
            for g in unrevealed:
                g.revealed_at = now  # retroactive: told the morning after, never before
            reveals = [labels[g.pillar].lower() for g in unrevealed]

            meals_y = await self._meals_on(session, yesterday, c.tz)
            window = window_of(body)
            y_status, y_last = None, None
            if meals_y and window:
                first = meals_y[0].eaten_at.astimezone(c.tz).time()
                y_last = meals_y[-1].eaten_at.astimezone(c.tz).time()
                y_status = "kept" if kept(window, first, y_last) else "missed"

            since = datetime.combine(day, time(), c.tz) - timedelta(days=30)
            recent = (await session.scalars(select(Meal).where(Meal.eaten_at >= since))).all()
            own: dict[tuple, OwnBreakfast] = {}
            for m in recent:
                if not m.items or slot_for(m.eaten_at.astimezone(c.tz).time()) != "breakfast":
                    continue
                key = fingerprint(m.items)
                label = ", ".join(describe_portion(_portion(i)) for i in m.items)
                prev = own.get(key)
                own[key] = OwnBreakfast(
                    label, _has_protein(m.items), (prev.times_eaten if prev else 0) + 1
                )
            breakfast = choose_breakfast(
                list(own.values()), profile.diet_pattern or "vegetarian", day
            )

            shown = [
                cl for cl in body["clauses"] if cl["pillar"] in {"nutrition", "movement", "sleep"}
            ]
            facts = BriefFacts(
                day=day,
                phase=contract.phase,
                program_day=(day - user.program_started_on).days + 1,
                observation_days=body.get("observation_days", 0),
                window=(window.open, window.close) if window else None,
                is_fasting_day=_is_fasting_day(body["parameters"].get("fasting_days") or [], day),
                breakfast=breakfast,
                yesterday_window=y_status,
                yesterday_last_bite=y_last,
                grace_reveals=reveals,
                recognitions=recognitions,
                clause_lines=[cl["text"] for cl in shown],
            )
            payload = {
                "contract_version": contract.version,
                "clauses": [cl["id"] for cl in shown]
                + (["eating_window.target"] if window else []),
                "breakfast": {"label": breakfast[0], "source": breakfast[1]},
                "grace_revealed": [g.pillar for g in unrevealed],
            }
            session.add(
                AuditLog(
                    user_id=c.user_id,
                    actor="rituals",
                    action="ritual.morning_brief",
                    contract_version=contract.version,
                    contract_clause=",".join(payload["clauses"]),
                    evidence=payload,
                )
            )
        await self.gw.send_message(c.chat_id, compose_brief(facts))
        await self._set_status(c.user_id, "morning_brief", day, "sent", payload)

    # --- The 21:30 check-in -----------------------------------------------------------

    async def evening_checkin(self, c: Candidate, day: date, now: datetime) -> None:
        async with user_session(self.svc.engine, c.user_id) as session:
            contract = await self._contract(session)
            window = window_of(contract.body)
            meals = await self._meals_on(session, day, c.tz)
            log_row = await session.scalar(select(DailyLog).where(DailyLog.log_date == day))
        signals = {"nothing_logged": not meals}
        if meals and window:
            first = minutes(meals[0].eaten_at.astimezone(c.tz).time())
            last = minutes(meals[-1].eaten_at.astimezone(c.tz).time())
            signals["first_bite_before_window"] = first < minutes(window.open) - 15
            signals["last_bite_after_window"] = last > minutes(window.close) + 15
        signals["meal_without_protein"] = any(not _has_protein(m.items) for m in meals)
        signals["energy_low"] = bool(log_row and log_row.energy and log_row.energy <= 2)
        qid, question = choose_question(signals)

        buttons = []
        for key, label in pillar_labels():
            if key == "mind":
                continue  # answering the check-in *is* the mind pillar
            buttons.append(Button(f"{label} ✓", f"ci:{day.isoformat()}:{key}:1"))
            buttons.append(Button(f"{label} ✗", f"ci:{day.isoformat()}:{key}:0"))
        await self.gw.send_message(c.chat_id, checkin_prompt(question), buttons=buttons)
        await self._set_status(
            c.user_id,
            "evening_checkin",
            day,
            "sent",
            {"question_id": qid, "question": question, "signals": signals},
        )

    # --- Answers -------------------------------------------------------------------

    async def record_report(self, user_id: uuid.UUID, day: date, pillar: str, kept_: bool) -> None:
        async with user_session(self.svc.engine, user_id) as session:
            row = await session.scalar(select(DailyLog).where(DailyLog.log_date == day))
            if row is None:
                row = DailyLog(user_id=user_id, log_date=day, pillar_reports={})
                session.add(row)
            row.pillar_reports = {**(row.pillar_reports or {}), pillar: kept_}

    async def record_causal_answer(
        self, user_id: uuid.UUID, answer: str, now: datetime | None = None
    ) -> bool:
        """Store the reply to tonight's causal question as a semantic fact."""
        now = now or datetime.now(UTC)
        window = timedelta(minutes=schedule()["answer_window_minutes"])
        async with user_session(self.svc.engine, user_id) as session:
            run = await session.scalar(
                select(RitualRun)
                .where(
                    RitualRun.ritual == "evening_checkin",
                    RitualRun.status == "sent",
                    RitualRun.answer.is_(None),
                    RitualRun.sent_at >= now - window,
                )
                .order_by(RitualRun.sent_at.desc())
                .limit(1)
            )
            if run is None:
                return False
            run.answer, run.answered_at = answer[:2000], now
            # Answering is the "mind" pillar for the day.
            row = await session.scalar(select(DailyLog).where(DailyLog.log_date == run.local_date))
            if row is None:
                row = DailyLog(user_id=user_id, log_date=run.local_date, pillar_reports={})
                session.add(row)
            row.pillar_reports = {**(row.pillar_reports or {}), "mind": True}
            session.add(
                MemoryFact(
                    user_id=user_id,
                    category="causal_answer",
                    fact=(
                        f'{run.local_date}: asked "{run.payload.get("question")}"'
                        f" — answered: {answer[:500]}"
                    ),
                    source="evening_checkin",
                    source_ref=run.id,
                )
            )
        return True

    async def set_time(self, user_id: uuid.UUID, ritual: str, at: time) -> None:
        async with user_session(self.svc.engine, user_id) as session:
            user = await session.get(User, user_id)
            setattr(user, RITUALS[ritual], at)


def _portion(item: dict[str, Any]):
    from swasthya_engines.plate import ItemOut

    return ItemOut(
        item["name"],
        item.get("food_code"),
        item.get("food_name"),
        item["quantity"],
        item["unit"],
        item.get("grams"),
        item.get("grams_source", "unknown"),
        item["role"],
        item.get("confidence", "high"),
        item.get("nutrients"),
    )


async def reports_by_pillar(services: Services, user_id: uuid.UUID) -> dict[str, list[str]]:
    """Pillar → recent results, newest last (for tests and the Program tab)."""
    async with user_session(services.engine, user_id) as session:
        rows = (
            await session.execute(
                select(PillarDay.pillar, PillarDay.result).order_by(PillarDay.day)
            )
        ).all()
    out: dict[str, list[str]] = defaultdict(list)
    for pillar, result in rows:
        out[pillar].append(result)
    return dict(out)
