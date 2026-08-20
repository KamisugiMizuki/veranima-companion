"""跨通道图片消息契约。"""
from __future__ import annotations

import base64
import binascii
import io
import re
from dataclasses import dataclass

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class ImagePayloadError(ValueError):
    pass


@dataclass(frozen=True)
class ImagePayload:
    data_url: str
    raw: bytes
    content_type: str
    animated: bool = False
    source: str = ""


def sniff_image_type(raw: bytes) -> str | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def make_image_payload(raw: bytes, *, content_type: str | None = None, source: str = "") -> ImagePayload:
    raw = bytes(raw or b"")
    if not raw:
        raise ImagePayloadError("图片为空")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImagePayloadError("图片过大")
    detected = sniff_image_type(raw)
    ctype = (content_type or "").split(";", 1)[0].lower() or detected or ""
    if detected is None or ctype not in ALLOWED_IMAGE_TYPES or ctype != detected:
        raise ImagePayloadError("不支持的图片类型")
    animated = _validate_image(raw, ctype)
    return ImagePayload(
        data_url=f"data:{ctype};base64,{base64.b64encode(raw).decode('ascii')}",
        raw=raw,
        content_type=ctype,
        animated=animated,
        source=source,
    )


def payload_from_data_url(data_url: str, *, source: str = "") -> ImagePayload:
    """Decode and validate a data URL at a trust boundary."""
    match = re.fullmatch(r"data:(image/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/=\r\n]+)", str(data_url or ""))
    if not match:
        raise ImagePayloadError("图片必须是 base64 data URL")
    encoded = re.sub(r"\s+", "", match.group(2))
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ImagePayloadError("image exceeds 10MB")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ImagePayloadError("图片 base64 无效") from exc
    return make_image_payload(raw, content_type=match.group(1), source=source)


def _validate_image(raw: bytes, content_type: str) -> bool:
    """Reject truncated/decompression-bomb inputs and detect animation."""
    from PIL import Image

    expected = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if expected.get(str(image.format).upper()) != content_type:
                raise ImagePayloadError("图片格式与类型不匹配")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ImagePayloadError("图片像素尺寸过大")
            animated = bool(getattr(image, "is_animated", False))
            image.verify()
            return animated
    except ImagePayloadError:
        raise
    except Exception as exc:
        raise ImagePayloadError("图片内容损坏") from exc
