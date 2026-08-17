"""MVP2 承诺机制（DESIGN.md 5.4 显性反馈承诺）。

- 用户明确要求/请求 → 高优先级承诺记录（procedural 层记忆 + meta.promise 标记）
- 后续对话中触发相关话题时注入提醒
- 定期自我检讨（每 N 轮注入"我答应过你的事"）
"""

from __future__ import annotations

import logging
import re

from ..memory.store import MemoryStore

logger = logging.getLogger(__name__)

# 承诺意图识别：用户明确要求 agent 做某事
PROMISE_PATTERNS = [
    r"(?:记得|别忘了|答应我|一定要|务必).{0,20}(?:提醒|告诉|叫我|喊我)",
    r"(?:提醒|告诉我|叫我).{0,20}(?:要|去|做|记得|别忘了)",
    r"帮(?:我|人家).{0,20}(?:记|留意|看着|提醒)",
    r"(?:明天|下周|过几天|到时候|下次|每天).{0,10}(?:提醒|记得|别忘了)",
    r"你(?:要|得|可以|能).{0,20}(?:记住|提醒|记着)",
]

# 承诺检索关键词（触发相关话题时提醒）
PROMISE_TRIGGER_HINT = "我答应过你的事"


class PromiseBook:
    """承诺账本：识别 → 记录（procedural 层）→ 检索 → 检讨。"""

    def __init__(self, memory: MemoryStore):
        self.memory = memory

    # ---------- 识别与记录 ----------

    def extract(self, user_text: str) -> str | None:
        """从用户消息识别承诺意图，返回承诺文本（规范化）或 None。"""
        for pat in PROMISE_PATTERNS:
            m = re.search(pat, user_text)
            if m:
                return self._normalize(user_text)
        return None

    @staticmethod
    def _normalize(text: str) -> str:
        return text.strip()[:120]

    def record(self, user_text: str) -> int | None:
        """识别并记录承诺。返回记忆 id 或 None。"""
        promise = self.extract(user_text)
        if not promise:
            return None
        entry = self.memory.store(
            "procedural",
            f"承诺：{promise}",
            importance=0.9,
            confidence=0.9,
            provenance="promise-book",
            category="promise",
            meta={"promise": True, "status": "open"},
        )
        logger.info("promise recorded: #%s %s", entry.id, promise[:40])
        return entry.id

    # ---------- 检索与注入 ----------

    def open_promises(self, limit: int = 10) -> list:
        """未兑现承诺（procedural 层 promise 标记且最新版本 status=open）。

        版本链语义：同 provenance 记录取 version 最大者（最新状态）。
        """
        rows = self.memory.con.execute(
            """SELECT * FROM memories m WHERE layer='procedural'
               AND version = (SELECT max(version) FROM memories
                              WHERE layer='procedural' AND provenance = m.provenance)
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            e = self.memory._row_to_entry(r)
            if e.meta.get("promise") and e.meta.get("status") == "open":
                out.append(e)
        return out

    def to_prompt_block(self, query_hint: str = "") -> str:
        """注入 prompt：相关承诺提醒 + 定期检讨。

        - 有开放承诺时：注入（用户提及相关内容时模型应主动想起）
        - 检索：用承诺内容与当前对话做语义召回，命中才注入（避免每轮都灌）
        """
        promises = self.open_promises()
        if not promises:
            return ""
        # 语义召回：当前话题与承诺相关性（用最近消息做查询）
        hits = []
        if query_hint:
            rec = self.memory.recall(query_hint, top_k=5, layer="procedural")
            hit_ids = {e.id for e in rec}
            hits = [e for e in promises if e.id in hit_ids]
        if not hits:
            # 无相关命中时只做低强度检讨（设计：定期自我检讨）
            hits = promises[:2]
        lines = [f"- {e.content[:80]}" for e in hits]
        return "【我答应过你的事（要记得履行，必要时主动提起）】\n" + "\n".join(lines)

    def mark_done(self, promise_id: int) -> None:
        """兑现标记（版本链更新状态，内容保留）。"""
        entry = self.memory.get(promise_id)
        if entry is None:
            raise KeyError(promise_id)
        self.memory.update_latest(promise_id, entry.content, confidence=1.0, meta={"status": "done"})
        logger.info("promise #%s marked done", promise_id)
