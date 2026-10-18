"""Prepare an uploaded photo for the vision model."""

import base64
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

# Long edge beyond which the API downsamples anyway; sending more is pure latency.
MAX_EDGE_PX = 1568
MAX_INPUT_BYTES = 15 * 1024 * 1024


class InvalidImageError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedImage:
    media_type: str
    data: bytes

    def as_content_block(self) -> dict:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": self.media_type,
                "data": base64.standard_b64encode(self.data).decode("ascii"),
            },
        }


def prepare_image(raw: bytes) -> PreparedImage:
    if not raw:
        raise InvalidImageError("empty upload")
    if len(raw) > MAX_INPUT_BYTES:
        raise InvalidImageError("image larger than 15 MB")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85, optimize=True)
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError("not a readable image") from exc
    return PreparedImage(media_type="image/jpeg", data=out.getvalue())
