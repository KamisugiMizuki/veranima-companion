"""牵挂账本（MIND_LOOP_SPEC M1）。

角色两次开口之间仍在演进的心智状态：每条=一件挂着的事，有来源、强度、
下一次演进时刻与剧本步骤。本模块只做**确定性生命周期**（产生/推进/消退/
消费取材）；剧本内容的编写（下一步是什么、几点演）归夜眠消化（M3），
M1 阶段用保守默认脚本（强度半衰 + 到点自然淡出）。

设计复用既有哲学：
- 程序驱动日间状态（零 LLM 成本），LLM 只在开口取材和夜间消化时参与
- fail-open：任何异常不影响主聊天链路
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent import Agent

logger = logging.getLogger(__name__)

# 衰减：每天 ×0.85；低于阈值自然关闭（真人也不会永远惦记一顿午饭）
_HALF_LIFE_FACTOR = 0.85
_CLOSE_THRESHOLD = 0.15
# 对话注入取前 N 条；主动素材同频去重窗口
_TOP_N = 3


def _naive(iso: str) -> dt.datetime | None:
    try:
        d = dt.datetime.fromisoformat(str(iso))
        return d.replace(tzinfo=None) if d.tzinfo is None else d.astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


class ThreadLedger:
    """牵挂账本门面（挂在 agent.threads）。role_key 为空（PC/QQ 单角色时代）
    照常工作——用卡名做键，行为一致。"""

    def __init__(self, agent: "Agent") -> None:
        self.agent = agent
        self.role = agent.role_key or agent.card.name

    # ---------- 产生 ----------

    def from_schedule_event(self, summary: str, *, intensity: float = 0.55) -> int:
        """日终摘要里挑得出牵挂：只收有信息量的（含失败/等待/完成语义），
        纯数字报表不开线（那是 moments D01 的活，不是心事）。"""
        s = str(summary or "").strip()
        if not s or len(s) < 6:
            return 0
        topic = s[:60]
        return self.agent.memory.thread_add(self.role, topic, "schedule", intensity=intensity)

    def from_user(self, text: str, *, intensity: float = 0.7) -> int:
        """judges.thread_candidate 裁决命中：用户说了一件她该记在心的事。"""
        s = str(text or "").strip()
        if not s:
            return 0
        return self.agent.memory.thread_add(self.role, s[:60], "user", intensity=intensity)

    def from_promise(self, content: str) -> int:
        return self.agent.memory.thread_add(self.role, content[:60], "promise", intensity=0.65)

    # ---------- 推进（tick 驱动，纯算术） ----------

    def tick(self, now: dt.datetime | None = None) -> None:
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone().replace(tzinfo=None)
        try:
            rows = self.agent.memory.thread_list(self.role)
        except Exception:
            logger.debug("thread tick list failed", exc_info=True)
            return
        for r in rows:
            age_days = (now - (_naive(r["updated_at"]) or now)).total_seconds() / 86400.0
            decayed = float(r["intensity"]) * (_HALF_LIFE_FACTOR ** max(0.0, age_days))
            beat = _naive(r.get("next_beat_at") or "")
            if beat and now >= beat:
                # 到剧本步点：M1 默认动作=推进一步、强度小涨（到点的事更压心头），
                # 下一步默认 +24h（M3 夜眠会改写成有意义的演进脚本）
                decayed = min(1.0, decayed + 0.15)
                self.agent.memory.thread_update(
                    r["id"], intensity=round(decayed, 3), beat_step=int(r["beat_step"]) + 1,
                    next_beat_at=(now + dt.timedelta(hours=24)).isoformat(timespec="seconds"))
                continue
            if decayed < _CLOSE_THRESHOLD:
                self.agent.memory.thread_update(r["id"], intensity=0.0, status="done")
            elif abs(decayed - float(r["intensity"])) > 0.001:
                self.agent.memory.thread_update(r["id"], intensity=round(decayed, 3))

    # ---------- 消费 ----------

    def top(self, n: int = _TOP_N) -> list[dict]:
        try:
            return self.agent.memory.thread_list(self.role)[:n]
        except Exception:
            return []

    def prompt_block(self) -> str:
        """对话/织文共用的心智注入块（本轮级，禁进 system 前缀——缓存纪律）。"""
        rows = self.top()
        if not rows:
            return ""
        lines = "; ".join(
            f"「{r['topic']}」（{'刚挂上' if r['intensity'] >= 0.6 else '已经放了一阵'}）"
            for r in rows)
        return ("【你心里挂着的事】" + lines + "\n"
                "这些是你的真实状态：如果顺嘴就自然地带一句你那边的事（不必等对方问），"
                "不顺嘴就一个字也别提。绝不允许编造这里没有的进展。")

    def spoken(self, r: dict) -> str:
        """牵挂 → 可直接开口的第一人称成品句（待织池素材必须是话不是指令——
        单条素材走 _weave_ritual 直返路径，指令体漏发=机器文本泄漏）。"""
        topic = str(r.get("topic") or "").strip()
        if r.get("origin") == "user":
            return f"你之前说「{topic[:24]}」，我一直替你记着呢。"
        return topic

    def ritual_material(self, now: dt.datetime | None = None) -> dict | None:
        """待织池自我发起源（M1 核心）：TA 心里有事想说——与 context_probe
        （猜你在干嘛）对称的那个「说我自己」。强度 ≥0.45 的 top 牵挂，
        同一条同剧本步 12h 内不重复（说过了，除非剧本又推进）。"""
        rows = [r for r in self.agent.memory.thread_list(self.role)
                if float(r["intensity"]) >= 0.45]
        if not rows:
            return None
        pick = rows[0]
        now = now or dt.datetime.now(dt.timezone.utc)
        key = f"thread:{pick['id']}:{pick['beat_step']}"
        last = getattr(self, "_last_material", {})
        if last.get("key") == key and (now - last.get("at", now)).total_seconds() < 12 * 3600:
            return None
        self._last_material = {"key": key, "at": now}
        line = self.spoken(pick)
        return {"source": "thread", "text": line}

    def moment_material(self, now: dt.datetime | None = None) -> tuple | None:
        """moments 动态引擎的牵挂素材源（四元组同款契约）。"""
        m = self.ritual_material(now)
        if not m:
            return None
        return ("D08", f"你心里挂着「{m['text']}」——把它写成一条动态独白",
                f"th:{self.role}:{now.strftime('%Y%m%d%H') if now else ''}", m["text"])
