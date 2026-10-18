"""Telegram channel: where you act (docs/PLAN.md §7).

``handle_update`` takes a raw Bot API update dict and a ``TelegramGateway``,
so the same logic serves webhook mode, polling mode, and tests. Identity comes
only from the linked ``channel_identities`` row — never from anything in the
message text.
"""

import logging
from datetime import date, time
from typing import Any

from swasthya_engines.clock import parse_time
from swasthya_ingestion.images import InvalidImageError
from swasthya_ingestion.plate_agent import PerceptionError

from swasthya_api.budget import TokenCapExceeded
from swasthya_api.gateway import Button, TelegramGateway
from swasthya_api.meals import (
    MealNotFound,
    MealService,
    NotAMeal,
    PlateCard,
    redeem_link_code,
    resolve_channel_user,
)
from swasthya_api.program import ProgramService, Reply
from swasthya_api.services import FeatureUnavailable, Services

log = logging.getLogger(__name__)
CHANNEL = "telegram"

HELP = (
    "Send me a photo of your plate and I'll log it.\n"
    "New here? /onboard sets up your program.\n"
    "Other ways to log:\n"
    "  /log 2 roti, dal, bhindi — describe it\n"
    "  /same — same as yesterday's meal at this time\n"
    "  /picks — your usual meals, one tap\n"
    "Wrong item? Reply to my message with what it actually was.\n"
    "/time morning 06:45 or /time evening 21:00 moves your rituals."
)
NOT_LINKED = (
    "This bot is invite-only. Open Swasthya, get a link code, and send it here as:\n/start YOURCODE"
)
FIX_HINT = "\n\nWrong? Reply to this message with what it actually was."


def _ref(chat_id: int, message_id: int) -> str:
    return f"{CHANNEL}:{chat_id}:{message_id}"


async def _send_card(
    gw: TelegramGateway,
    meals: MealService,
    user_id,
    chat_id: int,
    card: PlateCard,
    reply_to: int | None,
) -> None:
    sent = await gw.send_message(chat_id, card.as_text() + FIX_HINT, reply_to=reply_to)
    await meals.set_source_ref(user_id, card.meal_id, _ref(chat_id, sent))


async def handle_update(update: dict[str, Any], services: Services, gw: TelegramGateway) -> None:
    try:
        await _handle(update, services, gw)
    except Exception:  # never let one bad update kill the webhook / poller
        log.exception("telegram update %s failed", update.get("update_id"))
        chat_id = _chat_id(update)
        if chat_id is not None:
            await gw.send_message(chat_id, "Something went wrong on my side. Please try again.")


def _chat_id(update: dict[str, Any]) -> int | None:
    msg = update.get("message") or (update.get("callback_query") or {}).get("message")
    return (msg or {}).get("chat", {}).get("id")


async def _handle(update: dict[str, Any], services: Services, gw: TelegramGateway) -> None:
    meals = MealService(services)

    if callback := update.get("callback_query"):
        await gw.answer_callback(callback["id"], "Noted")
        user_id = await resolve_channel_user(services, CHANNEL, str(callback["from"]["id"]))
        chat_id = callback["message"]["chat"]["id"]
        if user_id is None:
            await gw.send_message(chat_id, NOT_LINKED)
            return
        data = callback.get("data", "")
        if data.startswith("pick:"):
            await _guarded(
                gw,
                chat_id,
                meals,
                user_id,
                None,
                lambda: meals.log_quick_pick(user_id, _uuid(data[5:])),
            )
        elif data.startswith("ob:"):
            reply = await ProgramService(services).answer(user_id, data[3:])
            await _send_reply(gw, chat_id, reply)
        elif data.startswith("ci:"):
            from swasthya_api.rituals import RitualService

            _, day, pillar, value = data.split(":")
            await RitualService(services, gw).record_report(
                user_id, date.fromisoformat(day), pillar, value == "1"
            )
        return

    msg = update.get("message")
    if not msg or msg.get("chat", {}).get("type") != "private":
        return  # only 1:1 chats; health data never goes to groups
    chat_id = msg["chat"]["id"]
    from_id = str(msg["from"]["id"])
    text = (msg.get("text") or "").strip()

    if text.startswith("/start"):
        code = text.removeprefix("/start").strip()
        if not code:
            linked = await resolve_channel_user(services, CHANNEL, from_id)
            await gw.send_message(chat_id, HELP if linked else NOT_LINKED)
            return
        user_id = await redeem_link_code(services, code, CHANNEL, from_id)
        await gw.send_message(
            chat_id,
            ("Linked. " + HELP) if user_id else "That code is invalid or expired. Get a fresh one.",
        )
        return

    user_id = await resolve_channel_user(services, CHANNEL, from_id)
    if user_id is None:
        await gw.send_message(chat_id, NOT_LINKED)
        return

    message_id = msg["message_id"]
    program = ProgramService(services)

    if text.startswith("/onboard"):
        await _send_reply(gw, chat_id, await program.start(user_id, CHANNEL))
        return
    if text.startswith("/cancel"):
        await program.cancel(user_id)
        await gw.send_message(chat_id, "Stopped. /onboard starts again whenever you like.")
        return
    if text and not text.startswith("/") and await program.is_onboarding(user_id):
        await _send_reply(gw, chat_id, await program.answer(user_id, text))
        return
    if text.startswith("/time"):
        await gw.send_message(chat_id, await _set_time(services, gw, user_id, text))
        return

    if photos := msg.get("photo"):
        largest = max(photos, key=lambda p: p.get("file_size") or p["width"] * p["height"])
        await gw.typing(chat_id)
        raw = await gw.download_file(largest["file_id"])
        await _guarded(
            gw,
            chat_id,
            meals,
            user_id,
            message_id,
            lambda: meals.log_photo(user_id, raw, caption=msg.get("caption")),
        )
        return

    if msg.get("voice") or msg.get("audio"):
        await gw.send_message(
            chat_id,
            "Voice logging arrives with speech-to-text later. "
            "For now: a photo, /log, /same or /picks.",
        )
        return

    reply = msg.get("reply_to_message")
    if reply and text and not text.startswith("/"):
        meal_id = await meals.meal_for_source_ref(user_id, _ref(chat_id, reply["message_id"]))
        if meal_id is not None:
            await gw.typing(chat_id)
            await _guarded(
                gw,
                chat_id,
                meals,
                user_id,
                message_id,
                lambda: meals.correct_from_text(user_id, meal_id, text),
            )
            return

    if text.startswith("/log"):
        description = text.removeprefix("/log").strip()
        if not description:
            await gw.send_message(chat_id, "Tell me what you ate, e.g. /log 2 roti, dal, salad")
            return
        await gw.typing(chat_id)
        await _guarded(
            gw, chat_id, meals, user_id, message_id, lambda: meals.log_text(user_id, description)
        )
        return

    if text.startswith("/same"):
        await _guarded(
            gw, chat_id, meals, user_id, message_id, lambda: meals.log_same_as_yesterday(user_id)
        )
        return

    if text.startswith("/picks"):
        picks = await meals.quick_picks(user_id)
        if not picks:
            await gw.send_message(chat_id, "No usual meals yet — they appear after a few logs.")
            return
        await gw.send_message(
            chat_id,
            "Your usual meals — tap one to log it now:",
            buttons=[Button(p["label"][:60], f"pick:{p['meal_id']}") for p in picks],
        )
        return

    if text and not text.startswith("/"):
        from swasthya_api.rituals import RitualService

        if await RitualService(services, gw).record_causal_answer(user_id, text):
            await gw.send_message(
                chat_id, "Thank you — noted. That's what the weekly letter learns from."
            )
            return

    await gw.send_message(chat_id, HELP)


async def _send_reply(gw: TelegramGateway, chat_id: int, reply: Reply) -> None:
    buttons = [Button(o.replace("_", " "), f"ob:{o}") for o in reply.options]
    await gw.send_message(chat_id, reply.text, buttons=buttons)


async def _set_time(services: Services, gw: TelegramGateway, user_id, text: str) -> str:
    from swasthya_api.rituals import RitualService

    parts = text.split()
    which = {"morning": "morning_brief", "evening": "evening_checkin"}
    if len(parts) != 3 or parts[1].lower() not in which:
        return "Use: /time morning 06:45  or  /time evening 21:00"
    try:
        at: time = parse_time(parts[2])
    except ValueError:
        return "That time didn't parse — try 06:45 or 9.30pm."
    await RitualService(services, gw).set_time(user_id, which[parts[1].lower()], at)
    return f"Done — your {parts[1].lower()} ritual moves to {at:%H:%M}."


def _uuid(value: str):
    import uuid

    return uuid.UUID(value)


async def _guarded(gw, chat_id, meals, user_id, reply_to, action) -> None:
    """Run a logging action and turn known failures into honest replies."""
    try:
        card = await action()
    except NotAMeal as exc:
        await gw.send_message(chat_id, f"I don't think that's a meal. {exc}", reply_to=reply_to)
    except InvalidImageError:
        await gw.send_message(chat_id, "I couldn't read that image.", reply_to=reply_to)
    except MealNotFound:
        await gw.send_message(chat_id, "I couldn't find a meal to repeat.", reply_to=reply_to)
    except TokenCapExceeded:
        await gw.send_message(
            chat_id,
            "I've hit today's usage limit. Logging works again tomorrow.",
            reply_to=reply_to,
        )
    except FeatureUnavailable as exc:
        log.error("telegram feature unavailable: %s", exc)
        await gw.send_message(
            chat_id, "Meal logging isn't set up on the server yet.", reply_to=reply_to
        )
    except PerceptionError:
        log.exception("perception failed")
        await gw.send_message(
            chat_id,
            "I couldn't make sense of that one. Try again, or /log it in words.",
            reply_to=reply_to,
        )
    else:
        await _send_card(gw, meals, user_id, chat_id, card, reply_to)


class BotGateway:
    """python-telegram-bot implementation of the gateway."""

    def __init__(self, token: str) -> None:
        from telegram import Bot

        self.bot = Bot(token)

    async def send_message(self, chat_id, text, *, reply_to=None, buttons=()):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters

        markup = (
            InlineKeyboardMarkup(
                [[InlineKeyboardButton(b.text, callback_data=b.callback_data)] for b in buttons]
            )
            if buttons
            else None
        )
        sent = await self.bot.send_message(
            chat_id,
            text,
            reply_parameters=ReplyParameters(reply_to) if reply_to else None,
            reply_markup=markup,
        )
        return sent.message_id

    async def download_file(self, file_id):
        file = await self.bot.get_file(file_id)
        return bytes(await file.download_as_bytearray())

    async def answer_callback(self, callback_id, text=None):
        await self.bot.answer_callback_query(callback_id, text=text)

    async def typing(self, chat_id):
        await self.bot.send_chat_action(chat_id, "typing")
