"""用户近况对照（USER_MOOD_SPEC P1，2026-09-20）。

确定性统计、零 LLM、零新表：只回答「要不要让判断点多看一眼」，定性归判断点
（§2.2）。判据按 P0 离线校准定稿（§2.1）：稳健中心基线 + 相邻重复不计 +
≥2 条不同文本。铁律：不点破、fail-open、宁漏勿误（§2.4）。
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence

logger = logging.getLogger(__name__)

BASE_WINDOW = 50            # 基线 = 最近 50 条本角色用户消息
BASE_MIN_SAMPLES = 20       # 样本不足不判（画像未成熟 = 静默）
BASE_MIN_CHARS = 15.0       # 基线本就极短 ⇒ 长度没有分辨力，永不命中
RUN_MIN = 4                 # 连续短句条数
WINDOW_MAX = 6              # 窗口最多 6 条
SESSION_GAP_MINUTES = 180   # 同一会话：3 小时内
SHORT_ABS = 6               # 绝对短：字数 < 6
SHORT_RATIO = 0.5           # 相对短：不高于基线一半
NORMAL_CHARS = 12           # 「这条写得正常」的门槛（同日纠正用）

# 第二信号（只加权、不独立触发）：表情 / 感叹号 / 省略号
_EXPRESSIVE_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF!！?？…]|\.\.\.")


def _len(text: str) -> int:
    return len(str(text or "").strip())


def _is_short(n: int, base: float) -> bool:
    return n < SHORT_ABS or n <= SHORT_RATIO * base


def _stamp(row: Mapping) -> dt.datetime | None:
    raw = str(row.get("created_at") or "").strip()
    if not raw:
        return None
    try:
        value = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _tail_window(rows: Sequence[Mapping]) -> list[str]:
    """最近一段会话的窗口（末条之前 180 分钟内的用户消息，最多 6 条）。"""
    stamps = [_stamp(r) for r in rows]
    last = next((s for s in reversed(stamps) if s), None)
    kept: list[str] = []
    for row, stamp in zip(rows, stamps):
        text = str(row.get("content") or "").strip()
        if not text:
            continue
        if last and stamp and (last - stamp) > dt.timedelta(minutes=SESSION_GAP_MINUTES):
            kept = []
            continue
        kept.append(text)
    return kept[-WINDOW_MAX:]


def _trailing_short_run(window: Sequence[str], base: float) -> tuple[int, int, str]:
    """从末尾往回数连续短句：相邻重复内容不计入连发（重发=重试，不是没力气）。

    返回 (连续短句数, 其中不同文本数, 连发原文用 ' / ' 拼的一行)。
    """
    dedup = [t for i, t in enumerate(window) if i == 0 or t != window[i - 1]]
    run: list[str] = []
    for text in reversed(dedup):
        if _is_short(_len(text), base):
            run.append(text)
            continue
        break
    run.reverse()
    return len(run), len(set(run)), " / ".join(run)


@dataclass(frozen=True)
class MoodComparison:
    """一次对照的结论：hit=要不要让判断点多看一眼（不是情绪结论）。"""

    hit: bool = False
    base: float = 0.0
    run: int = 0
    distinct: int = 0
    window: tuple[str, ...] = ()
    expressive_drop: bool = False
    reason: str = ""            # 未命中的原因（留痕/诊断用）

    def __bool__(self) -> bool:
        return self.hit

    def context_block(self) -> str:
        """送判附加（条件注入，§2.2）：对照事实 + 最近原话 + 偏离级取值要求。"""
        recent = " / ".join(self.window[-RUN_MIN:]) or "（略）"
        extra = "，而且最近几条里没有平时的表情/标点" if self.expressive_drop else ""
        return (
            "\n\n【他最近的状态对照】他平时消息长度约 {base:.0f} 字{extra}；"
            "最近连着 {run} 条很短：{recent}。\n"
            "这可能代表低落 / 焦虑 / 烦躁，也可能只是忙、在打游戏、赶路。"
            "请在 JSON 里多给一格 user_mood：\"low\"（明显偏离且内容负面）/ "
            "\"mild\"（疑似偏离、证据不足）/ \"none\"（无偏离或判不出）。"
            "拿不准一律 none。判断只写进 JSON，不要在回复里点破。"
        ).format(base=self.base, extra=extra, run=self.run, recent=recent)


def compare(rows: Sequence[Mapping], *, role_id: str = "") -> MoodComparison:
    """对照最近窗口与基线（rows=按时间正序的用户消息行，content/created_at）。

    任何异常都 fail-open 成「未命中」——对照层不阻塞对话（§2.4-2）。
    """
    try:
        texts = [str(r.get("content") or "").strip() for r in rows]
        texts = [t for t in texts if t]
        if len(texts) < BASE_MIN_SAMPLES:
            return MoodComparison(reason="samples")
        base = float(statistics.median(_len(t) for t in texts[-BASE_WINDOW:]))
        if base < BASE_MIN_CHARS:
            return MoodComparison(base=base, reason="baseline_too_short")
        window = _tail_window(rows)
        run, distinct, joined = _trailing_short_run(window, base)
        if run < RUN_MIN:
            return MoodComparison(base=base, run=run, reason="run")
        if distinct < 2:
            return MoodComparison(base=base, run=run, distinct=distinct, reason="repeat")
        tail = [str(r.get("content") or "") for r in rows[-BASE_WINDOW:]]
        base_exp = sum(1 for t in tail if _EXPRESSIVE_RE.search(t)) / max(1, len(tail))
        win_exp = sum(1 for t in window if _EXPRESSIVE_RE.search(t)) / max(1, len(window))
        drop = base_exp > 0.15 and win_exp == 0.0
        logger.info("user mood comparison hit: base=%.1f run=%d role=%s", base, run, role_id)
        return MoodComparison(hit=True, base=base, run=run, distinct=distinct,
                              window=tuple(window), expressive_drop=drop, reason=joined)
    except Exception:
        logger.debug("mood compare failed (fail-open)", exc_info=True)
        return MoodComparison(reason="error")


def is_normal_message(text: str) -> bool:
    """这条写得正常（够长）——同日纠正的证据门槛：短句的 none 不算证据（§2.3.1）。"""
    return _len(text) >= NORMAL_CHARS
