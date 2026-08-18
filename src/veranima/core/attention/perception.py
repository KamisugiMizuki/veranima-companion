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
