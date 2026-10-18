import hmac
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from pydantic import BaseModel

from swasthya_api.auth import CurrentPrincipal
from swasthya_api.channels import issue_link_code
from swasthya_api.deps import CurrentServices
from swasthya_api.telegram import BotGateway, handle_update

router = APIRouter(tags=["channels"])


class LinkCode(BaseModel):
    code: str
    expires_at: datetime
    instructions: str


@router.post("/v1/channels/telegram/link-code", status_code=201)
async def telegram_link_code(principal: CurrentPrincipal, services: CurrentServices) -> LinkCode:
    code, expires_at = await issue_link_code(services, principal.user_id)
    return LinkCode(
        code=code,
        expires_at=expires_at,
        instructions=f"Send /start {code} to the Swasthya bot on Telegram.",
    )


@router.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(
    request: Request,
    services: CurrentServices,
    background: BackgroundTasks,
    secret: Annotated[str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")] = None,
) -> dict[str, bool]:
    settings = services.settings
    if not (settings.telegram_bot_token and settings.telegram_webhook_secret):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if secret is None or not hmac.compare_digest(secret, settings.telegram_webhook_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    update = await request.json()
    gateway = request.app.state.telegram_gateway or BotGateway(settings.telegram_bot_token)
    request.app.state.telegram_gateway = gateway
    # Acknowledge at once; Telegram re-delivers updates that take too long.
    background.add_task(handle_update, update, services, gateway)
    return {"ok": True}
