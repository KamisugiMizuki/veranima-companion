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
    "moments": {"enabled": True, "frequency": "medium", "mention_user": "indirect",
                "allowed_types": ["D01", "D02", "D03", "D04", "D05", "D06", "D07"]},
    "proactive": {"enabled": True, "frequency": "medium",
                  "allowed_types": []},   # 空=全放行；非空=RITUAL_SOURCES 白名单
    "interaction": {"comment_response_style": "character", "dm_after_like": False,
                    "react_to_user_moments": False},  # P4：角色回访用户动态，默认关
    "expression": {"fixed_nickname": "", "sensitive_topics_extra": [], "expressiveness": "natural"},
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

# 虚拟天气（裁决 Q5）：纯函数哈希，同城同天全消费者一致；档=体感可说的朴素词
_WEATHERS = ("晴", "多云", "阴", "雨", "降温", "大风")
_WEEK = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def virtual_weather(city: str, day: dt.date) -> str:
    """确定性伪随机天气：季节微调（冬=降温/雨多，夏=晴多）+ 城市日哈希。"""
    seed = int(hashlib.sha1(f"{city}|{day.isoformat()}".encode()).hexdigest()[:8], 16)
    weights = {12: [2, 3, 3, 3, 5, 3], 1: [2, 3, 3, 3, 5, 3], 2: [2, 3, 3, 3, 4, 3],
               3: [3, 3, 3, 3, 2, 3], 4: [4, 3, 2, 2, 1, 2], 5: [4, 3, 2, 2, 1, 2],
               6: [4, 2, 2, 3, 0, 1], 7: [4, 2, 1, 4, 0, 1], 8: [4, 2, 1, 4, 0, 1],
               9: [4, 3, 2, 2, 1, 2], 10: [3, 3, 3, 2, 2, 3], 11: [2, 3, 3, 3, 4, 4]}.get(day.month, [3] * 6)
    pool = []
    for w_, name in zip(weights, _WEATHERS):
        pool.extend([name] * w_)
    return pool[seed % len(pool)] if pool else "晴"


def _looks_truncated(t: str) -> bool:
    """残句：以句读/连接词悬空结尾（LLM 预算截断的机器指纹）。"""
    t = t.strip()
    if not t:
        return False
    tail = t[-1]
    if tail in ("，", "、", "：", "；", ",", ":", ";", "(", "（"):
        return True
    if tail in (chr(34), "“", "「", "『"):
        return True  # 引号开头类悬空（未闭合引用）
    return False


def _looks_machine(t: str) -> bool:
    """统计口径残留：内部字段名词面（有效活动/作息偏移/睡眠债务=日终摘要
    原文指纹，人话动态不会这么说话），或数字+单位成串（≥3=报表腔）。"""
    import re
    if any(w in t for w in ("有效活动", "作息偏移", "睡眠债务")):
        return True
    return len(re.findall(r"\d+\s*(分钟|小时|%)", t)) >= 3



# 活动键→人话（喂 LLM 素材用；未收录原样——英文键 LLM 也懂，UI 那份 actMap 是显示用）
_ACT_LABELS = {
    "wake_routine": "起床收拾", "focused_practice": "专注做自己的事", "reset": "在路上",
    "personal_interest_a": "待在自己的爱好里", "personal_interest_b": "待在自己的爱好里",
    "quiet_rest": "歇着", "sleep": "睡着", "commute_transit": "挤通勤",
    "model_training_work": "盯着训练跑", "late_takeout_dinner": "吃夜宵外卖",
    "meme_archiving": "收藏表情包", "video_with_you": "等你一起看片", "blog_browsing": "刷博客",
}
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

    # ---------- 素材收集（今日事件池） ----------
    # 四元组=(kind, 给 LLM 的素材指令, 溯源键, 织文失败时的可发布降级文本)。
    # 降级文本必须第一人称且不含内部数值——素材指令是给机器的（"精力86%"），
    # 直接入库就是把统计报表发到朋友圈。None=该素材不可降级，宁缺毋滥。

    def _materials(self, now: dt.datetime) -> list[tuple[str, str, str, str | None]]:
        a = self.agent
        mats: list[tuple[str, str, str, str | None]] = []
        role = a.schedule_runtime.outline.role_id if a.schedule_runtime else a.role_key
        # D01 日程衍生：最近**日终摘要**未成动态的（dedupe=event id）。
        # 表里混着空间事件（"当前虚拟地点：…"）——只要 day_close_summary，
        # 那是"今天有效活动 X 分钟/中断/睡眠债务"的生活流水，能织出真动态。
        try:
            for ev in a.memory.virtual_life_events(role, limit=8):
                if str(ev.get("event_kind") or "") != "day_close_summary":
                    continue
                summary = str(ev.get("summary") or "")
                if summary:
                    mats.append(("D01", summary, f"event:{ev.get('id')}", None))
                    break
        except Exception:
            logger.debug("D01 collect failed", exc_info=True)
        # D03 情绪宣泄：state.mood 变化提示（近 24h 有对话情绪落点=现值可用）
        try:
            mood = str(a.state.mood or "")
            energy = float(a.state.energy)
            if mood in ("低落", "开心", "期待") and not a.state.user_asleep:
                _fb = {"开心": "今天心情挺好，说不上为什么。",
                       "低落": "有点闷，不太想说话。",
                       "期待": "有件事在前面等着，挺好的。"}[mood]
                mats.append(("D03", f"你现在情绪是「{mood}」，精力 {int(energy)}%",
                             f"mood:{mood}:{now.date().isoformat()}", _fb))
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
                    lab = _ACT_LABELS.get(act, act)
                    mats.append(("D05", f"你此刻正在「{lab}」（{place}），随手记一笔",
                                 f"act:{act}:{now.strftime('%Y%m%d%H')}",
                                 (f"此刻在{lab}。" if act != "sleep" else None)))
        except Exception:
            logger.debug("D05 collect failed", exc_info=True)
        # D06 未来预告：明日计划里的前两项活动（计划是今晚生成的=真实待发生）
        try:
            rt = a.schedule_runtime
            plan = getattr(rt, "_next_day_plan", None) if rt is not None else None
            if plan is not None and getattr(plan, "items", None):
                nxt = plan.items[0]
                label = str(getattr(nxt, "activity_key", "") or getattr(nxt, "block_id", ""))
                lab = _ACT_LABELS.get(label, label)
                mats.append(("D06", f"明天（{plan.local_date}）你安排的第一件事是「{lab}」，可以期待或紧张一下",
                             f"plan:{plan.plan_id}:{label}", f"明天安排了「{lab}」。"))
        except Exception:
            logger.debug("D06 collect failed", exc_info=True)
        # D02 环境感知：虚拟天气。种子=角色名（卡内城市多为无名设定，
        # 角色即"那座城市"；确定性不依赖城市名）。dedupe 含日期=每日至多一条。
        try:
            day_key = now.astimezone().date() if now.tzinfo else now.date()
            wx = virtual_weather(a.card.name, day_key)
            mats.append(("D02", f"你那里今天{wx}，有感而发一句", f"w:{a.card.name}:{day_key}",
                         f"今天{wx}。"))
        except Exception:
            logger.debug("D02 collect failed", exc_info=True)
        # D04 记忆闪回：低置信旧事（复用联想 C 类挖掘器，无话题线索=纯随机翻）
        try:
            dug = a._dig_old_memory()
            if dug:
                text, conf = dug
                hedge = "你有点记不清细节了，可以带糊" if conf < 0.7 else ""
                mats.append(("D04", f"你突然想起ta以前说过：「{text[:60]}」。{hedge}",
                             f"dig:{a.role_key}:{now.strftime('%Y%m%d%H')}",
                             "突然想起以前的一件事，不知道ta现在怎么样了。"))
        except Exception:
            logger.debug("D04 collect failed", exc_info=True)
        # D07 关系表达：里程碑（消息数台阶/在一起的日子）——只对亲密期开放
        try:
            from .persona import derive_relationship_stage
            stage = derive_relationship_stage(a.relationship)
            # 计数=该角色会话行数（消息级共享=和凛说过 500 条≠许眠的里程碑）
            total = a.memory.role_message_count(a.role_key)
            for step_ in (100, 500, 1000, 2000, 5000):
                if step_ <= total < step_ + 30:
                    mats.append(("D07", f"你们已经说过 {total} 多条话了，{stage}阶段的你有感",
                                 f"mile:{step_}", "我们已经聊过好多话了。"))
                    break
        except Exception:
            logger.debug("D07 collect failed", exc_info=True)
        # 类型轮换权重：D01/D03 高，D05 中，D02/D04/D06 点缀，D07 稀有（排序稳定=按源优先级）
        return [x for x in mats if x[0] in _KINDS]

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
        allowed = (cfg.get("moments") or {}).get("allowed_types") or list(_KINDS)
        mats = [m for m in mats if m[0] in allowed]
        if not mats:
            return 0
        # 选素材：优先与最近两条不同类型（repeat 豁免路径同样受益）
        recent_kinds = set(self.agent.memory.moments_recent_kinds(role, limit=2))
        # 稀有度权重（设计稿：D07 关系表达最稀有；D01/D03 优先）
        prio = {"D01": 0, "D03": 1, "D05": 2, "D02": 3, "D06": 4, "D04": 5, "D07": 6}
        mats.sort(key=lambda m: (m[0] in recent_kinds, prio.get(m[0], 9)))
        kind, text, ref, fallback = mats[0]
        dedupe = f"{role}|{ref}"
        mention = str((cfg.get("moments") or {}).get("mention_user", "indirect"))
        content = self._compose(kind, text, mention, fallback)
        # 发布硬闸（2026-09-04 真机实锤）：织文"成功返回"也可能是截断残句
        # （句读处断掉没写完）或统计口径没洗掉（"X分钟 中断Y"）——宁发骨架不发残次品。
        if content and (_looks_truncated(content) or _looks_machine(content)):
            logger.info("moment rejected (%s): %r", kind, content[:40])
            content = (fallback or "").strip()
        if not content:
            return 0
        pub = self.agent.memory.moment_publish(role, content, kind=kind,
                                               source_ref=ref, dedupe_key=dedupe)
        if pub:
            logger.info("moment published %s/%s: %s", role, kind, content[:40])
        return 1 if pub else 0

    def _compose(self, kind: str, material: str, mention: str, fallback: str | None = None) -> str:
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
        # 与 _weave_ritual 同款预算教训（2026-09 实测）：思考模型 512 常烧空返回
        # length 截断——首试默认、空则加倍 2048 重试一次，再败走降级骨架。
        text = ""
        for budget in (None, 2048):
            try:
                text = (a._short_task(task, max_tokens=budget) or "").strip()
            except Exception:
                text = ""
            if text:
                break
        if text and _TAIL_PAT.search(text):  # 喊话体：重生成一次止损
            try:
                text = (a._short_task(task + "\n（上次写成了隔空喊话，这次改成自言自语。）") or "").strip()
            except Exception:
                pass
        if not text:
            # 降级=可发布的第一人称骨架；机器统计类素材无骨架则本轮放弃
            # （素材不销毁，下一 tick 还在池里——幂等键保证不重复）
            text = (fallback or "").strip()
        return text[:140]

    # ---------- P4：用户动态回访（赞/评；react_to_user_moments 默认关） ----------

    def maybe_react_to_user_moments(self, now: dt.datetime | None = None) -> int:
        """角色刷用户动态（低频概率事件，设置关=零行为）。

        每次最多处理一条最旧未反应的用户动态：赞 60% / 赞+评 25% / 静默 15%
        （评论走 comment_response_style；none=退化为只赞）。回访不占聊天未读
        （通知走 moments 自己的交互流，不进 _pending——评论区见，别私信轰炸）。
        幂等：actor=role 的 interaction 行存在=已反应过，永不重复。"""
        a = self.agent
        if not a.role_key:
            return 0
        cfg = self.settings()
        react = cfg.get("interaction") or {}
        if not bool(react.get("react_to_user_moments", False)):
            return 0
        if bool((cfg.get("moments") or {}).get("enabled", True)) is False:
            # 连自己动态都关了的角色，回访同样没心情（一个总闸精神）
            pass  # 不禁：用户裁决只要求 react 开关控它；保持独立语义
        rows = a.memory.con.execute(
            """SELECT m.* FROM moments m
               WHERE m.role_id='user'
                 AND NOT EXISTS (SELECT 1 FROM moment_interactions i
                                 WHERE i.moment_id=m.id AND i.actor=?)
               ORDER BY m.id ASC LIMIT 1""", (a.role_key,)).fetchall()
        if not rows:
            return 0
        mom = dict(rows[0])
        import random
        dice = random.random()
        if dice < 0.15:
            # 静默=先记账防下轮再掷骰子（否则这条永远悬着反复摇）
            a.memory._ensure_mi_seen_kind()
            a.memory.moment_ack(mom["id"], a.role_key)
            return 0
        a.memory.moment_toggle_like(mom["id"], a.role_key)
        done = 1
        if dice >= 0.60 and str(react.get("comment_response_style", "character")) != "none":
            try:
                text = (a._short_task(
                    (f"你是{a.card.name}。看到喜欢的人（用户）发了一条动态："
                     + "「" + str(mom["content"])[:100] + "」"
                     + chr(10) + "以你的语气在下面评论一句，不超过20字，像朋友刷到动态的随手反应"
                       "（好奇/接梗/关心都可以），别像客服。只输出评论正文。"),
                    max_tokens=1024) or "").strip()
            except Exception:
                text = ""
            if text:
                a.memory.moment_comment(mom["id"], text[:60], a.role_key)
                done += 1
        return done

    # ---------- 评论响应（bridge.comment_moment 调；P2 固定 character 风格） ----------

    def interaction_cfg(self) -> dict:
        return self.settings().get("interaction") or {}

    def reply_comment(self, moment_id: int, user_text: str) -> str:
        a = self.agent
        mom = a.memory.moment_get(moment_id)
        if not mom:
            return ""
        style = str(self.interaction_cfg().get("comment_response_style", "character"))
        if style == "none":
            return ""
        line = ("以你的语气回一句，不超过30字，像评论区互动（可以反问/自嘲/接梗），"
                "不要展开成长篇对话。只输出回复正文。" if style == "character" else
                "回一句极短的话（不超过6个字，像'嗯''谢谢''来了'），只输出回复正文。")
        task = (
            f"你是{a.card.name}。你发过这条动态：「{mom['content']}」\n"
            + f"用户在下面评论：「{user_text[:120]}」\n"
            + line
        )
        try:
            return (a._short_task(task) or "").strip()[:60]
        except Exception:
            return ""
