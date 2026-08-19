"""VISION_SPEC 2.3/2.4：扫视-注视状态机 + 习惯化 + 分层冷却。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import perception, saliency
from .events import AttentionEvent
from .visibility_policy import VisibilityPolicy

logger = logging.getLogger(__name__)


@dataclass
class AttentionConfig:
    # VISION_SPEC 2.3 参数表（默认值；config.yaml `attention:` 段可覆盖）
    global_scan_sec: float = 5.0        # 全局快照间隔
    fixation_min_ms: int = 300          # 注视最短停留
    fixation_max_ms: int = 3000         # 注视最长停留
    saccade_interval_ms: int = 800      # 扫视最小间隔
    orient_delay_ms: int = 300          # 朝向反应延迟（实现为：显著变化立即扫视）
    habituation_sec: float = 60.0       # 区域无新内容衰减阈值
    observe_cooldown_win: float = 5.0   # 窗口切换观察冷却
    observe_cooldown_shift: float = 15.0  # 注视转移观察冷却
    observe_cooldown_full: float = 60.0   # 全屏观察冷却
    saliency_weights: tuple = (0.5, 0.3, 0.2)
    fovea_ratio: float = 0.15           # 注视区域半径（屏幕短边比例）
    mouse_focus_stay_s: float = 2.0     # 鼠标停留判定
    mouse_focus_idle_s: float = 30.0    # 鼠标静止 → 前台窗口兜底
    away_idle_s: float = 1800.0         # VISION_SPEC 3：30min 无输入 → away


class AttentionScheduler:
    """扫视-注视状态机（VISION_SPEC 2.3/3）。tick() 每次返回事件列表。"""

    def __init__(self, llm=None, config: dict | None = None):
        self.llm = llm
        self.cfg = AttentionConfig(**{k: v for k, v in (config or {}).items()
                                      if hasattr(AttentionConfig, k)})
        self.state = "fixation"          # fixation / scanning / away（VISION_SPEC 3）
        self.focus = None                # 当前注视区域 {center, since, last_change}
        self._prev_frame = None          # 上一次全局快照
        self._last_scan_ts = 0.0         # 全局快照时间
        self._last_saccade_ts = 0.0      # 上次扫视
        self._last_obs: dict = {}        # {kind: ts} 分层冷却
        self._habituation: dict = {}     # {区域key: last_change_ts}
        self._last_mouse = (0, 0)
        self._last_mouse_move_ts = 0.0
        self._last_win = ""
        self._last_win_ts = 0.0
        self._last_input_ts = time.time()  # 最近用户输入（away 判定）
        self._was_away = False
        self.policy = VisibilityPolicy(config)

    def note_user_input(self) -> None:
        """用户有输入（聊天/键鼠）→ 离开 away（VISION_SPEC 3 恢复 orienting）。"""
        self._last_input_ts = time.time()
        if self.state == "away":
            self.state = "fixation"
            self._was_away = False
            logger.info("attention: away → orienting (user input)")

    # ---------- 主循环 ----------

    def tick(self) -> list[AttentionEvent]:
        events: list[AttentionEvent] = []
        now = time.time()
        frame = perception.grab_gray_downsampled()
        if frame is None:
            return events

        # 0. away 状态（VISION_SPEC 3：30min 无输入 → away）
        if now - self._last_input_ts > self.cfg.away_idle_s:
            if self.state != "away":
                self.state = "away"
                self._was_away = True
                events.append(AttentionEvent(
                    kind="away", region=(0, 0, 1, 1),
                    note="用户长时间无输入", ts=now, source="cursor",
                    reason="idle > away_idle_s",
                ))
            return events  # away 期间不做屏幕感知（VISION_SPEC 3）

        # 1. 窗口切换（最高价值，独立于像素）
        win = self._foreground()
        if win and win != self._last_win:
            self._last_win = win
            self._last_win_ts = now
            events.append(AttentionEvent(kind="window_switch", region=(0, 0, 1, 1),
                                         note=win, ts=now, source="foreground",
                                         reason="foreground window changed"))

        # 2. 显著度（全局快照节奏）
        if now - self._last_scan_ts >= self.cfg.global_scan_sec:
            self._last_scan_ts = now
            regions = saliency.compute_saliency(frame, self._prev_frame,
                                                self.cfg.saliency_weights)
            self._prev_frame = frame

            # 习惯化：区域无新内容 → 降权
            regions = [r for r in regions if self._habituated(now, r)]

            # 3. 注视状态机
            if regions and self._should_saccade(now):
                events.extend(self._saccade(regions, now, frame))

        # 4. 鼠标焦点（VISION_SPEC 2.5.1）
        mouse_ev = self._mouse_focus(now)
        if mouse_ev:
            events.append(mouse_ev)

        # 5. 习惯化事件（当前注视区域衰减）
        if self.focus and now - self.focus["since"] > self.cfg.habituation_sec:
            if not self._region_changed(now, self.focus):
                events.append(AttentionEvent(kind="habituation",
                                             region=self._focus_region(), ts=now,
                                             source="saliency", reason="no novelty 60s"))
                self.focus = None
        return events

    # ---------- 内部 ----------

    def _should_saccade(self, now: float) -> bool:
        """扫视条件：注视超时 或 显著区域更高。"""
        if self.focus is None:
            return True
        if now - self.focus["since"] >= self.cfg.fixation_max_ms / 1000:
            return True
        return now - self._last_saccade_ts >= self.cfg.saccade_interval_ms / 1000

    def _saccade(self, regions: list, now: float, frame) -> list[AttentionEvent]:
        """扫视到最高显著度区域（非当前焦点）。"""
        self._last_saccade_ts = now
        target = regions[0]
        h, w = frame.shape
        cx, cy = target["center"]
        # 网格坐标 → 归一化区域（fovea 半径）
        r = self.cfg.fovea_ratio
        x0, y0 = max(0.0, cx / w - r), max(0.0, cy / h - r)
        x1, y1 = min(1.0, cx / w + r), min(1.0, cy / h + r)
        self.focus = {"center": (cx, cy), "since": now, "last_change": now}
        key = self._region_key((x0, y0, x1, y1))
        self._habituation.setdefault(key, now)

        ev = AttentionEvent(kind="fixation_shift",
                            region=(round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)),
                            note=f"显著度 {target['score']}（{target['source']}）", ts=now)
        # L3 观察由 consumer（pet_server._observe_event）执行（VISION_SPEC 2：
        # scheduler 属于 L0-L2，禁止直接调 LLM）
        return [ev]

    def _observe(self, ev: AttentionEvent, frame, x0, y0, x1, y1) -> None:
        """VISION_SPEC 2 已移除：L3 观察由 consumer 执行，scheduler 不调 LLM。"""

    def _mouse_focus(self, now: float) -> AttentionEvent | None:
        """鼠标位置焦点（VISION_SPEC 2.5.1）：停留 >2s → 焦点=鼠标。"""
        try:
            import ctypes
            class _POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            p = _POINT()
            if not ctypes.windll.user32.GetCursorPos(ctypes.byref(p)):
                return None
            pos = (p.x, p.y)
        except Exception:
            return None
        if pos != self._last_mouse:
            self._last_mouse = pos
            self._last_mouse_move_ts = now
            return None
        if now - self._last_mouse_move_ts >= self.cfg.mouse_focus_stay_s:
            if self.focus is None or self.focus.get("from_mouse") != pos:
                self.focus = {"center": pos, "since": now, "last_change": now, "from_mouse": pos}
                return AttentionEvent(kind="fixation_shift", region=(0, 0, 1, 1),
                                      note=f"焦点=鼠标{pos}", ts=now)
        return None

    def _foreground(self) -> str:
        try:
            from veranima.core.presence import foreground_app
            return foreground_app()
        except Exception:
            return ""

    # ---------- 习惯化 / 冷却 ----------

    def _region_key(self, region: tuple) -> str:
        return f"{region[0]:.2f},{region[1]:.2f}"

    def _region_changed(self, now: float, focus: dict) -> bool:
        return now - focus.get("last_change", 0) < self.cfg.habituation_sec

    def _habituated(self, now: float, region: dict) -> bool:
        key = f"{region['center'][0]},{region['center'][1]}"
        last = self._habituation.get(key)
        if last is None:
            return True  # 新区域 → 新奇偏好
        return now - last < self.cfg.habituation_sec

    def _cooldown_ok(self, kind: str, now: float) -> bool:
        cds = {"shift": self.cfg.observe_cooldown_shift,
               "win": self.cfg.observe_cooldown_win,
               "full": self.cfg.observe_cooldown_full}
        return now - self._last_obs.get(kind, 0) >= cds.get(kind, 60)

    def _mark_observe(self, kind: str, ts: float) -> None:
        self._last_obs[kind] = ts

    def _focus_region(self) -> tuple:
        if not self.focus or "center" not in self.focus:
            return (0, 0, 1, 1)
        cx, cy = self.focus["center"]
        return (max(0.0, cx - 0.15), max(0.0, cy - 0.15), min(1.0, cx + 0.15), min(1.0, cy + 0.15))
