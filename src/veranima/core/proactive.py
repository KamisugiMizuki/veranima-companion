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
    """时间段问候：早(6-10)/午(11-14)/晚(18-23)，每日去重。"""

    greeted: set[str] = field(default_factory=set)

    def due_greeting(self, now=None) -> str | None:
        """返回当前应发的问候类型（morning/noon/evening）或 None（已问候过/非窗口）。"""
        import datetime
        now = now or datetime.datetime.now()
        h = now.hour
        if 6 <= h < 10:
            slot = "morning"
        elif 11 <= h < 14:
            slot = "noon"
        elif 18 <= h < 23:
            slot = "evening"
        else:
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

    def scheduled_at(self, day, meal: str):
        import datetime
        hour, _text = self.slots[meal]
        digest = hashlib.sha256(f"{day.isoformat()}:{meal}".encode()).digest()
        offset = random.Random(int.from_bytes(digest[:8], "big")).randint(
            -self.jitter_minutes, self.jitter_minutes,
        )
        return datetime.datetime.combine(day, datetime.time(hour=hour)) + datetime.timedelta(minutes=offset)

    def due(self, *, now=None, sent_ids: set[str] | None = None):
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
