"""Meal logging — every method ends in the same deterministic analysis.

Photo and text go through the plate agent (perception); quick-pick and "same
as yesterday" copy a previous meal without any model call. In every case the
numbers come from ``swasthya_engines.plate`` and the database write happens in
a short user-scoped transaction *after* any model call, never during one.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text, update
from swasthya_engines.meals import fingerprint, slot_for
from swasthya_engines.plate import (
    ItemIn,
    PlateAnalysis,
    analyse,
    describe_portion,
    estimate_flagged,
)
from swasthya_ingestion.images import prepare_image
from swasthya_ingestion.plate_agent import Perception, PerceptionResult, perceive_plate

from swasthya_api.budget import TokenBudget
from swasthya_api.db.models import AuditLog, Meal, MemoryFact, ProgramContract, User
from swasthya_api.db.session import user_session
from swasthya_api.services import Services

QUICK_PICK_WINDOW_DAYS = 30
REPEAT_LOOKBACK_DAYS = 7


class NotAMeal(Exception):
    """Perception saw no food."""


class MealNotFound(Exception):
    pass


@dataclass(frozen=True)
class PlateCard:
    """What a channel shows after a log. No calorie numbers during Reset."""

    meal_id: uuid.UUID
    log_method: str
    items: list[str]
    sentence: str
    unpriced: list[str]
    has_estimates: bool
    numbers_visible: bool
    totals: dict[str, float] | None
    note: str | None = None

    def as_text(self) -> str:
        lines = ["Logged: " + ", ".join(self.items) + "."]
        if self.note:
            lines.append(self.note)
        lines.append("")
        lines.append(self.sentence)
        if self.unpriced:
            lines.append("")
            lines.append("Not in my nutrition table yet: " + ", ".join(self.unpriced) + ".")
        if self.numbers_visible and self.totals:
            t = self.totals
            lines.append("")
            lines.append(
                f"About {t.get('energy_kcal', 0):.0f} kcal · {t.get('protein_g', 0):.0f} g protein"
                f" · {t.get('fibre_g', 0):.0f} g fibre"
                + (" (portions partly estimated)" if self.has_estimates else "")
            )
        return "\n".join(lines)


def _items_in(perception: Perception) -> list[ItemIn]:
    return [
        ItemIn(
            name=i.name,
            food_code=i.food_code,
            quantity=i.quantity,
            unit=i.unit,
            estimated_grams=i.estimated_grams,
            role=i.role,
            confidence=i.confidence,
        )
        for i in perception.items
    ]


def _items_from_stored(items: list[dict[str, Any]]) -> list[ItemIn]:
    return [
        ItemIn(
            name=i["name"],
            food_code=i.get("food_code"),
            quantity=i["quantity"],
            unit=i["unit"],
            estimated_grams=i.get("grams"),
            role=i["role"],
            confidence=i.get("confidence", "high"),
        )
        for i in items
    ]


def _meta(result: PerceptionResult) -> dict[str, Any]:
    return {
        "model": result.model,
        "turns": result.turns,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "dropped_codes": result.dropped_codes,
    }


async def _phase(session) -> str:
    phase = await session.scalar(
        select(ProgramContract.phase)
        .where(ProgramContract.superseded_at.is_(None))
        .order_by(ProgramContract.version.desc())
        .limit(1)
    )
    # Before a contract exists the user is in observation, which behaves like Reset.
    return phase or "reset"


def _card(meal: Meal, analysis: PlateAnalysis, phase: str, note: str | None) -> PlateCard:
    return PlateCard(
        meal_id=meal.id,
        log_method=meal.log_method,
        items=[describe_portion(i) for i in analysis.items],
        sentence=analysis.sentence,
        unpriced=list(analysis.unpriced),
        has_estimates=any(estimate_flagged(i) for i in analysis.items),
        numbers_visible=phase != "reset",
        totals=analysis.totals if phase != "reset" else None,
        note=note,
    )


class MealService:
    def __init__(self, services: Services) -> None:
        self.svc = services

    def _budget(self, user_id: uuid.UUID) -> TokenBudget:
        return TokenBudget(self.svc.engine, user_id, self.svc.settings.daily_token_cap)

    def _analyse(self, items: list[ItemIn]) -> PlateAnalysis:
        return analyse(
            items,
            table=self.svc.require_food_table(),
            portions=self.svc.portions,
            rules=self.svc.plate_rules,
        )

    async def _perceive(self, user_id: uuid.UUID, **inputs: Any) -> PerceptionResult:
        result = await perceive_plate(
            client=self.svc.require_anthropic(),
            table=self.svc.require_food_table(),
            meter=self._budget(user_id).metered,
            effort=self.svc.settings.plate_agent_effort,
            **inputs,
        )
        if not result.perception.is_food or not result.perception.items:
            raise NotAMeal(result.perception.note or "I couldn't see a meal in that.")
        return result

    async def _store(
        self,
        user_id: uuid.UUID,
        *,
        log_method: str,
        analysis: PlateAnalysis,
        perceived: list[dict[str, Any]],
        eaten_at: datetime | None,
        description: str | None = None,
        photo_path: str | None = None,
        repeat_of: uuid.UUID | None = None,
        meta: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> PlateCard:
        data = analysis.to_dict()
        async with user_session(self.svc.engine, user_id) as session:
            meal = Meal(
                user_id=user_id,
                eaten_at=eaten_at or datetime.now(UTC),
                log_method=log_method,
                description=description,
                photo_path=photo_path,
                items=data["items"],
                perceived_items=perceived,
                nutrients=analysis.totals,
                plate_read={"rule_id": analysis.rule_id, "sentence": analysis.sentence},
                analysis_meta={"table": analysis.table, **(meta or {})},
                repeat_of_meal_id=repeat_of,
            )
            session.add(meal)
            await session.flush()
            return _card(meal, analysis, await _phase(session), note)

    # --- The logging methods -------------------------------------------------

    async def log_photo(
        self,
        user_id: uuid.UUID,
        raw: bytes,
        *,
        caption: str | None = None,
        eaten_at: datetime | None = None,
    ) -> PlateCard:
        image = prepare_image(raw)
        self.svc.require_food_table()
        self.svc.require_anthropic()
        path = self.svc.photos.save(user_id, image.data, image.media_type)
        result = await self._perceive(user_id, image=image, text=caption)
        return await self._store(
            user_id,
            log_method="photo",
            analysis=self._analyse(_items_in(result.perception)),
            perceived=[i.model_dump() for i in result.perception.items],
            eaten_at=eaten_at,
            description=caption,
            photo_path=path,
            meta=_meta(result),
            note=result.perception.note,
        )

    async def log_text(
        self, user_id: uuid.UUID, description: str, *, eaten_at: datetime | None = None
    ) -> PlateCard:
        result = await self._perceive(user_id, text=description)
        return await self._store(
            user_id,
            log_method="text",
            analysis=self._analyse(_items_in(result.perception)),
            perceived=[i.model_dump() for i in result.perception.items],
            eaten_at=eaten_at,
            description=description,
            meta=_meta(result),
            note=result.perception.note,
        )

    async def _copy(
        self, user_id: uuid.UUID, source: Meal, log_method: str, eaten_at: datetime | None
    ) -> PlateCard:
        return await self._store(
            user_id,
            log_method=log_method,
            analysis=self._analyse(_items_from_stored(source.items)),
            perceived=source.perceived_items,
            eaten_at=eaten_at,
            description=source.description,
            repeat_of=source.id,
            meta={"copied_from": str(source.id)},
        )

    async def log_quick_pick(
        self, user_id: uuid.UUID, meal_id: uuid.UUID, *, eaten_at: datetime | None = None
    ) -> PlateCard:
        async with user_session(self.svc.engine, user_id) as session:
            source = await session.get(Meal, meal_id)
        if source is None:
            raise MealNotFound(str(meal_id))
        return await self._copy(user_id, source, "quick_pick", eaten_at)

    async def log_same_as_yesterday(
        self, user_id: uuid.UUID, *, now: datetime | None = None
    ) -> PlateCard:
        """Repeat yesterday's meal in the current slot (falls back up to a week)."""
        now = now or datetime.now(UTC)
        async with user_session(self.svc.engine, user_id) as session:
            tz = ZoneInfo(await session.scalar(select(User.timezone).where(User.id == user_id)))
            local_now = now.astimezone(tz)
            slot = slot_for(local_now.time())
            start_of_today = datetime.combine(local_now.date(), time(), tz)
            candidates = (
                await session.scalars(
                    select(Meal)
                    .where(
                        Meal.eaten_at < start_of_today,
                        Meal.eaten_at >= start_of_today - timedelta(days=REPEAT_LOOKBACK_DAYS),
                    )
                    .order_by(Meal.eaten_at.desc())
                )
            ).all()
        same_slot = [m for m in candidates if slot_for(m.eaten_at.astimezone(tz).time()) == slot]
        source = (same_slot or candidates or [None])[0]
        if source is None:
            raise MealNotFound("no meal in the last week to repeat")
        return await self._copy(user_id, source, "repeat", now)

    async def quick_picks(self, user_id: uuid.UUID, limit: int = 5) -> list[dict[str, Any]]:
        """Most frequent distinct meals of the last 30 days, most recent instance first."""
        since = datetime.now(UTC) - timedelta(days=QUICK_PICK_WINDOW_DAYS)
        async with user_session(self.svc.engine, user_id) as session:
            meals = (
                await session.scalars(
                    select(Meal).where(Meal.eaten_at >= since).order_by(Meal.eaten_at.desc())
                )
            ).all()
        groups: dict[tuple, dict[str, Any]] = {}
        for meal in meals:
            if not meal.items:
                continue
            key = fingerprint(meal.items)
            if key not in groups:
                label = ", ".join(
                    describe_portion(i) for i in self._analyse(_items_from_stored(meal.items)).items
                )
                groups[key] = {
                    "meal_id": meal.id,
                    "label": label,
                    "count": 0,
                    "last_eaten_at": meal.eaten_at,
                }
            groups[key]["count"] += 1
        ranked = sorted(
            groups.values(), key=lambda g: (-g["count"], -g["last_eaten_at"].timestamp())
        )
        return ranked[:limit]

    # --- Corrections ("fix this") --------------------------------------------

    async def correct_items(
        self,
        user_id: uuid.UUID,
        meal_id: uuid.UUID,
        items: list[ItemIn],
        *,
        said: str | None = None,
    ) -> PlateCard:
        analysis = self._analyse(items)
        data = analysis.to_dict()
        async with user_session(self.svc.engine, user_id) as session:
            meal = await session.get(Meal, meal_id)
            if meal is None:
                raise MealNotFound(str(meal_id))
            before = [i["name"] for i in meal.items]
            meal.items = data["items"]
            meal.nutrients = analysis.totals
            meal.plate_read = {"rule_id": analysis.rule_id, "sentence": analysis.sentence}
            meal.corrected_at = datetime.now(UTC)
            after = [i.name for i in analysis.items]
            # Corrections become stored facts (F3) and training signal for the golden set.
            session.add(
                MemoryFact(
                    user_id=user_id,
                    category="plate_correction",
                    fact=f"Perceived {', '.join(before)}; it was actually {', '.join(after)}.",
                    source="meal_correction",
                    source_ref=meal.id,
                )
            )
            session.add(
                AuditLog(
                    user_id=user_id,
                    actor="user",
                    action="meal.corrected",
                    evidence={"meal_id": str(meal.id), "before": before, "after": after},
                    payload={"said": said} if said else {},
                )
            )
            await session.flush()
            return _card(meal, analysis, await _phase(session), None)

    async def correct_from_text(
        self, user_id: uuid.UUID, meal_id: uuid.UUID, correction: str
    ) -> PlateCard:
        async with user_session(self.svc.engine, user_id) as session:
            meal = await session.get(Meal, meal_id)
        if meal is None:
            raise MealNotFound(str(meal_id))
        previously = ", ".join(i["name"] for i in meal.items)
        result = await self._perceive(
            user_id,
            text=(
                f"Correction of a meal previously logged as: {previously}. "
                f"What the person says it actually was: {correction}. "
                "Return the full corrected list of items."
            ),
        )
        return await self.correct_items(
            user_id, meal_id, _items_in(result.perception), said=correction
        )

    # --- Reads -----------------------------------------------------------------

    async def set_source_ref(self, user_id: uuid.UUID, meal_id: uuid.UUID, ref: str) -> None:
        async with user_session(self.svc.engine, user_id) as session:
            await session.execute(update(Meal).where(Meal.id == meal_id).values(source_ref=ref))

    async def meal_for_source_ref(self, user_id: uuid.UUID, ref: str) -> uuid.UUID | None:
        async with user_session(self.svc.engine, user_id) as session:
            return await session.scalar(select(Meal.id).where(Meal.source_ref == ref))

    async def meals_on(self, user_id: uuid.UUID, day: date) -> list[Meal]:
        async with user_session(self.svc.engine, user_id) as session:
            tz = ZoneInfo(await session.scalar(select(User.timezone).where(User.id == user_id)))
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


async def resolve_channel_user(
    services: Services, channel: str, external_id: str
) -> uuid.UUID | None:
    async with services.engine.begin() as conn:
        return await conn.scalar(
            text("SELECT app.resolve_channel_user(:c, :e)"), {"c": channel, "e": external_id}
        )


async def redeem_link_code(
    services: Services, code: str, channel: str, external_id: str
) -> uuid.UUID | None:
    async with services.engine.begin() as conn:
        return await conn.scalar(
            text("SELECT app.redeem_link_code(:code, :c, :e)"),
            {"code": code.strip(), "c": channel, "e": external_id},
        )
