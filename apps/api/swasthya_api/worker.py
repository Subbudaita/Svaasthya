"""The always-on worker: ritual scheduler + (locally) the Telegram poller.

    uv run --env-file .env python -m swasthya_api.worker

* Scheduler: APScheduler ticks every minute; ``RitualService.tick`` is
  idempotent, so overlapping or repeated ticks never double-send.
* Telegram: long polling unless TELEGRAM_WEBHOOK_SECRET is set, in which case
  updates arrive at POST /telegram/webhook on the API and only the scheduler
  runs here.
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from swasthya_api.rituals import RitualService
from swasthya_api.services import build_services
from swasthya_api.settings import get_settings
from swasthya_api.telegram import BotGateway, handle_update

log = logging.getLogger("swasthya.worker")


async def _poll(services, gw: BotGateway) -> None:
    await gw.bot.delete_webhook()  # polling and webhooks are mutually exclusive
    me = await gw.bot.get_me()
    log.info("telegram: polling as @%s", me.username)
    offset = None
    while True:
        try:
            updates = await gw.bot.get_updates(offset=offset, timeout=30)
        except Exception:
            log.exception("telegram: get_updates failed; retrying in 5 s")
            await asyncio.sleep(5)
            continue
        for upd in updates:
            offset = upd.update_id + 1
            await handle_update(upd.to_dict(), services, gw)


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set (see .env.example)")
    services = await build_services(settings)
    gw = BotGateway(settings.telegram_bot_token)
    rituals = RitualService(services, gw)

    async def tick() -> None:
        for user_id, ritual, status in await rituals.tick():
            log.info("ritual %s → %s: %s", ritual, user_id, status)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        tick, "interval", minutes=1, max_instances=1, coalesce=True, next_run_time=None
    )
    scheduler.start()
    await tick()
    log.info("scheduler: ticking every minute")
    try:
        if settings.telegram_webhook_secret:
            log.info("telegram: webhook mode — inbound handled by the API")
            await asyncio.Event().wait()
        else:
            await _poll(services, gw)
    finally:
        scheduler.shutdown(wait=False)
        await services.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(main())
