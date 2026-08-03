"""对话引擎：消息 → 状态 → 记忆 → prompt → LLM → 回复 → 存储。

MVP1 范围：人格（角色卡）+ 状态机 + 五层记忆（存/取/遗忘）+ 本地 LLM 对话。
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from ..llm.client import LLMClient, LLMUnavailableError
from ..llm.prompts import build_system_prompt
from ..memory.store import MemoryStore
from .character import CharacterCard
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

    def handle(self, user_text: str) -> TurnResult:
        """处理一条用户消息，返回回复。"""
        user_text = user_text.strip()
        if not user_text:
            return TurnResult(reply="", energy=self.state.energy, mood=self.state.mood)

        # 1. 状态推进 + 用户反馈
        self.state.tick(self.config.get("state", {}).get("energy_decay_per_minute", 0.02))
        self.state.on_user_message(recover_per_message=self.config.get("state", {}).get("energy_recover_per_message", 3.0))

        # 2. 零开销摄入：消息立即入库（FTS5 同步索引）
        self.memory.store_message("user", user_text, self.state.energy, self.state.mood)

        # 3. 记忆检索（预算内注入）
        system = build_system_prompt(
            self.card, self.state, self.memory,
            core_profile_budget=self.config.get("memory", {}).get("core_profile_budget", 1200),
            section_budget=self.config.get("memory", {}).get("section_budget", 1600),
            session_budget=self.config.get("memory", {}).get("session_budget", 600),
        )

        # 4. 组装对话（历史 + 当前）
        messages = [{"role": "system", "content": system}]
        messages.extend(self._history[-self.config.get("chat", {}).get("history_max_messages", 20):])
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

        # 5. 生成（低精力时限短）
        low_energy = self.state.energy < 40
        try:
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
        self._history.append({"role": "user", "content": user_text})
        self._history.append({"role": "assistant", "content": reply})
        self.state.on_assistant_message()

        # 7. 事件记忆提取（延迟整理简化版：每 4 轮提取一次情节记忆）
        self._maybe_extract_events(user_text)

        # 8. 主动发言（低概率，MVP1 简化）
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

    # ---------- 内部 ----------

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

    def _maybe_extract_events(self, user_text: str) -> None:
        """每 4 轮对话提取一次情节记忆（简化：规则提取重要信号，不调 LLM）。

        MVP1 用规则近似：包含强烈情感词或明确"记住"诉求时入库 episodic。
        MVP2 替换为 LLM 事件卡片提取。
        """
        if self.state.total_messages % 4 != 0:
            return
        importance = 0.5
        signals = ["记住", "今天", "生日", "纪念", "难过", "开心", "重要", "烦", "累", "辞职", "考试", "工作"]
        if any(s in user_text for s in signals):
            importance = 0.8
        if importance >= 0.8:
            entry = self.memory.store(
                "episodic",
                f"{user_text[:100]}",
                importance=importance,
                confidence=0.6,
                provenance="auto-extract",
                category="event",
            )
            logger.info("episodic extracted: #%s", entry.id)

    def _try_proactive(self) -> str:
        """低精力/随机时刻的主动发言（MVP1 简化：时间问候类）。返回消息或空串。"""
        if self.state.energy < 30:
            return ""
        pool = [
            "（想起一件事）对了，你上次说的那件事后来怎么样了？",
            "今天有看到什么有意思的东西吗？",
            "我刚刚走神了……你说，猫如果会开冰箱，会不会互相分享吃的？",
        ]
        msg = random.choice(pool)
        self.memory.store_message("assistant", msg, self.state.energy, self.state.mood)
        self._history.append({"role": "assistant", "content": msg})
        return msg
