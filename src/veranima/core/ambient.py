"""时空沉浸引擎（DESIGN 5.规则优先级 + R4_SPEC 1）：场景状态锁 + 通道互斥 + 主动发起仲裁最小版。

R4 最小实现（ponytail: 仲裁只做拦截+排序，不做权重公式——等实测需要再加）：
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
    """通道互斥（DESIGN 5.4 通道互斥）：各通道最近活动时间，30min 窗口。"""

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


@dataclass(frozen=True)
class ProactiveCandidate:
    """R4 主动候选（R4_SPEC 1）：来源事件 → 候选。"""

    source: str                 # shared_episode/commitment/scene/ritual/attention
    reason: str                 # 内部可解释原因
    relevance: float = 0.5      # 0..1
    urgency: float = 0.5        # 0..1
    intent: str = "check_in"    # remind/check_in/share/bridge
    context: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProactiveDecision:
    """R4 主动决策（R4_SPEC 1）：allow=false 是正常返回，不是异常。"""

    allow: bool
    reason: str
    cooldown_until: float = 0.0
    candidate: ProactiveCandidate | None = None


class ProactiveGate:
    """R4 确定性闸门（R4_SPEC 2）：按顺序执行，任一失败返回 allow=false。

    不保留 random 概率作为主入口；如需自然性，调用方可在此之后做一次
    小概率抑制并记录 reason（R4_SPEC 2 末条）。
    """

    # 来源默认相关度要求（R4_SPEC 3 来源策略；ritual/scene 有真实来源免阈值）
    SOURCE_RELEVANCE_MIN = {
        "shared_episode": 0.65,
        "commitment": 0.65,
        "attention": 0.65,
    }
    # 来源默认优先级（低=高优先）
    SOURCE_PRIORITY = {
        "commitment": 0, "shared_episode": 1, "scene": 2, "ritual": 3, "attention": 4,
    }

    def __init__(self, config: dict | None = None, now: float | None = None) -> None:
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.max_per_day = int(cfg.get("max_per_day", 2))
        self.min_gap_minutes = int(cfg.get("min_gap_minutes", 30))
        self.source_gap_minutes = int(cfg.get("source_gap_minutes", 120))
        self.quiet_hours = cfg.get("quiet_hours", [23, 8])
        self.ignore_backoff = bool(cfg.get("ignore_backoff", True))
        self._now = now
        self._last_any = 0.0
        self._last_sent: dict[str, float] = {}
        self._today_count = 0
        self._today_key = ""
        self._ignored_streak: dict[str, int] = {}
        self._paused = False  # 用户明确"不想被打扰" → 暂停直到用户主动恢复

    def _t(self) -> float:
        return self._now if self._now is not None else time.time()

    def _day(self) -> str:
        import datetime
        return datetime.datetime.fromtimestamp(self._t()).strftime("%Y-%m-%d")

    def _in_quiet_hours(self) -> bool:
        import datetime
        h = datetime.datetime.fromtimestamp(self._t()).hour
        start, end = self.quiet_hours
        if start <= end:
            return start <= h < end
        return h >= start or h < end  # 跨午夜（如 [23, 8]）

    # ---------- 主入口 ----------

    def decide(self, candidate: ProactiveCandidate, *, scene: str = "normal",
               other_channel_active: bool = False) -> ProactiveDecision:
        """9 条确定性闸门（R4_SPEC 2）。"""
        now = self._t()
        # 1. enabled 与用户暂停开关
        if not self.enabled:
            return ProactiveDecision(False, "proactive disabled", candidate=candidate)
        if self._paused:
            return ProactiveDecision(False, "paused by user (不想被打扰)", candidate=candidate)
        # 2. 场景不是 busy/away/blocked
        if scene not in ("normal", "chat"):
            return ProactiveDecision(False, f"scene={scene}", candidate=candidate)
        # 3. 当前没有其他通道活跃
        if other_channel_active:
            return ProactiveDecision(False, "other channel active", candidate=candidate)
        # 4. quiet hours 外
        if self._in_quiet_hours():
            return ProactiveDecision(False, "quiet hours", candidate=candidate)
        # 5. 当日上限未满
        d = self._day()
        if d != self._today_key:
            self._today_key = d
            self._today_count = 0
        if self._today_count >= self.max_per_day:
            return ProactiveDecision(False, "daily cap reached", candidate=candidate)
        # 6. 距上次主动消息足够久（全局 30min；同源 2h）
        if now - self._last_any < self.min_gap_minutes * 60:
            return ProactiveDecision(False, "min gap not elapsed", candidate=candidate)
        if now - self._last_sent.get(candidate.source, 0.0) < self.source_gap_minutes * 60:
            return ProactiveDecision(False, f"source gap ({candidate.source})", candidate=candidate)
        # 7. 连续忽略抑制（R4_SPEC 4：连续 2 次未响应 → 同源冷却翻倍）
        streak = self._ignored_streak.get(candidate.source, 0)
        if self.ignore_backoff and streak >= 2:
            grow = self.source_gap_minutes * 60 * (2 ** (streak - 1))
            if now - self._last_sent.get(candidate.source, 0.0) < grow:
                return ProactiveDecision(False, f"ignored backoff ({candidate.source} ×{streak})", candidate=candidate)
        # 8. relevance 与来源要求（R4_SPEC 2 第 8 条 / R4_SPEC 3）
        min_rel = self.SOURCE_RELEVANCE_MIN.get(candidate.source)
        if min_rel is not None and candidate.relevance < min_rel:
            return ProactiveDecision(False, f"relevance {candidate.relevance:.2f} < {min_rel}", candidate=candidate)
        if candidate.source == "attention" and not candidate.context.get("matched_episode"):
            return ProactiveDecision(False, "attention without shared memory", candidate=candidate)
        if candidate.source == "ritual" and not candidate.context.get("calendar_source"):
            return ProactiveDecision(False, "ritual without real memory source", candidate=candidate)
        # 9. LLM 可用性在生成前检查（R4_SPEC 2 第 9 条；由调用方执行，见 note_failure）
        return ProactiveDecision(True, "allowed", cooldown_until=0.0, candidate=candidate)

    # ---------- 反馈 ----------

    def commit(self, candidate: ProactiveCandidate) -> None:
        """发起成功：记全局/同源冷却 + 日计数 + 忽略计数清零。"""
        now = self._t()
        self._last_any = now
        self._last_sent[candidate.source] = now
        self._today_count += 1
        self._ignored_streak[candidate.source] = 0

    def note_failure(self, candidate: ProactiveCandidate) -> None:
        """生成/解析失败：不算主动发送（R4_SPEC 2 第 9 条降级）。"""
        logger.debug("proactive generation failed: %s", candidate.reason)

    def note_ignored(self, source: str) -> None:
        """R4_SPEC 4：连续两次未响应 → 同源冷却翻倍。"""
        n = self._ignored_streak.get(source, 0) + 1
        self._ignored_streak[source] = n
        if n >= 2:
            logger.info("proactive ignored %d× (source=%s), backoff active", n, source)

    def pause(self) -> None:
        """用户明确"不想被打扰"：立即暂停直到用户主动恢复。"""
        self._paused = True
        logger.info("proactive paused by user until explicit resume")

    def resume(self) -> None:
        self._paused = False
        logger.info("proactive resumed by user")

    def sort(self, candidates: list[ProactiveCandidate]) -> list[ProactiveCandidate]:
        """同轮多候选按来源优先级排序（commitment 最先）。"""
        return sorted(candidates, key=lambda c: self.SOURCE_PRIORITY.get(c.source, 9))


class Arbitrator:
    """主动发起仲裁器最小版（R4_SPEC 2.确定性闸门顺序）：拦截 + 排序，不做权重。

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
