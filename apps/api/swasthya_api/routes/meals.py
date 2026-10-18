from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field
from swasthya_engines.plate import ItemIn

from swasthya_api.auth import CurrentPrincipal
from swasthya_api.deps import CurrentServices
from swasthya_api.meals import MealService, PlateCard

router = APIRouter(prefix="/v1/meals", tags=["meals"])

Role = Literal[
    "grain", "protein", "vegetable", "dairy", "fruit", "fried", "sweet", "beverage", "other"
]


class PlateCardOut(BaseModel):
    meal_id: UUID
    log_method: str
    items: list[str]
    sentence: str
    unpriced: list[str]
    has_estimates: bool
    numbers_visible: bool
    totals: dict[str, float] | None
    note: str | None

    @classmethod
    def of(cls, card: PlateCard) -> "PlateCardOut":
        return cls(**card.__dict__)


class TextLog(BaseModel):
    description: str = Field(min_length=2, max_length=1000)
    eaten_at: datetime | None = None


class RepeatLog(BaseModel):
    eaten_at: datetime | None = None


class CorrectedItem(BaseModel):
    name: str
    food_code: str | None = None
    quantity: float = Field(gt=0)
    unit: str
    grams: float | None = Field(default=None, gt=0)
    role: Role


class QuickPick(BaseModel):
    meal_id: UUID
    label: str
    count: int
    last_eaten_at: datetime


class MealOut(BaseModel):
    id: UUID
    eaten_at: datetime
    log_method: str
    items: list[str]
    sentence: str | None
    corrected: bool


@router.post("/photo", status_code=201)
async def log_photo(
    principal: CurrentPrincipal,
    services: CurrentServices,
    photo: Annotated[UploadFile, File()],
    caption: Annotated[str | None, Form(max_length=500)] = None,
) -> PlateCardOut:
    raw = await photo.read()
    card = await MealService(services).log_photo(principal.user_id, raw, caption=caption)
    return PlateCardOut.of(card)


@router.post("/text", status_code=201)
async def log_text(
    principal: CurrentPrincipal, services: CurrentServices, body: TextLog
) -> PlateCardOut:
    card = await MealService(services).log_text(
        principal.user_id, body.description, eaten_at=body.eaten_at
    )
    return PlateCardOut.of(card)


@router.post("/same-as-yesterday", status_code=201)
async def log_same(principal: CurrentPrincipal, services: CurrentServices) -> PlateCardOut:
    return PlateCardOut.of(await MealService(services).log_same_as_yesterday(principal.user_id))


@router.get("/quick-picks")
async def list_quick_picks(
    principal: CurrentPrincipal, services: CurrentServices
) -> list[QuickPick]:
    return [QuickPick(**p) for p in await MealService(services).quick_picks(principal.user_id)]


@router.post("/quick-picks/{meal_id}", status_code=201)
async def log_quick_pick(
    meal_id: UUID,
    principal: CurrentPrincipal,
    services: CurrentServices,
    body: RepeatLog | None = None,
) -> PlateCardOut:
    card = await MealService(services).log_quick_pick(
        principal.user_id, meal_id, eaten_at=body.eaten_at if body else None
    )
    return PlateCardOut.of(card)


@router.put("/{meal_id}/items")
async def correct_items(
    meal_id: UUID,
    principal: CurrentPrincipal,
    services: CurrentServices,
    items: list[CorrectedItem],
) -> PlateCardOut:
    """The Plate Card's "fix this": replace items, recompute deterministically."""
    card = await MealService(services).correct_items(
        principal.user_id,
        meal_id,
        [ItemIn(i.name, i.food_code, i.quantity, i.unit, i.grams, i.role, "high") for i in items],
    )
    return PlateCardOut.of(card)


@router.get("")
async def list_meals(
    principal: CurrentPrincipal, services: CurrentServices, day: date
) -> list[MealOut]:
    from swasthya_engines.plate import describe_portion

    from swasthya_api.meals import _items_from_stored

    service = MealService(services)
    out = []
    for meal in await service.meals_on(principal.user_id, day):
        analysed = service._analyse(_items_from_stored(meal.items)) if meal.items else None
        out.append(
            MealOut(
                id=meal.id,
                eaten_at=meal.eaten_at,
                log_method=meal.log_method,
                items=[describe_portion(i) for i in analysed.items] if analysed else [],
                sentence=(meal.plate_read or {}).get("sentence"),
                corrected=meal.corrected_at is not None,
            )
        )
    return out
