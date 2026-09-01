"""好友动态引擎（MOMENTS_MULTIROLE_SPEC P2）。

动态=角色虚拟生活的自然溢出，不是随机生成：每条素材都有可追溯来源
（日终摘要/情绪波动/当前活动/明日计划），入库带 kind+source_ref+dedupe_key。

设计复用主动消息管线同款哲学：
- 频率闸（enabled/每日上限/距上次≥6h/同类型连续≤2）在发送侧，素材照常收集
- LLM 织文失败降级=素材原文截断入库（宁可不美不能丢生活记录）
- dedupe_key UNIQUE=同一素材永不二次成文（重复插入静默拒绝）
- 动态只进信息流：不发通知、不占未读、不私聊（Q3：MVP 无用户发布）
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re

logger = logging.getLogger(__name__)

# 角色设置默认值（P2 最小集；P3/P4 组随实现扩，merge 语义=浅合并顶层键）
DEFAULT_SETTINGS = {
    "moments": {"enabled": True, "frequency": "medium", "mention_user": "indirect"},
    "proactive": {"enabled": True, "frequency": "medium"},
}
_FREQ_DAILY = {"low": 1, "medium": 1, "high": 2}  # low=每2-3天1条（靠 6h 闸自然拉长→14h 起步）


def merge_settings(raw: dict | None) -> dict:
    out = {k: dict(v) for k, v in DEFAULT_SETTINGS.items()}
    for k, v in (raw or {}).items():
        if isinstance(v, dict):
            out.setdefault(k, {})
            out[k].update({kk: vv for kk, vv in v.items() if kk in out.get(k, {}) or True})
        # 未知顶层键也保留（向前兼容 P3 组）
        elif k not in out:
            out[k] = v
    return out


_KINDS = {"D01", "D03", "D05", "D06", "D02", "D04", "D07"}
_TAIL_PAT = re.compile(r"(你在吗|你知道吗|你在干嘛呢.*发出来|@你)")  # 喊话检查（重生成一次即止损）


class MomentsEngine:
    """单角色的动态生成器。Agent 组合持有；tick 由 bridge 驱动（活跃角色实时+首开追补）。"""

    def __init__(self, agent):
        self.agent = agent
        self._last_checked_day = ""

    # ---------- 设置 ----------

    def settings(self) -> dict:
        try:
            raw = self.agent.memory.role_settings_get(self.agent.role_key)
        except Exception:
            raw = None
        return merge_settings(raw)

    # ---------- 素材收集（今日事件池；返回 [(kind, text, source_ref)]） ----------

    def _materials(self, now: dt.datetime) -> list[tuple[str, str, str]]:
        a = self.agent
        mats: list[tuple[str, str, str]] = []
        role = a.schedule_runtime.outline.role_id if a.schedule_runtime else a.role_key
        # D01 日程衍生：昨日/最近日终摘要未成动态的（dedupe=event id）
        try:
            for ev in a.memory.virtual_life_events(role, limit=2):
                summary = str(ev.get("summary") or "")
                if summary:
                    mats.append(("D01", summary, f"event:{ev.get('id')}"))
                    break
        except Exception:
            logger.debug("D01 collect failed", exc_info=True)
        # D03 情绪宣泄：state.mood 变化提示（近 24h 有对话情绪落点=现值可用）
        try:
            mood = str(a.state.mood or "")
            energy = float(a.state.energy)
            if mood in ("低落", "开心", "期待") and not a.state.user_asleep:
                mats.append(("D03", f"你现在情绪是「{mood}」，精力 {int(energy)}%", f"mood:{mood}:{now.date().isoformat()}"))
        except Exception:
            logger.debug("D03 collect failed", exc_info=True)
        # D05 碎碎念：当前日程活动（此刻在做什么，真实活动名）
        try:
            rt = a.schedule_runtime
            if rt is not None and not rt.sleeping:
                ctx = rt.current_context(now)
                act = str(getattr(ctx, "activity_key", "") or "")
                place = str(getattr(ctx, "place_label", "") or "")
                if act:
                    mats.append(("D05", f"你此刻正在「{act}」（{place}），随手记一笔", f"act:{act}:{now.strftime('%Y%m%d%H')}"))
        except Exception:
            logger.debug("D05 collect failed", exc_info=True)
        # D06 未来预告：明日计划里的前两项活动（计划是今晚生成的=真实待发生）
        try:
            rt = a.schedule_runtime
            plan = getattr(rt, "_next_day_plan", None) if rt is not None else None
            if plan is not None and getattr(plan, "items", None):
                nxt = plan.items[0]
                label = str(getattr(nxt, "activity_key", "") or getattr(nxt, "block_id", ""))
                mats.append(("D06", f"明天（{plan.local_date}）你安排的第一件事是「{label}」，可以期待或紧张一下",
                             f"plan:{plan.plan_id}:{label}"))
        except Exception:
            logger.debug("D06 collect failed", exc_info=True)
        return [(k, t, r) for (k, t, r) in mats if k in _KINDS]

    # ---------- 发送闸 ----------

    def _gate(self, now: dt.datetime, cfg: dict) -> str:
        m = cfg.get("moments") or {}
        if not m.get("enabled", True):
            return "off"
        daily_max = _FREQ_DAILY.get(str(m.get("frequency", "medium")), 1)
        if m.get("frequency") == "low":
            daily_max = 0  # 低档=每2-3天1条：靠 min_gap 14h 表达（每日上限不设，见下）
        today = now.date().isoformat()
        cnt = self.agent.memory.moments_count_today(self.agent.role_key, today)
        if daily_max and cnt >= daily_max:
            return "daily"
        gap_h = 14.0 if m.get("frequency") == "low" else 6.0
        last = self.agent.memory.moments_last_at(self.agent.role_key)
        if last:
            try:
                lt = dt.datetime.fromisoformat(last)
                if lt.tzinfo is None:
                    lt = lt.replace(tzinfo=dt.timezone.utc)
                if (now - lt).total_seconds() < gap_h * 3600:
                    return "gap"
            except ValueError:
                pass
        # 同类型连续 ≤2：看最近 2 条同型则今天换型失败→跳过本轮
        recent_kinds = self.agent.memory.moments_recent_kinds(self.agent.role_key, limit=2)
        if len(set(recent_kinds)) == 1 and len(recent_kinds) == 2:
            return "repeat"
        return ""

    # ---------- 生成主入口（bridge tick 调；返回本轮发布数） ----------

    def tick(self, now: dt.datetime | None = None, *, catch_up: bool = False) -> int:
        now = now or dt.datetime.now(dt.timezone.utc)
        role = self.agent.role_key
        if not role:
            return 0  # PC/QQ 单角色时代无角色键：动态体系不启用（数据无处归属）
        cfg = self.settings()
        blocked = self._gate(now, cfg)
        if blocked and not catch_up:
            return 0
        if blocked in ("off", "daily", "gap"):
            return 0  # 追补也不越过硬闸（catch_up 只豁免 repeat）
        mats = self._materials(now)
        if not mats:
            return 0
        # 选素材：优先与最近两条不同类型（repeat 豁免路径同样受益）
        recent_kinds = set(self.agent.memory.moments_recent_kinds(role, limit=2))
        mats.sort(key=lambda m: (m[0] in recent_kinds,))
        kind, text, ref = mats[0]
        dedupe = f"{role}|{ref}"
        mention = str((cfg.get("moments") or {}).get("mention_user", "indirect"))
        content = self._compose(kind, text, mention)
        if not content:
            return 0
        pub = self.agent.memory.moment_publish(role, content, kind=kind,
                                               source_ref=ref, dedupe_key=dedupe)
        if pub:
            logger.info("moment published %s/%s: %s", role, kind, content[:40])
        return 1 if pub else 0

    def _compose(self, kind: str, material: str, mention: str) -> str:
        """LLM 织文（≤100字口语碎片，注入最近动态防重复）；失败降级素材直录。"""
        a = self.agent
        hist = a.memory.moments_recent_texts(a.role_key, limit=5)
        rule = {"yes": "可以直接提到用户。", "indirect": "可以间接想到用户（像自言自语），但禁止直接呼唤ta（不许出现「你在吗」「你知道吗」这类喊话）。",
                "no": "完全不提用户，只写你自己的事。"}.get(mention, "")
        task = (
            f"你是{a.card.name}。把你生活里的一件事写成一条朋友圈式动态（发在自己的动态页，不是发给谁的消息）。\n"
            f"素材：{material}\n"
            f"要求：不超过100字；口语化、碎片化，像真人随手写的；带一点你这个角色的语气；"
            f"不要总结陈词、不要感叹号堆砌。{rule}\n"
            + (f"你最近发过的动态（别重复这些主题和句式）：{' / '.join(hist)}\n" if hist else "")
            + "只输出动态正文。"
        )
        try:
            text = (a._short_task(task) or "").strip()
        except Exception:
            text = ""
        if text and _TAIL_PAT.search(text):  # 喊话体：重生成一次止损
            try:
                text = (a._short_task(task + "\n（上次写成了隔空喊话，这次改成自言自语。）") or "").strip()
            except Exception:
                pass
        if not text:
            # 降级：素材直录截断（零丢失优先于文采）；D01 摘要是机器文本→转第一人称骨架
            text = material[:100]
        return text[:140]

    # ---------- 评论响应（bridge.comment_moment 调；P2 固定 character 风格） ----------

    def reply_comment(self, moment_id: int, user_text: str) -> str:
        a = self.agent
        mom = a.memory.moment_get(moment_id)
        if not mom:
            return ""
        task = (
            f"你是{a.card.name}。你发过这条动态：「{mom['content']}」\n"
            f"用户在下面评论：「{user_text[:120]}」\n"
            "以你的语气回一句，不超过30字，像评论区互动（可以反问/自嘲/接梗），"
            "不要展开成长篇对话。只输出回复正文。"
        )
        try:
            return (a._short_task(task) or "").strip()[:60]
        except Exception:
            return ""
