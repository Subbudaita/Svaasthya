from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from swasthya_api.auth import CurrentPrincipal
from swasthya_api.db.models import ConsistencyTrack, Reward
from swasthya_api.db.session import user_session
from swasthya_api.deps import CurrentServices
from swasthya_api.program import ProgramService

router = APIRouter(prefix="/v1/program", tags=["program"])


class ContractOut(BaseModel):
    version: int
    phase: str
    body: dict[str, Any]


class TrackOut(BaseModel):
    pillar: str
    identity: str | None
    current_run_days: int
    longest_run_days: int


class ProgramOut(BaseModel):
    contract: ContractOut
    tracks: list[TrackOut]
    rewards_paid: list[str]


@router.get("")
async def read_program(principal: CurrentPrincipal, services: CurrentServices) -> ProgramOut:
    contract = await ProgramService(services).current_contract(principal.user_id)
    if contract is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no program yet — onboarding first")
    async with user_session(services.engine, principal.user_id) as session:
        tracks = (
            await session.scalars(select(ConsistencyTrack).order_by(ConsistencyTrack.pillar))
        ).all()
        paid = (await session.scalars(select(Reward.text).where(Reward.status == "paid"))).all()
    return ProgramOut(
        contract=ContractOut(version=contract.version, phase=contract.phase, body=contract.body),
        tracks=[
            TrackOut(
                pillar=t.pillar,
                identity=t.identity_statement,
                current_run_days=t.current_run_days,
                longest_run_days=t.longest_run_days,
            )
            for t in tracks
        ],
        # Unpaid rewards are deliberately not listed: never a pre-announced carrot.
        rewards_paid=list(paid),
    )
