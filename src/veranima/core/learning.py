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
    delay: float = 0.0              # 用户读完上一条回复后到本条消息的间隔（秒）

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
        # 延迟信号：读得久（>30s）说明回复有内容值得思考；秒回长回复说明没读完
        if self.delay > 30:
            r += 0.1
        if self.delay < 3 and self.reply_len > 300:
            r -= 0.2
        return max(-1.0, min(1.0, r))


def extract_feedback(user_text: str, reply: str, prev_reply: str = "", delay: float = 0.0) -> FeedbackSignal:
    """从一轮对话提取隐式反馈信号（纯规则）。"""
    s = FeedbackSignal(
        user_len=len(user_text),
        reply_len=len(reply),
        positive=any(w in user_text for w in POSITIVE_WORDS),
        negative=any(w in user_text for w in NEGATIVE_WORDS),
        correction=any(w in user_text for w in CORRECTION_WORDS),
        user_asked=("?" in user_text or "？" in user_text or "吗" in user_text or "什么" in user_text),
        delay=delay,
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
    M-6（MEMORY_SPEC 13）：profile 为慢变量（用户文风统计），feedback 为快变量，
    两个来源分别记录，prompt 合并为 UserStyleBrief。
    """

    def __init__(self, params: StyleParams | None = None, *, persist_path: str | None = None):
        self.params = params or StyleParams()
        self.profile = UserStyleProfile()
        self.persist_path = persist_path
        # 每个参数一个 3 臂计数（升/降/保持的累计奖励）
        self._bandits: dict[str, list[float]] = {p: [0.0, 0.0, 0.0] for p in STYLE_PARAMS}
        self._steps = 0

    # ---------- 学习 ----------

    def observe(self, signal: FeedbackSignal, user_text: str | None = None) -> dict:
        """用一轮反馈更新参数（快变量）+ 用户文风画像（慢变量）。返回变更摘要。"""
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

        # M-6 慢变量：用户文风画像（仅合格样本）
        if user_text and is_style_sample(user_text):
            self.profile.observe(user_text)

        return {"steps": self._steps, "delta": delta, "reward": round(reward, 2),
                "style_samples": self.profile.sample_count}

    def to_prompt_block(self) -> str:
        """M-6（MEMORY_SPEC 13.5/13.6）：稳定画像摘要 + 风格参数合并为 UserStyleBrief。

        画像不成熟时只注入参数块（向后兼容）。
        """
        param_block = self.params.to_prompt_block()
        profile_block = self.profile.to_prompt_block()
        if not profile_block:
            return param_block
        return param_block + "\n\n" + profile_block

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

    SCHEMA_VERSION = 2  # M-6：style.json 版本（v1 无 profile → 迁移）

    def snapshot(self) -> dict:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "params": self.params.snapshot(),
            "bandits": self._bandits,
            "steps": self._steps,
            "profile": self.profile.snapshot(),
        }

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
            # M-6：v1 旧文件无 profile → 默认值迁移（缺字段用默认）
            prof = data.get("profile", {})
            for k, v in prof.items():
                if hasattr(self.profile, k) and isinstance(v, (int, float, str)):
                    setattr(self.profile, k, v)
            return True
        except Exception as e:
            logger.warning("style learner load failed: %s", e)
            return False

    def reset(self) -> None:
        """reset --style：恢复默认参数与画像（核心人格不受影响）。"""
        self.params = StyleParams()
        self.profile = UserStyleProfile()
        self._bandits = {p: [0.0, 0.0, 0.0] for p in STYLE_PARAMS}
        self._steps = 0
        if self.persist_path:
            Path(self.persist_path).unlink(missing_ok=True)


# ---------- M-6 用户文风画像（MEMORY_SPEC 13） ----------

# 排除样本：命令/代码/URL/引用/太短/纯标点
STYLE_SAMPLE_EXCLUDE = (
    "http://", "https://", "```", "git ", "cd ", "pip ", "npm ", "python ",
    "def ", "import ", "C:\\", "D:\\", "SELECT ", "INSERT ", "curl ", "mkdir ",
    "rm ", "cp ", "mv ", "cat ", "echo ",
)


def is_style_sample(user_text: str) -> bool:
    """MEMORY_SPEC 13.2：只有合格自然消息进入文风统计。"""
    if not user_text or len(user_text.strip()) < 4:
        return False
    t = user_text.strip()
    if any(k in t.lower() for k in STYLE_SAMPLE_EXCLUDE):
        return False
    # 纯标点/符号（无中文无字母无数字）
    return any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in t)


@dataclass
class UserStyleProfile:
    """MEMORY_SPEC 13.3：稳定文风统计画像（聚合值，不保存原文）。"""

    sample_count: int = 0
    char_count: int = 0
    avg_message_chars: float = 0.0
    avg_sentence_chars: float = 0.0
    question_ratio: float = 0.0
    newline_ratio: float = 0.0
    emoji_ratio: float = 0.0
    exclamation_ratio: float = 0.0
    ellipsis_ratio: float = 0.0
    parenthetical_ratio: float = 0.0
    ascii_ratio: float = 0.0
    japanese_ratio: float = 0.0
    formality: float = 0.5
    directness: float = 0.5
    detail_preference: float = 0.5
    confidence: float = 0.0
    updated_at: str = ""

    EMA_ALPHA = 0.05        # MEMORY_SPEC 13.4：慢速演化
    MAX_STEP = 0.02         # 单轮最大变化
    MIN_SAMPLES = 20        # 画像生效所需样本数

    def observe(self, text: str, *, now: str | None = None) -> None:
        """单条合格样本 → EMA 更新聚合统计（13.4：单轮变化有上限）。"""
        from datetime import datetime, timezone

        t = text.strip()
        n = self.sample_count
        new_count = n + 1
        total_chars = self.char_count + len(t)
        self.avg_message_chars = total_chars / new_count
        # 句子平均：按中文句号/问号/叹号/换行切分
        sentences = [s for s in re.split(r"[。！？!?\n]", t) if s.strip()]
        if sentences:
            avg_s = sum(len(s) for s in sentences) / len(sentences)
            self.avg_sentence_chars = self.avg_sentence_chars + (avg_s - self.avg_sentence_chars) * self.EMA_ALPHA
        # 比率类：EMA 平滑
        q = (t.count("？") + t.count("?")) / max(1, len(t))
        nl = t.count("\n") / max(1, len(t))
        emoji = len(re.findall(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", t)) / max(1, len(t))
        ex = (t.count("！") + t.count("!")) / max(1, len(t))
        paren = (t.count("（") + t.count("(")) / max(1, len(t))
        ascii_ = sum(1 for ch in t if ord(ch) < 128 and ch.isalnum()) / max(1, len(t))
        jp = len(re.findall(r"[\u3040-\u30ff]", t)) / max(1, len(t))
        # 正式度：敬语/礼貌词密度（"请/麻烦/谢谢/您"）
        polite = sum(t.count(w) for w in ("请", "麻烦", "谢谢", "您", "是否", "能否")) / max(1, len(t))
        # 直接度：命令式/结论先行（祈使词 + 短句比例）
        imperative = sum(t.count(w) for w in ("帮我", "给我", "直接", "尽快", "赶紧", "记得")) / max(1, len(t))
        short_sentence = sum(1 for s in sentences if len(s) <= 8) / max(1, len(sentences))
        ell = (t.count("…") + t.count("...")) / max(1, len(t) / 3)

        def ema(old: float, new: float) -> float:
            step = (new - old) * self.EMA_ALPHA
            step = max(-self.MAX_STEP, min(self.MAX_STEP, step))
            return max(0.0, min(1.0, old + step))

        self.question_ratio = ema(self.question_ratio, q)
        self.newline_ratio = ema(self.newline_ratio, nl)
        self.emoji_ratio = ema(self.emoji_ratio, emoji)
        self.exclamation_ratio = ema(self.exclamation_ratio, ex)
        self.ellipsis_ratio = ema(self.ellipsis_ratio, min(1.0, ell))
        self.parenthetical_ratio = ema(self.parenthetical_ratio, paren)
        self.ascii_ratio = ema(self.ascii_ratio, ascii_)
        self.japanese_ratio = ema(self.japanese_ratio, jp)
        self.formality = ema(self.formality, min(1.0, polite * 8))
        self.directness = ema(self.directness, min(1.0, imperative * 8 + short_sentence * 0.5))
        # 展开偏好：平均长度 > 60 字符 → 偏好详细
        self.detail_preference = ema(self.detail_preference, 1.0 if self.avg_message_chars > 60 else 0.0)
        self.sample_count = new_count
        self.char_count = total_chars
        self.confidence = min(1.0, new_count / self.MIN_SAMPLES)
        self.updated_at = now or datetime.now(timezone.utc).isoformat(timespec="seconds")

    def is_mature(self) -> bool:
        """13.4：至少 MIN_SAMPLES 条合格样本才生效。"""
        return self.sample_count >= self.MIN_SAMPLES and self.confidence >= 0.3

    def to_prompt_block(self) -> str:
        """13.6 契约：注入稳定画像摘要（非数值表），≤300 字符。"""
        if not self.is_mature():
            return ""
        parts: list[str] = []
        # 长度偏好
        if self.avg_message_chars < 15:
            parts.append("习惯用很短的句子")
        elif self.avg_message_chars < 40:
            parts.append("通常用中短句")
        elif self.avg_message_chars < 80:
            parts.append("会用较长的叙述")
        else:
            parts.append("习惯大段展开")
        if self.question_ratio > 0.15:
            parts.append("爱提问，期望得到回应")
        if self.emoji_ratio > 0.03:
            parts.append("偶尔用 emoji")
        if self.parenthetical_ratio > 0.03:
            parts.append("常用括号补充说明")
        if self.formality > 0.6:
            parts.append("语气偏正式")
        elif self.formality < 0.3:
            parts.append("语气很随意")
        if self.directness > 0.6:
            parts.append("说话直接、结论先行")
        if self.detail_preference > 0.6:
            parts.append("愿意听详细说明")
        elif self.detail_preference < 0.35:
            parts.append("偏好简短回应")
        if not parts:
            return ""
        joined = "、".join(parts)
        block = (
            f"【用户交流偏好】{joined}。\n"
            "请在保持角色自身说话方式的前提下适度适应，不要模仿口癖或复述用户句子。"
        )
        return block[:300]

    def snapshot(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "char_count": self.char_count,
            "avg_message_chars": round(self.avg_message_chars, 2),
            "avg_sentence_chars": round(self.avg_sentence_chars, 2),
            "question_ratio": round(self.question_ratio, 4),
            "newline_ratio": round(self.newline_ratio, 4),
            "emoji_ratio": round(self.emoji_ratio, 4),
            "exclamation_ratio": round(self.exclamation_ratio, 4),
            "ellipsis_ratio": round(self.ellipsis_ratio, 4),
            "parenthetical_ratio": round(self.parenthetical_ratio, 4),
            "ascii_ratio": round(self.ascii_ratio, 4),
            "japanese_ratio": round(self.japanese_ratio, 4),
            "formality": round(self.formality, 4),
            "directness": round(self.directness, 4),
            "detail_preference": round(self.detail_preference, 4),
            "confidence": round(self.confidence, 4),
            "updated_at": self.updated_at,
        }


# ---------- 语言镜像 ----------

MIRROR_TOP_N = 5          # 最多镜像多少个词
MIRROR_MAX_USES = 3       # 单个词使用上限（防刻意）
# M-6 镜像过滤（MEMORY_SPEC 13.7）：停用词/内容词不镜像
MIRROR_STOPWORDS = {
    "这个", "那个", "什么", "怎么", "我们", "你们", "他们", "自己", "时候",
    "今天", "明天", "昨天", "现在", "就是", "还是", "因为", "所以", "但是",
    "然后", "觉得", "知道", "可以", "应该", "没有", "一个", "一下", "有点",
}
# 敏感词（不进入镜像/画像）
MIRROR_SENSITIVE = {"密码", "验证码", "卡号", "私钥", "密钥", "token", "password"}


class LanguageMirror:
    """用户高频词统计，偶尔自然沿用（带使用上限 + M-6 停用词/敏感词过滤）。"""

    def __init__(self, *, persist_path: str | None = None):
        self._counter: dict[str, int] = {}
        self._uses: dict[str, int] = {}
        self.persist_path = persist_path

    def observe(self, user_text: str) -> None:
        """统计用户消息中的候选词（M-6：过滤停用词/敏感词/过短片段/内容实体）。"""
        words = re.findall(r"[\u4e00-\u9fff]{2,4}", user_text)
        for w in words:
            if w in MIRROR_STOPWORDS or w in MIRROR_SENSITIVE:
                continue
            if w in user_text and w.count(w[0]) == len(w):
                continue  # 重复字片段（"好好好"）
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
