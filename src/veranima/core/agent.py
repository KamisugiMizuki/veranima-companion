"""对话引擎：消息 → 状态 → 记忆 → prompt → LLM → 回复 → 存储。

MVP1 范围：人格（角色卡）+ 状态机 + 五层记忆（存/取/遗忘）+ 本地 LLM 对话。
MVP2 范围：隐式反馈学习 + 风格参数（bandits+EMA）+ 语言镜像 + 承诺机制 + curator 整理。
"""

from __future__ import annotations

import datetime
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..llm.client import LLMClient, LLMUnavailableError
from .prompts import build_system_prompt, is_clarification
from .segments import extract_segments
from .ambient import ChannelActivityTracker, ProactiveCandidate, ProactiveGate, SceneLock
from .interrupt import InterruptDecider, TopicFrequency
from ..memory.store import MemoryStore
from ..tools.search import SEARCH_TOOL, SearXNGClient
from .character import CharacterCard
from .learning import LanguageMirror, StyleLearner, extract_feedback
from .proactive import GreetingScheduler, OccasionChecker
from .promises import PromiseBook
from .review import MonthlyReview
from .state import AgentState

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
        self._history: list[dict] = []
        self._last_reply_ts: float | None = None  # 上一条回复时间（延迟信号用）

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
            recent = self.memory.recent_messages(limit=hist_limit)
            self._history = [
                {"role": m["role"], "content": m["content"]}
                for m in recent if m["role"] in ("user", "assistant")
            ]
            if self._history:
                logger.info("restored %d history messages for continuity", len(self._history))
        except Exception as e:
            logger.warning("state/history restore failed, starting fresh: %s", e)

        # P-3 关系模型（PERSONA_LOOP_SPEC）：从快照恢复，否则用 initial_affection 先验
        from .persona import RelationshipModel
        if self.state.relationship:
            self.relationship = RelationshipModel.from_dict(self.state.relationship)
        else:
            ia = float(((self.card.veranima or {}).get("initial_affection") or 0.5))
            self.relationship = RelationshipModel.from_initial(initial_affection=ia)

        # P-5 反思计数器（每 20 个有效人格候选触发一次整合）
        self._reflection_counters = {"persona_candidates": 0, "high_emotion_events": 0, "user_corrections": 0}

        # P-7 冲突跟踪（随 state.relationship 持久化；重启恢复）
        from .persona import ConflictTracker
        self._conflicts = ConflictTracker.from_dict(self.state.relationship.get("conflicts") if isinstance(self.state.relationship, dict) else None)

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
        self.mirror.load()
        self.promises = PromiseBook(memory)

        # 主动触发（定时问候 + 节庆纪念；CLI 与 QQ adapter 共用 tick_proactive）
        self.greeter = GreetingScheduler()
        self.occasion = OccasionChecker()

        # R4 时空沉浸：场景锁 + 通道互斥 + 主动仲裁（最小版，R4_SPEC 1）
        self.scene_lock = SceneLock()
        self.activity = ChannelActivityTracker()
        # R4 统一主动入口（R4_SPEC 1/2）：ProactiveCandidate → ProactiveGate 9 闸门
        self.gate = ProactiveGate(self.config.get("proactive", {}))

        # 联网搜索（DESIGN.md 8.5 方案 A：工具调用；默认关闭，config 开启）
        search_cfg = self.config.get("search", {})
        self.search_enabled = bool(search_cfg.get("enabled", False))
        self.search = SearXNGClient(
            base_url=search_cfg.get("base_url", "http://127.0.0.1:8080"),
            max_results=int(search_cfg.get("max_results", 4)),
        )

    # ---------- MVP2 状态 ----------

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
        self.style.reset()
        self.mirror.reset()
        return self.learning_summary()

    def monthly_review(self) -> str:
        """月度回顾：检索记忆 → LLM 生成"我们一起走过的日子"。"""
        review = MonthlyReview(self.memory, llm=self.llm)
        text = review.generate(name=self.card.name)
        # 回顾本身也作为一条 assistant 消息入档
        self.memory.store_message("assistant", text, self.state.energy, self.state.mood)
        return text

    # ---------- 公开接口 ----------

    def _persist_state(self) -> None:
        """状态持久化（重启续接）：状态变更后写入 SQLite agent_state 单行。"""
        try:
            # P-3：关系模型快照同步进 AgentState；P-7：冲突状态随关系快照
            rel = self.relationship.to_dict()
            rel["conflicts"] = self._conflicts.to_dict()
            rel["imprints"] = self._imprints.to_dict()
            self.state.relationship = rel
            self.memory.save_state(self.state.to_snapshot())
        except Exception as e:
            logger.debug("state persist failed: %s", e)

    def start(self) -> str:
        """会话启动：恢复状态、时间问候或初遇开场白。"""
        self.state.tick(self.config.get("state", {}).get("energy_decay_per_minute", 0.02))
        # 首次会话：初遇开场白
        msgs = self.memory.recent_messages(limit=2)
        if not msgs:
            opening = self.card.first_mes or f"你好，我是{self.card.name}。今天想聊点什么？"
            self.memory.store_message("assistant", opening, self.state.energy, self.state.mood)
            self._history.append({"role": "assistant", "content": opening})
            self._persist_state()
            return opening
        # 非首次：按时间段问候
        greeting = self._time_greeting()
        self._persist_state()
        return greeting

    def handle(self, user_text: str, images: list[str] | None = None, channel: str = "im") -> TurnResult:
        """处理一条用户消息，返回回复。

        images: 图片 data URL 列表（如 data:image/png;base64,...），
        多模态模型直接看图（DESIGN 8.6.2）；纯文本时传 None/[]。
        图片会以 OpenAI 多模态 content 数组形式进当前轮 LLM 请求；
        记忆/历史用 [图片] 占位（避免 base64 撑爆上下文与 FTS5）。
        channel: 通道标识（im/tts，DESIGN 4.8 通道感知），注入 system prompt 的通道语境。
        """
        user_text = user_text.strip()
        images = images or []
        if not user_text and not images:
            return TurnResult(reply="", energy=self.state.energy, mood=self.state.mood)

        # ===== R0 阶段 1: prepare_turn（R0_SPEC 5）=====
        # 输入规整 → 状态推进 → 场景/打断 → 零开销入库 → 记忆检索预算
        # （第一阶段保持现有行为，仅按注释划分；后续逐步抽为独立函数）
        # 记忆/历史占位文本（图片不直接入库，防 base64 膨胀）
        store_text = user_text + (" [图片]" * len(images)) if images else user_text

        # 1. 状态推进 + 用户反馈
        self.state.tick(self.config.get("state", {}).get("energy_decay_per_minute", 0.02))
        self.state.on_user_message(recover_per_message=self.config.get("state", {}).get("energy_recover_per_message", 3.0))

        # 1.5 R4 场景锁：用户消息进来时更新场景（进入/退出 busy/away）
        scene = self.scene_lock.note(user_text)
        if scene != "normal":
            logger.info("scene active: %s", scene)

        # 1.5.1 P-7 冲突信号检测（澄清推进/越界新开 + 关系事件联动）
        try:
            from .persona import apply_relationship_event, note_conflict_from_user_text
            action = note_conflict_from_user_text(self._conflicts, user_text)
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
            current_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
            state=self.state.to_snapshot(),
        )

        # 1.6 R0 打断决策：话题复现计数 → L0-L3 分级（R0_SPEC 5）
        topic_count = self.topic_freq.note(user_text)
        interrupt_level = self.interrupt_decider.decide(topic_count)
        if interrupt_level > 0:
            logger.info("interrupt L%d (topic count=%d)", interrupt_level, topic_count)

        # 2. 零开销摄入：消息立即入库（FTS5 同步索引）
        user_msg_id = self.memory.store_message("user", store_text, self.state.energy, self.state.mood)

        # ===== R0 阶段 2: build_turn_prompt（R0_SPEC 5）=====
        # 记忆检索（预算内注入）+ MVP2 附加块（风格/镜像/承诺）+ 打断指令
        query_hint = user_text or "图片"
        # P-6/P-9：预构建 PersonaBrief 选回用动作与表达计划（prompts 内会再次构建——轻量，接受重复）
        reuse_action = ""
        response_plan = None
        try:
            from .persona import build_persona_brief, build_response_plan, choose_reuse_action
            # 冲突压力同步到 state（choose_reuse_action/build_response_plan 从 state 读；
            # conflict_tension 真值在 relationship）
            self.state.conflict_tension = self.relationship.conflict_tension
            pre_brief = build_persona_brief(query_hint, self.card, self.relationship, self.state, self.memory)
            action = choose_reuse_action(pre_brief, user_text, self.state)
            if action != "none" and pre_brief.relevant_user_frameworks:
                key = pre_brief.relevant_user_frameworks[0]["content"][:20]
                if self._reuse_cd.allow(key, self._turn_n):
                    reuse_action = action
            response_plan = build_response_plan({"user_text": user_text}, pre_brief, self.state)
        except Exception as e:
            logger.debug("persona reuse/plan skipped: %s", e)
        self._turn_n += 1
        extra_blocks = [
            self.style.to_prompt_block(),  # M-6：参数 + 文风画像合并块
            self.mirror.to_prompt_block(),
            self.promises.to_prompt_block(query_hint=query_hint),
        ]
        if response_plan is not None:
            # P-9：表达意图注入（不暴露内部思考；只给意图与开场）
            plan_bits = [f"intent={response_plan.intent}"]
            if response_plan.opening_move:
                plan_bits.append(f"开场={response_plan.opening_move}")
            if response_plan.conflict:
                plan_bits.append(response_plan.conflict)
            extra_blocks.append(f"【表达意图】{'；'.join(plan_bits)}")
        if interrupt_level > 0:
            extra_blocks.append(_interrupt_prompt(interrupt_level))
        system = build_system_prompt(
            self.card, self.state, self.memory,
            core_profile_budget=self.config.get("memory", {}).get("core_profile_budget", 1200),
            procedural_budget=self.config.get("memory", {}).get("procedural_budget", 1000),
            section_budget=self.config.get("memory", {}).get("section_budget", 2400),
            session_budget=self.config.get("memory", {}).get("session_budget", 600),
            channel=channel,
            clarification=is_clarification(user_text),  # R1 可逆性：追问 → 精确值（R1_SPEC 3）
            extra_blocks=extra_blocks,
            relationship=self.relationship,  # P-4：PersonaBrief 接入口
            reuse_action=reuse_action,       # P-6：本轮回用动作
        )

        # 4. 组装对话（历史 + 当前）；当前轮含图时用多模态 content 数组
        messages = [{"role": "system", "content": system}]
        hist = self._history[-self.config.get("chat", {}).get("history_max_messages", 20):]
        # 2026-08-04 修复：proactive/late_reply/问候会向 _history 追加孤立的 assistant
        # 消息（无配对 user），截断后序列可能以 assistant 开头；llama.cpp Qwen3 jinja
        # 模板要求第一条非 system 消息必须是 user，否则 400 "No user query found in
        # messages"（现象：跑若干轮后偶发 400，且被 client 误报为"模型未加载"）。
        # 丢弃开头的孤立 assistant，保证序列 [user, assistant, ..., user]。
        while hist and hist[0]["role"] != "user":
            hist = hist[1:]
        messages.extend(hist)
        if images:
            content: list[dict] = [{"type": "text", "text": user_text or "（用户发了一张图片）"}]
            content.extend({"type": "image_url", "image_url": {"url": u}} for u in images)
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_text})

        # ===== R0 阶段 3: call_llm（R0_SPEC 5）=====
        # 模型可用性前置检查（远程 API：is_model_loaded 恒 True，防御性保留）
        check = getattr(self.llm, "is_model_loaded", None)
        if check is not None and not check():
            reply = "（我好像还没醒过来……服务没在运行。检查一下 API 配置？）"
            self.memory.store_message("assistant", reply, self.state.energy, self.state.mood)
            self._history.append({"role": "assistant", "content": reply})
            self.state.on_assistant_message()
            self._persist_state()
            return TurnResult(reply=reply, energy=self.state.energy, mood=self.state.mood)

        # 5. 生成（低精力时限短；联网搜索开启时走工具调用链路）
        low_energy = self.state.energy < 40
        try:
            if self.search_enabled:
                reply = self._chat_with_search(messages, low_energy)
            else:
                reply = self.llm.chat(
                    messages,
                    max_tokens=self.llm.low_energy_max_tokens if low_energy else None,
                )
        except LLMUnavailableError as e:
            # 服务不可用（连接失败/鉴权失败）：角色化提示，不冒充"卡了"
            logger.warning("LLM unavailable during turn: %s", e)
            reply = "（我好像还没醒过来……服务没在运行。检查一下 API 配置？）"
        except Exception as e:
            logger.error("chat failed: %s", e)
            reply = "（我这边有点卡……让我缓一下，你再说一遍？）"

        # 5.5 场景锁（R4）：busy 场景截短回复（模拟"在忙，简单回一句"）
        max_len = self.scene_lock.max_len()
        if max_len and len(reply) > max_len:
            cut = reply[:max_len].rstrip("，。！？ ")
            reply = cut + "……（我这边有点忙，回头细说）"

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
            reply = parsed.text or reply
            tone = parsed.tone
            portrait = parsed.portrait
            ja_text = parsed.ja_text
            if portrait and not self._portrait_valid(portrait):
                portrait = ""  # 词表外标签回退（防 OOC）
        else:
            from .reply import Reply, ReplySegment
            turn_reply = Reply(segments=[ReplySegment(text=reply)])

        # ===== R0 阶段 5: persist_turn（R0_SPEC 5）=====
        # 回复入库 + 历史更新
        self.memory.store_message("assistant", reply, self.state.energy, self.state.mood)
        self._history.append({"role": "user", "content": store_text})
        self._history.append({"role": "assistant", "content": reply})
        self.state.on_assistant_message()

        # 7. 定期触发遗忘衰减（每 10 轮；MVP1 简化：无后台调度器，随对话驱动）
        if self.state.total_messages % 10 == 0:
            result = self.memory.decay()
            logger.info("memory decay applied: updated=%s faded=%s", result.get("updated", 0), result.get("faded", 0))

        # 8. 事件记忆提取（延迟整理简化版：每 4 轮提取一次情节记忆）
        self._maybe_extract_events(user_text)

        # 8.5 MVP2 学习：隐式反馈 → 风格参数 + 语言镜像 + 承诺识别
        prev_reply = self._history[-3]["content"] if len(self._history) >= 3 else ""
        delay = (time.time() - self._last_reply_ts) if self._last_reply_ts else 0.0
        sig = extract_feedback(user_text, reply, prev_reply, delay=delay)
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
                f"{'用户' if m['role'] == 'user' else '我'}: {m['content']}"
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
        kind = cand["kind"]
        layer = self.memory_store_layer(kind)
        content = cand["content"]
        # meta 透传扩展字段（MEMORY_SPEC 5 候选契约）
        meta = {
            "kind": kind,
            "source_message_id": cand.get("source_message_id"),
            "subject": cand.get("subject", "user"),
            "event_time": cand.get("event_time"),
            "emotion": cand.get("emotion"),
            "expires_at": cand.get("expires_at"),
            "status": cand.get("status", "active"),
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
            sim = self._text_similarity(content, e.content)
            if sim >= 0.92 and not cand.get("correction"):
                logger.debug("candidate dup ignored (sim=%.2f): %s", sim, content[:30])
                return
            if sim >= 0.78 or cand.get("correction"):
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

    def proactive_from_visual(self, tag: str) -> str:
        """R4 联想式主动发起（R4_SPEC 1.三段式决策）。

        屏幕 focus.tag × 事件记忆模糊匹配：episodic 层含 tag 关键词 → 生成联想消息。
        无匹配 / 模型不可用 → 返回 ""。
        """
        if not tag or self.state.energy < 30:
            return "", ""
        # 检索 episodic 层含 tag 的记忆
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
        self.memory.store_message("assistant", reply, self.state.energy, self.state.mood)
        self._history.append({"role": "assistant", "content": reply})
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
                last_user = str(m.get("content") or "")[:80]
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
        self.memory.store_message("assistant", reply, self.state.energy, self.state.mood)
        self._history.append({"role": "assistant", "content": reply})
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

    def tick_proactive(self, now=None) -> list[str]:
        """定时问候 + 节庆纪念检查（每日去重）。

        返回本次应发送的主动消息列表（已入档 memory）；adapter（CLI/QQ）
        负责展示/发送。供后台 tick 线程调用，幂等。``now`` 注入便于测试。

        R4：统一主动入口——ProactiveCandidate(ritual) → ProactiveGate 9 闸门（R4_SPEC 1/2）。
        """
        # P-7：未闭合冲突时禁止 ritual 主动（不加重关系压力）
        if self.persona_proactive_blocked("ritual"):
            return []
        # gate.decide 需要 epoch 秒（now 可能是 datetime 注入，转 timestamp）
        now_ts = now.timestamp() if isinstance(now, datetime.datetime) else now
        msgs: list[str] = []
        cand = ProactiveCandidate(
            source="ritual", reason="定时问候/节庆纪念",
            relevance=0.9, urgency=0.5, intent="share",
            context={"calendar_source": "greeter/occasion"},
        )
        decision = self.gate.decide(
            cand, scene=self.scene_lock.current(),
            other_channel_active=self.activity.blocking("qq"),
            now=now_ts,  # 测试注入；生产传真实时间
        )
        if not decision.allow:
            return msgs
        slot = self.greeter.due_greeting(now=now)
        if slot:
            # 8.7.5 个性化问候（结合最近记忆；LLM 不可用回退模板）
            msg = self.greeting_message(slot)
            self.memory.store_message("assistant", msg, self.state.energy, self.state.mood)
            msgs.append(msg)
        occasion = self.occasion.due_occasion(self.memory, now=now)
        if occasion:
            msg = self.occasion.occasion_reaction(occasion, self.card.name)
            self.memory.store_message("assistant", msg, self.state.energy, self.state.mood)
            msgs.append(msg)
        if msgs:
            self.gate.commit(cand)
        return msgs

    def heartbeat(self) -> str:
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
        )
        if not self.gate.decide(
            cand, scene=self.scene_lock.current(),
            other_channel_active=self.activity.blocking("qq"),
        ).allow:
            return ""
        recent = self.memory.recent_messages(limit=8)
        if not recent or recent[-1]["role"] != "assistant":
            return ""  # 用户刚说完话或有未闭合对话，不需要破冰
        if getattr(self.llm, "is_model_loaded", None) is not None and self.llm.is_model_loaded():
            try:
                ctx = ("最近的对话：\n" + "\n".join(
                    f"{m['role']}: {m['content'][:60]}" for m in recent[-4:]
                )) if recent else ""
                task = (
                    f"{ctx}\n\n你刚在整理聊天记录（离线成长），想跟用户说点什么破冰。"
                    "自然带出一点整理时的发现（他之前提过的事/你记住的细节），"
                    "像平时聊天一样自然，长度随意。不要用「欢迎回来」这类生硬话。"
                )
                reply = self._short_task(task, max_tokens=1024)
                if reply:
                    self.memory.store_message("assistant", reply, self.state.energy, self.state.mood)
                    self._history.append({"role": "assistant", "content": reply})
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
        self.memory.store_message("assistant", reply, self.state.energy, self.state.mood)
        self._history.append({"role": "assistant", "content": reply})
        self.gate.commit(cand)
        return reply

    def late_reply(self) -> str:
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
        )
        if not self.gate.decide(
            cand, scene=self.scene_lock.current(),
            other_channel_active=self.activity.blocking("qq"),
        ).allow:
            return ""
        # 对话闭合检查：最后一条必须是 user 消息（有未回应完的内容）
        recent = self.memory.recent_messages(limit=8)
        if not recent or recent[-1]["role"] != "user":
            return ""
        if getattr(self.llm, "is_model_loaded", None) is not None and self.llm.is_model_loaded():
            try:
                user_msgs = [m["content"][:80] for m in reversed(recent) if m["role"] == "user"]
                if user_msgs:
                    last = user_msgs[0]
                    task = (
                        f"用户之前说过：\"{last}\"，但你现在才空下来，"
                        "想补一条迟来的回应。自然提起这件事，补充想法或表达关心。"
                        "像平时聊天一样自然，长度随意。"
                    )
                    reply = self._short_task(task, max_tokens=1024)
                    if reply:
                        self.memory.store_message("assistant", reply, self.state.energy, self.state.mood)
                        self._history.append({"role": "assistant", "content": reply})
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
        self.memory.store_message("assistant", msg, self.state.energy, self.state.mood)
        self._history.append({"role": "assistant", "content": msg})
        self.gate.commit(cand)
        return msg

    # ---------- 内部 ----------

    def _short_task(self, task: str, max_tokens: int | None = None, bilingual: bool = False) -> str | tuple[str, str]:
        """短任务生成：带完整 system prompt（角色锚定）。

        实测（2026-08，qwen3-8b）：thinking 模型对裸 user prompt 的短任务
        会把全部 token 预算耗在 reasoning 上（≤80 token 必空；512 也常跑偏），
        带完整 system prompt 后 thinking 收敛、输出正常角色化回复。
        空/异常由调用方回退模板。

        max_tokens=None → 读配置 llm.short_task_max_tokens（默认 1024）；
        不得传 120/200 这类小预算（R0_SPEC 6：思考模型预算不足只剩残留）。
        bilingual=True：任务要求输出双语 JSON（ja 台词/zh 翻译），返回
        (zh, ja)；非双语返回纯文本字符串。
        """
        if max_tokens is None:
            max_tokens = int((self.config.get("llm", {}) or {}).get("short_task_max_tokens", 1024))
        if bilingual:
            task = (
                task
                + '\n用日语说这句台词（yuki 说日语），并给出中文翻译。'
                  '只输出 JSON：{"segments":[{"ja":"日语台词","zh":"中文翻译","tone":"中性","portrait":"微笑"}]}'
                  '直接输出最终答案的 JSON，禁止输出草稿、多个选项、思考过程、'
                  '解释或对指令的复述——只要一段 JSON，不要 markdown 代码块。'
            )
        system = build_system_prompt(self.card, self.state, self.memory)
        reply = self.llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": task},
            ],
            max_tokens=max_tokens,
        )
        if not bilingual:
            return reply
        text, _tone, _portrait, ja = extract_segments(reply, bilingual=True, card=self.card)
        return text, ja

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
        self.memory.store_message("assistant", msg, self.state.energy, self.state.mood)
        self._history.append({"role": "assistant", "content": msg})
        return msg

    def _chat_with_search(self, messages: list[dict], low_energy: bool) -> str:
        """工具调用链路：带 search_web 工具 → 模型自主决定是否搜索 → 结果回填 → 最终回复。

        约束：单轮最多 1 次搜索（设计 8.5 节）；搜索失败降级为直接回复。
        """
        import json
        max_tokens = self.llm.low_energy_max_tokens if low_energy else None
        msg = self.llm.chat_raw(messages, max_tokens=max_tokens, tools=[SEARCH_TOOL])
        content = (msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return content
        # 执行 search_web（最多 1 个）
        messages.append({"role": "assistant", "content": content or None, "tool_calls": tool_calls})
        executed = False
        for tc in tool_calls[:1]:
            fn = tc.get("function", {})
            if fn.get("name") != "search_web":
                continue
            try:
                query = json.loads(fn.get("arguments") or "{}").get("query", "")
            except Exception:
                query = ""
            if not query:
                continue
            logger.info("search_web: %s", query)
            results = self.search.search(query)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", "call_1"),
                "content": self.search.format_results(results),
            })
            executed = True
        if not executed:
            return content
        # 第二轮：基于搜索结果生成最终回复
        final = self.llm.chat_raw(messages, max_tokens=max_tokens)
        return (final.get("content") or "").strip()

    def _maybe_extract_events(self, user_text: str) -> None:
        """规则提取记忆（MVP1 简化，每条消息检查；MVP2 替换为 LLM 事件卡片提取）。

        - 强信号（记住/生日/纪念/重要…）→ episodic（情节，0.8）
        - 偏好事实（我喜欢/我是/我的…）→ semantic（长期事实，0.7）
        """
        strong = ["记住", "生日", "纪念", "重要", "考试", "辞职", "生病", "难忘"]
        prefer = ["我特别喜欢", "我很喜欢", "我特别", "我最爱", "我最喜欢", "我喜欢", "我讨厌", "我害怕",
                  "我是", "我的", "我住在", "我在", "我养", "我爱"]
        emotion = self._detect_emotion(user_text)
        if any(s in user_text for s in strong):
            entry = self.memory.store(
                "episodic",
                user_text[:100],
                importance=0.8,
                confidence=0.6,
                provenance="auto-extract",
                category="event",
                meta={"emotion": emotion} if emotion else None,
            )
            logger.info("episodic extracted: #%s", entry.id)
        elif any(s in user_text for s in prefer):
            entry = self.memory.store(
                "semantic",
                user_text[:100],
                importance=0.7,
                confidence=0.6,
                provenance="auto-extract",
                category="preference",
                meta={"emotion": emotion} if emotion else None,
            )
            logger.info("semantic extracted: #%s", entry.id)

    @staticmethod
    def _detect_emotion(user_text: str) -> str | None:
        """从用户消息粗略检测情绪（8.7.2 情感色彩；规则信号词）。"""
        happy = ("哈哈", "开心", "高兴", "太好了", "耶", "棒", "爽", "嘻嘻", "嘿嘿")
        sad = ("难过", "伤心", "哭", "委屈", "烦", "累死", "压力", "焦虑", "崩溃", "emo")
        if any(w in user_text for w in happy):
            return "很开心"
        if any(w in user_text for w in sad):
            return "有点低落"
        return None

    def _dig_old_memory(self) -> str | None:
        """主动考古（8.7.1）：从 episodic/semantic 随机挖一条旧事。

        排除最近 24 小时内的提取（避免考古"最近事"显得假）；无旧事返回 None。
        """
        import datetime
        try:
            eps = self.memory.list_layer("episodic", limit=30)
            sems = self.memory.list_layer("semantic", limit=30)
            pool = [e for e in (eps + sems) if e.id % 3 != 0]  # 简易分散
            if not pool:
                return None
            entry = random.choice(pool)
            return entry.content[:60]
        except Exception as e:
            logger.debug("dig old memory failed: %s", e)
            return None

    def greeting_message(self, slot: str) -> str:
        """个性化问候（8.7.5）：结合最近记忆；LLM 不可用时回退模板。"""
        base = GreetingScheduler.greeting_text(slot)
        if not (getattr(self.llm, "is_model_loaded", None) is not None and self.llm.is_model_loaded()):
            return base
        try:
            recent = self.memory.recent_messages(limit=8)
            user_msgs = [m["content"][:60] for m in reversed(recent) if m["role"] == "user"]
            if not user_msgs:
                return base
            ctx = "\n".join(user_msgs[:3])
            task = (
                f"现在是{'早晨' if slot == 'morning' else '中午' if slot == 'noon' else '晚上'}。"
                f"用户最近说过：\n{ctx}\n\n"
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
                '{"meaning": "一句话含义", "moods": ["情绪标签1", "情绪标签2"], '
                '"scenarios": ["适用情景1", "适用情景2"]}\n'
                "情绪标签从 [开心, 难过, 生气, 无语, 惊讶, 鼓励, 调侃, 无奈, 敷衍, 卖萌] 中选；"
                "适用情景用简短短语描述（如'用户答应请求'）。只输出 JSON，不要其他文字。"
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
    return {
        "meaning": str(d.get("meaning", "")).strip(),
        "moods": [str(x).strip() for x in d.get("moods", []) if str(x).strip()],
        "scenarios": [str(x).strip() for x in d.get("scenarios", []) if str(x).strip()],
    }


def _data_url_from_bytes(raw: bytes, default_ctype: str = "image/png") -> str:
    """原始图片字节 → data URL（8.6.3 表情包标注用）。"""
    import base64
    # 从文件头嗅探类型（PNG/JPEG/GIF/WEBP），未知回退 default
    ctype = default_ctype
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        ctype = "image/png"
    elif raw[:3] == b"\xff\xd8\xff":
        ctype = "image/jpeg"
    elif raw[:6] in (b"GIF87a", b"GIF89a"):
        ctype = "image/gif"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        ctype = "image/webp"
    return f"data:{ctype};base64," + base64.b64encode(raw).decode()
