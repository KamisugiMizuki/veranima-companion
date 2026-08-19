"""R0 打断决策（R0_SPEC 5）+ 共享话题频率表。

- TopicFrequency：话题复现计数（语义相似度低配：提取 ≥4 字中文片段做指纹，
  相似话题共享片段 → count 增长）。同时服务 4.4 复述频率修正与 4.5 话题复现计数。
- InterruptDecider：L0-L3 打断分级（第 3 次 L1 轻推 / 第 4 次 L2 转移）+ 自愈回退
  （负面反馈 → 30min 冷却 + 阈值提高一级）。

ponytail: 语义指纹用片段集合（无分词依赖）；要精确语义聚类时换 embedding 归类。
"""
from __future__ import annotations

import re
import time

_CN_FRAG = re.compile(r"[\u4e00-\u9fff]{3,}")


class TopicFrequency:
    """话题复现计数器（共享表：断片复述频率 + 打断计数共用）。

    归并策略：新消息指纹与已有话题算 Jaccard 重叠，≥ 0.2 视为同一话题。
    ponytail: O(话题数) 线性归并，话题量小可接受；量大时换倒排索引。
    """

    MERGE_THRESHOLD = 0.05  # 短句共享任一 3-gram 即视为同话题（Jaccard 天然偏低）

    def __init__(self, now: float | None = None) -> None:
        self._counts: dict[frozenset, dict] = {}
        self._now = now

    def _t(self) -> float:
        return self._now if self._now is not None else time.time()

    @staticmethod
    def fingerprint(text: str) -> frozenset:
        """话题指纹：中文 3-gram 集合（相似话题共享 n-gram，无需分词）。

        ponytail: 3-gram 低配语义归类；要精确聚类时换 embedding。
        """
        grams = set()
        for m in _CN_FRAG.findall(text):
            for i in range(len(m) - 2):
                grams.add(m[i:i + 3])
        return frozenset(grams)

    @staticmethod
    def overlap(a: frozenset, b: frozenset) -> float:
        """Jaccard 重叠度。"""
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def note(self, text: str) -> int:
        """记录一次话题提及，返回累计次数（0=无指纹不计数）。"""
        fp = self.fingerprint(text)
        if not fp:
            return 0
        # 与已有话题归并（重叠度 ≥ 阈值视为同一话题）
        for key, entry in self._counts.items():
            if self.overlap(fp, key) >= self.MERGE_THRESHOLD:
                entry["count"] += 1
                entry["last"] = self._t()
                return entry["count"]
        self._counts[fp] = {"count": 1, "last": self._t()}
        return 1

    def count(self, text: str) -> int:
        fp = self.fingerprint(text)
        for key, entry in self._counts.items():
            if self.overlap(fp, key) >= self.MERGE_THRESHOLD:
                return entry["count"]
        return 0


class InterruptDecider:
    """打断决策器（DESIGN 4.5 L0-L3 分级 + 自愈回退）。"""

    def __init__(self, now: float | None = None) -> None:
        self._now = now
        self._cooldown_until = 0.0     # 负面反馈后 30min 冷却
        self._level_boost = 0          # 自愈：阈值提高一级

    def _t(self) -> float:
        return self._now if self._now is not None else time.time()

    def decide(self, topic_count: int, *, prob: float = 0.5) -> int:
        """按话题复现次数返回打断级别 L0-L3（DESIGN 4.5 表）。

        L0=不打断；第 3 次（40~60% 概率）L1 轻推；第 4 次（20~40%）L2 转移。
        prob 为随机数（0~1），由调用方注入保证可测。
        """
        if self._t() < self._cooldown_until:
            return 0  # 冷却期不打断
        n = topic_count - 2 - self._level_boost  # 第 1-2 次 → n≤0；第 3 次 → n=1
        if n <= 0:
            return 0
        if n == 1:
            return 1 if prob < 0.5 else 0   # 第 3 次 → L1（40~60% 区间简化为 50%）
        if n == 2:
            return 2 if prob < 0.3 else 0   # 第 4 次 → L2（20~40% 区间简化为 30%）
        return 2  # 更频繁 → L2

    def note_negative(self) -> None:
        """自愈回退：用户负面反馈 → 30min 冷却 + 阈值提高一级。"""
        self._cooldown_until = self._t() + 1800.0
        self._level_boost += 1
