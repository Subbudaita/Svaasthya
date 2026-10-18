"""Local-only helpers. Refuses to run with SWASTHYA_ENV=production.

    uv run --env-file .env python -m swasthya_api.devtools create-user --email you@example.com

Creates a user row directly (no Supabase account) and prints a Telegram link
code, so the bot can be tested end to end on a laptop. Runs as the RLS-bound
API role: it can only create a user by acting as that user.
"""

import argparse
import uuid

from sqlalchemy.dialects.postgresql import insert

from swasthya_api import aio
from swasthya_api.channels import issue_link_code
from swasthya_api.db.models import User
from swasthya_api.db.session import user_session
from swasthya_api.services import build_services
from swasthya_api.settings import Environment, get_settings


async def create_user(email: str, timezone: str | None) -> None:
    settings = get_settings()
    if settings.env == Environment.PRODUCTION:
        raise SystemExit("devtools are disabled in production")
    services = await build_services(settings)
    try:
        user_id = uuid.uuid4()
        async with user_session(services.engine, user_id) as session:
            await session.execute(
                insert(User).values(
                    id=user_id, email=email, timezone=timezone or settings.default_timezone
                )
            )
        code, expires_at = await issue_link_code(services, user_id)
    finally:
        await services.aclose()
    print(f"user id:   {user_id}")
    print(f"link code: {code}   (expires {expires_at:%H:%M} UTC)")
    print(f"\nIn Telegram, send your bot:  /start {code}")


async def ask(user_id: str, question: str) -> None:
    """Ask the coach a question as a user, e.g. "why am I so tired today?"."""
    from swasthya_api.chat import ChatService

    settings = get_settings()
    if settings.env == Environment.PRODUCTION:
        raise SystemExit("devtools are disabled in production")
    from swasthya_api.services import FeatureUnavailable

    services = await build_services(settings)
    try:
        r = await ChatService(services).reply(uuid.UUID(user_id), question, channel="pwa")
    except FeatureUnavailable as exc:
        raise SystemExit(f"Can't ask yet: {exc} (see docs/SETUP.md)") from exc
    finally:
        await services.aclose()
    print(r.text)
    print(
        f"\n[{r.specialist} · confidence {r.confidence} · clauses {r.cited_clauses} · "
        f"days {r.cited_days} · guard {r.guard_action}]"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="swasthya_api.devtools")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user", help="create a local user and print a Telegram link code")
    cu.add_argument("--email", required=True)
    cu.add_argument("--timezone", help="IANA zone, default DEFAULT_TIMEZONE")
    q = sub.add_parser("ask", help="ask the coach a question as a user")
    q.add_argument("--user-id", required=True)
    q.add_argument("question")
    args = parser.parse_args()
    if args.cmd == "create-user":
        aio.run(create_user(args.email, args.timezone))
    elif args.cmd == "ask":
        aio.run(ask(args.user_id, args.question))


if __name__ == "__main__":
    main()
