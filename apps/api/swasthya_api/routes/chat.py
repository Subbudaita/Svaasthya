from fastapi import APIRouter
from pydantic import BaseModel, Field

from swasthya_api.auth import CurrentPrincipal
from swasthya_api.chat import ChatService
from swasthya_api.deps import CurrentServices

router = APIRouter(prefix="/v1/chat", tags=["chat"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatOut(BaseModel):
    reply: str
    specialist: str | None
    cited_clauses: list[str]
    cited_days: list[str]
    confidence: str | None
    escalated: bool


@router.post("")
async def chat(principal: CurrentPrincipal, services: CurrentServices, body: ChatIn) -> ChatOut:
    """Same thread as Telegram: start there at lunch, continue here at night."""
    r = await ChatService(services).reply(principal.user_id, body.message, channel="pwa")
    return ChatOut(
        reply=r.text,
        specialist=r.specialist,
        cited_clauses=r.cited_clauses,
        cited_days=r.cited_days,
        confidence=r.confidence,
        escalated=r.escalated,
    )
