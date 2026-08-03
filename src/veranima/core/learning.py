"""MVP2 学习模块：隐式反馈 → 风格参数学习（多臂老虎机 + EMA）+ 语言镜像。

设计依据（DESIGN.md 第 5 节）：
- 在 prompt 参数层学习，不做模型权重微调/PPO/RLHF
- 保守演化：风格变化以周为尺度，防突变
- 提供 reset --style 回滚（参数快照）
- 语言镜像带使用上限防刻意
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------- 风格参数（可学习维度） ----------

STYLE_PARAMS = ("reply_length", "formality", "humor", "topic_follow")


@dataclass
class StyleParams:
    """风格参数值（0-1 范围），注入 prompt 的语言风格段落。"""

    reply_length: float = 0.5   # 0=极简 1=详细
    formality: float = 0.6      # 0=随意 1=正式
    humor: float = 0.3          # 0=严肃 1=爱开玩笑
    topic_follow: float = 0.5   # 0=自说自话 1=紧跟用户话题

    def to_prompt_block(self) -> str:
        def desc(v: float, low: str, mid: str, high: str) -> str:
            return low if v < 0.33 else high if v > 0.66 else mid
        return (
            "【风格参数（学习所得，自然遵循即可，不必刻意）】\n"
            f"- 回复长度：{desc(self.reply_length, '简短回应', '中等长度', '可以详细展开')}\n"
            f"- 语气：{desc(self.formality, '随意放松', '日常自然', '正式礼貌')}\n"
            f"- 幽默感：{desc(self.humor, '认真为主', '偶尔轻松', '喜欢开玩笑')}\n"
            f"- 话题跟随：{desc(self.topic_follow, '按自己节奏', '跟随用户话题', '紧紧跟随用户说的内容')}"
        )

    def snapshot(self) -> dict:
        return {
            "reply_length": round(self.reply_length, 3),
            "formality": round(self.formality, 3),
            "humor": round(self.humor, 3),
            "topic_follow": round(self.topic_follow, 3),
        }


# ---------- 隐式反馈信号 ----------

POSITIVE_WORDS = ("哈哈", "好玩", "喜欢", "有道理", "说得对", "对呀", "同意", "👍", "谢谢", "不错", "有意思")
NEGATIVE_WORDS = ("别", "不喜", "无聊", "没意思", "算了", "敷衍", "啰嗦", "太长", "太短", "不对", "错了")
CORRECTION_WORDS = ("不是", "不对", "错了", "你理解错", "没听懂", "换个说法")


@dataclass
class FeedbackSignal:
    """一轮对话提取的隐式反馈（全部由规则从文本/行为计算，不调 LLM）。"""

    user_len: int = 0
    reply_len: int = 0
    positive: bool = False          # 用户积极词
    negative: bool = False          # 用户消极词
    correction: bool = False        # 用户纠正
    user_asked: bool = False        # 用户提了问（需要详细回答）
    topic_continuation: bool = False  # 用户延续了 agent 的话题（粗略：消息里含上一轮关键词）

    @property
    def reward(self) -> float:
        """整体奖励 -1~1（正=当前风格受欢迎，负=要调整）。"""
        r = 0.0
        if self.positive:
            r += 0.5
        if self.negative:
            r -= 0.5
        if self.correction:
            r -= 0.8
        if self.user_asked:
            r += 0.2  # 用户想聊，倾向详细
        return max(-1.0, min(1.0, r))


def extract_feedback(user_text: str, reply: str, prev_reply: str = "") -> FeedbackSignal:
    """从一轮对话提取隐式反馈信号（纯规则）。"""
    s = FeedbackSignal(
        user_len=len(user_text),
        reply_len=len(reply),
        positive=any(w in user_text for w in POSITIVE_WORDS),
        negative=any(w in user_text for w in NEGATIVE_WORDS),
        correction=any(w in user_text for w in CORRECTION_WORDS),
        user_asked=("?" in user_text or "？" in user_text or "吗" in user_text or "什么" in user_text),
    )
    # 话题延续：用户消息包含上一轮回复中的任意双字词（粗略信号）
    if prev_reply:
        chars = re.findall(r"[\u4e00-\u9fff]", prev_reply)
        bigrams = {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}
        s.topic_continuation = any(b in user_text for b in bigrams)
    return s


# ---------- 风格学习器（多臂老虎机 + EMA） ----------

EMA_ALPHA = 0.2          # EMA 平滑系数（周尺度保守演化）
BANDIT_LR = 0.05         # bandit 建议值的学习率


class StyleLearner:
    """风格参数学习器。

    每个参数 = 一个多臂老虎机（3 臂：升/保持/降），按反馈奖励更新；
    最终参数 = EMA 平滑 + bandit 倾向微调。保守演化：单步变化有上限。
    """

    def __init__(self, params: StyleParams | None = None, *, persist_path: str | None = None):
        self.params = params or StyleParams()
        self.persist_path = persist_path
        # 每个参数一个 3 臂计数（升/降/保持的累计奖励）
        self._bandits: dict[str, list[float]] = {p: [0.0, 0.0, 0.0] for p in STYLE_PARAMS}
        self._steps = 0

    # ---------- 学习 ----------

    def observe(self, signal: FeedbackSignal) -> dict:
        """用一轮反馈更新参数。返回变更摘要。"""
        self._steps += 1
        reward = signal.reward
        delta: dict[str, float] = {}

        # 规则 → 各参数目标方向，累积到 bandit 计数（|reward|：只记录"方向被要求的强度"，方向语义已由规则编码）
        targets = self._rule_targets(signal)
        for p, direction in targets.items():  # direction: -1 降 / 0 保持 / +1 升
            idx = 1 + direction
            self._bandits[p][idx] += abs(reward) * BANDIT_LR

        # EMA 平滑更新（有反馈才漂移，无反馈保持 → 保守演化）
        for p in STYLE_PARAMS:
            bandit = self._bandits[p]
            best = max(range(3), key=lambda i: bandit[i])
            drift = (best - 1) * 0.02 * abs(reward)
            new_v = self.params.__dict__[p] * (1 - EMA_ALPHA) + (
                self.params.__dict__[p] + drift
            ) * EMA_ALPHA
            new_v = max(0.05, min(0.95, new_v))
            if abs(new_v - self.params.__dict__[p]) > 1e-4:
                delta[p] = round(new_v - self.params.__dict__[p], 4)
            self.params.__dict__[p] = new_v

        return {"steps": self._steps, "delta": delta, "reward": round(reward, 2)}

    def _rule_targets(self, s: FeedbackSignal) -> dict[str, int]:
        """规则 → 各参数目标方向（-1 降 / 0 保持 / +1 升）。"""
        t = {p: 0 for p in STYLE_PARAMS}
        if s.correction:
            t["topic_follow"] = 1   # 理解错了 → 更紧跟用户
            t["reply_length"] = -1 if s.reply_len > 250 else 0
        if s.negative and s.reply_len > 300:
            t["reply_length"] = -1  # 用户嫌长
        if s.user_asked:
            t["reply_length"] = 1 if s.reply_len < 120 else 0  # 提问应详细
            t["topic_follow"] = 1
        if s.positive:
            t["humor"] = 1 if s.reply_len < 150 else 0  # 用户回应轻松 → 保持轻松倾向
        return t

    # ---------- 持久化与回滚 ----------

    def snapshot(self) -> dict:
        return {"params": self.params.snapshot(), "bandits": self._bandits, "steps": self._steps}

    def save(self) -> None:
        if not self.persist_path:
            return
        Path(self.persist_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.persist_path).write_text(json.dumps(self.snapshot(), ensure_ascii=False), encoding="utf-8")

    def load(self) -> bool:
        if not self.persist_path or not Path(self.persist_path).exists():
            return False
        try:
            data = json.loads(Path(self.persist_path).read_text(encoding="utf-8"))
            for p in STYLE_PARAMS:
                self.params.__dict__[p] = float(data["params"][p])
                self._bandits[p] = [float(x) for x in data["bandits"][p]]
            self._steps = int(data["steps"])
            return True
        except Exception as e:
            logger.warning("style learner load failed: %s", e)
            return False

    def reset(self) -> None:
        """reset --style：恢复默认参数（核心人格不受影响）。"""
        self.params = StyleParams()
        self._bandits = {p: [0.0, 0.0, 0.0] for p in STYLE_PARAMS}
        self._steps = 0
        if self.persist_path:
            Path(self.persist_path).unlink(missing_ok=True)


# ---------- 语言镜像 ----------

MIRROR_TOP_N = 5          # 最多镜像多少个词
MIRROR_MAX_USES = 3       # 单个词使用上限（防刻意）


class LanguageMirror:
    """用户高频词统计，偶尔自然沿用（带使用上限）。"""

    def __init__(self, *, persist_path: str | None = None):
        self._counter: dict[str, int] = {}
        self._uses: dict[str, int] = {}
        self.persist_path = persist_path

    def observe(self, user_text: str) -> None:
        """统计用户消息中的中文双字词（粗略：连续 2-4 字片段）。"""
        words = re.findall(r"[\u4e00-\u9fff]{2,4}", user_text)
        for w in words:
            self._counter[w] = self._counter.get(w, 0) + 1

    def pick(self, n: int = 1) -> list[str]:
        """选 n 个高频且未超使用上限的词（带随机性，防刻意）。"""
        candidates = [
            w for w, c in sorted(self._counter.items(), key=lambda kv: -kv[1])
            if c >= 2 and self._uses.get(w, 0) < MIRROR_MAX_USES
        ]
        if not candidates:
            return []
        picked = random.sample(candidates[: MIRROR_TOP_N * 3], min(n, len(candidates[: MIRROR_TOP_N * 3])))
        for w in picked:
            self._uses[w] = self._uses.get(w, 0) + 1
        return picked

    def to_prompt_block(self) -> str:
        """注入 prompt：偶尔自然沿用这些词（可选，非强制）。"""
        words = self.pick(2)
        if not words:
            return ""
        return f"【语言镜像】用户偶尔喜欢用这些词：{'、'.join(words)}。你可以偶尔自然沿用其中一两个，不要刻意。"

    def stats(self) -> dict:
        return {"top": dict(sorted(self._counter.items(), key=lambda kv: -kv[1])[:10]), "uses": self._uses}

    def save(self) -> None:
        if not self.persist_path:
            return
        Path(self.persist_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.persist_path).write_text(
            json.dumps({"counter": self._counter, "uses": self._uses}, ensure_ascii=False), encoding="utf-8"
        )

    def load(self) -> bool:
        if not self.persist_path or not Path(self.persist_path).exists():
            return False
        try:
            data = json.loads(Path(self.persist_path).read_text(encoding="utf-8"))
            self._counter = {k: int(v) for k, v in data["counter"].items()}
            self._uses = {k: int(v) for k, v in data["uses"].items()}
            return True
        except Exception as e:
            logger.warning("mirror load failed: %s", e)
            return False

    def reset(self) -> None:
        self._counter = {}
        self._uses = {}
        if self.persist_path:
            Path(self.persist_path).unlink(missing_ok=True)
