"""JITAI delivery (F5): decide each minute, send rarely, score what happened.

Decisions come from ``swasthya_engines.jitai``; this module only gathers the
tailoring variables from the person's data, enforces delivery through the
same exactly-once pattern as rituals (unique per rule per local day), and
scores each nudge's proximal outcome when the next morning closes the day.
"""

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from swasthya_engines.eating_window import kept
from swasthya_engines.jitai import Gate, Tailoring, decide, outcome_met
from swasthya_engines.meals import slot_for
from swasthya_engines.program_contract import window_of

from swasthya_api.db.models import (
    DailyLog,
    HealthProfile,
    JitaiNudge,
    Meal,
    PillarDay,
    ProgramContract,
    RitualRun,
    User,
)
from swasthya_api.db.session import user_session
from swasthya_api.gateway import TelegramGateway
from swasthya_api.rituals import PROTEIN_ROLES, Candidate, RitualService, _is_fasting_day
from swasthya_api.services import Services

log = logging.getLogger(__name__)
LOOKBACK_DAYS = 7


def _protein(meal: Meal) -> bool:
    return any(i.get("role") in PROTEIN_ROLES for i in meal.items)


class NudgeService:
    def __init__(self, services: Services, gateway: TelegramGateway) -> None:
        self.svc = services
        self.gw = gateway

    async def tick(self, now: datetime | None = None) -> list[tuple[uuid.UUID, str, str]]:
        now = now or datetime.now(UTC)
        done = []
        for c in await RitualService(self.svc, self.gw).candidates():
            try:
                outcome = await self._consider(c, now)
            except Exception:
                log.exception("jitai decision for %s failed", c.user_id)
                continue
            if outcome:
                done.append((c.user_id, *outcome))
        return done

    async def _tailoring(self, s, c: Candidate, local: datetime) -> tuple[Tailoring, Gate]:
        today = local.date()
        contract = await s.scalar(
            select(ProgramContract)
            .where(ProgramContract.superseded_at.is_(None))
            .order_by(ProgramContract.version.desc())
            .limit(1)
        )
        body = contract.body
        window = window_of(body)
        user = await s.get(User, c.user_id)
        profile = await s.scalar(select(HealthProfile))
        dip = (profile.lifestyle or {}).get("energy_dip_time") if profile else None

        since = datetime.combine(today - timedelta(days=LOOKBACK_DAYS), time(), c.tz)
        meals = (
            await s.scalars(select(Meal).where(Meal.eaten_at >= since).order_by(Meal.eaten_at))
        ).all()
        by_day: dict[date, list[Meal]] = {}
        for m in meals:
            by_day.setdefault(m.eaten_at.astimezone(c.tz).date(), []).append(m)
        today_meals = by_day.get(today, [])

        late, no_breakfast = [], 0
        for d, ms in by_day.items():
            if d == today:
                continue
            last = ms[-1].eaten_at.astimezone(c.tz).time()
            if (
                window
                and kept(window, ms[0].eaten_at.astimezone(c.tz).time(), last) is False
                and slot_for(last) == "dinner"
            ):
                late.append(last)
            if not any(slot_for(m.eaten_at.astimezone(c.tz).time()) == "breakfast" for m in ms):
                no_breakfast += 1

        yesterday = today - timedelta(days=1)
        sleep_result = await s.scalar(
            select(PillarDay.result).where(PillarDay.pillar == "sleep", PillarDay.day == yesterday)
        )
        reports = await s.scalar(
            select(DailyLog.pillar_reports).where(DailyLog.log_date == yesterday)
        )
        short_sleep = sleep_result in ("missed", "grace") or (reports or {}).get("sleep") is False
        brief = await s.scalar(
            select(RitualRun.payload).where(
                RitualRun.ritual == "morning_brief", RitualRun.local_date == today
            )
        )

        sent_7d = await s.scalar(
            select(func.count())
            .select_from(JitaiNudge)
            .where(
                JitaiNudge.decision == "send", JitaiNudge.decided_at >= local - timedelta(days=7)
            )
        )
        sent_today = await s.scalar(
            select(func.count())
            .select_from(JitaiNudge)
            .where(JitaiNudge.decision == "send", JitaiNudge.local_date == today)
        )
        program_day = (today - user.program_started_on).days + 1
        tailoring = Tailoring(
            local_now=local,
            window_open=window.open if window else None,
            window_close=window.close if window else None,
            energy_dip_time=time.fromisoformat(dip) if dip else None,
            breakfast_plan=((brief or {}).get("breakfast") or {}).get("label"),
            short_sleep_last_night=short_sleep,
            meals_today=len(today_meals),
            dinner_logged=any(
                slot_for(m.eaten_at.astimezone(c.tz).time()) == "dinner" for m in today_meals
            ),
            late_last_bites_7d=tuple(late),
            breakfast_missing_days_7d=no_breakfast,
        )
        gate = Gate(
            in_observation=program_day <= body.get("observation_days", 0),
            is_fasting_day=_is_fasting_day(body["parameters"].get("fasting_days") or [], today),
            sent_last_7_days=sent_7d,
            sent_today=sent_today,
            ritual_times=tuple(c.times.values()),
        )
        return tailoring, gate

    async def _consider(self, c: Candidate, now: datetime) -> tuple[str, str] | None:
        local = now.astimezone(c.tz)
        if local.date() <= c.started_on:
            return None
        async with user_session(self.svc.engine, c.user_id) as s:
            tailoring, gate = await self._tailoring(s, c, local)
            decision, nudge = decide(tailoring, gate)
            if nudge is None:
                return None
            sending = decision == "send"
            row = await s.execute(
                insert(JitaiNudge)
                .values(
                    user_id=c.user_id,
                    rule_id=nudge.rule_id,
                    local_date=local.date(),
                    decided_at=now,
                    decision="send" if sending else "suppressed",
                    suppressed_because=None if sending else decision.split(":", 1)[1],
                    tailoring=nudge.tailoring,
                    message=nudge.text,
                    outcome_name=nudge.outcome,
                )
                .on_conflict_do_nothing(constraint="jitai_nudges_user_rule_day_key")
                .returning(JitaiNudge.id)
            )
            if row.scalar() is None:
                return None  # already decided for this rule today
        if sending:
            await self.gw.send_message(c.chat_id, nudge.text)
        return nudge.rule_id, decision

    async def score_outcomes(
        self, user_id: uuid.UUID, day: date, tz: ZoneInfo, now: datetime
    ) -> int:
        """Proximal outcomes for the day's sent nudges. Called when the day closes."""
        async with user_session(self.svc.engine, user_id) as s:
            nudges = (
                await s.scalars(
                    select(JitaiNudge).where(
                        JitaiNudge.local_date == day,
                        JitaiNudge.decision == "send",
                        JitaiNudge.outcome_scored_at.is_(None),
                    )
                )
            ).all()
            if not nudges:
                return 0
            start = datetime.combine(day, time(), tz)
            meals = (
                await s.scalars(
                    select(Meal)
                    .where(Meal.eaten_at >= start, Meal.eaten_at < start + timedelta(days=1))
                    .order_by(Meal.eaten_at)
                )
            ).all()
            window_result = await s.scalar(
                select(PillarDay.result).where(
                    PillarDay.pillar == "eating_window", PillarDay.day == day
                )
            )
            for n in nudges:
                n.outcome_met = outcome_met(
                    n.outcome_name,
                    sent_at=n.decided_at,
                    meal_times=[m.eaten_at for m in meals],
                    protein_meal_times=[m.eaten_at for m in meals if _protein(m)],
                    window_kept={"met": True, "missed": False, "grace": False}.get(window_result),
                )
                n.outcome_scored_at = now
            return len(nudges)


def as_dict(n: JitaiNudge) -> dict[str, Any]:
    return {
        "rule": n.rule_id,
        "day": n.local_date.isoformat(),
        "decision": n.decision,
        "outcome_met": n.outcome_met,
    }
