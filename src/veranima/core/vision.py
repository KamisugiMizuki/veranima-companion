"""M4 视觉注意力调度器（M4_SPEC 1.x）：锚点 + 三态 + 像素差异判定。

实现范围（MVP）：
- 兴趣锚点：归一化区域 + 三态切换（稳定/触发/游离）
- 像素差异：锚点区域灰度直方图卡方距离（Pillow，Windows ImageGrab）
- L0-L3 分级：L0 由外部（在场检测）喂事件；L2 用启发式（直方图差异）替代本地小模型
  （ponytail: 无可用本地视觉模型，启发式先顶住；接入本地模型后替换 _interesting 实现）

截屏依赖 Pillow ImageGrab（Windows）；非 Windows 环境降级为纯逻辑（无截屏）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

try:
    from PIL import ImageGrab
    _CAN_CAPTURE = True
except ImportError:
    _CAN_CAPTURE = False

# 三态参数（M4_SPEC 1.2）
STABLE_INTERVAL = 30.0     # 稳定期：30s 低频
TRIGGER_INTERVAL = 5.0     # 触发期：5s 高频
WANDER_INTERVAL = 120.0    # 游离期：2min 低分辨率全屏
CHI2_THRESHOLD = 0.2       # 直方图卡方距离阈值（显著变化）
TRIGGER_RESET_COUNT = 3    # 触发期连续 N 次无变化回稳定


@dataclass
class Anchor:
    name: str
    region: tuple[float, float, float, float]  # 归一化 (x0,y0,x1,y1)
    tag: str = ""
    priority: int = 1


@dataclass
class VisualObservation:
    tag: str = ""
    note: str = ""
    ts: float = 0.0


class VisualAttention:
    """三态视觉调度：稳定 → 触发 → 游离，像素差异驱动。"""

    def __init__(self, anchors: list[Anchor] | None = None, now: float | None = None) -> None:
        self.anchors = anchors or [
            Anchor("窗口标题栏", (0.05, 0.0, 0.95, 0.05), tag="应用", priority=1),
        ]
        self._now = now
        self.state = "stable"          # stable / trigger / wander
        self.focus: dict = {}          # {"tag": str, "since": ts}
        self.observations: list = []   # 环形 10 条（M4_SPEC 1.4）
        self._last_capture: dict | None = None   # 上次锚点区域直方图
        self._trigger_count = 0        # 触发期连续无变化计数
        self._last_observe_ts = 0.0    # L3 观察冷却（≥60s，M4_SPEC 1.3）

    def _t(self) -> float:
        return self._now if self._now is not None else time.time()

    # ---------- 三态 ----------

    def tick(self, presence: bool = True) -> str:
        """按在场状态推进状态机，返回当前态。"""
        if not presence:
            self.state = "wander"
            return self.state
        if self.state == "trigger":
            self._trigger_count += 1
            if self._trigger_count >= TRIGGER_RESET_COUNT:
                self.state = "stable"
                self._trigger_count = 0
        elif self.state == "wander":
            self.state = "stable"
        return self.state

    def interval(self) -> float:
        """当前态截图间隔（秒）。"""
        return {"stable": STABLE_INTERVAL, "trigger": TRIGGER_INTERVAL, "wander": WANDER_INTERVAL}[self.state]

    # ---------- 像素差异（L2 启发式） ----------

    def capture_anchor_histogram(self, screen_size: tuple[int, int] | None = None):
        """截取锚点区域灰度直方图（64 bins）。无截屏能力返回 None。"""
        if not _CAN_CAPTURE:
            return None
        try:
            img = ImageGrab.grab()
            w, h = screen_size or img.size
            hist = []
            for a in self.anchors:
                x0, y0, x1, y1 = a.region
                box = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
                crop = img.crop(box).convert("L")
                hgram = crop.histogram()
                # 压缩到 64 bins
                bins = [0] * 64
                for i, v in enumerate(hgram):
                    bins[i * 64 // 256] += v
                total = sum(bins) or 1
                hist.append([v / total for v in bins])
            return hist
        except Exception:
            return None

    @staticmethod
    def chi2_distance(h1: list[float], h2: list[float]) -> float:
        """灰度直方图卡方距离（M4_SPEC 1.2）。"""
        if not h1 or not h2 or len(h1) != len(h2):
            return 0.0
        return sum(((a - b) ** 2) / (a + b + 1e-9) for a, b in zip(h1, h2)) / len(h1)

    def significant_change(self) -> bool:
        """锚点区域是否有显著变化（直方图卡方距离 > 阈值）。"""
        cur = self.capture_anchor_histogram()
        if cur is None:
            return False
        if self._last_capture is None:
            self._last_capture = cur
            return False
        changed = any(
            self.chi2_distance(a, b) > CHI2_THRESHOLD
            for a, b in zip(cur, self._last_capture)
        )
        self._last_capture = cur
        if changed:
            self._trigger_count = 0
            if self.state == "stable":
                self.state = "trigger"
        return changed

    # ---------- 观察（L3 结果注入） ----------

    def note_observe(self, tag: str, note: str = "") -> bool:
        """注入 L3 观察（环形缓冲 10 条 + 60s 冷却）。返回是否记录。"""
        now = self._t()
        if now - self._last_observe_ts < 60.0:
            return False
        self._last_observe_ts = now
        self.observations.append(VisualObservation(tag=tag, note=note, ts=now))
        if len(self.observations) > 10:
            self.observations.pop(0)
        if tag:
            self.focus = {"tag": tag, "since": now}
        return True
