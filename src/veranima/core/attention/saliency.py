"""VISION_SPEC 2.2 显著度地图：三通道加权，输出显著区域列表。"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def compute_saliency(frame: np.ndarray, prev: np.ndarray | None,
                     weights: tuple[float, float, float] = (0.5, 0.3, 0.2),
                     grid: tuple[int, int] = (5, 8)) -> list[dict]:
    """全局快照 → 网格显著度区域列表（按 score 降序）。

    frame/prev：降采样灰度图（np.uint8 2D）。三通道：
    - 运动：|frame - prev|（prev 为 None 时 0）
    - 对比：Sobel 梯度幅度（近似：|dx| + |dy| 的水平和垂直差分）
    - 结构：高频边缘密度（对比通道 > 阈值的占比，即文本/UI 区域）
    输出：[{center:(x,y), score, source}]，center 为网格中心（网格坐标）。
    """
    if frame is None:
        return []
    gh, gw = grid
    h, w = frame.shape
    ch, cw = h // gh, w // gw
    # 首帧（prev=None）无运动历史 → motion 通道置 0，只靠对比/结构
    if prev is None:
        motion = np.zeros_like(frame, dtype=np.int16)
    else:
        motion = np.abs(frame.astype(np.int16) - prev.astype(np.int16))
    # 对比：水平+垂直差分（Sobel 近似）
    gx = np.abs(np.diff(frame, axis=1, prepend=frame[:, :1]))
    gy = np.abs(np.diff(frame, axis=0, prepend=frame[:1, :]))
    contrast = gx + gy
    structure = (contrast > 24).astype(np.float32)  # 高频边缘密度

    w_m, w_c, w_s = weights
    regions = []
    for iy in range(gh):
        for ix in range(gw):
            y0, y1 = iy * ch, min((iy + 1) * ch, h)
            x0, x1 = ix * cw, min((ix + 1) * cw, w)
            m = float(motion[y0:y1, x0:x1].mean()) / 255.0
            c = float(contrast[y0:y1, x0:x1].mean()) / 255.0
            s = float(structure[y0:y1, x0:x1].mean())
            score = w_m * m + w_c * c + w_s * s
            if score > 0.01:
                regions.append({
                    "center": (int((x0 + x1) // 2), int((y0 + y1) // 2)),
                    "score": round(score, 4),
                    "source": "motion" if m > c else ("contrast" if c > s else "structure"),
                })
    regions.sort(key=lambda r: r["score"], reverse=True)
    return regions
