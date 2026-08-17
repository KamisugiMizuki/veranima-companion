"""时空沉浸引擎（DESIGN 4.7 + M3_SPEC 2.1/2.2）：场景状态锁 + 通道互斥 + 主动发起仲裁最小版。

M3a 最小实现（ponytail: 仲裁只做拦截+排序，不做权重公式——等实测需要再加）：
- SceneLock：normal → busy（慢回/短回/禁主动）→ 自动恢复（2h 无触碰）
- ChannelActivityTracker：30min 活跃窗口，供互斥查询
- Arbitrator：五机制请求汇入 → 场景/互斥/频率拦截 → 优先级排序
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 场景类型（子串匹配；覆盖"我去看个电影了"这类变体）
SCENE_KEYWORDS = {
    "busy": ["电影", "看剧", "追剧", "开会", "上班", "写稿", "写报告", "赶工", "加班", "学习", "复习", "游戏", "副本", "上课", "忙"],
    "away": ["睡觉", "睡了", "出门", "出去", "洗澡", "吃饭", "午休"],
}


@dataclass
class Scene:
    type: str = "normal"          # normal / busy / away
    started_at: float = 0.0
    last_touch_at: float = 0.0

    @property
    def active(self) -> bool:
        return self.type != "normal"


class SceneLock:
    """场景状态锁：normal → busy/away → 2h 无触碰自动恢复 normal。

    busy：慢回/短回/禁主动；away：不打扰（连回复都延迟）。
    """

    AUTO_RESET_SECONDS = 2 * 3600   # 2h 无触碰自动恢复
    BUSY_MAX_LEN = 40               # busy 场景回复长度上限（字）
    BUSY_DELAY_SECONDS = 30         # busy 场景回复延迟下限（模拟「在忙没看到」）
    AWAY_DELAY_SECONDS = 300        # away 场景回复延迟（不打扰，等回来）

    def __init__(self, now: float | None = None) -> None:
        self.scene = Scene()
        self._now = now

    def _t(self) -> float:
        return self._now if self._now is not None else time.time()

    def note(self, user_text: str) -> str:
        """用户消息进来时更新场景。返回当前场景类型。"""
        t = self._t()
        if self.scene.active and self.scene.type == "busy":
            # busy 中：检测「结束」信号（用户主动回来/说看完了）
            if any(k in user_text for k in ("看完了", "结束了", "回来了", "忙完", "下班", "上线了")):
                self.scene = Scene(type="normal", started_at=t, last_touch_at=t)
                logger.info("scene: busy -> normal (user returned)")
                return "normal"
            self.scene.last_touch_at = t
            return "busy"
        if self.scene.active and self.scene.type == "away":
            if any(k in user_text for k in ("回来了", "醒了", "起了", "出门回来了", "在吗")):
                self.scene = Scene(type="normal", started_at=t, last_touch_at=t)
                logger.info("scene: away -> normal (user returned)")
                return "normal"
            self.scene.last_touch_at = t
            return "away"
        # normal：检测进入场景
        for kw in SCENE_KEYWORDS["busy"]:
            if kw in user_text:
                self.scene = Scene(type="busy", started_at=t, last_touch_at=t)
                logger.info("scene: normal -> busy (kw=%s)", kw)
                return "busy"
        for kw in SCENE_KEYWORDS["away"]:
            if kw in user_text:
                self.scene = Scene(type="away", started_at=t, last_touch_at=t)
                logger.info("scene: normal -> away (kw=%s)", kw)
                return "away"
        return "normal"

    def current(self) -> str:
        """当前场景；超时自动恢复 normal。"""
        if self.scene.active and self._t() - self.scene.last_touch_at > self.AUTO_RESET_SECONDS:
            old = self.scene.type
            self.scene = Scene(type="normal", started_at=self._t(), last_touch_at=self._t())
            logger.info("scene: %s -> normal (auto reset)", old)
        return self.scene.type

    def reply_delay(self) -> float:
        """当前场景下回复应延迟的秒数。normal=0（不延迟）。"""
        s = self.current()
        if s == "away":
            return self.AWAY_DELAY_SECONDS
        if s == "busy":
            return self.BUSY_DELAY_SECONDS
        return 0.0

    def max_len(self) -> int | None:
        """当前场景回复长度上限（字）；None=不限制。"""
        return self.BUSY_MAX_LEN if self.current() == "busy" else None


class ChannelActivityTracker:
    """通道互斥（M3_SPEC 2.2）：各通道最近活动时间，30min 窗口。"""

    WINDOW_SECONDS = 30 * 60

    def __init__(self, now: float | None = None) -> None:
        self._last = {}  # channel -> ts
        self._now = now

    def _t(self) -> float:
        return self._now if self._now is not None else time.time()

    def touch(self, channel: str) -> None:
        self._last[channel] = self._t()

    def active(self, channel: str) -> bool:
        """该通道是否在活跃窗口内（用户注意力在此通道）。"""
        ts = self._last.get(channel)
        if ts is None:
            return False
        return self._t() - ts <= self.WINDOW_SECONDS

    def any_active(self) -> bool:
        return any(self.active(c) for c in self._last)

    def blocking(self, requester: str) -> bool:
        """请求方是否被其他通道的活跃阻塞（互斥：其他通道活跃则请求方静默）。"""
        for c in self._last:
            if c != requester and self.active(c):
                return True
        return False


class Arbitrator:
    """主动发起仲裁器最小版（M3_SPEC 2.1）：拦截 + 排序，不做权重。

    优先级：conflict > ritual > associative > fatigue > idle（DESIGN 4.7）。
    """

    PRIORITY = {"conflict": 0, "ritual": 1, "associative": 2, "fatigue": 3, "idle": 4}
    COOLDOWN_SECONDS = 30 * 60   # 同一发起源冷却
    MAX_PER_DAY = 8              # 全天主动消息上限

    def __init__(self, now: float | None = None) -> None:
        self._cooldown: dict[str, float] = {}
        self._today_count = 0
        self._today_key = ""
        self._fail_streak: dict[str, int] = {}  # 连续失败计数（4.7 连续失败抑制）
        self._now = now

    def _t(self) -> float:
        return self._now if self._now is not None else time.time()

    def _day(self) -> str:
        import datetime
        base = datetime.datetime.fromtimestamp(self._t())
        return base.strftime("%Y-%m-%d")

    def request(self, mechanism: str, *, scene: str = "normal", other_channel_active: bool = False) -> bool:
        """请求主动发起；返回 True=允许。拦截条件：场景非 normal / 其他通道活跃 / 冷却 / 日上限。"""
        # 日计数滚动
        d = self._day()
        if d != self._today_key:
            self._today_key = d
            self._today_count = 0
        if scene != "normal":
            logger.debug("arbitrator: blocked by scene=%s", scene)
            return False
        if other_channel_active:
            logger.debug("arbitrator: blocked by other channel active")
            return False
        now = self._t()
        if now < self._cooldown.get(mechanism, 0):
            logger.debug("arbitrator: blocked by cooldown(%s)", mechanism)
            return False
        if self._today_count >= self.MAX_PER_DAY:
            logger.debug("arbitrator: blocked by daily cap")
            return False
        return True

    def commit(self, mechanism: str) -> None:
        """发起成功：记冷却 + 日计数 + 失败计数清零。"""
        self._cooldown[mechanism] = self._t() + self.COOLDOWN_SECONDS
        self._today_count += 1
        self._fail_streak[mechanism] = 0

    def note_failure(self, mechanism: str) -> None:
        """连续失败抑制（DESIGN 4.7）：连续 2 次被无视/打断 → 冷却指数增长。

        第 n 次连续失败 → 冷却 = 30min × 2^(n-2)（第 2 次起翻倍）。
        """
        n = self._fail_streak.get(mechanism, 0) + 1
        self._fail_streak[mechanism] = n
        if n >= 2:
            grow = self.COOLDOWN_SECONDS * (2 ** (n - 1))  # 第 2 次=60min，第 3 次=120min…
            self._cooldown[mechanism] = self._t() + grow
            logger.info("arbitrator: %s failed %d×, cooldown extended to %.0f min",
                        mechanism, n, grow / 60)

    def sort(self, requests: list[str]) -> list[str]:
        """按优先级排序（conflict 最先）。"""
        return sorted(requests, key=lambda m: self.PRIORITY.get(m, 9))
