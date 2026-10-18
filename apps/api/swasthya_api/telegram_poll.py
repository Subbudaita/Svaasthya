"""Run the Telegram bot by long polling — for local use, no public URL needed.

    uv run --env-file .env python -m swasthya_api.telegram_poll

In production use webhook mode instead (POST /telegram/webhook).
"""

import asyncio
import logging

from swasthya_api.services import build_services
from swasthya_api.settings import get_settings
from swasthya_api.telegram import BotGateway, handle_update

log = logging.getLogger("swasthya.telegram")


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (see .env.example)")
    services = await build_services(settings)
    gw = BotGateway(settings.telegram_bot_token)
    await gw.bot.delete_webhook()  # polling and webhooks are mutually exclusive
    me = await gw.bot.get_me()
    log.info("polling as @%s", me.username)
    offset = None
    try:
        while True:
            updates = await gw.bot.get_updates(offset=offset, timeout=30)
            for upd in updates:
                offset = upd.update_id + 1
                await handle_update(upd.to_dict(), services, gw)
    finally:
        await services.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
