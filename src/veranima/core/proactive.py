"""主动触发（MVP3）：定时问候 + 节庆与纪念日反应。

- GreetingScheduler：时间段问候（早/午/晚），每日每时段去重
- OccasionChecker：系统节日 + 记忆中的纪念日（生日等）→ 触发反应
纯函数设计（now 注入），便于测试；CLI 用后台线程驱动。
"""

from __future__ import annotations

import logging
import hashlib
import random
import re
from dataclasses import dataclass, field

from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

MEAL_SLOTS = {
    "breakfast": (8, "到饭点了，先去吃点早饭。"),
    "lunch": (12, "到饭点了，先去吃午饭。"),
    "dinner": (17, "到饭点了，先去吃晚饭。"),
}

# 系统节日（公历固定日期；农历节日暂不处理）
FIXED_HOLIDAYS = {
    (1, 1): "元旦",
    (2, 14): "情人节",
    (3, 8): "妇女节",
    (5, 1): "劳动节",
    (6, 1): "儿童节",
    (9, 10): "教师节",
    (10, 1): "国庆节",
    (12, 25): "圣诞节",
}

# 纪念日提取：从记忆内容里找"生日/纪念日" + 日期
DATE_PATTERNS = [
    r"(\d{1,2})月(\d{1,2})日",          # 3月14日
    r"(\d{1,2})\.(\d{1,2})",            # 3.14
    r"(\d{1,2})-(\d{1,2})",             # 3-14
]


@dataclass
class GreetingScheduler:
    """时间段问候：早(6-10)/午(11-14)/晚(18-23)，每日去重。

    greeted 持久化进 agent_state 快照（2026-08-31 用户反馈：问候轰炸——
    每次重启进程内 set 清零，同一天同一窗口反复重发早安）。
    """

    greeted: set[str] = field(default_factory=set)

    def to_state(self) -> list[str]:
        return sorted(self.greeted)

    def restore_state(self, keys) -> None:
        # 只回灌当日键（键格式 "YYYY-MM-DD:slot"）——历史键留在库里也无害，
        # 但没必要让 set 无限膨胀
        import datetime
        today = datetime.date.today().isoformat()
        self.greeted.update(str(k) for k in (keys or ()) if str(k).startswith(today + ":"))

    @staticmethod
    def slot_at(now=None) -> str | None:
        """当前时刻落在哪个问候窗口（不判当日是否已发过）。"""
        import datetime
        now = now or datetime.datetime.now()
        h = now.hour
        if 6 <= h < 10:
            return "morning"
        if 11 <= h < 14:
            return "noon"
        if 18 <= h < 23:
            return "evening"
        return None

    def due_greeting(self, now=None) -> str | None:
        """返回当前应发的问候类型（morning/noon/evening）或 None（已问候过/非窗口）。"""
        import datetime
        now = now or datetime.datetime.now()
        slot = self.slot_at(now)
        if slot is None:
            return None
        key = f"{now.date()}:{slot}"
        if key in self.greeted:
            return None
        self.greeted.add(key)
        return slot

    @staticmethod
    def greeting_text(slot: str) -> str:
        if slot == "morning":
            return "早。今天有什么打算？"
        if slot == "noon":
            return "中午好，吃过饭了吗？"
        return "晚上好。今天过得怎么样？"


@dataclass
class MealReminderScheduler:
    """三餐提醒：每天各在锚点前后 10 分钟内确定性随机一次。"""

    jitter_minutes: int = 10
    enabled: bool = True
    slots: dict = field(default_factory=lambda: dict(MEAL_SLOTS))

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.enabled = bool(config.get("enabled", True))
        self.jitter_minutes = max(0, int(config.get("jitter_minutes", 10)))
        self.slots = dict(MEAL_SLOTS)
        for meal, default in MEAL_SLOTS.items():
            raw = config.get(meal, {}) or {}
            if isinstance(raw, dict):
                hour = int(raw.get("hour", default[0]))
                text = str(raw.get("text", default[1])).strip() or default[1]
            else:
                hour, text = default
            self.slots[meal] = (max(0, min(23, hour)), text)

    def adjust_to_user_cycle(self, wake_hour: float | None) -> None:
        """2026-08-30 用户拍板：用户作息明显偏离 6/12/17 时按用户作息推三餐锚点。

        早餐=起床+2h、午餐=起床+6h、晚餐=起床+11h（近似常规间隔）；
        用户起床时间与默认 6 点差 <1h 则不动（等于常规作息）。
        """
        if wake_hour is None:
            return
        try:
            wake_hour = float(wake_hour)
        except (TypeError, ValueError):
            return
        if not (0 <= wake_hour < 24) or abs(wake_hour - 6.0) < 1.0:
            return  # 常规作息，保持默认 6/12/17
        anchors = (
            ("breakfast", (wake_hour + 2.0) % 24),
            ("lunch", (wake_hour + 6.0) % 24),
            ("dinner", (wake_hour + 11.0) % 24),
        )
        for meal, hour in anchors:
            _text = self.slots.get(meal, ("", ""))[1]
            self.slots[meal] = (int(hour) % 24, _text)

    def scheduled_at(self, day, meal: str):
        import datetime
        hour, _text = self.slots[meal]
        digest = hashlib.sha256(f"{day.isoformat()}:{meal}".encode()).digest()
        offset = random.Random(int.from_bytes(digest[:8], "big")).randint(
            -self.jitter_minutes, self.jitter_minutes,
        )
        return datetime.datetime.combine(day, datetime.time(hour=hour)) + datetime.timedelta(minutes=offset)

    def due(self, *, now=None, sent_ids: set[str] | None = None):
        """命中到点的餐（同餐当日去重）。

        """
        import datetime
        now = now or datetime.datetime.now()
        if now.tzinfo is not None:
            now = now.astimezone().replace(tzinfo=None)
        if not self.enabled:
            return None
        sent_ids = sent_ids or set()
        for meal, (hour, text) in self.slots.items():
            candidate_id = f"meal:{now.date().isoformat()}:{meal}"
            if candidate_id in sent_ids:
                continue
            target = self.scheduled_at(now.date(), meal)
            window_end = datetime.datetime.combine(now.date(), datetime.time(hour=hour)) + datetime.timedelta(
                minutes=self.jitter_minutes,
            )
            if target <= now <= window_end:
                return meal, text, candidate_id
        return None


@dataclass
class OccasionChecker:
    """节庆与纪念日检查（每日去重）。"""

    triggered: set[str] = field(default_factory=set)

    def due_occasion(self, memory: MemoryStore | None, now=None) -> str | None:
        """返回今天应触发的节日/纪念日名称，或 None（无/已触发过）。"""
        import datetime
        now = now or datetime.datetime.now()
        md = (now.month, now.day)
        key = str(now.date())

        # 系统节日
        holiday = FIXED_HOLIDAYS.get(md)
        if holiday and f"{key}:{holiday}" not in self.triggered:
            self.triggered.add(f"{key}:{holiday}")
            return f"今天是个特别的日子：{holiday}"

        # 记忆中的纪念日（用户生日等）；memory 为 None 时跳过（纯节日检查）
        if memory is not None:
            anniversary = self._find_anniversary(memory, md)
            if anniversary and f"{key}:{anniversary}" not in self.triggered:
                self.triggered.add(f"{key}:{anniversary}")
                return anniversary
        return None

    @staticmethod
    def _find_anniversary(memory: MemoryStore, md: tuple[int, int]) -> str | None:
        """从 semantic/episodic 记忆里找今天命中的纪念日（含'生日/纪念'字样）。"""
        hits = []
        for layer in ("semantic", "episodic"):
            for e in memory.list_layer(layer, limit=100):
                if not re.search(r"生日|纪念|周年", e.content):
                    continue
                for pat in DATE_PATTERNS:
                    m = re.search(pat, e.content)
                    if m and (int(m.group(1)), int(m.group(2))) == md:
                        hits.append(e.content[:50])
                        break
        if not hits:
            return None
        return "今天好像是你的特别日子：" + hits[0]

    @staticmethod
    def occasion_reaction(kind: str, name: str = "小V") -> str:
        """节日/纪念日的反应文案（模板；有记忆佐证时更具体）。"""
        if "生日" in kind:
            return f"今天是你的生日呀。{name}不怎么会挑礼物，但想跟你说：谢谢你来到这个世界。"
        return f"{kind}。{name}记得今天，想先跟你说一声。"
class OfflineThinkTimer:
    """8.7.4 离线思考定时器：静默 N 分钟后低概率触发（窗口去重）。

    纯判定逻辑（now 注入），便于测试。触发一次后，需再静默满一个窗口
    才可能再次触发（防止 bot 自说自话刷屏）。
    """

    def __init__(
        self,
        silence_minutes: int = 30,
        probability: float = 0.3,
        max_per_day: int = 2,
        growth_factor: float = 0.08,
        max_probability: float = 0.95,
        rand: random.Random | None = None,
    ):
        self.silence_minutes = max(1, int(silence_minutes))
        self.probability = max(0.0, min(1.0, probability))  # 当前概率（miss 后增长）
        self._base_probability = self.probability           # 基础概率（发送后重置回）
        self.growth_factor = max(0.0, min(1.0, growth_factor))
        self.max_probability = max(0.0, min(1.0, max_probability))
        self.max_per_day = max(0, int(max_per_day))  # 0 = 不限
        self._rand = rand or random.Random()
        self._last_check_at: float | None = None  # 上次掷骰时间（窗口内只掷一次）
        self._day: str | None = None         # 当前计数日（YYYY-MM-DD）
        self._day_count: int = 0             # 当日已触发次数

    def due(self, now: float, last_activity: float | None) -> bool:
        """静默超过 N 分钟且本窗口未触发 → 掷骰决定是否触发。

        每个静默窗口只掷一次骰（`_last_check_at` 窗口去重），
        否则 60s tick 会在窗口内掷 30 次骰、概率闸门形同虚设
        （2026-08-04 修复：0.3 概率实际 ≈100% 每 30 分钟必发）。

        渴望度积累（借鉴 revive-companion 的 PoissonEngine）：
        掷骰未命中 → 概率 +growth_factor（"想念"随时间积累）；
        命中 → 概率重置回基础值（"想念"得到满足）。
        概率为 0 时视为关闭（永不触发，也不增长）。

        每日上限：同一天触发次数达到 max_per_day 后不再触发
        （2026-08 修复：防止用户入睡后整夜反复轰炸）。
        """
        if last_activity is None:
            return False
        if now - last_activity < self.silence_minutes * 60:
            return False
        # 窗口内只判定一次（无论是否触发，满一个窗口才重新掷骰）
        if self._last_check_at is not None and now - self._last_check_at < self.silence_minutes * 60:
            return False
        self._last_check_at = now
        if self.max_per_day > 0:
            day = self._day_of(now)
            if day != self._day:
                self._day = day
                self._day_count = 0
            if self._day_count >= self.max_per_day:
                return False  # 当日额度已用完
        if self._rand.random() >= self.probability:
            self._grow()  # miss → 渴望度积累
            return False
        # 命中：概率重置（发送后"想念满足"）
        self.probability = self._base_probability
        if self.max_per_day > 0:
            self._day_count += 1
        return True

    def _grow(self) -> None:
        """渴望度积累：未命中时概率增长，封顶 max_probability。"""
        if self.probability <= 0:
            return  # 0 = 关闭（永不触发）
        self.probability = min(self.max_probability, self.probability + self.growth_factor)

    @staticmethod
    def _day_of(now: float) -> str:
        import datetime
        return datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
