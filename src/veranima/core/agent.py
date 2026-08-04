"""对话引擎：消息 → 状态 → 记忆 → prompt → LLM → 回复 → 存储。

MVP1 范围：人格（角色卡）+ 状态机 + 五层记忆（存/取/遗忘）+ 本地 LLM 对话。
MVP2 范围：隐式反馈学习 + 风格参数（bandits+EMA）+ 语言镜像 + 承诺机制 + curator 整理。
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..llm.client import LLMClient, LLMUnavailableError
from ..llm.prompts import build_system_prompt
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
            "open_promises": len(self.promises.open_promises()),
        }

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

    def start(self) -> str:
        """会话启动：恢复状态、时间问候或初遇开场白。"""
        self.state.tick(self.config.get("state", {}).get("energy_decay_per_minute", 0.02))
        # 首次会话：初遇开场白
        msgs = self.memory.recent_messages(limit=2)
        if not msgs:
            opening = self.card.first_mes or f"你好，我是{self.card.name}。今天想聊点什么？"
            self.memory.store_message("assistant", opening, self.state.energy, self.state.mood)
            self._history.append({"role": "assistant", "content": opening})
            return opening
        # 非首次：按时间段问候
        return self._time_greeting()

    def handle(self, user_text: str, images: list[str] | None = None) -> TurnResult:
        """处理一条用户消息，返回回复。

        images: 图片 data URL 列表（如 data:image/png;base64,...），
        多模态模型直接看图（DESIGN 8.6.2）；纯文本时传 None/[]。
        图片会以 OpenAI 多模态 content 数组形式进当前轮 LLM 请求；
        记忆/历史用 [图片] 占位（避免 base64 撑爆上下文与 FTS5）。
        """
        user_text = user_text.strip()
        images = images or []
        if not user_text and not images:
            return TurnResult(reply="", energy=self.state.energy, mood=self.state.mood)

        # 记忆/历史占位文本（图片不直接入库，防 base64 膨胀）
        store_text = user_text + (" [图片]" * len(images)) if images else user_text

        # 1. 状态推进 + 用户反馈
        self.state.tick(self.config.get("state", {}).get("energy_decay_per_minute", 0.02))
        self.state.on_user_message(recover_per_message=self.config.get("state", {}).get("energy_recover_per_message", 3.0))

        # 2. 零开销摄入：消息立即入库（FTS5 同步索引）
        self.memory.store_message("user", store_text, self.state.energy, self.state.mood)

        # 3. 记忆检索（预算内注入）+ MVP2 附加块（风格/镜像/承诺）
        query_hint = user_text or "图片"
        system = build_system_prompt(
            self.card, self.state, self.memory,
            core_profile_budget=self.config.get("memory", {}).get("core_profile_budget", 1200),
            section_budget=self.config.get("memory", {}).get("section_budget", 1600),
            session_budget=self.config.get("memory", {}).get("session_budget", 600),
            extra_blocks=[
                self.style.params.to_prompt_block(),
                self.mirror.to_prompt_block(),
                self.promises.to_prompt_block(query_hint=query_hint),
            ],
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

        # 4.5 模型加载前置检查：未加载则唤醒提示，不发请求
        # （LM Studio 收到请求会自动重载模型、瞬间吃回显存，游戏模式下必须避免）
        check = getattr(self.llm, "is_model_loaded", None)
        if check is not None and not check():
            reply = "（我好像还没醒过来……模型没在运行。跑一下 bash scripts/run_lmstudio.sh 叫醒我？）"
            self.memory.store_message("assistant", reply, self.state.energy, self.state.mood)
            self._history.append({"role": "assistant", "content": reply})
            self.state.on_assistant_message()
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
            # 模型未加载/服务不可用（游戏模式 off）：角色化唤醒提示，不冒充"卡了"
            logger.warning("LLM unavailable during turn: %s", e)
            reply = "（我好像还没醒过来……模型没在运行。跑一下 bash scripts/run_lmstudio.sh 叫醒我？）"
        except Exception as e:
            logger.error("chat failed: %s", e)
            reply = "（我这边有点卡……让我缓一下，你再说一遍？）"

        # 6. 回复入库 + 历史更新
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
        self.style.observe(sig)
        self.mirror.observe(user_text)
        self.promises.record(user_text)
        # curator 整理（每 8 轮）+ 持久化（每 20 轮）
        if self.state.total_messages % 8 == 0:
            result = self.memory.curate()
            logger.info("curator: %s", result.get("ops"))
        if self.state.total_messages % 20 == 0:
            self.style.save()
            self.mirror.save()

        # 9. 主动发言（低概率，MVP1 简化）
        proactive_msg = ""
        if random.random() < float(self.config.get("chat", {}).get("proactive_message_prob", 0.1)):
            proactive_msg = self._try_proactive()

        return TurnResult(
            reply=reply,
            recalled=[],
            proactive=bool(proactive_msg),
            proactive_msg=proactive_msg or "",
            energy=self.state.energy,
            mood=self.state.mood,
        )

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

    def tick_proactive(self, now=None) -> list[str]:
        """定时问候 + 节庆纪念检查（每日去重）。

        返回本次应发送的主动消息列表（已入档 memory）；adapter（CLI/QQ）
        负责展示/发送。供后台 tick 线程调用，幂等。``now`` 注入便于测试。
        """
        msgs: list[str] = []
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
        return msgs

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
        return msg

    # ---------- 内部 ----------

    def _short_task(self, task: str, max_tokens: int = 512) -> str:
        """短任务生成：带完整 system prompt（角色锚定）。

        实测（2026-08，qwen3-8b）：thinking 模型对裸 user prompt 的短任务
        会把全部 token 预算耗在 reasoning 上（≤80 token 必空；512 也常跑偏），
        带完整 system prompt 后 thinking 收敛、输出正常角色化回复。
        空/异常由调用方回退模板。
        """
        system = build_system_prompt(self.card, self.state, self.memory)
        return self.llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": task},
            ],
            max_tokens=max_tokens,
        )

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
        """主动发言（MVP3：LLM 生成虚拟日常分享，增强连续性；模型不可用降级模板池）。

        前置：模型加载检查（不触发 LM Studio 自动重载）。低精力不触发。
        """
        if self.state.energy < 30:
            return ""
        # LLM 生成（8.7.1 考古优先：先挖旧记忆，无旧事则分享虚拟日常）
        if getattr(self.llm, "is_model_loaded", None) is not None and self.llm.is_model_loaded():
            try:
                recent = self.memory.recent_messages(limit=6)
                ctx = "\n".join(f"{m['role']}: {m['content'][:60]}" for m in reversed(recent))
                old = self._dig_old_memory()
                if old:
                    task = (
                        f"你突然想起一件旧事：\"{old}\"。"
                        "给用户发一条消息，以'突然想起来'或'对了'的方式自然提起这件事，"
                        "问问后续或表达关心。"
                    )
                else:
                    task = (
                        f"下面是你和用户最近的对话：\n{ctx}\n\n"
                        "现在你打算主动给用户发一条消息，分享一件你虚拟日常里的小事"
                        "（窗台的绿萝、楼下遇到的猫、面包店、窗外天气等生活细节，要有连续性）。"
                        "只说这件事本身，像平时聊天一样自然。"
                    )
                reply = self._short_task(task, max_tokens=1024)
                if reply:
                    self.memory.store_message("assistant", reply, self.state.energy, self.state.mood)
                    self._history.append({"role": "assistant", "content": reply})
                    return reply
            except Exception as e:
                logger.debug("proactive LLM failed, fallback to template: %s", e)
        # 降级：模板池（MVP1 简化）
        pool = [
            "（想起一件事）对了，你上次说的那件事后来怎么样了？",
            "今天有看到什么有意思的东西吗？",
            "我刚刚走神了……你说，猫如果会开冰箱，会不会互相分享吃的？",
        ]
        msg = random.choice(pool)
        self.memory.store_message("assistant", msg, self.state.energy, self.state.mood)
        self._history.append({"role": "assistant", "content": msg})
        return msg

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
