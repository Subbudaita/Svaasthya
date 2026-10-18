"""Long-lived dependencies shared by the HTTP app and the Telegram poller."""

import logging
from dataclasses import dataclass

import anthropic
from sqlalchemy.ext.asyncio import AsyncEngine
from swasthya_engines.nutrition import FoodTable, PlateRules, PortionRules

from swasthya_api.db.session import assert_rls_enforced, create_engine
from swasthya_api.photos import LocalPhotoStore, PhotoStore
from swasthya_api.settings import Settings

log = logging.getLogger(__name__)


class FeatureUnavailable(Exception):
    """A dependency this feature needs is not configured (maps to HTTP 503)."""


@dataclass
class Services:
    settings: Settings
    engine: AsyncEngine
    photos: PhotoStore
    portions: PortionRules
    plate_rules: PlateRules
    food_table: FoodTable | None
    anthropic: anthropic.AsyncAnthropic | None

    def require_food_table(self) -> FoodTable:
        if self.food_table is None:
            raise FeatureUnavailable(
                "Nutrition table not generated — run `make indb` (data/nutrition/README.md)."
            )
        return self.food_table

    def require_anthropic(self) -> anthropic.AsyncAnthropic:
        if self.anthropic is None:
            raise FeatureUnavailable("ANTHROPIC_API_KEY is not set.")
        return self.anthropic

    async def aclose(self) -> None:
        if self.anthropic is not None:
            await self.anthropic.close()
        await self.engine.dispose()


async def build_services(settings: Settings) -> Services:
    engine = create_engine(str(settings.database_url))
    await assert_rls_enforced(engine)
    try:
        table: FoodTable | None = FoodTable.load()
    except FileNotFoundError as exc:
        log.warning("%s Meal logging will return 503.", exc)
        table = None
    client = (
        anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=30.0, max_retries=1)
        if settings.anthropic_api_key
        else None
    )
    return Services(
        settings=settings,
        engine=engine,
        photos=LocalPhotoStore(settings.photo_storage_dir),
        portions=PortionRules.load(),
        plate_rules=PlateRules.load(),
        food_table=table,
        anthropic=client,
    )
