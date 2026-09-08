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

# 问候型主动触发源清单（2026-09-01 用户裁决 v2）：tick_proactive 求值全表、
# 到期素材**全部收集**，一次织成同一条语义连续的消息（或多段连发）发出——
# 不损失信息、不各自单发导致上下文断裂。新增触发源=写一个求值函数并注册。
# （异步旁路源=睡醒公告/心跳/追问等不在此表：它们与 tick 消息撞车由
#  proactive.merge_window_minutes 合并窗口错峰，窗口内素材不销毁、下轮并入。）
RITUAL_SOURCES = (
    "greeting",        # 时段问候（早/午/晚，每日每时段去重）——longing 攒着时并入
    "context_probe",   # 当下情境推测（联想 B 类：TA 此刻在干嘛；日 ≤2）
    "sleep_hint",      # 26h 无作息报告轻提示（每日一次）
    "occasion",        # 节庆/纪念日（每日一次）
    "schedule_adapt",  # 角色作息向用户偏移的理由消息（每日一次）
    "meal",            # 三餐提醒（每餐当日一次）
    "thread",          # 牵挂自述（M1 自我发起源：TA 自己心里有事想说，非刺激驱动）
    "longing",         # M2 想念驱力：静默×依恋攒过阈值——「没事，就是想看看你在」
    "care_need",       # M2 求照顾驱力：她自己的疲劳/低落——示弱（低频，卡人设门槛）
)


class DesireLedger:
    """M2 驱力池（MIND_LOOP_SPEC 3.2，纯算术零 LLM）：心里攒出事才开口。

    - longing：用户静默时长 × 依恋系数逐 tick 累积；用户回话即泄洪清零。
      过阈值=一句「也没什么事」——问候从时刻表变成性格（Q1 裁决：时刻表
      降为原料：到点的问候若 longing 在场，把成分并进织文不各说各话）。
    - care_need：她的疲劳（energy 低）/低落残响累积；被用户关心（正向互动）
      即缓。低频（阈值高、日限 1）+卡人设门槛（卡 extensions.veranima.
      care_need=false 的卡不产——示弱不是人人设都吃这套）。
    - 持久化=relationship 快照 desires 键（{名: [level, date]}）——MIUI 杀
      后台存活；跨日 level 减半（一天没说话不该攒到天荒地老）。
    share/nag/express 的演进脚本归 M3 夜眠消化（spec：随 reflection v2）。
    """

    _LONG_RATE = 0.005   # 每静默分钟 × attachment：依恋 1.0 时 3.3h 过阈（1/0.005/60）
    _CARE_RATE = 0.02    # 每低精力/低落分钟累积
    _LONG_TH = 1.0
    _CARE_TH = 1.6
    # 09-08 真机实锤：0.055 的实际过阈是 23 分钟（0.055*60*0.8=2.64/h），
    # 于是「闲下来才发现你半天没声了」发在沉默 21 分钟后——常量与注释意图差 8.7 倍。
    _LONG_LINES = ("也没什么事，就是想看看你在不在。",
                   "闲下来才发现你有一阵没声了。忙你的，我就是念叨一句。")
    _CARE_LINES = ("今天有点扛不住了，跟你说一声，不用你安慰，就是想让你知道。",
                   "我这边状态一般，晚上可能早点缴。不是要你管，报备一下。")

    def __init__(self, agent: "Agent") -> None:  # noqa: F821
        self.agent = agent
        self._d: dict[str, list] = {}
        self._anchor_ts = 0.0   # 最后一条 user 消息时刻（泄洪锚）
        self._last_ts = 0.0     # 上次评估时刻（增量基准）

    def restore(self, snap) -> None:
        if isinstance(snap, dict):
            self._d = {str(k): [float(v[0] or 0.0), str(v[1] or "")]
                       for k, v in snap.items()
                       if isinstance(v, (list, tuple)) and len(v) >= 2}

    def to_dict(self) -> dict:
        return {k: [round(v[0], 3), v[1]] for k, v in self._d.items()}

    def _level(self, name: str) -> float:
        return self._d.get(name, [0.0, ""])[0]

    def _set(self, name: str, val: float) -> None:
        fired = self._d.get(name, [0.0, ""])[1]
        self._d[name] = [max(0.0, min(2.0, val)), fired]

    def _fired_today(self, name: str, today: str) -> bool:
        return self._d.get(name, [0.0, ""])[1] == today

    def _mark_fired(self, name: str, today: str) -> None:
        self._d[name] = [0.0, today]  # 泄洪：产料后归零，只留当日已发标记

    def tick(self, now) -> None:
        import datetime as _dt
        now = (now if isinstance(now, _dt.datetime)
               else _dt.datetime.fromtimestamp(now or _dt.datetime.now().timestamp()))
        now = self.agent._naive_local(now) if now.tzinfo else now
        today = now.date().isoformat()
        a = self.agent
        try:
            last_user = None
            for row in reversed(a._recent_msgs(limit=10)):
                if row.get("role") == "user":
                    last_user = _dt.datetime.fromisoformat(
                        str(row.get("created_at")).replace("Z", "+00:00"))
                    break
            if last_user is None:
                return
            last_user = a._naive_local(last_user)
            # 跨日半衰（隔夜不该攒到天荒地老）
            for name in list(self._d):
                if self._d[name][1] and self._d[name][1] != today:
                    self._d[name] = [self._level(name) * 0.5, ""]
            # longing：用户回话即泄洪（锚点=最后一条 user 的时刻，变新即重置）；
            # 静默期间按「距上次评估的分钟数 × 依恋」累积。
            lu_ts = last_user.timestamp()
            if lu_ts != self._anchor_ts:
                self._anchor_ts = lu_ts
                self._d["longing"] = [0.0, self._d.get("longing", [0, today])[1] if self._fired_today("longing", today) else ""]
            else:
                dt_min = max(0.0, (now.timestamp() - self._last_ts) / 60) if self._last_ts else 0.0
                if dt_min > 0 and not self._fired_today("longing", today):
                    self._set("longing", self._level("longing") + self._LONG_RATE * dt_min * a.state.attachment)
            # care_need：低精力/低落时段累积（断档 >4h 按 4h 封顶，进程蒸发补偿）
            if a.state.energy < 45 or a.state.mood == "低落":
                dt_min = min(240.0, (now.timestamp() - self._last_ts) / 60) if self._last_ts else 0.0
                if dt_min > 0 and not self._fired_today("care_need", today):
                    self._set("care_need", self._level("care_need") + self._CARE_RATE * dt_min)
            self._last_ts = now.timestamp()
        except Exception:
            logger.debug("desire tick failed", exc_info=True)

    def soothe(self, positive: bool) -> None:
        """被关心即缓（spec 表）：正向互动压 care_need。"""
        if positive:
            self._set("care_need", self._level("care_need") * 0.4)

    def material(self, now) -> dict | None:
        """过阈值→带原料进待织池（合并窗口/织文原样复用）。日限各 1。"""
        import datetime as _dt
        now = (now if isinstance(now, _dt.datetime)
               else _dt.datetime.fromtimestamp(now or _dt.datetime.now().timestamp()))
        today = (self.agent._naive_local(now) if now.tzinfo else now).date().isoformat()
        a = self.agent
        if a.state.user_asleep:
            return None
        if (self._level("longing") >= self._LONG_TH
                and not self._fired_today("longing", today)):
            self._mark_fired("longing", today)
            pick = self._LONG_LINES[sum(map(ord, today)) % len(self._LONG_LINES)]
            return {"source": "longing", "text": pick}
        cfg = (a.card.veranima or {})
        if cfg.get("care_need", True) is False:
            return None  # 卡人设门槛：这角色不吃示弱
        energy_ok = a.state.energy < 45 or a.state.mood == "低落"
        if (energy_ok and self._level("care_need") >= self._CARE_TH
                and not self._fired_today("care_need", today)):
            self._mark_fired("care_need", today)
            pick = self._CARE_LINES[sum(map(ord, today)) % len(self._CARE_LINES)]
            return {"source": "care_need", "text": pick}
        return None

MEAL_SLOTS = {
    "breakfast": (8, "到饭点了，先去吃点早饭。"),
    "lunch": (12, "到饭点了，先去吃午饭。"),
    "dinner": (17, "到饭点了，先去吃晚饭。"),
}


# 主动否决台账（§12-C 裁决，2026-09-07）：用户表态「别老提X类主动」后进
# 否决表（源名闭集=RITUAL_SOURCES），tick 收集端确定性剔除，跨周不复发。
# 识别走统一判断点（judges.veto_source），本模块的 VETO_PHRASES 词面只做
# 判断点缺席时的兜底——「语义判断每处一次 LLM、关键词=预筛兜底」铁律同款。
# 不并入 memory_review_inbox：那是记忆准入队列，容器错。

VETO_PHRASES = ("别再", "别老", "不要再", "不要总是", "别提醒", "不用提醒", "不用跟我")

# 否决句里的口语别名 → RITUAL_SOURCES 源名（闭集映射，LLM 与词表共用）
VETO_ALIASES = {
    "meal": ("饭", "吃饭", "饭点", "吃饭提醒", "提醒我吃饭"),
    "greeting": ("早安", "晚安", "问好", "打招呼", "问候"),
    "occasion": ("节日", "过节", "纪念日"),
    "context_probe": ("猜我在干嘛", "推测", "在干嘛"),
    "sleep_hint": ("作息", "催我睡", "睡觉报告"),
    "schedule_adapt": ("作息调整", "跟我调作息"),
    "thread": ("惦记", "念叨那件事"),
}


def _num(s: str) -> int:
    """阿拉伯或简单中文数字（一~十）→ int；解析不了=0。"""
    if s.isdigit():
        return int(s)
    cn = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return cn.get(s, 0)


def veto_from_keywords(text: str) -> tuple[str, int] | None:
    """(被否决源名, 保质期天数)；无命中=None。判断点可用时由 LLM 裁决，
    这里只是兜底。保质期=X天/N周/一个月，无限定词=0（永久）——「别老提醒
    我喝水」就是永久语义，「最近」当修饰词不封顶。
    """
    t = str(text or "")
    if not any(p in t for p in VETO_PHRASES):
        return None
    for src, aliases in VETO_ALIASES.items():
        if not any(a in t for a in aliases):
            continue
        days = 0
        m = re.search(r"([0-9一二两三四五六七八九十]+)\s*([天周]|个?月)", t)
        if m:
            n = _num(m.group(1))
            days = n * 7 if m.group(2) == "周" else n * 30 if m.group(2) != "天" else n
        return src, days
    return None


# 池排干顺序=易腐度（09-07 M2 第一砖「人不会把四件事缝成一段小作文」）：
# 过时作废的先出（问候/饭点/节庆），账本托底的缓事咽回池等下窗（牵挂/联想）。
# longing/care_need=半易腐（素材依赖「此刻还在静默/还在低落」，同 probe 档）；
# thread 最缓（mind_threads 账本托底，咽回去明晚还在）。
POOL_PERISH = ("greeting", "meal", "occasion", "sleep_hint",
               "schedule_adapt", "context_probe", "longing", "care_need",
               "thread")


def pool_take(pending: list[dict], cap: int) -> list[dict]:
    """从池中按易腐度取 ≤cap 条，其余留池（原地更新 pending）。"""
    order = {s: i for i, s in enumerate(POOL_PERISH)}
    pending.sort(key=lambda m: order.get(m.get("source"), 9))
    pool, rest = pending[:max(1, cap)], pending[max(1, cap):]
    pending[:] = rest
    return pool


def meal_word(hour: int) -> str:
    """整点 → 那顿饭该叫什么（三餐锚点随用户作息平移后，餐名跟钟点不跟槽位）。"""
    h = int(hour) % 24
    if 5 <= h < 11:
        return "早饭"
    if 11 <= h < 15:
        return "午饭"
    if 17 <= h < 22:
        return "晚饭"
    return "夜宵"
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

    def consume_slot(self, now=None) -> None:
        """把当前时段标记为已问候（2026-09-01：角色睡醒公告本身就是这一时段的
        招呼——别的源发过，时段问候不再重复说「早安」）。"""
        slot = self.slot_at(now)
        if slot is not None:
            import datetime
            now = now or datetime.datetime.now()
            self.greeted.add(f"{now.date()}:{slot}")

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
        餐名跟着钟点走（2026-09-02 真机实锤：用户 22:26 醒→+2h=0 点，凌晨
        00:01 发「到饭点了，先去吃点早饭」）：平移后整点若已不是那顿饭的
        时段，把内置文案里的餐名换成当时钟点对应的那顿（夜宵就是夜宵）。
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
        # 只挪锚点小时，文案不原地改写（旧版把「早饭」换成「夜宵」后写回
        # slots——次日作息回正、槽位重算时文案里再没有「早饭」可换，夜宵
        # 字样永久卡在午饭槽位）。餐名统一由 due() 在产出时按钟点现算。
        for meal, hour in anchors:
            self.slots[meal] = (int(hour) % 24, self.slots[meal][1])

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
                # 餐名跟钟点不跟槽位（09-02 真机实锤：凌晨 00:01 喊早饭）。
                # 按 now 现算——slots 里的模板文案永不改写（改写会跨日卡死）
                word = meal_word(now.hour)
                for old_word in ("早饭", "午饭", "晚饭"):
                    if old_word != word and old_word in text:
                        text = text.replace(old_word, word)
                        break  # 一行文案里只会有一个餐名
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
