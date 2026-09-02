"""对话引擎：消息 → 状态 → 记忆 → prompt → LLM → 回复 → 存储。

MVP1 范围：人格（角色卡）+ 状态机 + 五层记忆（存/取/遗忘）+ 本地 LLM 对话。
MVP2 范围：隐式反馈学习 + 风格参数（bandits+EMA）+ 语言镜像 + 承诺机制 + curator 整理。
"""

from __future__ import annotations

import datetime
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field, replace
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Literal

from ..llm.client import LLMClient, LLMTimeoutError, LLMUnavailableError
from .prompts import build_system_prompt, is_clarification
from .virtual_schedule import ScheduleContext, ScheduleOutline, ScheduleRuntime
from .holiday_calendar import HolidayCalendar
from .reply import is_failure_fallback_reply, is_internal_reply
from .segments import extract_segments
from .ambient import ChannelActivityTracker, ProactiveCandidate, ProactiveGate, SceneLock
from .interrupt import InterruptDecider, TopicFrequency
from ..memory.store import MemoryStore
from ..tools.search import (
    EvidencePack,
    SearchTrigger,
    SemanticLocator,
    SearXNGClient,
    analyze_search_intent,
    normalize_time_in_query,
)
from .character import CharacterCard
from .learning import LanguageMirror, StyleLearner, extract_feedback
from .proactive import GreetingScheduler, OccasionChecker, RITUAL_SOURCES
from .promises import PromiseBook
from .review import MonthlyReview
from .state import AgentState
from .tension import RelationalTension, event_meta_from_memory
from .tension_events import (
    classify_low_investment_streak,
    classify_user_tension_event,
    extract_direct_question,
)

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    reply: str
    recalled: list[str] = field(default_factory=list)
    proactive: bool = False
    proactive_msg: str = ""
    energy: float = 0.0
    mood: str = ""
    portrait: str = ""   # R2 表情标签驱动：情绪表情标签（如"开心脸红"），空=回退 idle（R2_SPEC 1）
    tone: str = ""       # R2：语气标签（R2_SPEC 1，TTS 预留）
    ja_text: str = ""    # R2 双语：日语台词（送 TTS）；空=非双语角色（R2_SPEC 1）
    reply_obj: object = None  # R2 统一 Reply（adapter 逐步消费；TurnResult 字段保留兼容）
    style_hint: str = ""  # P-9：PAD 渲染提示（short/normal/long，adapter 可选消费）


@dataclass(frozen=True)
class TurnContext:
    """DESIGN 4.1 统一协议：一轮对话的输入上下文。"""

    channel: Literal["im", "tts"]
    user_text: str
    images: tuple[str, ...] = ()
    scene: str = "normal"
    current_time: str = ""
    state: dict = field(default_factory=dict)
    recalled_memories: tuple[dict, ...] = ()
    active_focus: dict | None = None


def _interrupt_prompt(level: int) -> str:
    """R0 打断指令（R0_SPEC 5）：L1 轻推 / L2 转移+新出口，收尾协议。

    L1 = 委婉提醒用户重复说过（40~60% 概率触发）
    L2 = 主动转移话题 + 必须附带「新出口」（方案/新话题/问题）
    """
    if level == 1:
        return (
            "【打断指令·轻推】用户刚刚又提了之前说过的话题。"
            "委婉地提醒他这件事你说过了（自然点，别像查重），然后给一个更深入的视角或建议。"
        )
    if level == 2:
        return (
            "【打断指令·转移】用户反复纠缠同一个话题（已经说过好几次了）。"
            "自然地打断并转移话题——先点一句'这事咱们聊过'，然后换一个新话题或抛一个新问题，"
            "让对话换个频道。不要生硬，语气保持你自己的风格。"
        )
    # L3（DESIGN 4.5：L3 后不静默——转「工作模式」极简回应，不是停止回复）
    return (
        "【打断指令·直球】用户已经反复聊这个话题很多次了（≥5 次），情绪没有缓解。"
        "你可以直说'这个问题你聊了至少五遍了'，然后给两个选项：出狠招解决 / 只当听众。"
        "之后本轮回复转为工作模式：极简、就事论事、不投入情绪（比如'嗯。''知道了。'级别的短句）。"
        "但必须继续回复——沉默会被当成你挂了。"
    )


def _maybe_withdraw(reply: str, state, rand: float) -> str:
    """历史功能：表达瑕疵撤回（限频 15~25%）；新设计非目标「随机错误模拟器」（DESIGN.md §1），待 R2 清理。

    条件：低确信（<0.6）或低精力（<40）且回复含具体细节（长度 > 20）且概率命中。
    """
    low_confidence = getattr(state, "confidence", 1.0) < 0.6
    low_energy = getattr(state, "energy", 100) < 40
    if not (low_confidence or low_energy):
        return reply
    if len(reply) < 20:
        return reply  # 无具体细节不触发
    if rand > 0.2:
        return reply  # 限频 15~25%（20% 概率）
    return reply + "（撤回一下，我刚才打错了，应该是想说……算了，意思你懂就行）"


class Agent:
    def __init__(
        self,
        card: CharacterCard,
        memory: MemoryStore,
        llm: LLMClient,
        state: AgentState | None = None,
        config: dict | None = None,
    ):
        self.card = card
        self.memory = memory
        self.llm = llm
        self.state = state or AgentState()
        self.config = config or {}
        # 落库通道标签（2026-08-31 用户裁决）：core 主动/回复消息写 messages.channel 的
        # 值。PC（QQ/桌宠）=默认 "qq" 不变；安卓 device-config 置 "im"——历史导出/
        # 排查不再被误导成 QQ 链路。仅显示/查询标签，gate 分桶走各自 config。
        self.message_channel = str(self.config.get("channel_tag") or "qq")
        # 多角色会话隔离键（MOMENTS_MULTIROLE_SPEC P1）：角色目录名；
        # PC/QQ 端单角色时代=空串（读写全部不过滤，行为逐字不变）
        _src = str(getattr(self.card, "source_path", "") or "").replace("\\", "/")
        self.role_key = _src.split("/")[-2] if _src.endswith("character.json") and _src.count("/") >= 2 else ""
        self._history: list[dict] = []
        self._last_reply_ts: float | None = None  # 上一条回复时间（延迟信号用）
        self._last_search_request: dict[str, str] | None = None
        self.schedule_outline = self._load_schedule_outline()
        schedule_cfg = self.config.get("virtual_schedule", {}) or {}
        calendar_cfg = schedule_cfg.get("calendar", {}) or {}
        self.holiday_calendar = HolidayCalendar(
            base_url=str(calendar_cfg.get("base_url") or "https://date.nager.at/api/v3/PublicHolidays"),
            country_code=str(calendar_cfg.get("country_code") or "CN"),
            timeout=float(calendar_cfg.get("timeout_seconds", 8)),
            cache_ttl=float(calendar_cfg.get("cache_ttl_seconds", 86400)),
        ) if calendar_cfg.get("enabled", False) else None
        if self.schedule_outline is not None:
            timezone_override = str(schedule_cfg.get("timezone") or "system")
            if timezone_override != "system":
                self.schedule_outline = replace(self.schedule_outline, timezone=timezone_override)
            sleep_overrides = dict(self.schedule_outline.sleep)
            for key in ("grace_period_minutes", "max_extension_minutes"):
                if key in schedule_cfg:
                    sleep_overrides[key] = int(schedule_cfg[key])
            self.schedule_outline = replace(self.schedule_outline, sleep=sleep_overrides)
        self.schedule_runtime = (
            ScheduleRuntime(self.schedule_outline, planner=self._plan_schedule_with_llm)
            if self.schedule_outline is not None and self.schedule_outline.enabled
            and bool(schedule_cfg.get("enabled", True)) else None
        )
        if self.schedule_runtime is not None:
            self.schedule_runtime.space_enabled = bool(schedule_cfg.get("space_enabled", True))
            self.schedule_runtime.space_preference = str(schedule_cfg.get("space_preference") or "stable") if schedule_cfg.get("space_preference") in {None, "stable", "balanced"} else "stable"
            self.schedule_runtime.space_detail = str(schedule_cfg.get("space_detail") or "brief") if schedule_cfg.get("space_detail") in {None, "hidden", "brief"} else "brief"
            self.schedule_runtime.calendar = self.holiday_calendar
            profile_override = str(schedule_cfg.get("day_profile") or "auto")
            if profile_override not in {"auto", "default"} and profile_override in self.schedule_outline.day_profiles:
                self.schedule_runtime.profile_override = profile_override

        # R0 打断决策（R0_SPEC 5）：共享话题频率表 + 分级决策器
        self.topic_freq = TopicFrequency()
        self.interrupt_decider = InterruptDecider()

        # 重启续接（2026-08-04）：状态快照 + 最近对话从 SQLite 恢复，
        # 进程重启后像没重启过（依恋度/精力/情绪/对话上下文都在）
        try:
            snapshot = self.memory.load_state()
            if snapshot:
                self.state = AgentState.from_snapshot(
                    snapshot, initial_attachment=self.state.initial_attachment
                )
                logger.info("restored agent state: %s", self.state.summary())
            hist_limit = self.config.get("chat", {}).get("history_max_messages", 20)
            recent = self.memory.recent_messages(limit=hist_limit, role_id=(self.role_key or None))
            self._history = [
                self._history_entry(m["role"], m["content"], m.get("created_at"))
                for m in recent
                if m["role"] in ("user", "assistant")
                and not (m["role"] == "assistant" and is_internal_reply(m["content"]))
            ]
            if self._history:
                logger.info("restored %d history messages for continuity", len(self._history))
        except Exception as e:
            logger.warning("state/history restore failed, starting fresh: %s", e)

        # P-3 关系模型（PERSONA_LOOP_SPEC）：从快照恢复，否则用 initial_affection 先验。
        # 跨角色隔离（2026-09-01）：agent_state 是共享单行——roster 模式按卡名各记
        # 各的账（凛↔许眠来回切不丢进度）；旧库无 roster → owner 标守卫（切卡重置
        # 而非污染，下一次 persist 自动升级为 roster 结构）。
        from .persona import RelationshipModel
        _rel0 = self.state.relationship or {}
        self._rel_roster = _rel0.get("roster") if isinstance(_rel0.get("roster"), dict) else None
        _roster = self._rel_roster
        if _roster:
            _mine = _roster.get(self.card.name)
            if isinstance(_mine, dict) and _mine:
                self.state.relationship = dict(_mine)
                try:
                    self.state.attachment = max(0.0, min(0.95, float(_mine.get("attachment", self.state.attachment))))
                except (TypeError, ValueError):
                    pass
            else:
                logger.info("roster has no entry for %r, booting fresh", self.card.name)
                self.state.relationship = {}
                self.state.attachment = self.state.initial_attachment
        else:
            _owner = str(_rel0.get("owner") or "")
            if _owner and _owner != self.card.name:
                logger.info("relationship snapshot owned by %r, booting %r fresh", _owner, self.card.name)
                # 下方「当日去重键从快照恢复」读的就是 state.relationship——清空后
                # greeter 自然拿到空集，问候去重键也随之归零（新角色第一天该说早安）
                self.state.relationship = {}
                self.state.attachment = self.state.initial_attachment
            elif not _owner and self.state.relationship:
                # 旧库无 owner 标：归属「当前在跑的卡」这个兼容假设在此钉死；
                # 落库不用手动 persist——_persist_state 每次写入都会带 owner 标
                self.state.relationship["owner"] = self.card.name
        if self.state.relationship:
            self.relationship = RelationshipModel.from_dict(self.state.relationship)
        else:
            ia = float(((self.card.veranima or {}).get("initial_affection") or 0.5))
            self.relationship = RelationshipModel.from_initial(
                initial_affection=ia,
                preset=(self.card.veranima or {}).get("relationship_preset"))
        if self.schedule_runtime is not None and self.state.relationship:
            saved_schedule = self.state.relationship.get("virtual_schedule_runtime")
            if isinstance(saved_schedule, dict):
                self.schedule_runtime = ScheduleRuntime.from_snapshot(
                    self.schedule_outline, saved_schedule, planner=self._plan_schedule_with_llm,
                )
                self.schedule_runtime.space_enabled = bool(schedule_cfg.get("space_enabled", True))
                self.schedule_runtime.space_preference = str(schedule_cfg.get("space_preference") or "stable") if schedule_cfg.get("space_preference") in {None, "stable", "balanced"} else "stable"
                self.schedule_runtime.space_detail = str(schedule_cfg.get("space_detail") or "brief") if schedule_cfg.get("space_detail") in {None, "hidden", "brief"} else "brief"
                self.schedule_runtime.calendar = self.holiday_calendar
                profile_override = str(schedule_cfg.get("day_profile") or "auto")
                if profile_override not in {"auto", "default"} and profile_override in self.schedule_outline.day_profiles:
                    self.schedule_runtime.profile_override = profile_override
                self.schedule_runtime.reconcile_after_downtime(datetime.datetime.now(datetime.timezone.utc))

        # P-5 反思计数器（每 20 个有效人格候选触发一次整合）
        self._reflection_counters = {"persona_candidates": 0, "high_emotion_events": 0, "user_corrections": 0}

        # P-7 冲突跟踪（随 state.relationship 持久化；重启恢复）
        from .persona import ConflictTracker
        self._conflicts = ConflictTracker.from_dict(self.state.relationship.get("conflicts") if isinstance(self.state.relationship, dict) else None)
        tension_snapshot = self.state.relationship.get("tension") if isinstance(self.state.relationship, dict) else None
        tension_entries = self.memory.list_layer("episodic", limit=200)
        self.tension = RelationalTension(
            config=self.config.get("relationship_tension", {}) or {},
        )
        self.tension.restore(tension_snapshot, event_meta_from_memory(tension_entries))

        # P-6/P-9 状态：框架引用冷却（8 轮）、表层印记、轮次计数
        from .persona import ImprintTracker, ReuseCooldown
        self._reuse_cd = ReuseCooldown()
        saved_imprints = self.state.relationship.get("imprints") if isinstance(self.state.relationship, dict) else {}
        self._imprints = ImprintTracker.from_dict(saved_imprints or {})
        self._turn_n = 0

        # MVP2 学习组件（持久化到 data/，随对话更新）
        root = self.config.get("root", ".")
        self.style = StyleLearner(persist_path=str(Path(root) / "data" / "style.json"))
        self.mirror = LanguageMirror(persist_path=str(Path(root) / "data" / "mirror.json"))
        self.style.load()
        self.style.enforce_retention()
        self.mirror.load()
        self.promises = PromiseBook(memory)

        # 主动触发（定时问候 + 节庆纪念 + 饭点兜底；CLI/QQ/安卓共用 tick_proactive）
        # 待织池：合并窗口关着时到期素材攒在这里，窗口一开全部织成一条
        # （ponytail: 仅内存态，重启丢当窗素材=可接受，主动消息非交易数据）
        self._ritual_pending: list[dict] = []
        self.greeter = GreetingScheduler()
        self.occasion = OccasionChecker()
        # 当日去重键从状态快照恢复（在 _persist_state 里随关系快照落库）
        _rel_snap = self.state.relationship or {}
        # 合并窗口起点：最近一次问候族主动消息发出时刻（UTC ISO）。所有
        # greeting-family 触发源（时段问候/睡醒公告/作息适应/提示/饭点/心跳…）
        # 共享这把闸：窗口内不再放行第二条（2026-09-01 用户反馈 07:09/07:11/08:04）。
        self._last_proactive_sent_at = str(_rel_snap.get("last_proactive_sent_at") or "")
        self.greeter.restore_state(_rel_snap.get("greeted") or [])
        _today_key = datetime.date.today().isoformat()
        self.occasion.triggered.update(
            str(k) for k in (_rel_snap.get("occasions") or [])
            if str(k).startswith(_today_key + ":"))
        from .proactive import MealReminderScheduler
        self.meals = MealReminderScheduler(
            (self.config.get("proactive") or {}).get("meal_reminders", {}))
        # 好友动态引擎（MOMENTS_MULTIROLE_SPEC P2）：素材四源→频率闸→织文→入库。
        # 懒 import 防环（moments 引 agent 类型仅注释层面）；无角色键时 tick 自动禁用。
        from .moments import MomentsEngine
        self.moments = MomentsEngine(self)

        # R4 时空沉浸：场景锁 + 通道互斥 + 主动仲裁（最小版，R4_SPEC 1）
        self.scene_lock = SceneLock()
        self.activity = ChannelActivityTracker()
        # R4 统一主动入口（R4_SPEC 1/2）：ProactiveCandidate → ProactiveGate 9 闸门
        self.gate = ProactiveGate(self.config.get("proactive", {}))
        self.gate.character_sleeping_check = lambda: bool(
            self.schedule_runtime is not None and self.schedule_runtime.sleeping
        )
        try:
            self.gate.restore_feedback(self.memory.recent_proactive_feedback(limit=200))
        except Exception as e:
            logger.debug("proactive gate restore failed: %s", e)

        # 联网搜索（DESIGN.md 8.5 方案 A：工具调用；默认关闭，config 开启）
        search_cfg = self.config.get("search", {})
        self.search_config = search_cfg
        self.search_enabled = bool(search_cfg.get("enabled", False))
        if str(search_cfg.get("provider", "searxng")).strip().lower() == "bocha":
            from ..tools.bocha import BochaClient
            self.search = BochaClient(
                api_key=str(search_cfg.get("api_key", "")),
                max_results=int(search_cfg.get("max_results", 5)),
                timeout=float(search_cfg.get("timeout_seconds", 8)),
                cache_ttl=float(search_cfg.get("cache_ttl_seconds", 900)),
                base_url=str(search_cfg.get("base_url") or "").strip() or None,
            )
        else:
            self.search = SearXNGClient(
                base_url=search_cfg.get("base_url", "http://127.0.0.1:8080"),
                max_results=int(search_cfg.get("max_results", 5)),
                timeout=float(search_cfg.get("timeout_seconds", 8)),
                max_response_bytes=int(search_cfg.get("max_response_bytes", 1_048_576)),
                cache_ttl=float(search_cfg.get("cache_ttl_seconds", 900)),
                fetch_pages=bool(search_cfg.get("fetch_pages", False)),
                max_page_results=int(search_cfg.get("max_page_results", 2)),
                page_char_limit=int(search_cfg.get("page_char_limit", 1200)),
                max_page_bytes=int(search_cfg.get("max_page_bytes", 524_288)),
            )
        self.search_trigger = SearchTrigger()
        self.semantic_locator = SemanticLocator(
            max_queries=int(search_cfg.get("semantic_locator_max_queries", 3)),
            max_verify_queries=int(search_cfg.get("semantic_locator_max_verify_queries", 1)),
        )

    # ---------- MVP2 状态 ----------

    def _load_schedule_outline(self):
        card_path = self.config.get("character_card", "")
        if not card_path:
            card_path = getattr(self.card, "source_path", "") or ""
        if not card_path:
            return None
        path = Path(card_path)
        if not path.is_absolute():
            path = Path(self.config.get("root", ".")) / path
        try:
            return ScheduleOutline.from_role_dir(path.parent)
        except Exception as exc:
            logger.warning("virtual schedule disabled: %s", exc)
            return None

    def _schedule_role_id(self) -> str:
        return self.schedule_outline.role_id if self.schedule_outline else ""

    def _plan_schedule_with_llm(self, when):
        """Ask the configured model for bounded adjustments; parser stays deterministic."""
        if not getattr(self.llm, "is_model_loaded", lambda: False)():
            return None
        import json
        template = {
            "day_profiles": {
                key: list((value or {}).get("allowed_block_ids") or [])
                for key, value in self.schedule_outline.day_profiles.items()
            } if self.schedule_outline else {},
            "blocks": [
                {"rule_id": block.id, "activity_pool": list(block.activity_pool),
                 "duration_min": block.duration_min, "duration_max": block.duration_max,
                 "required": block.required}
                for block in (self.schedule_outline.blocks if self.schedule_outline else ())
            ],
        }
        prompt = (
            "只根据角色日程模板，为下一周期选择已有活动的有限调整。"
            f"可用模板：{json.dumps(template, ensure_ascii=False)}。"
            "输出 JSON：{\"day_profile\":\"模板已有 profile\",\"items\":["
            "{\"rule_id\":\"模板已有 id\",\"activity_key\":\"模板已有 activity\","
            "\"operation\":\"none|shift|resize|substitute|skip_optional|recovery_mode\","
            "\"shift_minutes\":0,\"duration_minutes\":0}]}。"
            "禁止新增活动、现实地点、人物和用户事实，只输出 JSON。"
        )
        raw = self.llm.chat(
            [
                {"role": "system", "content": self.card.to_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max(2048, int((self.config.get("llm") or {}).get("short_task_max_tokens", 1024))),
        )
        try:
            value = json.loads(str(raw).strip().strip("`").removeprefix("json").strip())
            return value if isinstance(value, dict) else None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _sleep_care_note(self) -> str:
        """睡前牵挂（2026-08-31「不完结的牵挂」增量）：用户三餐锚点若落在角色
        睡眠窗口内，该餐提醒届时不会被发出（角色睡着，gate 拦截）→ 睡前公告文案
        替它带一句关怀。无错位返回空串（默认作息不触发）。只报一餐，不打包。"""
        runtime = getattr(self, "schedule_runtime", None)
        circ = runtime.outline.circadian if runtime is not None and runtime.outline else None
        if not circ:
            return ""

        def _hour(value: str) -> float:
            h, m = str(value).split(":", 1)
            return int(h) + int(m) / 60

        try:
            sleep_start = _hour(circ.sleep_start)
            wake_start = _hour(circ.wake_start)
        except (AttributeError, ValueError):
            return ""
        cn = {"breakfast": "早饭", "lunch": "午饭", "dinner": "晚饭"}
        # 关系阶段解锁（2026-08-31 自检缺口④）：牵挂是亲密行为，初识阶段发=越界
        if self.state.attachment < 0.5:
            return ""
        # 三餐锚点已被 adjust_to_user_cycle 按用户作息平移；落在角色必睡区间
        # [睡窗起点, 醒窗起点) 的餐届时无提醒 → 给睡前公告一条带指向的牵挂素材
        cares = []
        for meal, (hour, _text) in self.meals.slots.items():
            in_window = (hour >= sleep_start or hour < wake_start) if sleep_start > wake_start \
                else sleep_start <= hour < wake_start
            if in_window:
                cares.append(f"用户的{cn.get(meal, '饭')}点落在你睡着的时段里，你睡了之后没办法提醒ta按时吃")
                break  # 只报一餐，不打包
        # 联想 A 类补全（2026-09-01 设计）：未完成事项=开放承诺；睡前顺带记挂一件
        if len(cares) < 2:
            try:
                open_p = self.promises.open_promises(limit=1)
                if open_p:
                    cares.append(f"你想起用户还挂着件事没办完：「{open_p[0].content[:40]}」，"
                                 "你睡着的时候ta要是想起来找你，你没法当场回应")
            except Exception:
                pass
        return "；".join(cares)

    def schedule_notice_text(self, notice: str, now=None) -> str:
        tasks = {
            "sleep_preparing": "自然告诉用户你有点困，准备睡了，接下来起床前不会回复。只说一句，不要提系统或日程。",
            "woke": "自然告诉用户你刚恢复清醒。如果睡眠期间有消息，只表达刚看到，不编造消息内容。只说一句。",
        }
        task = tasks.get(str(notice))
        if not task or not getattr(self.llm, "is_model_loaded", lambda: False)():
            return ""
        if notice == "sleep_preparing":
            # 牵挂注入：有作息错位时，睡前这句自然带一句"我睡了之后你…"的前瞻
            care = self._sleep_care_note()
            if care:
                task += f"睡前顺带想到：{care}。把这个意思自然地融进同一句话里，像随口一想，不要提醒口吻。"
        text = self._short_task(task)
        # 睡醒公告=角色自己说的"早安"（2026-09-01 用户反馈 07:09 公告+07:11 时段
        # 问候双早安）：它吃掉当前时段问候位，本时段 ritual 问候不再重复招呼
        if text and notice == "woke":
            self.greeter.consume_slot(now)
            self._persist_state()
        return text

    def schedule_notice_candidate(self, notice: str, channel: str):
        if self.schedule_runtime is None or notice not in {"sleep_preparing", "woke"}:
            return None
        return ProactiveCandidate(
            source="virtual_schedule",
            reason=f"schedule state changed: {notice}",
            relevance=1.0,
            urgency=0.7 if notice == "sleep_preparing" else 0.4,
            intent="share",
            context={
                "event_id": f"{self.schedule_runtime.state.sleep_cycle_id}:{notice}",
                "schedule_notice": notice,
            },
            channel=channel,
        )

    def schedule_self_share_candidate(self, channel: str = "qq"):
        schedule_cfg = self.config.get("virtual_schedule", {}) or {}
        if self.schedule_runtime is None or schedule_cfg.get("self_share", "low") == "off":
            return None
        rows = self.memory.virtual_life_events(self.schedule_runtime.outline.role_id, limit=1)
        if not rows:
            return None
        row = rows[0]
        return ProactiveCandidate(
            source="virtual_schedule", reason="角色虚拟生活日终摘要",
            relevance=0.75, urgency=0.2, intent="share",
            context={"event_id": str(row["id"]), "dedupe_key": f"virtual-life:{row['id']}", "summary": row["summary"]}, channel=channel,
        )

    def schedule_curiosity_candidate(self, channel: str = "qq", user_scope: str = "qq:default"):
        schedule_cfg = self.config.get("virtual_schedule", {}) or {}
        if self.schedule_runtime is None or schedule_cfg.get("curiosity", "low") == "off":
            return None
        rows = self.memory.open_user_info_gaps(self.schedule_runtime.outline.role_id, user_scope, limit=1)
        if not rows:
            return None
        row = rows[0]
        return ProactiveCandidate(
            source="user_curiosity", reason=row["reason"], relevance=0.7, urgency=0.2,
            intent="check_in",
            context={"source_message_id": row["source_message_id"], "topic_key": row["topic_key"], "gap_id": row["id"], "dedupe_key": f"user-gap:{row['id']}", "owner_scope": user_scope},
            channel=channel,
        )

    def _capture_user_info_gap(self, user_text: str, message_id: int, channel: str,
                               user_scope: str = "qq:default") -> None:
        if self.schedule_runtime is None or channel != "im":
            return
        markers = ("我喜欢", "我不喜欢", "我最喜欢", "以后想", "我最近想")
        marker = next((value for value in markers if value in user_text), None)
        if not marker:
            return
        topic = user_text.split(marker, 1)[1].strip(" ，。！？")[:80]
        if topic:
            self.memory.upsert_user_info_gap(
                role_id=self.schedule_runtime.outline.role_id, user_scope=user_scope,
                topic_key=topic, reason=f"用户提到“{topic}”，但尚未说明更具体的偏好或原因",
                source_message_id=message_id,
            )

    def _schedule_context(self, channel: str, now=None) -> ScheduleContext | None:
        schedule_runtime = getattr(self, "schedule_runtime", None)
        if schedule_runtime is not None:
            return schedule_runtime.current_context(now or datetime.datetime.now(datetime.timezone.utc))
        if self.schedule_outline is None or not self.schedule_outline.enabled:
            return None
        current = now or datetime.datetime.now(datetime.timezone.utc)
        plan = self.schedule_outline.build_day_plan(current)
        return plan.context_at(current) if plan else None

    async def advance_schedule_async(self, when=None) -> None:
        runtime = self.schedule_runtime
        if runtime is None:
            return
        current = when or datetime.datetime.now(datetime.timezone.utc)
        before_scene = runtime.current_scene(current)
        calendar = getattr(runtime, "calendar", None)
        if calendar is not None:
            local_year = current.astimezone(ZoneInfo(runtime.outline.timezone)).year
            await asyncio.to_thread(calendar.prefetch, local_year)
            await asyncio.to_thread(calendar.prefetch, local_year + 1)
        snapshot = getattr(runtime, "to_snapshot", None)
        before = snapshot() if callable(snapshot) else None
        runtime.advance(current)
        after_scene = runtime.current_scene(current)
        if after_scene != before_scene or runtime.pending_scene_event:
            event = runtime.scene_event(current)
            pending_kind = runtime.pending_scene_event
            if pending_kind:
                event["event_kind"] = pending_kind
            key = json.dumps(
                {"event_kind": event["event_kind"], "scene": event["scene"]},
                ensure_ascii=False, sort_keys=True,
            )
            if key != runtime.last_scene_event_key:
                self.memory.store_virtual_life_event(
                    role_id=runtime.outline.role_id,
                    event_kind=event["event_kind"],
                    plan_id=str(after_scene.get("plan_id") or ""),
                    item_id=str(after_scene.get("item_id") or "") or None,
                    summary=(f"当前虚拟地点：{after_scene.get('place_label')}" if after_scene.get("place_label") else "空间状态发生变化"),
                    source={**after_scene, "at": event["at"]},
                )
                runtime.last_scene_event_key = key
            if pending_kind:
                runtime.pending_scene_event = ""
        if callable(snapshot) and snapshot() != before:
            self._persist_state()

    @staticmethod
    def _format_schedule_context(context: ScheduleContext | None) -> str:
        if context is None:
            return ""
        budget = ", ".join(f"{key}={value}" for key, value in context.reply_budget.items())
        ambient_labels = {
            "screen_cool": "屏幕冷光", "quiet_keyboard": "安静的键盘声",
            "soft_daylight": "柔和日光", "outside_quiet": "窗外较安静",
            "quiet_room": "安静的室内", "open_sky": "开阔天空",
            "wind": "有风", "soft_indoor": "柔和室内光线",
            "evening_light": "傍晚光线", "distant_town": "远处的小镇声",
            "low_public_noise": "低声的公共环境",
        }
        ambient = "、".join(ambient_labels.get(str(value), str(value)) for value in context.ambient_context.values())
        place = f"当前虚拟地点={context.place_label}，" if context.place_label else ""
        environment = f"活动环境={ambient}。" if ambient else ""
        scene = f"场景状态={context.scene_state}。"
        return (
            "【当前虚拟活动交互资源】这是角色虚拟日程的内部模拟状态，不是现实行动证据。"
            f"当前阶段={context.phase}，活动类别={context.activity_category}，活动={context.activity_key or '未指定'}，{place}"
            f"交互画像={context.interaction_profile}，可用度={context.availability:.2f}。"
            f"{scene}{environment}"
            f"回复约束：{budget or '按正常通道自然交流'}。"
            "普通回复无需播报当前活动；认真问题仍须回答核心内容。"
        )

    def current_space_answer(self, when=None) -> str:
        runtime = self.schedule_runtime
        if runtime is None:
            return "我这边没有可用的空间状态，只能说个大概。"
        scene = runtime.current_scene(when or datetime.datetime.now(datetime.timezone.utc))
        if scene.get("scene_state") in {"unknown_after_downtime", "reconciling"}:
            return "空间状态还没完全对上，我不硬编具体位置，先按虚拟日程继续走。"
        if scene.get("scene_state") == "in_transition":
            if getattr(runtime, "space_detail", "brief") == "hidden":
                return "我这会儿在处理自己的安排，等状态稳定一点再说。"
            return f"我正从{scene.get('place_label') or '刚才的位置'}往别处走，到了再说。"
        if scene.get("place_label"):
            return f"按现在的虚拟日程，我在{scene['place_label']}。"
        return "空间状态没对上，我不硬编，暂时只能说在自己的虚拟生活范围里。"

    @staticmethod
    def _format_history_content(content: str, created_at: str | None = None) -> str:
        """为历史消息加本地时间前缀；旧记录没有时间时保持原文。"""
        if not created_at:
            return str(content)
        try:
            stamp = datetime.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                stamp = stamp.astimezone().replace(tzinfo=None)
            return f"[{stamp.strftime('%Y-%m-%d %H:%M:%S')}] {content}"
        except (TypeError, ValueError):
            return str(content)

    @classmethod
    def _history_entry(cls, role: str, content: str, created_at: str | None = None) -> dict:
        return {"role": role, "content": str(content), "created_at": created_at}

    def _message_time_for_id(self, message_id: int) -> str | None:
        return self.memory.message_created_at(message_id)

    @staticmethod
    def _local_message_time() -> str:
        return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

    @classmethod
    def _prompt_history(cls, entry: dict) -> dict:
        return {
            "role": entry["role"],
            "content": cls._format_history_content(entry.get("content", ""), entry.get("created_at")),
        }

    @staticmethod
    def _time_context_instruction() -> str:
        return (
            "【消息时间规则】历史消息和当前消息正文前的方括号时间是发送时间，格式为 YYYY-MM-DD HH:MM:SS。"
            "判断刚才、今天、昨天、是否跨夜或间隔多久时，优先依据这些时间；"
            "凌晨/早上/中午/晚上/深夜这类时段词同样必须按时间戳选，一次回复里不得自相矛盾；"
            "不要仅凭晚安、睡觉或早安推断已经跨日，时间没有跨日就按连续对话处理；"
            "这些方括号时间是内部上下文标记，不要复制到回复正文。"
        )

    @classmethod
    def _message_context_line(cls, message: dict) -> str:
        role = {"user": "用户", "assistant": "我"}.get(message.get("role"), message.get("role", "消息"))
        return f"{role}: {cls._format_history_content(message.get('content', ''), message.get('created_at'))}"

    def _adjacent_proactive_context(self, user_text: str, channel: str) -> str:
        """仅为紧邻主动消息的澄清追问提供上一条主动原文。"""
        if channel != "im" or not is_clarification(user_text):
            return ""
        rows = self._recent_msgs(limit=8, channel=self.message_channel)
        if len(rows) < 2 or rows[-1].get("role") != "user":
            return ""
        previous = rows[-2]
        if previous.get("role") != "assistant":
            return ""
        try:
            previous_at = datetime.datetime.fromisoformat(
                str(previous.get("created_at")).replace("Z", "+00:00")
            )
            if previous_at.tzinfo is None:
                previous_at = previous_at.replace(tzinfo=datetime.timezone.utc)
        except (TypeError, ValueError):
            return ""
        for feedback in self.memory.recent_proactive_feedback(channel=self.message_channel, limit=20):
            try:
                sent_at = datetime.datetime.fromisoformat(
                    str(feedback.get("sent_at")).replace("Z", "+00:00")
                )
                if sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=datetime.timezone.utc)
            except (TypeError, ValueError):
                continue
            if abs((previous_at - sent_at).total_seconds()) > 180:
                continue
            return (
                "【主动消息追问衔接】用户正在追问你上一条主动消息。"
                f"上一条主动消息原文：{previous['content'][:300]}。"
                "只解释这条消息实际指向的内容；不要提主动任务来源、内部状态或时间。"
                "不要重新猜测多个事件，不要列分析输入/候选，不要输出 JSON 或解释草稿。"
            )
        return ""

    def _append_history_message(self, role: str, content: str, created_at: str | None = None) -> None:
        self._history.append(self._history_entry(role, content, created_at or self._local_message_time()))

    def learning_summary(self) -> dict:
        """学习状态摘要（/style 命令与 /status 用）。"""
        return {
            "params": self.style.params.snapshot(),
            "steps": self.style._steps,
            "mirror_top": self.mirror.stats()["top"],
            "profile": self.style.profile.snapshot(),
            "open_promises": len(self.promises.open_promises()),
        }

    def list_memories(self, layer: str | None = None, limit: int = 20) -> list[dict]:
        """M-7：分层列出记忆（current 版本，供用户查看）。"""
        layers = (layer,) if layer else ("core_profile", "procedural", "semantic", "episodic", "session")
        out: list[dict] = []
        for ly in layers:
            for e in self.memory.list_layer(ly, limit=limit):
                out.append({
                    "id": e.id,
                    "layer": ly,
                    "content": e.content,
                    "confidence": e.confidence,
                    "status": e.status,
                    "version": e.version,
                })
        return out

    def export_memories(self, fmt: str = "jsonl") -> str:
        """M-7：导出全部记忆（jsonl/markdown）。"""
        return self.memory.export(fmt=fmt)

    def reset_style(self) -> dict:
        """reset --style：回滚风格参数与镜像（核心人格不受影响）。"""
        active_corpus_id = self.style.active_corpus_id
        if active_corpus_id:
            from .style_corpus import StyleCorpusStore
            root = Path(self.config.get("root", "."))
            StyleCorpusStore(root / "data" / "style_corpora").deactivate(active_corpus_id, self.style)
        self.style.reset()
        self.mirror.reset()
        return self.learning_summary()

    def monthly_review(self) -> str:
        """月度回顾：检索记忆 → LLM 生成"我们一起走过的日子"。"""
        review = MonthlyReview(self.memory, llm=self.llm)
        text = review.generate(name=self.card.name)
        # 回顾本身也作为一条 assistant 消息入档
        self.memory.store_message("assistant", text, self.state.energy, self.state.mood, role_id=self.role_key)
        self._append_history_message("assistant", text)
        return text

    # ---------- 公开接口 ----------

    def _recent_msgs(self, limit: int = 8, channel: str | None = None) -> list[dict]:
        """读本角色的近期会话（2026-09-02 真机实锤修复）：主动链素材此前读
        无角色全量表——许眠 22:55 把凛窗口用户的「现在是真睡了」当成对自己说
        的话引用、00:01 按凛账上的 07:10 苏醒发「早饭」。用户作息/睡眠周期是
        全局事实（Q1 设计），但「翻聊天记录」属于 TA 自己那段关系，按角色隔离。
        PC 单角色时代 role_key='' → 行为不变（None=全量）。"""
        return self.memory.recent_messages(limit=limit, channel=channel,
                                           role_id=(self.role_key or None))

    def _persist_state(self) -> None:
        """状态持久化（重启续接）：状态变更后写入 SQLite agent_state 单行。"""
        try:
            # P-3：关系模型快照同步进 AgentState；P-7：冲突状态随关系快照
            rel = self.relationship.to_dict()
            rel["owner"] = self.card.name
            # roster：各卡的关系进度并存于共享单行（凛↔许眠来回切互不覆盖）。
            # 以库里既有 roster 为底、当前卡条目随本次 persist 刷新；owner 标保留
            # 作旧库兼容读路径的回退键。attachment 一并入条目（它同属"角色关系"）。
            rel["attachment"] = round(self.state.attachment, 4)
            rel["conflicts"] = self._conflicts.to_dict()
            rel["imprints"] = self._imprints.to_dict()
            rel["tension"] = self.tension.snapshot()
            if self.schedule_runtime is not None:
                rel["virtual_schedule_runtime"] = self.schedule_runtime.to_snapshot()
                cycle = self.schedule_runtime.state.sleep_cycle_id
                archived = rel.get("virtual_schedule_archived_cycle", "")
                if self.schedule_runtime.sleeping and cycle and cycle != archived:
                    summary = self.schedule_runtime.day_close_summary(
                        datetime.datetime.now(datetime.timezone.utc)
                    )
                    self.memory.store_virtual_life_event(
                        role_id=self.schedule_runtime.outline.role_id,
                        event_kind="day_close_summary",
                        summary=(
                            f"本周期有效活动 {summary['effective_span_minutes']} 分钟，"
                            f"中断 {summary['interruption_minutes']} 分钟，"
                            f"作息偏移 {summary['schedule_offset_minutes']} 分钟，"
                            f"睡眠债务 {summary['sleep_debt_minutes']} 分钟。"
                        ),
                        source={**summary, "sleep_cycle_id": cycle},
                    )
                    rel["virtual_schedule_archived_cycle"] = cycle
                    self.schedule_runtime.activity_spans.clear()
                    self.schedule_runtime.current_item_id = ""
            # 问候/节庆当日去重持久化（2026-08-31 用户反馈：每次重启内存 set 清零，
            # 同一天反复重发早安/中午好）——随关系快照落库，重启读回
            rel["greeted"] = self.greeter.to_state()
            rel["occasions"] = sorted(self.occasion.triggered)
            rel["last_proactive_sent_at"] = getattr(self, "_last_proactive_sent_at", "")
            # roster 落库：以 boot 读回的整张表为底刷新当前卡条目（rel 此时已完整）
            roster = dict(self._rel_roster) if getattr(self, "_rel_roster", None) else {}
            roster[self.card.name] = dict(rel)
            rel["roster"] = roster
            self.state.relationship = rel
            self.memory.save_state(self.state.to_snapshot())
        except Exception as e:
            logger.debug("state persist failed: %s", e)

    # ---------- 用户睡眠周期（2026-08-30 用户拍板） ----------

    _SLEEP_KEYWORDS = ("睡了", "晚安", "睡觉", "入睡", "困了", "躺下", "去睡", "要睡", "眯一会", "睡着了", "上床")
    _WAKE_KEYWORDS = ("醒了", "起来了", "起床", "睡醒", "睡不着", "没睡", "醒着", "起来", "睁眼")

    def _sync_user_asleep(self) -> None:
        """多 Agent 共享单行的登记守卫（2026-09-02 真机实锤）：agent_state 是
        共享单行，非活跃角色的内存 user_asleep 是 boot 时的旧值——用户对许眠
        说「堂堂起床」时凛的 Agent 攥着 asleep=True，守卫把登记静默丢弃；
        反向（stale False）则把该压的主动消息错发。DB 行=唯一真值，读前同步。
        PC 单 Agent 时代同步=自写自读的 no-op，行为不变。"""
        try:
            row = self.memory.con.execute(
                "SELECT user_asleep, last_sleep_report_at FROM agent_state WHERE id=1"
            ).fetchone()
        except Exception:
            return
        if row is None:
            return
        self.state.user_asleep = bool(row[0])
        self.state.last_sleep_report_at = str(row[1] or "")

    def _note_sleep_report(self, user_text: str, now) -> str:
        """识别用户「入睡/苏醒」报告并记录 sleep_cycles。

        判断点哲学（用户 2026-08 拍板；2026-08-31 并入统一判断点）：
        统一判断的 sleep_report 字段优先（"准备睡觉/堂堂起床"变体词表漏、
        "别熬夜"类误伤由裁决挡）；判断点未在场（LLM 挂/短消息未送判）
        才退回 关键词预筛→_confirm_sleep_report 旧链。一条消息最多一次判断。
        返回 ''（无报告）/'sleep'/'wake'。
        """
        text = str(user_text or "").strip()
        if not text:
            return ""
        # 守卫前先同步（陈旧内存态=登记被静默丢弃的根因，见 _sync_user_asleep）
        self._sync_user_asleep()
        action = ""
        judgment = self.turn_judgment(text)
        if judgment is not None:
            # 判断点在场即全权：none=明说不是作息报告（关键词命中也不采信）
            if judgment.sleep_report == "sleeping":
                action = "sleep"
            elif judgment.sleep_report == "waking":
                action = "wake"
        else:
            hit_sleep = any(k in text for k in self._SLEEP_KEYWORDS)
            hit_wake = any(k in text for k in self._WAKE_KEYWORDS)
            if not (hit_sleep or hit_wake):
                return ""
            action = self._confirm_sleep_report(text, hit_sleep, hit_wake)
        if not action:
            return ""
        # 作息报告=用户在分享生活节律：每次记录轻推 familiarity（关系事件表
        # 走既有上限/去重通道；同日同动作 event_id 幂等，一昼夜最多各一次）
        from .persona import apply_relationship_event
        day = now.date().isoformat() if hasattr(now, "date") else str(now)[:10]
        self.relationship = apply_relationship_event(
            self.relationship,
            {"type": "user_confirm", "cause": "用户报告作息（分享生活节律）",
             "event_id": f"sleep-report:{action}:{day}",
             "delta": {"familiarity": 0.02}},
        )
        self._persist_state()
        if action == "sleep" and not self.state.user_asleep:
            self.state.user_asleep = True
            self.state.last_sleep_report_at = now.isoformat(timespec="seconds")
            self.memory.open_sleep_cycle(now.isoformat(timespec="seconds"))
            logger.info("user sleep reported at %s", now.isoformat(timespec="seconds"))
            self._persist_state()
            return "sleep"
        if action == "wake" and self.state.user_asleep:
            self.state.user_asleep = False
            self.state.last_sleep_report_at = now.isoformat(timespec="seconds")
            cycle = self.memory.close_sleep_cycle(now.isoformat(timespec="seconds"))
            logger.info("user wake reported at %s", now.isoformat(timespec="seconds"))
            self._persist_state()
            if cycle:
                # 长睡眠（≥4h）苏醒 → 角色口吻睡眠总结（LLM；失败静默，不影响主回复）
                try:
                    summary = self._sleep_cycle_summary(cycle)
                    if summary:
                        self.memory.update_sleep_summary(cycle["id"], summary)
                        # 合并进本轮回复（2026-08-31 用户反馈：「醒了」触发三连发——
                        # 起床问候/作息调整通知/睡眠总结各一条）。总结交 handle() 注入
                        # 当轮 prompt 融合成一条；同时标记 sleep_summary 已消费，
                        # 阻断 adapter 旁路重发。
                        self._wake_summary_for_turn = summary
                        self.memory.record_proactive_feedback(
                            source="sleep_summary", channel=self.message_channel,
                            candidate_id=f"sleep_summary:{cycle['id']}")
                        logger.info("sleep summary: %s", summary[:60])
                except Exception as e:
                    logger.debug("sleep summary failed: %s", e)
            return "wake"
        return ""

    def _confirm_sleep_report(self, text: str, hit_sleep: bool, hit_wake: bool) -> str:
        """LLM 确认消息是否真是入睡/苏醒报告；失败回退关键词规则。"""
        if self.llm is not None and getattr(self.llm, "base_url", ""):
            try:
                task = (
                    f"用户消息：「{text[:120]}」\n"
                    "判断用户是否在报告自己入睡或苏醒（不是问对方睡没睡、不是描述别人、"
                    "不是『别睡太晚』这类叮嘱）。只输出 JSON：{\"action\":\"sleep\"|\"wake\"|\"none\"}"
                )
                raw = self.llm.chat(
                    [{"role": "user", "content": task}], max_tokens=128, temperature=0.2)
                import json as _json
                data = _json.loads((raw or "").strip())
                action = str(data.get("action") or "").strip()
                if action in ("sleep", "wake"):
                    return action
            except Exception as e:
                logger.debug("sleep report LLM failed: %s", e)
        # 回退：睡/醒关键词同时命中（「睡不着」「没睡醒」）→ 不判定；单一命中按关键词
        if hit_sleep and hit_wake:
            return "sleep" if any(k in text for k in ("睡了", "晚安", "睡觉", "去睡", "上床", "困了")) else "wake"
        return "sleep" if hit_sleep else "wake"

    def _sleep_cycle_summary(self, cycle: dict) -> str:
        """长睡眠苏醒总结（角色口吻）：概括自上次苏醒到本次苏醒的作息+评价。

        LLM 不可用/失败 → 返回空串（调用方静默）。
        """
        if self.llm is None or not getattr(self.llm, "base_url", ""):
            return ""
        prev = self.memory.latest_closed_cycle()
        fell = cycle.get("fell_asleep_at", "")
        woke = cycle.get("woke_at", "")
        try:
            from datetime import datetime
            f = datetime.fromisoformat(fell).astimezone().strftime("%m-%d %H:%M") if fell else "?"
            w = datetime.fromisoformat(woke).astimezone().strftime("%m-%d %H:%M") if woke else "?"
            dur_h = ""
            if fell and woke:
                dur_min = int((datetime.fromisoformat(woke) - datetime.fromisoformat(fell)).total_seconds() / 60)
                if dur_min < 0:
                    dur_min += 24 * 60  # 跨天（22:00 → 次日 07:00）
                dur_h = f"{dur_min // 60}小时{dur_min % 60}分"
            # 清醒时长=自上次苏醒到本次入睡（跨周期）
            awake = ""
            if prev and prev.get("woke_at") and fell:
                am = int((datetime.fromisoformat(fell) - datetime.fromisoformat(prev["woke_at"])).total_seconds() / 60)
                awake = f"；清醒时长：{am // 60}小时{am % 60}分" if am > 0 else ""
            task = (
                f"用户刚睡醒（现在当地时间 {w}）。入睡时刻：{f}；睡眠时长：{dur_h}{awake}。\n"
                "用你的口吻发一条简短的起床问候+睡眠状况总结（两三句话），"
                "可以轻松评价一下他的作息（规律/熬夜/睡得不错），别说教，像朋友刚睡醒时说话。"
                f"提到吃饭/活动安排时按「现在={w}」这个时刻选对的餐点（早上=早饭/晚上=晚饭），别串时段。"
            )
            return (self._short_task(task, max_tokens=256) or "").strip()
        except Exception as e:
            logger.debug("sleep summary compute failed: %s", e)
            return ""

    def _context_probe(self, now=None) -> str:
        """当下情境推测（联想 B 类）：距用户最后消息 2-6h 且白天时段 →
        一句带不确定感的猜测（"这个点你该忙完了吧？"）。

        防刻意：每日 ≤2 次、同时段（早/午/晚/夜）不重复、用户报告在睡不发、
        依恋 <0.3 不发（初识就盯着人家行程像监视）。数据=消息时间戳+最近
        话题（用户作息画像的雏形，无独立统计——时间戳本身就是作息）。
        """
        import datetime
        now = now or datetime.datetime.now()
        if self.state.user_asleep or self.state.attachment < 0.3:
            return ""
        # 距末条用户消息的间隔
        try:
            ref = self._naive_local(now if isinstance(now, datetime.datetime)
                                    else datetime.datetime.fromtimestamp(now))
            last_user = None
            for row in reversed(self._recent_msgs(limit=10)):
                if row.get("role") == "user":
                    last_user = self._naive_local(datetime.datetime.fromisoformat(
                        str(row.get("created_at")).replace("Z", "+00:00")))
                    break
            if last_user is None:
                return ""
            gap_h = (ref - last_user).total_seconds() / 3600
            if not (2.0 <= gap_h <= 6.0):
                return ""
        except Exception:
            return ""
        # 时段桶 + 当日次数（≤2）
        bucket = ("morning" if 6 <= ref.hour < 11 else "noon" if ref.hour < 15
                  else "evening" if 15 <= ref.hour < 23 else "night")
        if bucket == "night":
            return ""  # 深夜推测=打扰
        day = ref.date().isoformat()
        cid = f"probe:{day}:{bucket}"
        rows = self.memory.recent_proactive_feedback(source="context_probe", limit=30)
        today = [r for r in rows if str(r.get("candidate_id") or "").startswith(f"probe:{day}")]
        if len(today) >= 2 or any(str(r.get("candidate_id") or "") == cid for r in today):
            return ""
        last_text = ""
        for row in reversed(self._recent_msgs(limit=10)):
            if row.get("role") == "user":
                last_text = str(row.get("content") or "")[:60]
                break
        fallback = {
            "morning": "这个点你应该已经忙起来了，昨晚那事儿还压着吗？",
            "noon": "到下午了，你上午那摊子事收尾没？",
            "evening": "这个点该消停了，今天累不累？",
        }[bucket]
        if not (getattr(self.llm, "is_model_loaded", None) and self.llm.is_model_loaded()):
            self.memory.record_proactive_feedback(
                source="context_probe", channel=self.message_channel, candidate_id=cid)
            return fallback
        try:
            # 联想 D 类顺路（环境巧合）：把你此刻真实的日程活动带入，
            # "我这边刚煮上粥，你那边…"——自发对比，不单独占触发器
            my_side = ""
            rt = getattr(self, "schedule_runtime", None)
            if rt is not None and not rt.sleeping:
                ctx = rt.current_context(ref)
                act = str(getattr(ctx, "activity_key", "") or "")
                place = str(getattr(ctx, "place_label", "") or "")
                if act in {"wake_routine", "focused_practice", "personal_interest_a",
                           "personal_interest_b", "quiet_rest"} or place:
                    my_side = f"你这边正在{({'wake_routine':'梳洗准备一天','focused_practice':'忙自己的事','personal_interest_a':'摸自己的爱好','personal_interest_b':'摸自己的爱好','quiet_rest':'歇着'}.get(act, '做着手头的东西'))}" \
                              + (f"（在{place}）" if place else "") + "。"
            task = (
                f"已经 {gap_h:.0f} 小时没收到用户消息了，现在是{('上午' if bucket == 'morning' else '中午前后' if bucket == 'noon' else '傍晚')}。"
                + (f"TA 上次说过：「{last_text}」。" if last_text else "")
                + my_side
                + "猜一猜 TA 此刻大概在做什么/状态怎么样，用你的口吻发一条消息："
                  "要带不确定感（'该…了吧''不会还在…吧'这种），可关心可调侃，"
                  + ("可以自然带上你自己这边在做的事做对比。" if my_side else "")
                  + "猜错了也没关系，别写成查岗。只发消息本身。"
            )
            text = (self._short_task(task) or "").strip()
        except Exception as e:
            logger.debug("context probe llm failed: %s", e)
            text = ""
        if not text:
            return ""
        self.memory.record_proactive_feedback(
            source="context_probe", channel=self.message_channel, candidate_id=cid)
        return text

    def _missing_sleep_report_hint(self, now=None) -> str:
        """用户有睡眠报告史但最近 26h 无任何睡眠/苏醒报告 → 一句轻提示/吐槽。

        每日一次（按 hint 日期去重 proactive_feedback）；无史/不满足条件返回 ''。
        """
        import datetime
        now = now or datetime.datetime.now(datetime.timezone.utc)
        if isinstance(now, datetime.datetime) and now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        cycles = self.memory.recent_sleep_cycles(limit=5)
        if not cycles:
            return ""  # 无睡眠史，不提示
        # 最近一次报告时刻（入睡或苏醒）距今 >26h 才触发
        last_report = ""
        for c in cycles:
            last_report = c.get("woke_at") or c.get("fell_asleep_at") or ""
            if last_report:
                break
        if not last_report:
            return ""
        try:
            last_dt = datetime.datetime.fromisoformat(last_report)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
            if (now - last_dt).total_seconds() < 26 * 3600:
                return ""
        except Exception:
            return ""
        # 每日去重
        day_key = f"sleep_hint:{now.date().isoformat()}"
        feedback = self.memory.recent_proactive_feedback(source="sleep_hint", limit=30)
        if any(str(r.get("candidate_id") or "") == day_key for r in feedback):
            return ""
        if self.llm is not None and getattr(self.llm, "base_url", ""):
            try:
                task = (
                    "用户平时会跟你报告睡觉/起床，但这两天完全没动静。"
                    "用你的口吻发一句话轻吐槽或关心（别严肃，两三句内），"
                    "比如问他是不是又熬夜了。"
                )
                text = (self._short_task(task, max_tokens=128) or "").strip()
                if text:
                    self.memory.record_proactive_feedback(
                        source="sleep_hint", channel=self.message_channel, candidate_id=day_key)
                    return text
            except Exception as e:
                logger.debug("sleep hint LLM failed: %s", e)
        return ""

    def _user_wake_hour(self) -> float | None:
        """从最近睡眠周期推导用户起床时间（本地时区小时，含分钟小数）。

        取最近 3 个闭合周期 woke_at 的中位数；数据不足返回 None（保持默认三餐）。
        """
        import datetime
        hours = []
        for c in self.memory.recent_sleep_cycles(limit=3):
            woke = c.get("woke_at") or ""
            if not woke:
                continue
            try:
                dt = datetime.datetime.fromisoformat(woke).astimezone()
                hours.append(dt.hour + dt.minute / 60.0)
            except Exception:
                continue
        if not hours:
            return None
        hours.sort()
        return hours[len(hours) // 2]

    def _adapt_schedule_to_user(self, wake_hour: float | None, now, msgs: list[str]) -> None:
        """角色作息向用户作息偏移（每日一次，去重）。

        比较用户起床中位数与角色 circadian.sleep_end（角色起床时刻）；
        差 ≥2h 时把角色作息向用户方向偏移差值的 1/4（渐进、最多 ±4h），
        并生成一条角色口吻的理由消息（LLM 失败静默）。
        """
        if wake_hour is None or not self.schedule_runtime:
            return
        import datetime
        outline = self.schedule_runtime.outline
        circ = getattr(outline, "circadian", None)
        if circ is None:
            return
        try:
            hh, mm = (int(x) for x in str(circ.sleep_end).split(":"))
            role_wake = hh + mm / 60.0
        except Exception:
            return
        diff = wake_hour - role_wake  # 用户相对角色晚起为正
        if abs(diff) < 2.0:
            return  # 作息接近，不动
        now = now or datetime.datetime.now(datetime.timezone.utc)
        day_key = f"adapt:{now.date().isoformat()}"
        feedback = self.memory.recent_proactive_feedback(source="schedule_adapt", limit=30)
        if any(str(r.get("candidate_id") or "") == day_key for r in feedback):
            return
        # 差值的 1/4，渐进；单日步长与总量都卡到本卡偏移上限内
        # （异地恋人等职业硬约束卡 circadian.max_offset_minutes 收窄 → 跟不动用户）
        cap = getattr(circ, "max_offset_minutes", 720)
        step = int(max(-min(240, cap), min(min(240, cap), diff * 60 * 0.25)))
        target = max(-cap, min(cap, self.schedule_runtime.schedule_offset_minutes + step))
        shift = target - self.schedule_runtime.schedule_offset_minutes
        if shift == 0:
            return
        try:
            self.schedule_runtime.apply_offset(
                self.schedule_runtime.schedule_offset_minutes + shift,
                f"适应用户作息（用户起床 {wake_hour:.1f}h vs 角色 {role_wake:.1f}h）",
                now,
            )
            self._persist_state()
            logger.info("schedule adapted to user: +%d min (user wake %.1f, role %.1f)",
                        shift, wake_hour, role_wake)
        except Exception as e:
            logger.debug("schedule adapt apply failed: %s", e)
            return
        self.memory.record_proactive_feedback(
            source="schedule_adapt", channel=self.message_channel, candidate_id=day_key)
        if self.llm is not None and getattr(self.llm, "base_url", ""):
            try:
                task = (
                    "你注意到用户最近作息和你差得挺多（你早上起床的时候他往往还没睡，"
                    "或者你睡了很久他才睡）。你决定把自己的作息也往他的时间靠一靠。"
                    "用你的口吻发一句话说明这个决定，自然点，像随口提起，别解释系统机制。"
                )
                text = (self._short_task(task, max_tokens=128) or "").strip()
                if text:
                    msgs.append(text)
            except Exception as e:
                logger.debug("schedule adapt message failed: %s", e)

    def _apply_tension_event(self, **kwargs):
        result = self.tension.apply_event(**kwargs)
        if result.applied and result.event is not None:
            self.memory.store(
                "episodic",
                result.event.reason,
                importance=0.7,
                confidence=result.event.confidence,
                provenance=",".join(str(x) for x in result.event.evidence_message_ids) or None,
                meta=result.event.to_meta(),
            )
            self._persist_state()
        return result

    def relationship_event_candidate(self) -> dict | None:
        return self.tension.relationship_event_candidate()

    def confirm_relationship_event(self, candidate: dict, *, confirmed: bool) -> bool:
        """用户确认关系候选后才推动 conflict_tension；拒绝只保留候选审计。"""
        if not confirmed or not isinstance(candidate, dict):
            return False
        if candidate.get("kind") != "relationship_event":
            return False
        # C-4（SHARED_CREATION_SPEC §5.3）：同项目连续事件不线性累加——
        # 冷却期内的 shared_creation 候选只保留审计，不更新任何维度。
        if candidate.get("source") == "shared_creation" and candidate.get("cooldown_active"):
            logger.info("relationship candidate in cooldown (project=%s), audit only",
                        candidate.get("project_id"))
            return True
        from .persona import apply_relationship_event
        event_id = str(candidate.get("event_id") or "")
        if candidate.get("source") == "shared_creation":
            etype = str(candidate.get("event_type") or "user_confirm")
            self.relationship = apply_relationship_event(
                self.relationship,
                {"type": etype, "cause": str(candidate.get("content") or "共同创作事件"),
                 "event_id": event_id},
            )
            self._persist_state()
            return True
        self.relationship = apply_relationship_event(
            self.relationship,
            {"type": "major_event", "cause": str(candidate.get("content") or "关系事件"),
             "event_id": event_id, "delta": {"conflict_tension": 0.08}},
        )
        self._persist_state()
        return True

    def turn_judgment(self, user_text: str) -> "MessageJudgment | None":
        """本轮统一消息判断点（judges.py）：一条消息只判一次，消费方读缓存。

        同一文本幂等；LLM 不可用返回 None → 各消费方全量回退关键词规则。
        判断点原则（用户拍板）：关键词只做预筛/兜底，语义裁决归一次低成本
        LLM 调用（512 预算/单 JSON/fail-open）。

        预筛（控制成本，不是裁决）：消息 ≥6 字 且（含疑问/人称/时态等任何
        一类语义信号）才送判；极短确认词与纯表情不触发。
        """
        cached = getattr(self, "_judgment", None)
        key = str(user_text or "").replace(" [图片]", "").strip()
        if cached is not None and getattr(self, "_judgment_for", "") == key:
            return cached
        from .judges import judge_message  # 预筛（短消息/场景词例外）在 judge_message 内
        prev_assistant = next(
            (str(e.get("content") or "") for e in reversed(self._history)
             if e.get("role") == "assistant"), "")
        j = judge_message(self.llm, key, prev_assistant,
                          config=self.config.get("judgment", {}) or {})
        self._judgment = j
        self._judgment_for = key
        return j

    def _process_tension_user_message(self, text: str, *, channel: str, message_id: int) -> None:
        """把用户本轮的明确关系信号送入 TV；普通短消息不产生负向事件。"""
        normalized_channel = "pet" if channel == "tts" else self.message_channel
        if any(token in text for token in ("别主动找我", "不要主动找我", "不要打扰", "别打扰我")):
            self.tension.set_explicit_pause(True, reason="用户明确要求不要主动联系")
            self._persist_state()
            return
        if any(token in text for token in ("可以主动找我", "恢复主动", "解除免打扰")):
            self.tension.set_explicit_pause(False, reason="用户明确恢复主动联系")
            self._persist_state()
            return
        previous = next((entry for entry in reversed(self._history) if entry.get("role") == "assistant"), None)
        previous_text = str(previous.get("content") or "") if previous else ""
        direct_question = extract_direct_question(previous_text)
        judgment = self.turn_judgment(text)  # 幂等：handle 主链已生成则直接命中缓存
        new_conversation = judgment is None and any(
            token in text for token in ("我回来了", "我回来啦", "继续聊", "刚回来", "现在有空"))
        candidate = classify_user_tension_event(
            text, new_conversation=new_conversation, direct_question=direct_question,
            judgment=getattr(judgment, "tension", None) if judgment else None,
        )
        if candidate is None and judgment is None:
            # 判断点在场时 tension 字段已含敷衍裁决；仅未裁决时走词面兜底
            candidate = classify_low_investment_streak(self._history, text)
        if candidate is None:
            return
        self._apply_tension_event(
            event_type=candidate.event_type,
            channel=normalized_channel,
            base_delta=candidate.base_delta,
            reason=candidate.reason,
            dedupe_key=f"{candidate.event_type}:message:{message_id}",
            confidence=candidate.confidence,
            context_factor=candidate.context_factor,
            evidence_message_ids=[message_id],
        )
        if candidate.event_type in {"answered_question", "user_initiated"} and self.tension.state.open_event_ids:
            self.tension.clear_open_event(self.tension.state.open_event_ids[0], resolved_by=candidate.reason)
            self._persist_state()

    def start(self) -> str:
        """会话启动：恢复状态、时间问候或初遇开场白。"""
        self.state.tick(self.config.get("state", {}).get("energy_decay_per_minute", 0.02))
        # 首次会话：初遇开场白
        msgs = self._recent_msgs(limit=2)
        if not msgs:
            opening = self._sanitize_monologue(
                self.card.first_mes or f"你好，我是{self.card.name}。今天想聊点什么？")
            self.memory.store_message("assistant", opening, self.state.energy, self.state.mood, role_id=self.role_key)
            self._append_history_message("assistant", opening)
            self._persist_state()
            return opening
        # 非首次：按时间段问候
        greeting = self._time_greeting()
        self._persist_state()
        return greeting

    def _sanitize_monologue(self, text: str) -> str:
        """出口独白裁决（2026-08-31 两层防线的语义层）：规则层已杀封闭内部词，
        灰色行（第三人称+第一人称同现/人设元词）送一次小 LLM 判定。

        判断点原则（用户拍板）：关键词只做预筛与降级兜底，语义裁决归 LLM。
        失败 fail-open（保留规则层结果，绝不误删正常台词）。"""
        from .reply import drop_lines, monologue_suspect_lines
        text = str(text or "")
        suspects = monologue_suspect_lines(text)
        if not suspects or not getattr(self.llm, "is_model_loaded", lambda: False)():
            return text
        try:
            numbered = "\n".join(f"{i}. {ln}" for i, ln in enumerate(suspects))
            raw = self.llm.chat_structured(
                [{"role": "user", "content": (
                    "下面编号行来自聊天输出。判定每行：是角色对用户说的话，"
                    "还是角色在分析/计划/评价（把对方称为第三人称、谈论'问候/关心'这类"
                    "行为本身、自称风格、提到系统数值）？"
                    "判定是行为不是猜话题——正常聊天里也可以提到人设或吐槽。"
                    "只输出 JSON：{\"monologue_lines\":[被判定为分析行的编号]}"
                )}, {"role": "user", "content": numbered}],
                max_tokens=512, temperature=0.1,
            )
            data = json.loads(str(raw).strip().strip("`").removeprefix("json").strip())
            doomed = {suspects[i].strip() for i in data.get("monologue_lines", [])
                      if isinstance(i, int) and 0 <= i < len(suspects)}
            return drop_lines(text, doomed) if doomed else text
        except Exception as e:
            logger.debug("monologue judge failed (fail-open): %s", e)
            return text

    def handle(self, user_text: str, images: list[str] | None = None, channel: str = "im",
               attachments: str = "", now: datetime.datetime | None = None) -> TurnResult:
        """处理一条用户消息，返回回复。

        images: 图片 data URL 列表（如 data:image/png;base64,...），
        多模态模型直接看图（DESIGN 8.6.2）；纯文本时传 None/[]。
        图片会以 OpenAI 多模态 content 数组形式进当前轮 LLM 请求；
        记忆/历史用 [图片] 占位（避免 base64 撑爆上下文与 FTS5）。
        channel: 通道标识（im/tts，DESIGN 4.8 通道感知），注入 system prompt 的通道语境。
        now: 注入交互时间（测试用固定时钟；生产 None=真实当前时间）。
        """
        user_text = user_text.strip()
        images = [str(x) for x in (images or []) if isinstance(x, str)][:4]
        if images:
            from .image_payload import payload_from_data_url
            validated = []
            for image in images:
                try:
                    validated.append(payload_from_data_url(image, source="agent").data_url)
                except Exception as exc:
                    logger.warning("drop invalid image payload: %s", exc)
            images = validated
        if not user_text and not images:
            return TurnResult(reply="", energy=self.state.energy, mood=self.state.mood)

        interaction_now = now or datetime.datetime.now(datetime.timezone.utc)
        # 本轮所有 user_asleep 消费方（场景块/轻声问候/睡眠闸门）先同步真值：
        # 共享单行下非活跃 Agent 的内存副本是旧的（2026-09-02 显示侧同病灶）
        self._sync_user_asleep()
        if channel == "im" and any(token in user_text for token in ("你在哪", "你现在在哪里", "你在什么地方")):
            answer = self.current_space_answer(interaction_now)
            self.memory.store_message("assistant", answer, self.state.energy, self.state.mood, channel=self.message_channel, role_id=self.role_key)
            self._history.append(self._history_entry("user", user_text))
            self._history.append(self._history_entry("assistant", answer))
            self._persist_state()
            return TurnResult(reply=answer, energy=self.state.energy, mood=self.state.mood)

        schedule_runtime = getattr(self, "schedule_runtime", None)
        resolved_scope = getattr(self, "_current_user_scope", None) or ("pet:default" if channel == "tts" else "qq:default")
        if schedule_runtime is not None and channel == "im" and schedule_runtime.reconcile_from_user(user_text, interaction_now):
            self._persist_state()

        # 用户睡眠周期（2026-08-30 用户拍板）：识别「入睡/苏醒」报告 → sleep_cycles
        # 表 + state.user_asleep。判断点哲学：关键词预筛 → LLM 确认（失败回退关键词）。
        sleep_action = self._note_sleep_report(user_text, interaction_now)
        # 苏醒总结一次性取出（提前 return 的路径也要清残留，防串到下一轮）
        wake_summary = getattr(self, "_wake_summary_for_turn", "")
        self._wake_summary_for_turn = ""

        if schedule_runtime is not None:
            schedule_runtime.advance(interaction_now)
            context = schedule_runtime.current_context(interaction_now)
            if context.item_id and context.activity_category not in {"sleep_window", "gap"}:
                if context.item_id not in schedule_runtime.activity_spans:
                    schedule_runtime.start_activity(context.item_id, interaction_now)
                schedule_runtime.interrupt_activity(interaction_now)
        if schedule_runtime is not None and schedule_runtime.sleeping:
            message_id = self.memory.store_message(
                "user", user_text + (" [图片]" * len(images) if images else ""),
                self.state.energy, self.state.mood,
                channel="pet" if channel == "tts" else self.message_channel,
                attachments=attachments, role_id=self.role_key,
            )
            scope = resolved_scope
            self.memory.archive_sleep_message(
                role_id=schedule_runtime.outline.role_id,
                user_scope=scope,
                sleep_cycle_id=schedule_runtime.state.sleep_cycle_id,
                message_id=message_id,
                sender_scope=scope,
            )
            # 睡眠≠对作息报告聋（2026-09-02 用户反馈「睡眠中入睡/苏醒登记被忽略」）：
            # 登记本来就在吞回复前执行（周期没丢），丢的是回音——用户在 TA 睡着时
            # 报睡/报醒，得到一句带着困意的确认（=TA 浅眠里听见了），苏醒总结也
            # 直接回进这个窗口（此前旁路进 boot.agent 队列，常落到别的角色头上）。
            # 不让用户一句话把角色叫醒：作息是拟真底线，床边的软磨已有 grace 机制。
            mumble = {"sleep": "（从枕头上抬起一点声音）嗯……晚安，那一起睡了。我醒了来找你。",
                      "wake": "（迷迷糊糊）唔……你醒啦。"}
            reply = mumble.get(sleep_action, "")
            if sleep_action == "wake" and wake_summary:
                reply += "\n\n" + wake_summary
            if reply:
                self.memory.store_message("assistant", reply, self.state.energy,
                                          self.state.mood,
                                          channel=self.message_channel,
                                          role_id=self.role_key)
                self._append_history_message("assistant", reply)
            self._persist_state()
            return TurnResult(reply=reply, energy=self.state.energy, mood=self.state.mood)

        # ===== R0 阶段 1: prepare_turn（R0_SPEC 5）=====
        # 输入规整 → 状态推进 → 场景/打断 → 零开销入库 → 记忆检索预算
        # （第一阶段保持现有行为，仅按注释划分；后续逐步抽为独立函数）
        # 记忆/历史占位文本（图片不直接入库，防 base64 膨胀）
        store_text = user_text + (" [图片]" * len(images)) if images else user_text

        # 1. 状态推进 + 用户反馈
        self.state.tick(self.config.get("state", {}).get("energy_decay_per_minute", 0.02))
        self.state.on_user_message(recover_per_message=self.config.get("state", {}).get("energy_recover_per_message", 3.0))
        self.tension.decay(now=datetime.datetime.now(datetime.timezone.utc))
        # 用户来消息 → 闭合最近未回期待（responded/replied 幂等，QQ adapter 重复执行无害）
        try:
            fb = self.memory.recent_proactive_feedback(limit=3)
            pending = [f for f in fb if not f["responded"]]
            if pending:
                src = pending[-1]["source"]
                self.memory.record_proactive_feedback(source=src, channel=pending[-1].get("channel") or "", responded=True)
                self.gate.note_responded(src, channel=pending[-1].get("channel") or "")
        except Exception as e:
            logger.debug("close expectation on reply failed: %s", e)

        # 0. 统一判断点：本轮全部语义裁决一次生成，各消费方读缓存
        judgment = self.turn_judgment(user_text)

        # 1.5 R4 场景锁：用户消息进来时更新场景（进入/退出 busy/away）
        scene = self.scene_lock.note(user_text, getattr(judgment, "scene", None) if judgment else None)
        if scene != "normal":
            logger.info("scene active: %s", scene)

        # 1.5.1 P-7 冲突信号检测（澄清推进/越界新开 + 关系事件联动）
        try:
            from .persona import apply_relationship_event, note_conflict_from_user_text
            action = note_conflict_from_user_text(
                self._conflicts, user_text,
                getattr(judgment, "conflict", None) if judgment else None)
            if action == "violation":
                self.relationship = apply_relationship_event(
                    self.relationship, {"type": "user_violation", "cause": "用户表达越界反感", "event_id": "violation"}
                )
            elif action == "clarify":
                repaired = False
                for c in self._conflicts.open_conflicts():
                    if c.get("clarify_count", 0) >= 2 and c["status"] == "clarifying":
                        self._conflicts.repair(c["id"])
                        self._conflicts.close(c["id"])
                        repaired = True
                if repaired:
                    self.relationship = apply_relationship_event(
                        self.relationship, {"type": "conflict_repaired", "cause": "用户澄清后关系修复", "event_id": "repair"}
                    )
        except Exception as e:
            logger.warning("conflict detection failed (non-blocking): %s", e)

        # DESIGN 4.1 TurnContext：统一输入协议（handle 内部构造，adapter 签名不变）
        ctx = TurnContext(
            channel=channel,
            user_text=user_text or "",
            images=tuple(images or []),
            scene=scene,
            current_time=self._local_message_time(),
            state=self.state.to_snapshot(),
        )

        # 1.6 R0 打断决策：话题复现计数 → L0-L3 分级（R0_SPEC 5）
        topic_count = self.topic_freq.note(user_text)
        interrupt_level = self.interrupt_decider.decide(topic_count)
        if interrupt_level > 0:
            logger.info("interrupt L%d (topic count=%d)", interrupt_level, topic_count)

        # 2. 零开销摄入：消息立即入库（FTS5 同步索引）
        user_msg_id = self.memory.store_message(
            "user", store_text, self.state.energy, self.state.mood,
            channel="pet" if channel == "tts" else self.message_channel, attachments=attachments,
            role_id=self.role_key,
        )
        self._process_tension_user_message(store_text, channel=channel, message_id=user_msg_id)
        self._capture_user_info_gap(user_text, user_msg_id, channel, resolved_scope)

        # ===== R0 阶段 2: build_turn_prompt（R0_SPEC 5）=====
        # 记忆检索（预算内注入）+ MVP2 附加块（风格/镜像/承诺）+ 打断指令
        query_hint = user_text or "图片"
        # P-6/P-9：预构建 PersonaBrief 选回用动作与表达计划（prompts 内会再次构建——轻量，接受重复）
        reuse_action = ""
        response_plan = None
        explicit_style_length = ""
        for rule in self.memory.list_layer("procedural", limit=20):
            rule_kind = (rule.meta or {}).get("kind") or rule.category
            if rule_kind not in {"interaction_rule", "preference"}:
                continue
            if any(word in rule.content for word in ("简短", "一句话", "只说结论", "别展开", "不要详细")):
                explicit_style_length = "short"
                break
            if any(word in rule.content for word in ("详细回答", "详细说明", "展开说", "完整说明")):
                explicit_style_length = "long"
                break
        try:
            from .persona import build_persona_brief, build_response_plan, choose_reuse_action
            # 冲突压力同步到 state（choose_reuse_action/build_response_plan 从 state 读；
            # conflict_tension 真值在 relationship）
            self.state.conflict_tension = self.relationship.conflict_tension
            pre_brief = build_persona_brief(query_hint, self.card, self.relationship, self.state, self.memory)
            action = choose_reuse_action(
                pre_brief, user_text, self.state,
                wants_remember=getattr(judgment, "wants_remember", None) if judgment else None)
            if action != "none" and pre_brief.relevant_user_frameworks:
                key = pre_brief.relevant_user_frameworks[0]["content"][:20]
                if self._reuse_cd.allow(key, self._turn_n):
                    reuse_action = action
            response_plan = build_response_plan(
                {
                    "user_text": user_text,
                    "explicit_style_length": explicit_style_length,
                    "style_length": self.style.preferred_length(channel),
                    "relational_tension_band": self.tension.band,
                    "relational_tension_hint": self.tension.prompt_hint(
                        channel=channel,
                        expression_mode=((self.card.veranima or {}).get("tension_expression") or {}).get("mode", "neutral"),
                    ),
                },
                pre_brief,
                self.state,
            )
        except Exception as e:
            logger.debug("persona reuse/plan skipped: %s", e)
        self._turn_n += 1
        extra_blocks = [
            self.style.to_prompt_block(
                channel=channel,
                length_override=response_plan.desired_length if response_plan is not None else "",
            ),  # M-6：参数 + 通道化 StyleBrief；最终计划覆盖统计长度
            self.mirror.to_prompt_block(),
            self.promises.to_prompt_block(query_hint=query_hint),
        ]
        proactive_context = self._adjacent_proactive_context(user_text, channel)
        if proactive_context:
            extra_blocks.append(proactive_context)
        # 苏醒总结融合（2026-08-31 用户反馈「醒了」三连发）：wake_summary 在
        # handle 开头一次性取出（见 _note_sleep_report 后），融进本轮回复不旁路推送
        if wake_summary:
            extra_blocks.append(
                f"【苏醒总结融合】用户刚报告睡醒。你观察到的一段睡眠情况素材：{wake_summary}\n"
                "把这份睡眠情况自然融进你这句回复里（问候+总结一条说完），"
                "像顺嘴提起，不要复述素材原句，不要分条列点。"
            )
        sleep_archive_ids_to_process: list[int] = []
        if schedule_runtime is not None and schedule_runtime.state.sleep_reason == "woke":
            archive = self.memory.sleep_messages(
                schedule_runtime.outline.role_id, resolved_scope,
                schedule_runtime.last_sleep_cycle_id, limit=20,
            )
            pending = [row for row in archive if not row.get("processed_at")]
            excerpts = []
            for row in pending[-3:]:
                message = self.memory.message_by_id(row.get("message_id")) if row.get("message_id") else None
                if message:
                    excerpts.append(str(message.get("content") or "")[:120])
            if pending:
                detail = "；".join(excerpts) if excerpts else f"共收到 {len(pending)} 条消息"
                extra_blocks.append(f"【醒后衔接】角色睡眠期间用户发过消息：{detail}。自然合并回应，不要说已读或编造内容。")
                sleep_archive_ids_to_process = [row["id"] for row in pending]
        # 困倦渗透（2026-08-31 自检清单缺口①）：睡眠债务是算出来的，此前从未
        # 到达表达层。债务小时数 → 回复形式约束（短句、省略、反应慢半拍）。
        if schedule_runtime is not None:
            debt_h = schedule_runtime.state.sleep_debt_minutes / 60
            if schedule_runtime.state.state == "sleep_preparing" and schedule_runtime.state.sleep_reason == "late_sleep":
                extra_blocks.append(
                    "【强撑】你早该睡了，一直拖着没睡。回复带明显困意：句子变短、"
                    "偶尔用省略号、对复杂问题坦率说撑不住了想先睡。不要提系统或日程。"
                )
            elif debt_h >= 1 and schedule_runtime.state.sleep_reason == "woke":
                extra_blocks.append(
                    f"【欠睡】你没睡够，还差大约 {int(debt_h)} 小时的觉，现在很困。"
                    "回复变短、反应慢半拍、可以用省略号，用户不问就不提困。不要提系统或日程。"
                )
        if scene == "busy":
            extra_blocks.append(
                "【场景偏好·忙碌】用户正在学习或处理事情。回复尽量简短，优先直接回应当前输入；"
                "但必须先保证事实、语义和协议完整，不得截断句子，不得省略必要信息，不得输出忙碌状态或内部处理说明。"
            )
        if response_plan is not None:
            # P-9：表达意图注入（不暴露内部思考；只给意图与开场）
            plan_bits = [f"intent={response_plan.intent}", f"长度={response_plan.desired_length}"]
            if response_plan.opening_move:
                plan_bits.append(f"开场={response_plan.opening_move}")
            if response_plan.conflict:
                plan_bits.append(response_plan.conflict)
            if response_plan.tension_hint and response_plan.tension_hint != "calm":
                plan_bits.append(f"关系张力提示={response_plan.tension_hint}")
            extra_blocks.append(f"【表达意图】{'；'.join(plan_bits)}")
        if self.search_enabled:
            bare_retry = self.search_trigger.is_bare_retry(user_text)
            search_text = user_text
            if not bare_retry:
                self._last_search_request = None
            elif self._last_search_request:
                search_text = self._last_search_request["text"]
            else:
                previous_user = next(
                    (item.get("content", "") for item in reversed(self._history) if item.get("role") == "user"),
                    "",
                )
                previous_decision = self.search_trigger.determine(
                    previous_user,
                    allow_implicit=bool(self.search_config.get("allow_implicit_freshness_search", False)),
                    allow_explicit=bool(self.search_config.get("allow_user_explicit_request", True)),
                ) if previous_user else None
                if previous_decision and previous_decision.should_search:
                    self._last_search_request = {"text": previous_user, "query": previous_decision.query}
                    search_text = previous_user
            decision = self.search_trigger.determine(
                search_text,
                allow_implicit=bool(self.search_config.get("allow_implicit_freshness_search", False)),
                known_entities={self.card.name, *(
                    item.get("content", "") for item in self._history[-10:]
                    if item.get("role") in {"user", "assistant"}
                )},
                allow_explicit=bool(self.search_config.get("allow_user_explicit_request", True)),
                wants_search=getattr(judgment, "wants_search", None) if judgment else None,
            )
            if bare_retry and decision.should_search:
                decision = replace(decision, reason="retry_previous_search", force_refresh=True)
            if decision.should_search:
                self._last_search_request = {"text": search_text, "query": decision.query}
                logger.info("web search requested: reason=%s query_len=%d", decision.reason, len(decision.query))
                language = str(self.search_config.get("default_language", "zh-CN"))
                intent = analyze_search_intent(
                    search_text,
                    " ".join(item.get("content", "") for item in self._history[-10:]),
                )
                if self.search_config.get("semantic_locator_enabled", False) and self.semantic_locator.should_upgrade(intent):
                    located = self.semantic_locator.locate(
                        search_text,
                        client=self.search,
                        language=language,
                        force_refresh=decision.force_refresh,
                        context_text=" ".join(item.get("content", "") for item in self._history[-10:]),
                    )
                    evidence = located.evidence
                    logger.info("semantic search: kind=%s queries=%d verified=%s", intent.kind, len(located.queries), located.verified)
                else:
                    normalized_query = normalize_time_in_query(decision.query, intent.time_range)
                    evidence = EvidencePack.from_results(
                        normalized_query,
                        self.search.search(
                            normalized_query,
                            language=language,
                            force_refresh=decision.force_refresh,
                            time_range=intent.time_range,
                        ),
                        time_range=intent.time_range,
                        intent_kind=intent.kind,
                    )
                extra_blocks.append(evidence.to_prompt(channel=channel))
        if interrupt_level > 0:
            extra_blocks.append(_interrupt_prompt(interrupt_level))
        schedule_context = self._schedule_context(channel, now=datetime.datetime.now(datetime.timezone.utc))
        schedule_block = self._format_schedule_context(schedule_context)
        if schedule_block:
            extra_blocks.append(schedule_block)
        profile_block = self._profile_block()
        if profile_block:
            extra_blocks.append(profile_block)
        system = build_system_prompt(
            self.card, self.state, self.memory,
            core_profile_budget=self.config.get("memory", {}).get("core_profile_budget", 1200),
            procedural_budget=self.config.get("memory", {}).get("procedural_budget", 1000),
            section_budget=self.config.get("memory", {}).get("section_budget", 2400),
            session_budget=self.config.get("memory", {}).get("session_budget", 600),
            channel=channel,
            clarification=is_clarification(user_text, getattr(judgment, "clarification", None) if judgment else None),  # R1 可逆性：追问 → 精确值（R1_SPEC 3）
            extra_blocks=extra_blocks,
            relationship=self.relationship,  # P-4：PersonaBrief 接入口
            reuse_action=reuse_action,       # P-6：本轮回用动作
        )

        # 4. 组装对话（历史 + 当前）；当前轮含图时用多模态 content 数组
        messages = [{"role": "system", "content": system + "\n" + self._time_context_instruction()}]
        hist = [self._prompt_history(item) for item in self._history[-self.config.get("chat", {}).get("history_max_messages", 20):]]
        # 2026-08-04 修复：proactive/late_reply/问候会向 _history 追加孤立的 assistant
        # 消息（无配对 user），截断后序列可能以 assistant 开头；llama.cpp Qwen3 jinja
        # 模板要求第一条非 system 消息必须是 user，否则 400 "No user query found in
        # messages"（现象：跑若干轮后偶发 400，且被 client 误报为"模型未加载"）。
        # 丢弃开头的孤立 assistant，保证序列 [user, assistant, ..., user]。
        while hist and hist[0]["role"] != "user":
            hist = hist[1:]
        messages.extend(hist)
        current_prompt_time = self._message_time_for_id(user_msg_id) or ctx.current_time
        current_prompt_text = self._format_history_content(user_text or ("[图片]" if images else ""), current_prompt_time)
        if images:
            content: list[dict] = [{"type": "text", "text": current_prompt_text}]
            content.extend({"type": "image_url", "image_url": {"url": u}} for u in images)
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": current_prompt_text})

        # ===== R0 阶段 3: call_llm（R0_SPEC 5）=====
        # 模型可用性前置检查（远程 API：is_model_loaded 恒 True，防御性保留）
        check = getattr(self.llm, "is_model_loaded", None)
        if check is not None and not check():
            reply = "（我这边暂时没拿到回复，再说一遍？）"
            self._append_history_message("user", store_text, self._message_time_for_id(user_msg_id))
            self._persist_state()
            if schedule_runtime is not None and not schedule_runtime.sleeping:
                schedule_runtime.resume_activity(datetime.datetime.now(datetime.timezone.utc))
            return TurnResult(reply=reply, energy=self.state.energy, mood=self.state.mood)

        # 5. 生成（低精力时限短；联网搜索开启时走工具调用链路）
        low_energy = self.state.energy < 40
        generation_failed = False
        try:
            chat_fn = getattr(self.llm, "chat_structured", None) or self.llm.chat
            reply = chat_fn(
                messages,
                max_tokens=self.llm.low_energy_max_tokens if low_energy else None,
            )
        except LLMTimeoutError as e:
            logger.warning("LLM timed out during turn: %s", e)
            reply = "（连接有点慢……我没拿到这句回复，再说一遍？）"
            generation_failed = True
        except LLMUnavailableError as e:
            # 服务不可用（连接失败/鉴权失败）：角色化提示，不冒充"卡了"
            logger.warning("LLM unavailable during turn: %s", e)
            reply = "（我这边暂时没拿到回复，再说一遍？）"
            generation_failed = True
        except Exception as e:
            logger.error("chat failed: %s", e)
            reply = "（我这边有点卡……让我缓一下，你再说一遍？）"
            generation_failed = True

        # ===== R0 阶段 4: parse_reply（R0_SPEC 5）=====
        # R2 表情标签驱动：tts 通道解析结构化输出（text/tone/portrait）（R2_SPEC 2）
        #     R2 双语（角色卡 bilingual.enabled）：ja_text 送 TTS / reply(zh) 显示（R2_SPEC 2）
        portrait = ""
        tone = ""
        ja_text = ""
        turn_reply = None
        if channel == "tts":
            bilingual = bool(((self.card.veranima or {}).get("bilingual") or {}).get("enabled"))
            from .reply import parse_reply
            parsed = parse_reply(
                reply, channel="tts", card=self.card, bilingual=bilingual,
                max_segments=int((self.config.get("output", {}) or {}).get("max_segments", 6)),
                max_chars=int((self.config.get("output", {}) or {}).get("max_text_chars", 1200)),
            )
            turn_reply = parsed
            if parsed.degraded:
                logger.warning("reply parse degraded (tts): %s | raw[:120]=%s", parsed.degraded, reply[:120])
                generation_failed = True
            reply = parsed.text or ("（我这边没拿到可显示的回复，再说一遍？）" if parsed.degraded else reply)
            tone = parsed.tone
            portrait = parsed.portrait
            ja_text = parsed.ja_text
            if portrait and not self._portrait_valid(portrait):
                portrait = ""  # 词表外标签回退（防 OOC）
        else:
            from .reply import parse_reply
            parsed = parse_reply(
                reply, channel="im",
                max_chars=int((self.config.get("output", {}) or {}).get("max_text_chars", 1200)),
            )
            turn_reply = parsed
            if parsed.degraded:
                logger.warning("reply parse degraded (im): %s | raw[:120]=%s", parsed.degraded, reply[:120])
                generation_failed = True
            reply = parsed.text or ("（我这边没拿到可显示的回复，再说一遍？）" if parsed.degraded else reply)

        if is_failure_fallback_reply(reply):
            if not generation_failed:
                logger.warning("model echoed an internal failure fallback; suppressing it")
            generation_failed = True
            reply = "（我这边暂时没拿到回复，再说一遍？）"
            turn_reply = None
            portrait = ""
            tone = ""
            ja_text = ""

        # 独白裁决（两层防线语义层）：规则层杀不掉的灰色行送一次 LLM 判定；
        # 在入库/入历史/发送之前执行，泄漏止步于出口
        if not generation_failed and reply:
            reply = self._sanitize_monologue(reply)
            if not reply:
                generation_failed = True
                reply = "（我这边暂时没拿到回复，再说一遍？）"
                turn_reply = None

        if generation_failed:
            self._history.append(self._history_entry("user", store_text, self._message_time_for_id(user_msg_id)))
            self._persist_state()
            if schedule_runtime is not None and not schedule_runtime.sleeping:
                schedule_runtime.resume_activity(datetime.datetime.now(datetime.timezone.utc))
            return TurnResult(
                reply=reply,
                energy=self.state.energy,
                mood=self.state.mood,
                portrait=portrait,
                tone=tone,
                ja_text=ja_text,
                reply_obj=turn_reply,
            )

        # ===== R0 阶段 5: persist_turn（R0_SPEC 5）=====
        if channel == "im" and not tone:
            # P2：im 通道追加轻量情绪分类（失败/关闭→空，UI 回退 mood）
            tone = self._classify_tone(reply)
        self.memory.store_message("assistant", reply, self.state.energy, self.state.mood, role_id=self.role_key,
                                  channel="pet" if channel == "tts" else self.message_channel, tone=tone)
        if sleep_archive_ids_to_process:
            self.memory.mark_sleep_messages_processed(sleep_archive_ids_to_process)
            schedule_runtime.state = replace(schedule_runtime.state, sleep_reason="awake_reconciled")
        self._history.append(self._history_entry("user", store_text, self._message_time_for_id(user_msg_id)))
        self._history.append(self._history_entry("assistant", reply, self._local_message_time()))
        self.state.on_assistant_message()

        # 7. 定期触发遗忘衰减（每 10 轮；MVP1 简化：无后台调度器，随对话驱动）
        if self.state.total_messages % 10 == 0:
            result = self.memory.decay()
            logger.info("memory decay applied: updated=%s faded=%s", result.get("updated", 0), result.get("faded", 0))

        # 8. 事件记忆提取（判断点 memory_kind 优先，词表兜底）
        self._maybe_extract_events(user_text, judgment)
        self._apply_profile_facts(judgment)
        self._capture_nickname_feedback(user_text)

        # 8.5 MVP2 学习：隐式反馈 → 风格参数 + 语言镜像 + 承诺识别
        prev_reply = self._history[-3]["content"] if len(self._history) >= 3 else ""
        delay = (time.time() - self._last_reply_ts) if self._last_reply_ts else 0.0
        sig = extract_feedback(user_text, reply, prev_reply, delay=delay)
        if judgment is not None:
            # 语义裁决优先（词表 "别"=负向 这类误伤由判断点纠偏）；未裁决保持规则
            if judgment.feedback_like is not None or judgment.feedback_dislike is not None:
                sig.positive = bool(judgment.feedback_like)
                sig.negative = bool(judgment.feedback_dislike)
                sig.correction = bool(judgment.feedback_dislike) and any(
                    w in user_text for w in ("不对", "错了", "不是", "理解错", "没听懂"))
        self._last_reply_ts = time.time()
        self.style.observe(sig, user_text)   # M-6：feedback 快变量 + 文风画像慢变量
        self.mirror.observe(user_text)
        self.promises.record(user_text)
        # P-9：表层人格印记（正反馈 → candidate；纠正 → 拒绝方向）
        if sig.positive:
            self._imprints.note("depth", 1.0, user_msg_id, scope="对话")
        if sig.correction:
            self._imprints.note("depth", -1.0, user_msg_id)
        # curator 整理（每 8 轮）+ 持久化（每 20 轮）
        if self.state.total_messages % 8 == 0:
            result = self.memory.curate()
            logger.info("curator: %s", result.get("ops"))
        if self.state.total_messages % 20 == 0:
            self.style.save()
            self.mirror.save()

        # 9.3 R1 候选记忆写入（R1_SPEC 2 固定顺序：store assistant → rule_extract
        #      → validate → dedupe → store/update_latest）
        try:
            for cand in self._rule_extract(store_text, user_msg_id):
                self._store_candidate(cand)
        except Exception as e:
            logger.warning("candidate memory extraction failed (non-blocking): %s", e)

        # 9.3.1 P-1 用户思维框架（PERSONA_LOOP_SPEC P-1：extract → validate → convert → store）
        try:
            from .persona import extract_framework_candidates, persona_candidate_to_memory, validate_persona_candidate
            for pc in extract_framework_candidates(user_text, user_msg_id):
                issues = validate_persona_candidate(pc, self.card)
                if issues:
                    logger.debug("persona candidate rejected: %s", issues)
                    continue
                mc = persona_candidate_to_memory(pc, source_message_id=user_msg_id)
                if mc is not None:
                    self._store_candidate(mc)
                    self._reflection_counters["persona_candidates"] += 1
        except Exception as e:
            logger.warning("persona framework extraction failed (non-blocking): %s", e)

        # 9.3.2 P-2 共同意义（PERSONA_LOOP_SPEC P-2）
        try:
            from .persona import extract_shared_meaning_candidates, persona_candidate_to_memory, validate_persona_candidate
            for pc in extract_shared_meaning_candidates(user_text, user_msg_id):
                issues = validate_persona_candidate(pc, self.card)
                if issues:
                    logger.debug("shared meaning candidate rejected: %s", issues)
                    continue
                mc = persona_candidate_to_memory(pc, source_message_id=user_msg_id)
                if mc is not None:
                    self._store_candidate(mc)
                    self._reflection_counters["persona_candidates"] += 1
        except Exception as e:
            logger.warning("shared meaning extraction failed (non-blocking): %s", e)

        # 9.3.3 P-5 反思整合（低频：每 20 个有效人格候选触发一次）
        try:
            self._maybe_reflect()
        except Exception as e:
            logger.warning("persona reflection failed (non-blocking): %s", e)

        # 9.4 MEMORY_SPEC 6.2：Reply.memory_candidates 低置信候选（仍需程序校验）
        try:
            self._store_llm_candidates(turn_reply, user_msg_id=user_msg_id)
        except Exception as e:
            logger.warning("llm candidate store failed (non-blocking): %s", e)

        # 9.5 状态持久化（重启续接）
        self._persist_state()
        logger.debug("turn done: channel=%s scene=%s images=%d state=%s",
                     ctx.channel, ctx.scene, len(ctx.images), ctx.state.get("mood"))

        # 9.6 MEMORY_SPEC 9 历史压缩（超长时摘要最旧部分）
        try:
            self._compact_history()
        except Exception as e:
            logger.warning("history compaction failed (non-blocking): %s", e)

        # P-9：PAD 渲染提示（确定性；adapter 可选消费，TTS 短句/IM 标点）
        style_hint = ""
        try:
            from .persona import render_authenticity
            style_hint = render_authenticity(
                reply, {"valence": self.state.valence, "arousal": self.state.arousal}, channel,
            )["style_hint"]
        except Exception:
            pass

        if schedule_runtime is not None and not schedule_runtime.sleeping:
            schedule_runtime.resume_activity(datetime.datetime.now(datetime.timezone.utc))

        return TurnResult(
            reply=reply,
            recalled=[],
            proactive=False,
            proactive_msg="",
            energy=self.state.energy,
            mood=self.state.mood,
            portrait=portrait,
            tone=tone,
            ja_text=ja_text,
            reply_obj=turn_reply,
            style_hint=style_hint,
        )

    def _maybe_reflect(self) -> None:
        """P-5：低频反思整合（每 20 个有效人格候选 / 高情绪事件 / 用户纠正触发）。

        流程：due → propose（从人格证据）→ validate（核心兼容）→ apply（SelfModel 版本 +1）→
        存 self_model_snapshot（core_profile 版本链）。任何失败只记录，不阻断对话。
        """
        from .reflection import apply_reflection, propose_reflection, reflection_due, validate_reflection
        counters = self._reflection_counters
        trigger = None
        if counters.get("user_corrections", 0) > 0:
            trigger = "user_correction"
            counters["user_corrections"] = 0
        elif counters.get("high_emotion_events", 0) > 0:
            trigger = "high_emotion_event"
            counters["high_emotion_events"] = 0
        elif reflection_due("persona_candidates_20", counters):
            trigger = "persona_candidates_20"
            counters["persona_candidates"] = 0
        if not trigger:
            return
        # 收集人格证据（shared_meaning/user_framework/relationship_event）
        evidence = []
        for layer, kinds in (("semantic", ("user_framework", "character_belief")), ("episodic", ("shared_meaning", "relationship_event"))):
            for e in self.memory.list_layer(layer, limit=10):
                if (e.meta or {}).get("kind") in kinds and (e.meta or {}).get("needs_confirmation") is not True:
                    evidence.append({"id": e.id, "kind": (e.meta or {}).get("kind"),
                                     "content": e.content, "confidence": e.confidence})
        r = propose_reflection(evidence[:5])
        if r is None:
            return
        issues = validate_reflection(r, self.card)
        if issues:
            logger.info("reflection rejected: %s", issues)
            return
        r.status = "validated"
        models = apply_reflection(r, {"self_model": {
            "version": 1, "learned_beliefs": [], "stable_traits": [],
            "evidence_ids": [], "updated_at": "",
        }, "relationship": self.relationship.to_dict()})
        sm = models.get("self_model", {})
        if sm.get("version", 1) > 1:
            # 持久化 self_model_snapshot（core_profile 版本链，kind 标记）
            old = None
            for e in self.memory.list_layer("core_profile", limit=5, include_superseded=True):
                if (e.meta or {}).get("kind") == "self_model_snapshot":
                    old = e
                    break
            if old is not None:
                self.memory.update_latest(old.id, f"自我模型 v{sm['version']}: {sm.get('learned_beliefs', [])}",
                                          confidence=0.7, meta={**old.meta, "supersedes": old.id,
                                                               "kind": "self_model_snapshot", "version": sm["version"]})
            else:
                self.memory.store("core_profile", f"自我模型 v{sm['version']}: {sm.get('learned_beliefs', [])}",
                                  confidence=0.7, meta={"kind": "self_model_snapshot", "version": sm["version"],
                                                        "evidence_message_ids": r.evidence_ids})
            self.memory.store_self_model_chapter(
                title=f"自我模型阶段 {sm['version']}",
                self_interpretation=str(sm.get("learned_beliefs", "")),
                key_events=list(r.evidence_ids),
                relationship_changes=list((self.relationship.to_dict() or {}).get("open_relational_threads", [])),
            )
            logger.info("persona reflection applied (evidence=%s)", r.evidence_ids)

    def _compact_history(self) -> None:
        """MEMORY_SPEC 9：历史超长时把最旧部分压成摘要（session 层 history_summary）。

        - 保留最近完整 user/assistant 对（history_max_messages 轮）
        - 摘要只记录已出现信息，不生成新事实（prompt 约束）
        - LLM 失败 → 直接截断内存历史，不阻断对话
        - 摘要带消息区间（from/to message id）与 source_count
        """
        max_hist = int((self.config.get("chat", {}) or {}).get("history_max_messages", 20))
        if len(self._history) <= max_hist * 2:
            return
        half = len(self._history) - max_hist
        old_part = self._history[:half]
        new_part = self._history[half:]
        # 消息区间：从 messages 表反查旧部分最后一条 user 消息 id
        from_msg = to_msg = 0
        try:
            msgs = self.memory.recent_messages(limit=max_hist * 4)
            old_user_texts = {m["content"] for m in old_part if m["role"] == "user"}
            ids = [m["id"] for m in msgs if m["role"] == "user" and m["content"] in old_user_texts]
            if ids:
                from_msg, to_msg = min(ids), max(ids)
        except Exception:
            pass
        summary = ""
        try:
            transcript = "\n".join(
                f"{'用户' if m['role'] == 'user' else '我'}: {self._format_history_content(m['content'], m.get('created_at'))}"
                for m in old_part
            )
            summary = self._short_task(
                "把下面的对话压缩成 3-5 句中文摘要。只保留已出现的事实、承诺、情绪变化和未完成话题，"
                "绝对不要编造新内容：\n" + transcript,
            )
        except Exception as e:
            logger.warning("history compaction failed, truncating: %s", e)
        if summary and len(summary) > 10:
            try:
                self.memory.store(
                    "session",
                    f"【更早的对话】{summary[:400]}",
                    importance=0.4,
                    confidence=0.7,
                    meta={
                        "kind": "history_summary",
                        "from_message_id": from_msg,
                        "to_message_id": to_msg,
                        "source_count": len(old_part) // 2,
                    },
                )
            except Exception as e:
                logger.warning("history summary store failed: %s", e)
        self._history = new_part
        while self._history and self._history[0]["role"] != "user":
            self._history.pop(0)  # 保证序列 [user, ...]（jinja 400 防护）

    def _portrait_valid(self, label: str) -> bool:
        """R2：portrait 标签必须在角色卡 expressions 词表内（防 OOC 标签，R2_SPEC 2）。"""
        exprs = (self.card.veranima or {}).get("avatar", {}).get("expressions", {})
        return label in exprs

    # ---------- R1 候选记忆（R1_SPEC 2 / MEMORY_SPEC 5-6） ----------

    _R1_RULES = (
        # 口语变体必须覆盖（"我特别喜欢" 不包含 "我喜欢"）
        (("记住", "以后记得", "别忘了", "记住啊"), "user_fact", 0.85),
        (("我喜欢", "我很喜欢", "我特别喜欢", "我最喜欢", "我最爱", "我超爱", "我讨厌", "我不喜欢", "我特别讨厌", "我受不了"), "user_fact", 0.8),
        (("我不吃", "我不能吃", "过敏"), "user_fact", 0.85),
        (("我们一起", "刚才", "上次", "那天", "上次我们"), "shared_episode", 0.75),
        (("以后提醒", "下次记得", "你答应", "说好了", "别忘了提醒", "记得提醒"), "commitment", 0.85),
    )
    # 显式纠正（MEMORY_SPEC 8.2 correction：必须新版本 + 提升置信度）
    _CORRECTION_RULES = (
        ("不是", "user_fact"),
        ("其实是", "user_fact"),
        ("我说的是", "user_fact"),
        ("错了", "user_fact"),
        ("纠正一下", "user_fact"),
        ("不是我们", "shared_episode"),
    )

    def _rule_extract(self, user_text: str, source_message_id: int) -> list[dict]:
        """从用户消息规则提取候选记忆（R1_SPEC 2 / MEMORY_SPEC 6）。

        - “记住/以后/我喜欢/我讨厌/我不喜欢” → user_fact（口语变体全覆盖）
        - “我们一起/刚才/上次……结果” → shared_episode（仅当有事件/结果表达）
        - “以后提醒/下次记得/你答应” → commitment（问句不命中）
        - 显式纠正 → 对应 kind，confidence 提升 + meta.correction=True
        候选提取失败不影响回复；写入失败只记录日志（调用方已包 try）。
        """
        candidates: list[dict] = []
        for keywords, kind, conf in self._R1_RULES:
            if not any(k in user_text for k in keywords):
                continue
            if kind == "commitment" and user_text.rstrip().endswith("？"):
                continue  # 问句不命中承诺
            if kind == "shared_episode" and not any(
                k in user_text for k in ("了", "结果", "之后", "后来")
            ):
                continue  # 无事件/结果表达不提取（"一起"只是触发词）
            content = user_text.strip()[:200]
            if not content:
                continue
            candidates.append({
                "kind": kind,
                "content": content,
                "confidence": conf,
                "importance": 0.6,
                "source": "rule_extract",
                "source_message_id": source_message_id,
                "subject": "user",
            })
        # 显式纠正：检测到纠正 → 追加高置信候选（无论是否命中常规规则）
        for marker, kind in self._CORRECTION_RULES:
            if marker in user_text:
                content = user_text.strip()[:200]
                if content:
                    candidates.append({
                        "kind": kind,
                        "content": content,
                        "confidence": 0.85,
                        "importance": 0.7,
                        "source": "rule_extract",
                        "source_message_id": source_message_id,
                        "subject": "user",
                        "needs_confirmation": False,
                    })
                break
        return candidates

    def _store_llm_candidates(self, reply_obj, *, user_msg_id: int) -> None:
        """MEMORY_SPEC 6.2：Reply.memory_candidates → 程序校验 → 写入（LLM 不直连 SQL）。"""
        if reply_obj is None or not getattr(reply_obj, "memory_candidates", None):
            return
        for mc in reply_obj.memory_candidates:
            if not isinstance(mc, dict):
                continue
            try:
                cand = {
                    "kind": mc.get("kind", "user_fact"),
                    "content": mc.get("content") or mc.get("text") or "",
                    "confidence": float(mc.get("confidence", 0.5)),
                    "importance": float(mc.get("importance", 0.5)),
                    "source": "llm_extract",
                    "source_message_id": user_msg_id,
                    "needs_confirmation": True,  # LLM 候选默认低置信待确认
                }
                for key in ("topic", "status", "intent", "follow_up_days", "importance"):
                    if key in mc:
                        cand[key] = mc[key]
                self._store_candidate(cand)
            except Exception as e:
                logger.warning("llm candidate store failed (non-blocking): %s", e)

    def _store_candidate(self, cand: dict) -> None:
        """校验 → 去重 → 写入/版本链（R1_SPEC 1.2/3，MEMORY_SPEC 5-8）。"""
        from ..memory.store import validate_candidate
        issues = validate_candidate(cand)
        if issues:
            logger.debug("candidate rejected: %s", issues)
            return
        # MEMORY_BACKEND_EVAL M-D：收件箱开启时，低置信候选先入队待审（不写入 memories）
        mem_cfg = ((getattr(self, "config", None) or {}).get("memory") or {})
        if mem_cfg.get("review_inbox_enabled"):
            threshold = float(mem_cfg.get("review_confidence_below", 0.6))
            if float(cand.get("confidence", 0.75)) < threshold:
                self.memory.queue_review(
                    cand, reason=f"confidence {cand.get('confidence')} < {threshold}",
                )
                return
        kind = cand["kind"]
        layer = self.memory_store_layer(kind)
        content = cand["content"]
        source_id = int(cand["source_message_id"])
        existing_event = next(
            (entry for entry in self.memory.list_layer(layer, limit=100)
             if (entry.meta or {}).get("kind") == kind
             and kind == "conversation_event"
             and cand.get("topic")
             and (entry.meta or {}).get("topic") == cand.get("topic")),
            None,
        )
        if kind == "conversation_event":
            days = max(0, min(7, int(cand.get("follow_up_days", 3))))
            expires_at = cand.get("expires_at") or ""
            if cand.get("status") == "active" and not expires_at and days:
                expires_at = (datetime.datetime.now(datetime.timezone.utc)
                              + datetime.timedelta(days=days)).isoformat(timespec="seconds")
            if cand.get("status") != "active":
                expires_at = ""
            previous_ids = list((existing_event.meta if existing_event else {}).get("source_message_ids") or [])
            source_ids = list(dict.fromkeys(previous_ids + [source_id]))
        else:
            expires_at = cand.get("expires_at")
            source_ids = [source_id]
        # meta 透传扩展字段（MEMORY_SPEC 5 候选契约）
        meta = {
            "kind": kind,
            "source_message_id": cand.get("source_message_id"),
            "source_message_ids": source_ids,
            "subject": cand.get("subject", "user"),
            "event_time": cand.get("event_time"),
            "emotion": cand.get("emotion"),
            "expires_at": expires_at,
            "status": cand.get("status", "active"),
            "topic": cand.get("topic"),
            "intent": cand.get("intent"),
            "follow_up_days": cand.get("follow_up_days"),
            "needs_confirmation": bool(cand.get("needs_confirmation", False)),
            "correction": bool(cand.get("correction", False)),
            # P-1（PERSONA_LOOP_SPEC）：persona 候选透传字段
            "title": cand.get("title"),
            "scope": cand.get("scope"),
            "stability": cand.get("stability"),
            "emotional_weight": cand.get("emotional_weight"),
            "user_confirmed": bool(cand.get("user_confirmed", False)),
            "role_compatible": bool(cand.get("role_compatible", True)),
            "evidence_message_ids": cand.get("evidence_message_ids"),
        }
        meta = {k: v for k, v in meta.items() if v is not None and v is not False}
        # 去重：同层已有高度重叠记忆
        existing = self.memory.list_layer(layer, limit=50)
        for e in existing:
            if kind == "conversation_event" and existing_event is not None and e.id != existing_event.id:
                continue
            sim = self._text_similarity(content, e.content)
            if sim >= 0.92 and not cand.get("correction") and not (
                kind == "conversation_event" and existing_event is not None
            ):
                logger.debug("candidate dup ignored (sim=%.2f): %s", sim, content[:30])
                return
            if (kind == "conversation_event" and existing_event is not None and e.id == existing_event.id) or sim >= 0.78 or cand.get("correction"):
                # 保留新版本，旧版本入链（R1_SPEC 3：meta.supersedes=old_id）
                # P-1：人格 kind 二次确认 → stability 提升（cap 1.0）
                merged = {**e.meta, "supersedes": e.id, **meta}
                if kind in ("user_framework", "character_belief", "shared_meaning") and isinstance(e.meta.get("stability"), (int, float)):
                    merged["stability"] = min(1.0, float(e.meta["stability"]) + 0.1)
                new = self.memory.update_latest(
                    e.id, content, confidence=float(cand.get("confidence", 0.7)), meta=merged,
                )
                logger.info("R1 memory versioned: old=%s new=%s", e.id, new.id)
                return
        self.memory.store(
            layer, content,
            importance=float(cand.get("importance", 0.6)),
            confidence=float(cand.get("confidence", 0.7)),
            meta=meta,
        )
        logger.info("R1 memory stored: %s → %s", kind, content[:40])

    @staticmethod
    def memory_store_layer(kind: str) -> str:
        """R1 类型 → 旧 layer（与 store.LAYER_R1_MAP 一致，集中在此避免双份映射）。"""
        from ..memory.store import LAYER_R1_MAP
        return LAYER_R1_MAP.get(kind, kind)

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """文本相似度（0-1）：去重用轻量近似（ponytail: 未用 embedding 余弦，
        长文本重叠场景够用；若误去重明显再换向量相似度）。"""
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio()

    def proactive_from_visual(self, tag: str, matched_memory: str = "") -> str:
        """R4 联想式主动发起（R4_SPEC 1.三段式决策）。

        屏幕 focus.tag × 事件记忆模糊匹配：episodic 层含 tag 关键词 → 生成联想消息。
        无匹配 / 模型不可用 → 返回 ""。
        """
        if not tag or self.state.energy < 30:
            return "", ""
        old = str(matched_memory or "").strip()[:120]
        if not old:
            # 兼容旧调用；生产视觉链会把混合 recall 的命中直接传入。
            try:
                hits = [
                    e for e in self.memory.list_layer("episodic", limit=20)
                    if tag in (e.content or "")
                ]
            except Exception:
                hits = []
            if not hits:
                return "", ""
            old = hits[-1].content[:120]
        task = (
            f"你看到用户正在{tag}（屏幕焦点）。突然想起一件旧事：\"{old}\"。"
            f"给用户发一条消息，自然地提起这件事（可以带'咦''说起来'这类开头），"
            "顺便问问现在的情况。只发消息本身。"
        )
        try:
            reply, ja = self._short_task(task, max_tokens=1024, bilingual=True)
        except Exception as e:
            logger.warning("proactive_from_visual failed: %s", e)
            return "", ""
        if not reply:
            return "", ""
        self.record_proactive_message(reply, channel="pet")
        return reply, ja

    def _visual_match_episode(self, tag: str) -> bool:
        """R4：注意力候选必须匹配共同经历（R4_SPEC 3 attention=低）。

        视觉只提供候选；无共同经历匹配时 gate 拦截（R4_SPEC 2 第 8 条）。
        """
        if not tag:
            return False
        try:
            hits = [
                e for e in self.memory.list_layer("episodic", limit=20)
                if tag in (e.content or "")
            ]
            return bool(hits)
        except Exception:
            return False

    def seamless_greeting(self) -> str:
        """R1 无缝衔接（R1_SPEC 4.召回）：用户回到电脑前 → 从共享历史接续最近话题。

        取最近对话（跨通道共享），生成「你刚才说…」衔接语；无历史/低精力返回 ""。
        """
        if self.state.energy < 30:
            return "", ""
        recent = self.memory.recent_messages(limit=6)
        # 找最近一条用户消息（可能是 QQ 通道的）
        last_user = ""
        for m in reversed(recent):
            if m.get("role") == "user":
                last_user = self._message_context_line(m)[:120]
                break
        if not last_user:
            return "", ""
        task = (
            f"用户刚回到电脑前。他之前在别的端说过：\"{last_user}\"。"
            "发一条简短的衔接语，自然地提起这件事（比如'你刚才说的那个…后来怎么样了？'）。"
            "只说这一句，不要展开。"
        )
        try:
            reply, ja = self._short_task(task, max_tokens=1024, bilingual=True)
        except Exception as e:
            logger.warning("seamless_greeting failed: %s", e)
            return "", ""
        if not reply:
            return "", ""
        self.record_proactive_message(reply, channel="pet")
        return reply, ja

    def task_result_story(self, result: dict) -> str:
        """R5 任务结果角色化转述（R5_SPEC 3.生命周期：超时/失败 → 角色化转述）。

        成功：简要转述结果要点；失败：角色化说明（「那事我让助手去办了，它说卡住了」）。
        无 LLM（mock/未配置）→ 返回原始结果文本（不丢信息）。
        """
        output = str(result.get("output") or "").strip()
        ok = bool(result.get("ok"))
        task_id = str(result.get("task_id") or "")
        if not output:
            return "（任务没有任何输出……估计是没跑起来，我再看看）"
        if not ok:
            # 失败文案无需 LLM：固定角色化（R5_SPEC 3「那事我让助手去办了，它说卡住了」）
            return f"那事我让助手去办了，它说卡住了（{task_id}）。我再看看怎么回事。"
        if self.llm is None or not getattr(self.llm, "base_url", ""):
            return output  # 无 LLM 直接给原文（成功场景不丢信息）
        try:
            task = (
                f"你让桌面助手处理的任务（{task_id}）完成了。助手的结果原文：\n{output[:800]}\n"
                "请用你的口吻向用户转述：简短说结果要点。别用'助手'这个词太多次，"
                "像你自己经手办的一样。最多 3 句。"
            )
            reply = self._short_task(task, max_tokens=512)
            return reply or output
        except Exception as e:
            logger.warning("task_result_story failed: %s", e)
            return output

    def forget(self, keyword: str) -> int:
        """隐私擦除：删除包含关键词的记忆（级联）。"""
        n = self.memory.erase(content_contains=keyword)
        logger.info("forget '%s': %d memories erased", keyword, n)
        return n

    def review_memory(self, review_id: int, approve: bool) -> bool:
        """MEMORY_BACKEND_EVAL M-D：批准 → 走既有校验/去重写入；拒绝 → 丢弃。"""
        items = {item["id"]: item for item in self.memory.list_review(include_decided=True)}
        item = items.get(review_id)
        if item is None or not self.memory.decide_review(review_id, approve):
            return False
        if not approve:
            return True
        cand = item["candidate"]
        # 用户亲自确认：置信度视为最高；来源标记为 manual（人工批准）
        cand["confidence"] = 1.0
        cand["source"] = "manual"
        try:
            before = len(self.memory.list_layer("semantic", limit=1000, include_superseded=True))
            self._store_candidate(cand)
        except Exception as e:
            logger.warning("review approve store failed: %s", e)
            return False
        logger.info("review approved and stored: id=%s kind=%s", review_id, cand.get("kind"))
        return True

    # ---------- MEMORY_BACKEND_EVAL M-C：夜间整理（kiwi-mem Dream 借鉴） ----------

    def maybe_nightly_digest(self, *, min_episodes: int = 3) -> dict:
        """把当天新积累的情节片段整理成上层摘要；当日已生成则跳过。

        - 素材：**当日新增**的 episodic current 记忆（含来源消息 ID），
          且排除任何已被历史 digest 引用过的来源消息（防重复整理同批材料——
          DESIGN 用户 2026-08-30 指出 0827/0826 整理内容完全一样）
        - 摘要走 ADD-only 候选校验入库（shared_meaning 层，带 digest_date + 来源）
        - 被摘要原始片段降权（strength×0.5），不删除
        - LLM 输出非 JSON/为空 → created=False，不写任何内容
        """
        import datetime
        import json as _json

        if self.llm is None or not getattr(self.llm, "base_url", ""):
            return {"created": False, "reason": "no_llm"}
        today = datetime.date.today().isoformat()
        already = self.memory.con.execute(
            "SELECT count(*) FROM memories WHERE json_valid(meta) AND json_extract(meta,'$.digest_date')=?",
            (today,),
        ).fetchone()[0]
        if already:
            return {"created": False, "reason": "already_digested_today"}
        # 当日 0 点（UTC）起的新增 episodic——夜间整理只整理当天的事
        day_start = datetime.datetime.now(datetime.timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
        episodes = [
            e for e in self.memory.list_layer("episodic", limit=200)
            if e.created_at >= day_start
        ]
        if len(episodes) < min_episodes:
            return {"created": False, "reason": "not_enough_material", "episodes": len(episodes)}
        # 排除已被任何历史 digest 引用过的来源消息（防跨日重复整理同批片段）
        used = {
            int(sid)
            for (row,) in self.memory.con.execute(
                "SELECT json_each.value FROM memories, json_each(meta->'$.source_message_ids') "
                "WHERE json_valid(meta) AND meta LIKE '%digest_date%'"
            ).fetchall()
            for sid in [row]
        }
        episodes = [
            e for e in episodes
            if not any(sid in used for sid in ((e.meta or {}).get("source_message_ids") or []))
        ]
        # 过滤后仍不足则视为无新素材（宁可跳过也不重复整理旧材料）
        if len(episodes) < min_episodes:
            return {"created": False, "reason": "no_new_material", "episodes": len(episodes)}
        lines = [f"- {e.content}（来源消息：{', '.join(map(str, (e.meta or {}).get('source_message_ids') or []))}）"
                 for e in episodes[:10]]
        task = (
            f"以下是用户近几天的情节片段，请整理成一段客观概括（不超过 80 字），"
            "如果从中能看出用户的行为规律（深夜才来发消息、反复回到同一话题、"
            "提到某事时情绪明显变化），在概括末尾用一句点出（例：' ta 好像总在深夜聊游戏'）；"
            f"看不出规律就不要编。只输出 JSON：{{\"content\":\"概括\"}}。\n{chr(10).join(lines)}"
        )
        try:
            # 与 _short_task 相同的 system 锚定，但保留原始输出（JSON 协议，
            # 不走 IM parse_reply——它会把结构化输出解析成空文本）
            system = build_system_prompt(self.card, self.state, self.memory) + "\n" + self._time_context_instruction()
            raw = self.llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": task},
                ],
                max_tokens=256,
            )
        except Exception as e:
            logger.warning("nightly digest llm failed: %s", e)
            return {"created": False, "reason": "llm_failed"}
        content = ""
        try:
            data = _json.loads((raw or "").strip())
            content = str(data.get("content") or "").strip()
        except _json.JSONDecodeError:
            content = ""
        if not content:
            return {"created": False, "reason": "bad_output"}
        source_ids = sorted({sid for e in episodes for sid in ((e.meta or {}).get("source_message_ids") or [])})
        if not source_ids:
            return {"created": False, "reason": "no_source_messages"}
        cand = {
            "kind": "shared_meaning",
            "content": f"[{today} 夜间整理] {content}",
            "source_message_id": source_ids[0],
            "confidence": 0.9,
            "subject": "user",
            "digest_date": today,
            "source_message_ids": source_ids,
            "source": "agent_confirmed",
        }
        from ..memory.store import validate_candidate
        if validate_candidate(cand):
            return {"created": False, "reason": "invalid_candidate"}
        # 直接走 store（绕过收件箱阈值——digest 自身置信度固定且带完整来源）
        layer = self.memory_store_layer(cand["kind"])
        self.memory.store(layer, cand["content"], confidence=0.9, meta={
            "kind": cand["kind"], "subject": "user", "digest_date": today,
            "source_message_id": source_ids[0] if source_ids else None,
            "source_message_ids": source_ids,
        })
        # 原始片段降权（不删除；M-A 强度信号使其自然下沉）
        for e in episodes:
            self.memory.con.execute(
                "UPDATE memories SET strength=? WHERE id=? AND layer!='core_profile'",
                (max(0.05, float(e.strength) * 0.5), e.id),
            )
        self.memory.con.commit()
        logger.info("nightly digest stored (%d episodes -> summary)", len(episodes))
        return {"created": True, "episodes": len(episodes)}


    def status(self) -> dict:
        return {
            **self.state.summary(),
            "history_len": len(self._history),
            "memory_counts": self.memory.curate().get("counts", {}),
        }

    def persona_proactive_blocked(self, source: str) -> bool:
        """P-7：人格来源主动闸门（PERSONA_LOOP_SPEC 11 额外闸门）。

        - shared_meaning/open_tension/reflection：未闭合冲突时禁止（不加重关系压力）
        - boundary_held 存在时：除 commitment 外一律禁止
        - commitment 永不因冲突禁止（承诺提醒是任务不是情绪负担）
        """
        heavy = ("shared_meaning", "open_tension", "reflection", "shared_project", "ritual")
        open_c = self._conflicts.open_conflicts()
        if not open_c:
            return False
        if any(c["status"] == "boundary_held" for c in open_c):
            return source != "commitment"
        return source in heavy

    def tick_proactive(self, now=None, *, commit: bool = True, persist: bool | None = None) -> list[str]:
        """定时问候 + 节庆纪念检查（每日去重）。

        返回本次应发送的主动消息列表（已入档 memory）；adapter（CLI/QQ）
        负责展示/发送。供后台 tick 线程调用，幂等。``now`` 注入便于测试。

        R4：统一主动入口——ProactiveCandidate(ritual) → ProactiveGate 9 闸门（R4_SPEC 1/2）。
        """
        # P-7：未闭合冲突时禁止 ritual 主动（不加重关系压力）
        if self.persona_proactive_blocked("ritual"):
            return []
        self._sync_user_asleep()  # 三餐/问候/轻提示都查 user_asleep——共享单行下必须读真值
        msgs: list[str] = []
        # gate.decide 需要 epoch 秒（now 可能是 datetime 注入，转 timestamp）
        now_ts = now.timestamp() if isinstance(now, datetime.datetime) else now
        cand = ProactiveCandidate(
            source="ritual", reason="定时问候/节庆纪念",
            relevance=0.9, urgency=0.5, intent="share",
            context={"calendar_source": "greeter/occasion"},
            channel="qq",  # 闸门分桶路由（PC 与安卓都走 qq 桶，安卓该桶已 gen_config 归零）；落库标签见下
        )
        # 场景/日上限/静默时段（gate）仍然整体拦截：不放行时收集都不做，
        # 去重键不消耗（defer 语义）。
        decision = self.gate.decide(
            cand, scene=self.scene_lock.current(),
            now=now_ts,  # 测试注入；生产传真实时间
        )
        if not decision.allow:
            return []
        # 三餐锚点按用户作息调整（2026-08-30 用户拍板）：从最近睡眠周期推导起床时间
        try:
            wake_hour = self._user_wake_hour()
            self.meals.adjust_to_user_cycle(wake_hour)
        except Exception as e:
            logger.debug("meal anchor adjust failed: %s", e)

        # ---- 触发源清单求值（proactive.RITUAL_SOURCES）：全表收集到期素材进
        # 待织池，合并窗口一开就把池里所有素材织成一条语义连续的消息发出
        # （2026-09-01 用户裁决 v2：连发几条不是问题，语义接不上才是；
        #  睡醒公告后一小时才单发作息公告的因果脱节=素材攒到下条消息里说）----
        # 去重键在收集时消费：素材一经产出必进某条消息（编织失败回退分段
        # 拼接），唯一例外=进程在窗口期内存活不过 TTL 崩溃丢池（可接受，
        # 主动消息非交易数据）。
        materials: list[dict] = []

        def _collect_greeting():
            slot = self.greeter.due_greeting(now=now)
            return [{"source": "greeting", "text": self.greeting_message(slot)}] if slot else []

        def _collect_context_probe():
            # 联想 B 类（2026-09-01 设计）：角色闲着、用户 2-6 小时没动静 →
            # 基于时间/最近话题推测 TA 此刻在干嘛。素材进待织池：单独到点是
            # 完整一条，撞上别的素材则被织进同一条消息（天然不刻意）。
            probe = self._context_probe(now)
            return [{"source": "context_probe", "text": probe}] if probe else []

        def _collect_sleep_hint():
            hint = self._missing_sleep_report_hint(now)  # 26h 无作息报告轻提示（日一次）
            return [{"source": "sleep_hint", "text": hint}] if hint else []

        def _collect_occasion():
            occasion = self.occasion.due_occasion(self.memory, now=now)
            if not occasion:
                return []
            return [{"source": "occasion",
                     "text": self.occasion.occasion_reaction(occasion, self.card.name)}]

        def _collect_schedule_adapt():
            # 偏移立即执行（作息适应不依赖消息发送）；理由素材交编织器
            out: list[str] = []
            self._adapt_schedule_to_user(wake_hour, now, out)
            return [{"source": "schedule_adapt", "text": t} for t in out]

        def _collect_meal():
            # 依恋 <0.4 不发（"自己人"行为）；用户睡眠中不发；meal.due 当日各餐去重。
            # 问候窗口内饭点整体让位（2026-08 双发修复不变式）：「中午好吃过饭了吗」
            # 与「到饭点了」不叠发同一话题位——窗口内到点的餐顺延到窗口外再提醒。
            if (self.greeter.slot_at(now) is not None
                    or self.state.user_asleep or self.state.attachment < 0.4):
                return []
            meal_sent_ids = {
                str(row.get("candidate_id") or "")
                for row in self.memory.recent_proactive_feedback(source="meal", limit=30)
            }
            due = self.meals.due(now=now, sent_ids=meal_sent_ids)
            if not due:
                return []
            meal_name, meal_text, meal_cid = due
            self.memory.record_proactive_feedback(
                source="meal", channel=self.message_channel, candidate_id=meal_cid)
            return [{"source": "meal", "text": meal_text, "meal": meal_name}]

        collectors = {
            "greeting": _collect_greeting,
            "context_probe": _collect_context_probe,
            "sleep_hint": _collect_sleep_hint,
            "occasion": _collect_occasion,
            "schedule_adapt": _collect_schedule_adapt,
            "meal": _collect_meal,
        }
        for source_name in RITUAL_SOURCES:  # 清单顺序=素材排列顺序（织入时的话题先后）
            try:
                materials.extend(collectors[source_name]())
            except Exception as e:
                logger.debug("ritual source %s failed: %s", source_name, e)

        # 攒池 → 窗口开闸织发（ponytail: 池仅存内存；崩了丢当窗素材，
        # 升级路径=池随 agent_state 持久化，出现可观测丢信投诉再做）
        pending = self._ritual_pending
        ref_ts = (now if isinstance(now, datetime.datetime)
                  else datetime.datetime.fromtimestamp(now or time.time())).timestamp()
        pending[:] = [m for m in pending if ref_ts - m["ts"] < 180 * 60]  # TTL：早招呼不拖到午后
        # 角色主动消息类型白名单（P3 设置：空=全放行；非空=RITUAL_SOURCES 子集）
        _allow = ((getattr(self, "moments", None) and
                   self.moments.settings().get("proactive") or {}).get("allowed_types")
                  if hasattr(self, "moments") else None) or []
        if _allow:
            materials = [m for m in materials if m.get("source") in _allow]
        pending.extend({**m, "ts": ref_ts} for m in materials)
        if pending and self._ritual_send_open(now):
            pool, self._ritual_pending[:] = list(pending), []
            if len(pool) == 1 and pool[0]["source"] == "meal":
                # 单条饭点走原有口语化改写
                msgs.append(self._meal_message(pool[0]["meal"], pool[0]["text"]))
            else:
                woven = self._weave_ritual([m["text"] for m in pool])
                if woven:
                    msgs.append(woven)
        # ---- 清单求值结束 ----
        if not (persist is False) and msgs:
            for msg in msgs:
                self.record_proactive_message(msg, channel=self.message_channel, now=now)
                # 问句记期待（追问闭环的燃料；QQ 路径走自己的 _record_qq_expectation）
                try:
                    self.record_proactive_expectation(msg, source="ritual", channel=self.message_channel)
                except Exception as e:
                    logger.debug("record expectation failed: %s", e)
            # 问候/节庆去重键随状态落库（2026-08-31 修复：不落库=重启清零重发）
            self._persist_state()
        if msgs and commit:
            self.gate.commit(cand)
        return msgs

    def record_proactive_message(self, text: str, *, channel: str | None = None,
                                 now=None) -> None:
        """发送成功后写入主动 assistant 消息，避免发送失败污染历史。

        channel=None → 用 self.message_channel（安卓 device-config 标 im）。
        同时更新问候族合并窗口起点（所有主动源共享的唯一记账点）。
        """
        channel = channel or self.message_channel
        self.memory.store_message("assistant", text, self.state.energy, self.state.mood, channel=channel, role_id=self.role_key)
        self._append_history_message("assistant", text)
        self._mark_proactive_sent(now)  # 测试注入同一时间线；生产 None=真实时刻

    def _ritual_send_open(self, now=None) -> bool:
        """待织池的发送闸：合并窗口开 + 最近 5 分钟没有用户消息（刚聊完不插话，
        2026-08-31「醒了」三连发教训）。不满足=素材继续攒池，下一 tick 再判。"""
        if not self.proactive_merge_open(now):
            return False
        try:
            for row in reversed(self._recent_msgs(limit=5)):
                if row.get("role") == "user":
                    seen = self._naive_local(datetime.datetime.fromisoformat(
                        str(row.get("created_at")).replace("Z", "+00:00")))
                    ref = (now if isinstance(now, datetime.datetime)
                           else datetime.datetime.fromtimestamp(now or time.time()))
                    return abs((self._naive_local(ref) - seen).total_seconds()) >= 5 * 60
                break
        except Exception as e:
            logger.debug("ritual user-active check failed: %s", e)
        return True

    def _weave_ritual(self, texts: list[str]) -> str:
        """把多条到期素材织成一条语义连续的主动消息（2026-09-01 用户裁决 v2）。

        素材=各触发源的成品/半成品文本；LLM 负责串成一段自然的话，信息一条不丢、
        因果顺接（睡醒→作息调整→吃饭这种链条在一条里说完成立）。LLM 失败=回退
        分段拼接（语义连续性降级，但信息零丢失——宁可不美不能丢）。"""
        if not texts:
            return ""
        if len(texts) == 1:
            return texts[0].strip()
        task = (
            "你手上同时有几件想对用户说的事（都成立、都不能丢）：\n"
            + "\n".join(f"{i + 1}. {t.strip()}" for i, t in enumerate(texts))
            + "\n把它们合成一条自然连贯的消息，像一个人的连续口吻一次说完："
              "有事由和先后，用『对了』『顺便』『正好』这类过渡把话题串起来，"
              "不要编号、不要分段并列、不要漏掉任何一件事的内容。只发消息本身。"
        )
        woven = ""
        # 素材多条→思考量更大：首试默认预算，被截断则加倍重试一次（仍败=拼接回退）
        for budget in (None, 2048):
            try:
                woven = (self._short_task(task, max_tokens=budget) or "").strip()
            except Exception as e:
                logger.debug("ritual weave llm failed (budget=%s): %s", budget, e)
                woven = ""
            if woven:
                break
        return woven or "\n\n".join(t.strip() for t in texts if t.strip())

    @staticmethod
    def _naive_local(dt_value):
        """datetime → 朴素本地时刻（库内 _now() 存的是带时区 ISO；注入的多为朴素本地。
        双向归一到同一时间制再比较，杜绝 aware/naive 相减的隐性错位。"""
        if dt_value.tzinfo is not None:
            return dt_value.astimezone().replace(tzinfo=None)
        return dt_value

    def _mark_proactive_sent(self, now=None) -> None:
        stamp = now or datetime.datetime.now(datetime.timezone.utc)
        if isinstance(stamp, (int, float)):
            stamp = datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc)
        self._last_proactive_sent_at = stamp.astimezone().isoformat(timespec="seconds")

    def proactive_merge_open(self, now=None) -> bool:
        """问候族合并窗口（2026-09-01 用户反馈：07:09 睡醒公告与 07:11 时段问候
        两条"早安"背靠背）：任何一条问候族消息发出后的窗口期内，其余问候族
        触发源一律让位。窗口 = proactive.merge_window_minutes（默认 60，0=关）。
        """
        import datetime as _dt
        try:
            window = int((self.config.get("proactive") or {}).get("merge_window_minutes", 60))
        except (TypeError, ValueError):
            window = 60
        last = str(getattr(self, "_last_proactive_sent_at", "") or "")
        if window <= 0 or not last:
            return True
        try:
            then = _dt.datetime.fromisoformat(last)
            if then.tzinfo is None:
                then = then.astimezone()  # 朴素值=本地钟（全库统一约定）
            ref = now or _dt.datetime.now(_dt.timezone.utc)
            if isinstance(ref, _dt.datetime) and ref.tzinfo is None:
                ref = ref.astimezone()
            return abs((ref - then).total_seconds()) >= window * 60
        except (TypeError, ValueError):
            return True

    def record_proactive_expectation(self, text: str, *, source: str,
                                     channel: str = "im", candidate_id: str = "") -> None:
        """主动消息含直接问句 → 记一条待回复期待（过期由 followup_message 消费）。"""
        from .tension_events import extract_direct_question
        import datetime
        question = extract_direct_question(text or "")
        if not question:
            return
        window = 24.0
        try:
            window = float(self.tension.UNANSWERED_REPLY_WINDOW_HOURS)
        except Exception:
            pass
        self.memory.record_proactive_feedback(
            source=source, channel=channel, candidate_id=candidate_id or "proactive",
            requires_reply=True, direct_question=question,
            expires_at=(datetime.datetime.now(datetime.timezone.utc)
                        + datetime.timedelta(hours=window)).isoformat(timespec="seconds"),
        )

    def followup_message(self, now=None) -> str:
        """无人应答闭环（design_append §3/§5 最小落地），每轮 tick 调一次：

        1) pending 且过期 → 原子结算 expired + TV（QQ _expire_qq_expectations
           的 core 等价物，dedupe_key 保证不重复计）；
        2) expired 未回且没追问过 → 一句 ≤15 字轻追问（'asked' 原子占坑，只一次；
           追问后再石沉大海即终，不再叠）。返回空串=本轮无事。
        """
        import datetime
        now = now or datetime.datetime.now().astimezone()
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        if not self.proactive_merge_open(now):
            return ""  # 问候族合并窗口（2026-09-01）：刚发过别的主动消息，追问排队
        try:
            rows = self.memory.recent_proactive_feedback(limit=100)
        except Exception:
            return ""
        due = []
        for row in rows:
            if (row.get("channel") or "").lower() not in ("im", "qq") or not row.get("requires_reply"):
                continue
            if int(row.get("responded") or 0):
                continue
            try:
                expires = datetime.datetime.fromisoformat(str(row.get("expires_at")).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=datetime.timezone.utc)
            status = str(row.get("expectation_status"))
            if status == "pending" and now >= expires:
                if self.memory.expire_proactive_expectation(row["id"]):
                    self._apply_tension_event(
                        event_type="unanswered_proactive", channel=row.get("channel") or "im",
                        base_delta=10, reason="主动问题在回复窗口内没有得到回应",
                        dedupe_key=f"proactive-unanswered:{row['id']}",
                        confidence=1.0, evidence_message_ids=(),
                        related_candidate_id=row.get("candidate_id") or None,
                        occurred_at=now,
                    )
                status = "expired"
            if status == "expired" and not row.get("followup_status"):
                due.append((row, max(0, int((now - expires).total_seconds() // 3600))))
        if not due:
            return ""
        row, overdue_h = due[-1]
        # 原子占坑：同轮 tick 重复调用不双发（expire_proactive_expectation 同款 CAS）
        cur = self.memory.con.execute(
            "UPDATE proactive_feedback SET followup_status='asked' "
            "WHERE id=? AND (followup_status IS NULL OR followup_status='')",
            (row["id"],),
        )
        self.memory.con.commit()
        if cur.rowcount != 1:
            return ""
        try:
            task = (
                f"你在 {row.get('sent_at')} 说过：「{(row.get('direct_question') or '')[:80]}」，"
                f"已经过了大约 {overdue_h} 小时用户没回应。发一句非常短的追问/试探，"
                "≤15 字，符合当前心情，别责备别阴阳。"
                "要半显式地让人听出来你在等上一条回复（口语里自然带出"
                "「刚才那句」「你还没答我」这层意思即可，不点破具体问了什么），"
                "像顺口招呼一声，不是催债。"
            )
            text = self._short_task(task, max_tokens=120) or ""
        except Exception as e:
            logger.debug("followup llm failed: %s", e)
            text = ""
        if not text:
            self.memory.con.execute(
                "UPDATE proactive_feedback SET followup_status='' WHERE id=?", (row["id"],))
            self.memory.con.commit()
            return ""
        self.record_proactive_message(text, channel=row.get("channel") or "im")
        return text

    def heartbeat(self, *, commit: bool = True) -> str:
        """R4 后台心跳（R4_SPEC 1）：对话已闭合 + 静默 → 主动发起破冰。

        与 late_reply 互补：late_reply 要求对话未闭合（有没回完的话），
        心跳要求已闭合（无事发生太久，主动找话说）。返回空串=不触发。

        触发约束：
        - 最近一条是 assistant（对话闭合）；用户刚说话则不触发
        - 过仲裁器（场景 normal + 他通道不活跃 + idle 冷却/日上限）
        - LLM 生成「离线整理」破冰；失败降级模板池
        """
        # R4 闸门：scene 衔接（R4_SPEC 3 scene=低：场景结束后衔接，不打扰）
        cand = ProactiveCandidate(
            source="scene", reason="对话闭合后破冰衔接",
            relevance=0.7, urgency=0.4, intent="bridge",
            context={"closed_dialogue": True},
            channel="qq",
        )
        if not self.gate.decide(
            cand, scene=self.scene_lock.current(),
        ).allow:
            return ""
        # 问候族合并窗口（2026-09-01）：刚发过任何问候族消息 → 破冰让位
        if not self.proactive_merge_open():
            return ""
        recent = self._recent_msgs(limit=8)
        if not recent or recent[-1]["role"] != "assistant":
            return ""  # 用户刚说完话或有未闭合对话，不需要破冰
        if getattr(self.llm, "is_model_loaded", None) is not None and self.llm.is_model_loaded():
            try:
                ctx = ("最近的对话：\n" + "\n".join(
                    self._message_context_line(m)[:120] for m in recent[-4:]
                )) if recent else ""
                # 翻旧账接线（_dig_old_memory 原为孤儿函数）：给 LLM 一条旧事素材；
                # 以最后一条用户消息为话题线索做弱关联挖掘（缺口②）
                last_user_text = next((m["content"] for m in reversed(recent) if m["role"] == "user"), "")
                dug = self._dig_old_memory(topic_hint=last_user_text)
                dig_hint = ""
                if dug:
                    dig_text, dig_conf = dug
                    hedge = "（你有点记不太清了，可以带点不确定的口气）" if dig_conf < 0.7 else ""
                    dig_hint = f"\n（你刚想起一条旧事：{dig_text}）{hedge}"
                task = (
                    f"{ctx}{dig_hint}\n\n你刚在整理聊天记录（离线成长），想跟用户说点什么破冰。"
                    "自然带出一点整理时的发现（他之前提过的事/你记住的细节；"
                    "如果记忆里有点到你小习惯的规律，可以用'我注意到你总是…'这样被看穿的语气，一次别超过一点）。"
                    "像平时聊天一样自然，长度随意。不要用「欢迎回来」这类生硬话。"
                )
                reply = self._short_task(task, max_tokens=1024)
                if reply:
                    self.record_proactive_message(reply)
                    if commit:
                        self.gate.commit(cand)
                    return reply
            except Exception as e:
                logger.debug("heartbeat LLM failed, fallback to template: %s", e)
        # 降级：模板池
        pool = [
            "（刚在整理聊天记录）上次你说那事，后来有后续了吗？",
            "刚闲着没事翻了翻咱俩的聊天记录，发现你之前念叨的东西挺多的……最近都还好吗？",
            "（离线整理完毕）我突然想起你上次说的那个计划，后来怎么样了？",
        ]
        reply = pool[0]
        self.record_proactive_message(reply)
        if commit:
            self.gate.commit(cand)
        return reply

    def late_reply(self, *, commit: bool = True) -> str:
        """8.7.4 离线思考：静默一段时间后，针对之前话题发一条"迟来的回应"。

        仅 QQ 形态启用（CLI 不调用——用户可能只是离开，主动消息=打扰）。
        返回空串表示本次不触发；成功则已入档 memory 并追加历史。

        触发约束（2026-08 修复夜间轰炸）：
        - 对话必须未闭合：最近一条消息必须是 user（bot 还没回应完），
          若最后一条是 assistant（对话已闭合/自问自答）则不触发；
        - 低精力不触发（energy < 30）；
        - LLM 降级模板池：排除最近已发过的模板，避免同一条连发刷屏。
        """
        if self.state.energy < 30:
            return ""
        # R4 闸门：shared_episode 来源（针对之前话题，R4_SPEC 3 中）
        cand = ProactiveCandidate(
            source="shared_episode", reason="迟来的回应（未闭合对话）",
            relevance=0.75, urgency=0.5, intent="check_in",
            context={},
            channel="qq",
        )
        if not self.gate.decide(
            cand, scene=self.scene_lock.current(),
        ).allow:
            return ""
        # 对话闭合检查：最后一条必须是 user 消息（有未回应完的内容）
        recent = self._recent_msgs(limit=8)
        if not recent or recent[-1]["role"] != "user":
            return ""
        if getattr(self.llm, "is_model_loaded", None) is not None and self.llm.is_model_loaded():
            try:
                user_msgs = [self._message_context_line(m)[:120] for m in reversed(recent) if m["role"] == "user"]
                if user_msgs:
                    last = user_msgs[0]
                    task = (
                        f"{last}。但你现在才空下来，"
                        "想补一条迟来的回应。自然提起这件事，补充想法或表达关心。"
                        "像平时聊天一样自然，长度随意。"
                    )
                    reply = self._short_task(task, max_tokens=1024)
                    if reply:
                        self.record_proactive_message(reply)
                        if commit:
                            self.gate.commit(cand)
                        return reply
            except Exception as e:
                logger.debug("late reply LLM failed, fallback to template: %s", e)
        # 降级：模板池（排除最近已出现过的模板，防止同一条连发）
        pool = [
            "（想起刚才的事）你之前说的那件事，我后来想了想，觉得你说得有道理。",
            "刚在发呆，突然想到你之前说的话。没事，就是想告诉你我在听。",
            "（突然想起来）对了，你之前提过的那件事，后来怎么样了？",
            "安静了一会儿，忽然想到你之前说的话，想告诉你我在想这件事。",
        ]
        used = {m["content"] for m in recent if m["role"] == "assistant"}
        candidates = [p for p in pool if p not in used]
        if not candidates:
            return ""
        msg = random.choice(candidates)
        self.record_proactive_message(msg)
        if commit:
            self.gate.commit(cand)
        return msg

    # ---------- 内部 ----------

    def _classify_tone(self, text: str) -> str:
        """P2 逐条回复情绪分类：一次低成本 LLM 调用（≤256 token、8s 超时）。

        词表=角色卡 tones；失败/超时/词表外返回空串（UI 回退 mood 三档）。
        绝不阻塞主回复：调用方在回复生成后追加执行，失败静默。
        """
        ui = (self.config.get("ui") or {})
        if not ui.get("emotion_tags", True):
            return ""
        tones = list((self.card.tones or ["中性", "平静", "温柔"])[:19])
        prompt = (
            "给下面这句角色台词标一个情绪标签，只能从词表里选一个词，"
            "只输出 JSON：{\"tone\":\"词表内一词\"}。\n"
            f"词表：{'、'.join(tones)}\n台词：{str(text)[:300]}"
        )
        try:
            raw = self.llm.chat_structured(
                [{"role": "user", "content": prompt}],
                max_tokens=128,
                temperature=0.2,
            )
            value = json.loads(str(raw).strip().strip("`").removeprefix("json").strip())
            t = str(value.get("tone", "")).strip()
            return t if t in tones else ""
        except Exception as e:
            logger.debug("tone classify failed (fallback mood): %s", e)
            return ""

    def _short_task(self, task: str, max_tokens: int | None = None, bilingual: bool = False) -> str | tuple[str, str]:
        """短任务生成：带完整 system prompt（角色锚定）。

        实测（2026-08，qwen3-8b）：thinking 模型对裸 user prompt 的短任务
        会把全部 token 预算耗在 reasoning 上（≤80 token 必空；512 也常跑偏），
        带完整 system prompt 后 thinking 收敛、输出正常角色化回复。
        空/异常由调用方回退模板。

        max_tokens=None → 读配置 llm.short_task_max_tokens（默认 1024）；
        不得传 120/200 这类小预算（R0_SPEC 6：思考模型预算不足只剩残留）。
        2026-08-31 实测教训：followup 传 120 → reasoning 烧空预算，29 字符半截
        JSON 被容错解析抠出残句直接发出（"……您晚饭，吃"）。小预算一律抬到下限。
        bilingual=True：任务要求输出双语 JSON（ja 台词/zh 翻译），返回
        (zh, ja)；非双语返回纯文本字符串。
        """
        floor = int((self.config.get("llm", {}) or {}).get("short_task_max_tokens", 1024))
        if max_tokens is None:
            max_tokens = floor
        else:
            try:
                max_tokens = max(int(max_tokens), floor)
            except (TypeError, ValueError):
                max_tokens = floor
        if bilingual:
            task = (
                task
                + '\n用日语说这句台词（yuki 说日语），并给出中文翻译。'
                  '只输出 JSON：{"segments":[{"ja":"日语台词","zh":"中文翻译","tone":"中性","portrait":"微笑"}]}'
                  '直接输出最终答案的 JSON，禁止输出草稿、多个选项、思考过程、'
                  '解释或对指令的复述——只要一段 JSON，不要 markdown 代码块。'
            )
        system = build_system_prompt(self.card, self.state, self.memory) + "\n" + self._time_context_instruction()
        reply = self.llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": task},
            ],
            max_tokens=max_tokens,
        )
        from .reply import parse_reply
        output_cfg = self.config.get("output", {}) or {}
        max_chars = int(output_cfg.get("max_text_chars", 1200))
        if not bilingual:
            parsed = parse_reply(reply, channel="im", card=self.card, max_chars=max_chars)
            text = parsed.text if parsed.segments else ""
            text = "" if is_failure_fallback_reply(text) else text
            # 主动文案共享出口（问候/饭点/追问/心跳/公告）：同样过独白裁决，
            # 无灰色行时零成本直通
            return self._sanitize_monologue(text)
        parsed = parse_reply(
            reply,
            channel="tts",
            card=self.card,
            bilingual=True,
            max_segments=int(output_cfg.get("max_segments", 6)),
            max_chars=max_chars,
        )
        if is_failure_fallback_reply(parsed.text):
            return "", ""
        return parsed.text, parsed.ja_text

    def _time_greeting(self) -> str:
        import datetime
        h = datetime.datetime.now().hour
        if h < 6:
            msg = "这么晚还没睡……我陪你一会儿。"
        elif h < 11:
            msg = "早。今天有什么打算？"
        elif h < 14:
            msg = "中午好，吃过饭了吗？"
        elif h < 18:
            msg = "下午好。"
        else:
            msg = "晚上好。今天过得怎么样？"
        self.memory.store_message("assistant", msg, self.state.energy, self.state.mood, role_id=self.role_key)
        self._append_history_message("assistant", msg)
        return msg


    @staticmethod
    def _bigram_sim(a: str, b: str) -> float:
        """字符 bigram 重合率（取双向较小值）——中文短句的廉价相似度。"""
        def grams(s):
            s = "".join(str(s).split())
            return {s[i:i+2] for i in range(len(s) - 1)} if len(s) > 1 else {s} if s else set()
        ga, gb = grams(a), grams(b)
        if not ga or not gb:
            return 0.0
        inter = len(ga & gb)
        return min(inter / len(ga), inter / len(gb))

    def _relearn_or_store(self, layer: str, text: str, *, importance: float, category: str,
                          meta: dict | None = None) -> None:
        """遗忘后重新学习（2026-08-31 自检缺口③）：用户再次告知同一件事时，
        不插新条目，而是沿版本链更新旧记忆并把置信度拉回高段——
        表达层由此自动从"我记得好像…"（_fuzzy_ify 只作用于低确信档）
        恢复为肯定引用，等效于"啊对，我想起来了"。无相似旧事则照旧新增。
        """
        try:
            for hit in self.memory.recall(text[:100], top_k=3, layer=layer):
                if self._bigram_sim(hit.content, text) >= 0.5:
                    self.memory.update_latest(hit.id, text[:100], confidence=0.95,
                                              meta={"relearned": True})
                    logger.info("memory relearned (version bump): #%s", hit.id)
                    return
        except Exception as e:
            logger.debug("relearn lookup failed, fall through to store: %s", e)
        self.memory.store(layer, text[:100], importance=importance, confidence=0.6,
                          provenance="auto-extract", category=category, meta=meta)

    _NICK_FORBID_PAT = re.compile(
        r"(?:别|不要|不准|不许|不喜欢|不喜欢被|讨厌|受不了).{0,4}(?:叫|喊|称呼|管我叫)[我她他]?(?:「|“)?([^「」“”\s，。,.!?！？]{1,10})")

    def _apply_profile_facts(self, judgment) -> None:
        """统一判断点的 profile 字段落画像表（角色无关；对话提取 conf 0.7，
        被用户自述级覆盖后对话级不再回退——规则在 store.profile_set）。"""
        try:
            for k, v in (getattr(judgment, "profile", None) or {}).items():
                self.memory.profile_set(k, v, source="dialog", confidence=0.7)
        except Exception as e:
            logger.debug("profile facts store failed: %s", e)

    def _capture_nickname_feedback(self, user_text: str) -> None:
        """「别叫我X」→ 该角色对该称呼标记 forbidden（C 档闭集词面捕获）。"""
        try:
            m = self._NICK_FORBID_PAT.search(user_text or "")
            if m:
                from .persona import derive_relationship_stage
                rid = self._schedule_role_id() or self.card.name
                self.memory.nickname_mark(rid, m.group(1), "forbidden",
                                          stage=derive_relationship_stage(self.relationship))
        except Exception as e:
            logger.debug("nickname feedback capture failed: %s", e)

    def _profile_block(self) -> str:
        """用户画像（角色无关）+ 本角色称呼账（current/forbidden）+ 卡内称呼候选池
        （按关系阶段）→ prompt 块。空画像返回空串（不灌噪声、不动旧库）。"""
        try:
            prof = self.memory.profile_all()
            rid = self._schedule_role_id() or self.card.name
            nick = self.memory.nicknames_for(rid)
            stage = ""
            from .persona import derive_relationship_stage
            stage = derive_relationship_stage(self.relationship)
        except Exception as e:
            logger.debug("profile block build failed: %s", e)
            return ""
        if not prof and not any(nick.values()):
            return ""
        lines = ["【你对用户的了解（画像·跨角色共享，切换角色不重置）】"]
        label = {"real_name": "名字", "nickname_pref": "偏好被称", "gender": "性别",
                 "age": "年龄", "occupation": "职业", "city": "城市",
                 "love_language": "吃哪套关心", "comfort_style": "低落时想要",
                 "teasing_tolerance": "可调侃度", "health_notes": "健康注意",
                 "personality_traits": "性格自述"}
        for k, zh in label.items():
            if k in prof:
                src = prof[k].get("source")
                star = "（用户亲口说的，最高优先）" if src == "user" else ""
                lines.append(f"- {zh}：{prof[k]['value']}{star}")
        # 称呼账（角色×用户对，切换角色各叫各的）
        if nick.get("current"):
            lines.append(f"- 你平时叫ta：{'、'.join(nick['current'])}")
        if nick.get("forbidden"):
            lines.append(f"- 你绝不能这样叫ta（ta明确拒绝过）：{'、'.join(nick['forbidden'])}")
        if nick.get("history"):
            lines.append(f"- 曾用过已升级：{'、'.join(nick['history'])}")
        # 卡的称呼候选池按当前关系阶段（卡 extensions.veranima.nickname_pools）
        pools = ((self.card.veranima or {}).get("nickname_pools") or {})
        if pools.get(stage):
            lines.append(f"- 现阶段（{stage}）你可以用的称呼：{pools[stage]}")
            lines.append("  （首次采用新称呼时自然带出，别突兀；用户拒绝过的一律避开）")
        # 角色偏好（P3 设置，role_settings.expression 组）：称呼锁定/追加屏蔽/表达强度
        try:
            exp = (self.moments.settings().get("expression") or {}) if hasattr(self, "moments") else {}
        except Exception:
            exp = {}
        fixed = str(exp.get("fixed_nickname") or "").strip()
        if fixed:
            lines.append(f"- 用户对称呼做了指定：从此你固定用「{fixed}」称呼ta，池子里的其他叫法不再演化。")
        extra_taboo = [str(x) for x in (exp.get("sensitive_topics_extra") or []) if str(x).strip()]
        if extra_taboo:
            lines.append("- 本角色追加的屏蔽话题（谁提都不接）：" + "、".join(extra_taboo))
        style = str(exp.get("expressiveness") or "natural")
        if style == "cold":
            lines.append("- 当前表达强度=偏冷淡：句子更短，情感词减半，关心收着说。")
        elif style == "warm":
            lines.append("- 当前表达强度=偏热情：更舍得表达想念和在意，主动分享频率略升。")
        return "\n".join(lines)

    def _maybe_extract_events(self, user_text: str, judgment=None) -> None:
        """事件/偏好记忆提取（2026-08-31 判断点清算：memory_kind 有裁决
        以语义为准，"无辣不欢/下周去复查"这类变体不再依赖词表；
        未裁决退回 MVP1 关键词规则）。

        - 强信号（记住/生日/纪念/重要…）→ episodic（情节，0.8）
        - 偏好事实（我喜欢/我是/我的…）→ semantic（长期事实，0.7）
        """
        kind = getattr(judgment, "memory_kind", "none") if judgment is not None else "none"
        if kind not in ("event", "preference", "commitment"):
            kind = "none"
        emotion = self._detect_emotion(user_text, getattr(judgment, "emotion", "none") if judgment else "none")
        strong = ["记住", "生日", "纪念", "重要", "考试", "辞职", "生病", "难忘"]
        prefer = ["我特别喜欢", "我很喜欢", "我特别", "我最爱", "我最喜欢", "我喜欢", "我讨厌", "我害怕",
                  "我是", "我的", "我住在", "我在", "我养", "我爱"]
        if kind in ("event", "commitment") or (kind == "none" and any(s in user_text for s in strong)):
            entry = self.memory.store(
                "episodic",
                user_text[:100],
                importance=0.8,
                confidence=0.8 if kind != "none" else 0.6,  # 语义裁决比词表命中更可信
                provenance="auto-extract",
                category="event" if kind != "commitment" else "commitment",
                meta={"emotion": emotion} if emotion else None,
            )
            logger.info("episodic extracted: #%s (kind=%s)", entry.id, kind or "rule")
        elif kind == "preference" or (kind == "none" and any(s in user_text for s in prefer)):
            # 偏好类走重学路径：同一件事再说一遍 → 版本链刷新+置信拉回，
            # 而非堆重复条目（缺口③"遗忘后重新学习"的写侧落点）
            self._relearn_or_store("semantic", user_text, importance=0.7,
                                   category="preference",
                                   meta={"emotion": emotion} if emotion else None)

    @staticmethod
    def _detect_emotion(user_text: str, judgment: str = "none") -> str | None:
        """情绪着色：判断点 emotion 字段优先（happy/sad/angry/anxious），
        未裁决退回规则信号词。"""
        mapping = {"happy": "很开心", "sad": "有点低落", "angry": "有点火大", "anxious": "有点焦虑"}
        if judgment in mapping:
            return mapping[judgment]
        happy = ("哈哈", "开心", "高兴", "太好了", "耶", "棒", "爽", "嘻嘻", "嘿嘿")
        sad = ("难过", "伤心", "哭", "委屈", "烦", "累死", "压力", "焦虑", "崩溃", "emo")
        if any(w in user_text for w in happy):
            return "很开心"
        if any(w in user_text for w in sad):
            return "有点低落"
        return None

    def _dig_old_memory(self, topic_hint: str = "") -> tuple[str, float] | None:
        """主动考古（8.7.1）：从 episodic/semantic 挖一条旧事。

        排除最近 24 小时内的提取（避免考古"最近事"显得假）。topic_hint 非空时
        优先挖与之语义相关的旧事（2026-08-31 自检缺口②：随机挖容易刻意，
        真人"突然想起"是被眼前话题勾起来的弱关联）；无相关命中退回随机挖。
        """
        import datetime
        try:
            eps = self.memory.list_layer("episodic", limit=30)
            sems = self.memory.list_layer("semantic", limit=30)
            used = set(getattr(self, "_dug_memory_ids", []))
            pool = [e for e in (eps + sems) if e.id % 3 != 0 and e.id not in used]  # 分散+已挖排除
            if not pool:
                return None
            if topic_hint:
                try:
                    related = {e.id for e in self.memory.recall(topic_hint, top_k=10)}
                    biased = [e for e in pool if e.id in related]
                    if biased:
                        pick = random.choice(biased)
                        self._dug_memory_ids = (list(used) + [pick.id])[-12:]
                        return pick.content[:60], float(pick.confidence or 0.8)
                except Exception:
                    pass  # recall 失败退回随机，不阻塞破冰
            pick = random.choice(pool)
            self._dug_memory_ids = (list(used) + [pick.id])[-12:]
            return pick.content[:60], float(pick.confidence or 0.8)
        except Exception as e:
            logger.debug("dig old memory failed: %s", e)
            return None

    def _meal_message(self, meal_name: str, fallback: str) -> str:
        """饭点提醒去模板化（2026-08）：按角色口吻改写；LLM 不可用回退原文案。"""
        if not (getattr(self.llm, "is_model_loaded", None) is not None and self.llm.is_model_loaded()):
            return fallback
        cn = {"breakfast": "早饭", "lunch": "午饭", "dinner": "晚饭"}.get(meal_name, "饭")
        try:
            # 上下文相关（自检缺口⑤）：带上用户最近说了什么，让提醒显得"因为在意"
            # 而不是"因为到点"——用户说过忙/没吃/在减肥，口吻就该接得住。
            recent_user = [self._message_context_line(m)[:80] for m in reversed(
                self._recent_msgs(limit=10)) if m["role"] == "user"][:3]
            ctx = ("\n用户最近说过：" + "；".join(recent_user)) if recent_user else ""
            task = (
                f"到了该吃{cn}的时间点。用你自己的口吻提醒用户去吃饭，一句话，"
                f"不要说「到饭点了」这种模板话，别解释为什么提醒。{ctx}\n"
                "如果这些话里能自然接上（比如 ta 之前说忙/没吃/睡过头），就顺着提，别硬扯。"
            )
            return self._short_task(task, max_tokens=120) or fallback
        except Exception as e:
            logger.debug("meal LLM failed, fallback: %s", e)
            return fallback

    def greeting_message(self, slot: str) -> str:
        """个性化问候（8.7.5）：结合最近记忆；LLM 不可用时回退模板。

        2026-08-30 用户拍板：用户入睡后问候仍按原时间窗口发，但文案要
        模拟用户睡眠中（轻声打招呼，不期待回复）。
        """
        base = GreetingScheduler.greeting_text(slot)
        if not (getattr(self.llm, "is_model_loaded", None) is not None and self.llm.is_model_loaded()):
            return base if not self.state.user_asleep else "（轻声）还在睡吗？醒了记得跟我说一声。"
        try:
            recent = self._recent_msgs(limit=8)
            user_msgs = [self._message_context_line(m)[:90] for m in reversed(recent) if m["role"] == "user"]
            if not user_msgs:
                return base
            ctx = "\n".join(user_msgs[:3])
            asleep = self.state.user_asleep
            task = (
                f"现在是{'早晨' if slot == 'morning' else '中午' if slot == 'noon' else '晚上'}。"
                + (f"用户此刻在睡觉（他昨晚说去睡了），发一条轻的、不期待回复的问候，"
                   f"像对睡着的人说话：小声、简短、带点暖意，别长篇大论。\n"
                   if asleep else "")
                + f"用户最近说过：\n{ctx}\n\n"
                "给用户发一条简短的问候，如果能自然提到他最近说过的一件事（关心/跟进）最好；"
                "想不起来就不提，保持简单。"
            )
            return self._short_task(task, max_tokens=1024) or base
        except Exception as e:
            logger.debug("greeting LLM failed, fallback: %s", e)
            return base

    def _try_proactive(self) -> str:
        """R4 已废弃：无理由主动（idle/fatigue 类）按 R4_SPEC 3 关闭。

        所有主动发起必须携带 ProactiveCandidate（shared_episode/commitment/
        scene/ritual/attention）并经 ProactiveGate 9 闸门；本函数保留签名
        返回空串，避免外部残留调用崩溃，下一轮清理。
        """
        return ""

    # ---------- 8.6.3 表情包标注 ----------

    def annotate_sticker(self, image_data_url: str) -> dict | None:
        """表情包标注（8.6.3）：LLM 看图 → JSON（含义/情绪/适用情景）。

        返回 {"meaning","moods","scenarios"} 或 None（模型不可用/解析失败，
        调用方不强行入库）。JSON 用纯文本约束输出，防 thinking 模型吞预算。
        """
        if getattr(self.llm, "is_model_loaded", None) is not None and not self.llm.is_model_loaded():
            return None
        try:
            task = (
                "这是一张表情包图片。用 JSON 输出它的标注，格式：\n"
                '{"is_sticker": true, "kind": "sticker", "confidence": 0.9, '
                '"meaning": "一句话含义", "moods": ["情绪标签1", "情绪标签2"], '
                '"scenario_tags": ["固定情境标签"], "scenarios": ["适用情景1", "适用情景2"]}\n'
                "情绪标签从 [开心, 难过, 生气, 无语, 惊讶, 鼓励, 调侃, 无奈, 敷衍, 卖萌] 中选；"
                "固定情境标签从 [agreement, praise, affection, teasing, comfort, failure, surprise, "
                "refusal, request_help, fatigue, embarrassment, celebration] 中选；"
                "kind 只能是 sticker/photo/screenshot/unknown。普通照片/截图的 is_sticker 必须为 false。"
                "只输出 JSON，不要其他文字。"
            )
            messages = [
                {"role": "system", "content": "你是表情包标注助手，输出严格 JSON。"},
                {"role": "user", "content": [
                    {"type": "text", "text": task},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ]},
            ]
            # 不传 max_tokens：thinking 模型 reasoning 长，用 config 默认（2048）
            text = self.llm.chat(messages)
            return _parse_sticker_json(text)
        except Exception as e:
            logger.debug("sticker annotate failed: %s", e)
            return None


def _parse_sticker_json(text: str) -> dict | None:
    """解析 LLM 标注输出：剥离 ```json 围栏/前后杂文本，容错缺失字段。"""
    import json
    import re
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    else:
        # 无围栏：取第一个 { 到最后一个 }
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            text = text[s : e + 1]
    try:
        d = json.loads(text)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    is_sticker = d.get("is_sticker") is True
    if not is_sticker:
        return {
            "is_sticker": False,
            "kind": str(d.get("kind") or "unknown"),
            "confidence": 0.0,
            "meaning": "",
            "moods": [],
            "scenario_tags": [],
            "scenarios": [],
        }
    moods_allowed = {
        "开心", "难过", "生气", "无语", "惊讶", "鼓励", "调侃", "无奈", "敷衍", "卖萌",
    }
    tags_allowed = {
        "agreement", "praise", "affection", "teasing", "comfort", "failure",
        "surprise", "refusal", "request_help", "fatigue", "embarrassment", "celebration",
    }
    if d.get("kind") != "sticker":
        return None
    try:
        confidence = float(d["confidence"])
    except (KeyError, TypeError, ValueError):
        return None
    moods_raw = d.get("moods")
    tags_raw = d.get("scenario_tags")
    scenarios_raw = d.get("scenarios")
    meaning = str(d.get("meaning") or "").strip()
    if not 0.0 <= confidence <= 1.0 or not meaning:
        return None
    if not isinstance(moods_raw, list) or not isinstance(tags_raw, list) or not isinstance(scenarios_raw, list):
        return None
    moods = [str(value).strip() for value in moods_raw if str(value).strip()]
    tags = [str(value).strip() for value in tags_raw if str(value).strip()]
    scenarios = [str(value).strip() for value in scenarios_raw if str(value).strip()]
    if not moods or any(value not in moods_allowed for value in moods):
        return None
    if any(value not in tags_allowed for value in tags):
        return None
    return {
        "is_sticker": True,
        "kind": "sticker",
        "confidence": confidence,
        "meaning": meaning,
        "moods": moods,
        "scenario_tags": tags,
        "scenarios": scenarios,
    }
