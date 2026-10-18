"""Linking an external channel account (Telegram) to a signed-in user."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from swasthya_api.db.models import ChannelLinkCode
from swasthya_api.db.session import user_session
from swasthya_api.services import Services

LINK_CODE_TTL = timedelta(minutes=15)
# No 0/O, 1/I/L: codes are read off one screen and typed into another.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def hash_code(code: str) -> str:
    # Must match app.redeem_link_code: sha256 of the upper-cased UTF-8 code, hex.
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


async def issue_link_code(
    services: Services, user_id: uuid.UUID, channel: str = "telegram"
) -> tuple[str, datetime]:
    code = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    expires_at = datetime.now(UTC) + LINK_CODE_TTL
    async with user_session(services.engine, user_id) as session:
        session.add(
            ChannelLinkCode(
                user_id=user_id, channel=channel, code_hash=hash_code(code), expires_at=expires_at
            )
        )
    return code, expires_at
