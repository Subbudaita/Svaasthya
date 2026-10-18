"""The outbound channel interface rituals and handlers send through."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Button:
    text: str
    callback_data: str


class TelegramGateway(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to: int | None = None,
        buttons: Sequence[Button] = (),
    ) -> int: ...

    async def download_file(self, file_id: str) -> bytes: ...

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None: ...

    async def typing(self, chat_id: int) -> None: ...
