"""Telegram flow: linking, logging, correcting by reply, quick-pick buttons."""

import asyncio
import uuid

import pytest
from swasthya_api.channels import issue_link_code
from swasthya_api.telegram import NOT_LINKED, handle_update


class RecordingGateway:
    def __init__(self, photo: bytes = b"") -> None:
        self.sent: list[dict] = []
        self.photo = photo
        self._next_id = 1000

    async def send_message(self, chat_id, text, *, reply_to=None, buttons=()):
        self._next_id += 1
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "reply_to": reply_to,
                "buttons": list(buttons),
                "message_id": self._next_id,
            }
        )
        return self._next_id

    async def download_file(self, file_id):
        return self.photo

    async def answer_callback(self, callback_id):
        pass

    async def typing(self, chat_id):
        pass


def msg(update_id, tg_user, text=None, chat_type="private", **extra):
    m = {
        "message_id": update_id,
        "from": {"id": tg_user},
        "chat": {"id": tg_user, "type": chat_type},
    }
    if text is not None:
        m["text"] = text
    m.update(extra)
    return {"update_id": update_id, "message": m}


@pytest.fixture
def run(phase1_app):
    _, services = phase1_app

    def _run(update, gw):
        asyncio.run(handle_update(update, services, gw))  # fresh loop per update

    return _run


@pytest.fixture
def linked(phase1_app, make_token, run):
    """A signed-up user whose Telegram account 4242 is linked."""
    client, services = phase1_app
    user_id = uuid.uuid4()
    client.get("/v1/me", headers={"Authorization": f"Bearer {make_token(user_id)}"})
    code = client.post(
        "/v1/channels/telegram/link-code",
        headers={"Authorization": f"Bearer {make_token(user_id)}"},
    ).json()["code"]
    gw = RecordingGateway()
    run(msg(1, 4242, f"/start {code.lower()}"), gw)  # codes are case-insensitive
    assert gw.sent[-1]["text"].startswith("Linked.")
    return user_id


def test_unlinked_account_gets_nothing(run):
    gw = RecordingGateway()
    run(msg(1, 999, "hello"), gw)
    assert gw.sent[-1]["text"] == NOT_LINKED


def test_bad_code(run):
    gw = RecordingGateway()
    run(msg(1, 999, "/start WRONGCODE"), gw)
    assert "invalid or expired" in gw.sent[-1]["text"]


def test_group_chats_are_ignored(run, linked):
    gw = RecordingGateway()
    run(msg(2, 4242, "/same", chat_type="group"), gw)
    assert gw.sent == []


def test_photo_then_fix_by_reply(run, linked, model, item, jpeg_bytes, admin_conn):
    gw = RecordingGateway(photo=jpeg_bytes)
    model.will_see(item("plain dosa", "T004", 2, "dosa", 80, "grain"))
    run(
        msg(
            5,
            4242,
            photo=[
                {"file_id": "small", "width": 90, "height": 90},
                {"file_id": "big", "width": 1280, "height": 960},
            ],
        ),
        gw,
    )
    card = gw.sent[-1]
    assert card["text"].startswith("Logged: 2 plain dosa.")
    assert "Reply to this message" in card["text"]
    assert "kcal" not in card["text"]  # Reset: no numbers

    model.will_see(
        item("masala dosa", "T005", 2, "dosa", 400, "grain"),
        item("sambar", "T006", 1, "katori", 150, "protein"),
    )
    run(
        msg(
            6,
            4242,
            "it was masala dosa with sambar",
            reply_to_message={"message_id": card["message_id"]},
        ),
        gw,
    )
    assert gw.sent[-1]["text"].startswith("Logged: 2 masala dosa, 1 katori sambar.")
    assert "plain dosa" in model.calls[-1]["messages"][0]["content"][0]["text"]
    corrected = admin_conn.execute(
        "SELECT count(*) FROM meals WHERE user_id = %s AND corrected_at IS NOT NULL", (linked,)
    ).fetchone()[0]
    assert corrected == 1


def test_log_same_and_picks(run, linked, model, item):
    gw = RecordingGateway()
    model.will_see(item("chapati", "T001", 2, "chapati", 70, "grain"))
    run(msg(7, 4242, "/log 2 roti"), gw)
    assert gw.sent[-1]["text"].startswith("Logged: 2 chapati.")

    run(msg(8, 4242, "/picks"), gw)
    buttons = gw.sent[-1]["buttons"]
    assert buttons and buttons[0].text == "2 chapati"
    run(
        {
            "update_id": 9,
            "callback_query": {
                "id": "cb",
                "from": {"id": 4242},
                "data": buttons[0].callback_data,
                "message": {"message_id": 1, "chat": {"id": 4242, "type": "private"}},
            },
        },
        gw,
    )
    assert gw.sent[-1]["text"].startswith("Logged: 2 chapati.")


def test_voice_is_honest_about_phase(run, linked):
    gw = RecordingGateway()
    run(msg(10, 4242, voice={"file_id": "v"}), gw)
    assert "speech-to-text" in gw.sent[-1]["text"]


def test_webhook_requires_secret(phase1_app):
    client, services = phase1_app
    services.settings = services.settings.model_copy(
        update={"telegram_bot_token": "123:abc", "telegram_webhook_secret": "s3cret"}
    )
    assert client.post("/telegram/webhook", json={}).status_code == 401
    assert (
        client.post(
            "/telegram/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"}
        ).status_code
        == 401
    )


def test_webhook_disabled_without_config(phase1_app):
    client, _ = phase1_app
    assert client.post("/telegram/webhook", json={}).status_code == 404


async def test_link_code_is_stored_hashed(phase1_app, make_user, admin_conn):
    _, services = phase1_app
    user = make_user()
    code, _ = await issue_link_code(services, user)
    stored = admin_conn.execute(
        "SELECT code_hash FROM channel_link_codes WHERE user_id = %s", (user,)
    ).fetchone()[0]
    assert code not in stored and len(stored) == 64
