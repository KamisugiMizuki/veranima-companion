"""VISION_SPEC 2.1 三层感知：截屏降采样、区域裁剪、窗口/鼠标元数据。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageGrab
    _CAN_CAPTURE = True
except ImportError:
    _CAN_CAPTURE = False


def grab_gray_downsampled(scale: int = 8) -> "np.ndarray | None":
    """全屏截图 → 降采样灰度图（uint8 2D）。无截屏能力返回 None。"""
    if not _CAN_CAPTURE:
        return None
    try:
        import numpy as np
        img = ImageGrab.grab().convert("L")
        img = img.resize((img.width // scale, img.height // scale))
        return np.array(img, dtype=np.uint8)
    except Exception:
        return None


def grab_color_region(region: tuple[float, float, float, float],
                      crop_ratio: float = 0.30, max_side: int = 768) -> str:
    """Capture one in-memory RGB crop using normalized screen coordinates.

    The scheduler's grayscale frame is for saliency only.  L3 must receive a
    color crop captured from the same coordinate space, not grayscale data
    converted to RGB after the fact.
    """
    if not _CAN_CAPTURE:
        return ""
    try:
        import base64
        import io as _io
        img = ImageGrab.grab().convert("RGB")
        width, height = img.size
        x0n, y0n, x1n, y1n = (float(v) for v in region)
        x0 = max(0, min(width - 1, int(x0n * width)))
        y0 = max(0, min(height - 1, int(y0n * height)))
        x1 = max(x0 + 1, min(width, int(x1n * width)))
        y1 = max(y0 + 1, min(height, int(y1n * height)))
        max_span = max(1, int(min(width, height) * max(0.01, min(crop_ratio, 1.0))))
        crop_w, crop_h = x1 - x0, y1 - y0
        if crop_w > max_span or crop_h > max_span:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            crop_w = min(crop_w, max_span)
            crop_h = min(crop_h, max_span)
            x0 = max(0, min(width - crop_w, int(cx - crop_w / 2)))
            y0 = max(0, min(height - crop_h, int(cy - crop_h / 2)))
            x1, y1 = x0 + crop_w, y0 + crop_h
        crop = img.crop((x0, y0, x1, y1))
        if max(crop.size) > max_side:
            scale = max_side / max(crop.size)
            crop = crop.resize((max(1, int(crop.width * scale)),
                                max(1, int(crop.height * scale))))
        buf = _io.BytesIO()
        crop.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return ""


def grab_region(gray: "np.ndarray | None", x0: int, y0: int, x1: int, y1: int) -> str:
    """从降采样图截取区域 → base64 PNG（观察用小图，低 token）。空返回 ""。"""
    if gray is None:
        return ""
    try:
        import base64
        import io as _io
        import numpy as np
        h, w = gray.shape
        box = gray[max(y0, 0):min(y1, h), max(x0, 0):min(x1, w)]
        if box.size == 0:
            return ""
        # 放大回原分辨率比例（观察模型对正常尺寸更友好）：scale 8 → 小图直接送
        img = Image.fromarray(np.ascontiguousarray(box))
        buf = _io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""
