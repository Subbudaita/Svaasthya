"""Plate photo storage.

Local disk for now; a Supabase Storage implementation slots in behind the same
protocol when the project is connected. Paths are namespaced by user id.
"""

import uuid
from pathlib import Path
from typing import Protocol


class PhotoStore(Protocol):
    def save(self, user_id: uuid.UUID, data: bytes, media_type: str) -> str: ...


class LocalPhotoStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def save(self, user_id: uuid.UUID, data: bytes, media_type: str) -> str:
        ext = {"image/jpeg": "jpg", "image/png": "png"}.get(media_type, "bin")
        relative = Path(str(user_id)) / f"{uuid.uuid4()}.{ext}"
        target = self._root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return relative.as_posix()
