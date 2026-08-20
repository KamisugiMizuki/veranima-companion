"""MEMORY_SPEC M-1 数据真值测试：版本 current 过滤、session TTL、链删除。

覆盖：
- list_layer 默认排除被 supersedes 的旧版本
- recall 硬过滤非 current 与过期条目
- get_history 返回完整版本链
- session expires_at 过期不可召回
- erase 删除整条链且保留 messages
"""
from __future__ import annotations

import datetime

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.memory.store import MemoryStore


def _store(tmp_path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "m.db"), config={"embedding_model": "none"})


def _agent_with_memory(tmp_path) -> Agent:
    card = CharacterCard(name="测试卡", veranima={})
    store = MemoryStore(db_path=str(tmp_path / "mem.db"), config={"embedding_model": "none"})
    return Agent(card=card, memory=store, llm=None, state=None, config={})


def _ts(days: int = 0) -> str:
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).isoformat(timespec="seconds")


def test_list_layer_excludes_superseded(tmp_path):
    s = _store(tmp_path)
    old = s.store("semantic", "用户喜欢喝咖啡，每天一杯", meta={"kind": "user_fact"})
    new = s.update_latest(old.id, "用户喜欢喝咖啡，每天两杯", meta={"supersedes": old.id, "kind": "user_fact"})
    entries = s.list_layer("semantic")
    ids = [e.id for e in entries]
    assert new.id in ids
    assert old.id not in ids  # 旧版本被过滤
    assert s.get_history(old.id)[-1].id == new.id  # 链可审计


def test_recall_returns_current_version(tmp_path):
    s = _store(tmp_path)
    old = s.store("semantic", "用户养了一只猫，叫咪咪", meta={"kind": "user_fact"})
    new = s.update_latest(old.id, "用户养了一只猫，叫咪咪，是只橘猫", meta={"supersedes": old.id, "kind": "user_fact"})
    hits = s.recall("咪咪 橘猫", top_k=5)  # keyword fallback 需要词级命中
    assert hits and hits[0].id == new.id
    assert all(h.id != old.id for h in hits)


def test_session_expires_at_not_recalled(tmp_path):
    s = _store(tmp_path)
    s.store("session", "当前在写周报", meta={"expires_at": _ts(-1), "kind": "session"})
    s.store("session", "当前在写周报", meta={"expires_at": _ts(+1), "kind": "session"})
    assert len(s.list_layer("session")) == 1  # 过期一条被过滤
    hits = s.recall("在写什么", top_k=5)
    assert all(h.is_expired() is False for h in hits)


def test_get_history_full_chain(tmp_path):
    s = _store(tmp_path)
    a = s.store("semantic", "用户家在杭州", meta={"kind": "user_fact"})
    b = s.update_latest(a.id, "用户家搬到上海", meta={"supersedes": a.id, "kind": "user_fact"})
    c = s.update_latest(b.id, "用户家搬到深圳", meta={"supersedes": b.id, "kind": "user_fact"})
    chain = s.get_history(c.id)
    assert [e.id for e in chain] == [a.id, b.id, c.id]
    assert [e.content for e in chain] == ["用户家在杭州", "用户家搬到上海", "用户家搬到深圳"]


def test_erase_deletes_chain_keeps_messages(tmp_path):
    s = _store(tmp_path)
    s.store_message("user", "我家在杭州", 80, "平静")
    a = s.store("semantic", "用户家在杭州", meta={"kind": "user_fact"})
    b = s.update_latest(a.id, "用户家搬到上海", meta={"supersedes": a.id, "kind": "user_fact"})
    n = s.erase(b.id)
    assert n == 2  # 整条链
    assert s.get(a.id) is None and s.get(b.id) is None
    assert len(s.recent_messages(10)) == 1  # 原始消息保留


def test_erase_batch_expands_chain(tmp_path):
    s = _store(tmp_path)
    a = s.store("semantic", "用户喜欢香菜", meta={"kind": "user_fact"})
    b = s.update_latest(a.id, "用户不喜欢香菜了", meta={"supersedes": a.id, "kind": "user_fact"})
    n = s.erase(content_contains="香菜")
    assert n == 2
    assert s.get(a.id) is None and s.get(b.id) is None


# ---------- M-2 写入契约（MEMORY_SPEC 5/6/8） ----------

def test_validate_candidate_requires_source_and_meta(tmp_path):
    from veranima.memory.store import validate_candidate
    assert validate_candidate({"kind": "user_fact", "content": "X", "confidence": 0.8})  # 缺 source/message_id
    assert not validate_candidate({
        "kind": "user_fact", "content": "用户喜欢下雨天", "confidence": 0.8,
        "source": "rule_extract", "source_message_id": 3,
    })
    # 敏感信息拒绝
    assert validate_candidate({
        "kind": "user_fact", "content": "我的密码是 abc123", "confidence": 0.9,
        "source": "rule_extract", "source_message_id": 3,
    })
    # session 必须带 expires_at
    assert validate_candidate({
        "kind": "session", "content": "正在写周报", "source": "rule_extract", "source_message_id": 3,
    })
    # 非法 status
    assert validate_candidate({
        "kind": "user_fact", "content": "X", "status": "weird",
        "source": "rule_extract", "source_message_id": 3,
    })


def test_rule_extract_colloquial_variants():
    """口语变体全覆盖（'我特别喜欢' 必须命中，技能教训）。"""
    a = object.__new__(Agent)
    hits = a._rule_extract("我特别喜欢下雨天", 1)
    assert any(c["kind"] == "user_fact" for c in hits)


def test_rule_extract_correction_overrides():
    """显式纠正 → 高置信候选 + correction 标记 + 必须走版本链。"""
    a = object.__new__(Agent)
    hits = a._rule_extract("不是，我说的是周三，不是周二", 1)
    corr = [c for c in hits if c.get("needs_confirmation") is False and c["confidence"] >= 0.85]
    assert corr, hits


def test_store_candidate_correction_forces_version_chain(tmp_path):
    a = _agent_with_memory(tmp_path)
    old = a.memory.store("semantic", "用户周二开会", meta={"kind": "user_fact"})
    a._store_candidate({
        "kind": "user_fact", "content": "用户周三开会",
        "confidence": 0.85, "source": "rule_extract", "source_message_id": 1,
        "correction": True,
    })
    chain = a.memory.get_history(old.id)
    assert len(chain) == 2  # 纠正强制新版本（即使相似度不足 0.78）
    assert chain[-1].meta.get("correction") is True


def test_promise_mark_cancelled_status(tmp_path):
    from veranima.core.promises import PromiseBook
    s = _store(tmp_path)
    book = PromiseBook(s)
    pid = book.record("下周记得提醒我买猫粮")
    book.mark_cancelled(pid)
    assert not book.open_promises()  # cancelled 不再显示为 open
    chain = s.get_history(pid)
    assert chain[-1].meta.get("status") == "cancelled"


# ---------- M-3 召回（MEMORY_SPEC 10） ----------

def test_memory_fts_direct_hit(tmp_path):
    """FTS 直接索引规范记忆（不依赖消息巧合命中）。"""
    s = _store(tmp_path)
    s.store("semantic", "用户喜欢喝手冲咖啡", meta={"kind": "user_fact", "subject": "user"})
    hits = s.recall("手冲咖啡", top_k=5)
    assert hits and "手冲咖啡" in hits[0].content


def test_temporal_intent_past_boosts_event_time(tmp_path):
    s = _store(tmp_path)
    past = s.store("shared_episode", "上次一起爬山摔了一跤", meta={"kind": "shared_episode", "event_time": "2026-06-01T10:00:00+00:00"})
    now = s.store("shared_episode", "上次一起爬山很开心", meta={"kind": "shared_episode"})
    hits = s.recall("上次一起爬山怎么样", top_k=5)
    # past 意图 → 带 event_time 的条目 temporal 信号更高
    assert hits[0].id == past.id


def test_subject_match_user_query(tmp_path):
    s = _store(tmp_path)
    user_fact = s.store("semantic", "用户喜欢蓝色", meta={"kind": "user_fact", "subject": "user"})
    s.store("semantic", "角色喜欢蓝色", meta={"kind": "user_fact", "subject": "character"})
    hits = s.recall("我喜欢什么颜色", top_k=5)
    assert hits[0].id == user_fact.id  # "我" → subject=user 优先


# ---------- M-4 Context Brief（MEMORY_SPEC 10.4/9） ----------

def test_build_brief_budget_and_labels(tmp_path):
    from veranima.memory.brief import build_brief, format_brief
    from veranima.memory.store import MemoryEntry
    sems = [
        MemoryEntry(id=1, layer="semantic", content="用户喜欢喝咖啡", confidence=0.9, meta={"kind": "user_fact"}),
        MemoryEntry(id=2, layer="semantic", content="用户养了一只橘猫，叫咪咪", confidence=0.6, meta={"kind": "user_fact", "event_time": "2026-06-01T00:00:00+00:00"}),
    ]
    items = build_brief(semantic=sems, budgets={"semantic": 100, "episodic": 100})
    assert items[0].memory_id == 1
    assert items[0].confidence_label == "高"
    assert items[1].confidence_label == "中"
    assert items[1].temporal_label == "过去"
    out = format_brief(items)
    assert "【长期事实】" in out
    assert "用户喜欢喝咖啡" in out


def test_build_brief_full_item_truncation(tmp_path):
    from veranima.memory.brief import build_brief
    from veranima.memory.store import MemoryEntry
    long = MemoryEntry(id=1, layer="semantic", content="用" * 300, confidence=0.9, meta={"kind": "user_fact"})
    short = MemoryEntry(id=2, layer="semantic", content="短事实", confidence=0.9, meta={"kind": "user_fact"})
    items = build_brief(semantic=[long, short], budgets={"semantic": 290})
    # 第一条超预算 → 完整丢弃（不硬截断），第二条进来
    assert [i.memory_id for i in items] == [2]


def test_history_compaction_writes_summary(tmp_path):
    a = _agent_with_memory(tmp_path)
    # 手工撑大历史（超过 2×history_max_messages=40）
    a._history = []
    for i in range(22):
        a._history.append({"role": "user", "content": f"第{i}条消息"})
        a._history.append({"role": "assistant", "content": f"回复{i}"})
    a._short_task = lambda task, max_tokens=None, bilingual=False: "用户聊了二十二轮日常话题，没有特别承诺。"
    a._compact_history()
    assert len(a._history) <= 21  # 截断到最近 history_max_messages 轮内
    assert a._history[0]["role"] == "user"  # 序列保护
    sems = a.memory.list_layer("session")
    assert any(e.meta.get("kind") == "history_summary" for e in sems)


def test_history_compaction_failure_truncates(tmp_path):
    a = _agent_with_memory(tmp_path)
    a._history = []
    for i in range(22):
        a._history.append({"role": "user", "content": f"第{i}条"})
        a._history.append({"role": "assistant", "content": f"回{i}"})

    def boom(task, max_tokens=None, bilingual=False):
        raise RuntimeError("llm down")

    a._short_task = boom
    a._compact_history()  # 不抛异常
    assert len(a._history) <= 21
    assert a._history[0]["role"] == "user"


# ---------- M-6 文风学习（MEMORY_SPEC 13） ----------

def test_style_sample_filter():
    from veranima.core.learning import is_style_sample
    assert is_style_sample("我特别喜欢下雨天")
    assert is_style_sample("好的，那明天见吧")
    assert not is_style_sample("https://example.com/abc")
    assert not is_style_sample("```python\nprint(1)\n```")
    assert not is_style_sample("cd /tmp && ls")
    assert not is_style_sample(r"D:\logs\server.log")
    assert not is_style_sample("SELECT secret FROM users")
    assert not is_style_sample("好")  # 太短
    assert not is_style_sample("！！！？？")  # 纯标点


def test_style_profile_matures_and_adapts(tmp_path):
    from veranima.core.learning import StyleLearner, FeedbackSignal
    learner = StyleLearner(persist_path=str(tmp_path / "style.json"))
    # 20 条前不成熟
    for i in range(10):
        learner.observe(FeedbackSignal(), "今天天气不错，我们出去走走。")
    assert not learner.profile.is_mature()
    assert learner.to_prompt_block() != ""  # 参数块仍在（向后兼容）
    # 达到 20 条 → 画像生效
    for i in range(10):
        learner.observe(FeedbackSignal(), "能不能帮我查一下资料，谢谢。")
    assert learner.profile.is_mature()
    block = learner.profile.to_prompt_block()
    assert "用户交流偏好" in block
    assert "模仿口癖" in block  # 13.6 防复读声明
    assert len(block) <= 300
    # 保存/加载 roundtrip
    learner.save()
    learner2 = StyleLearner(persist_path=str(tmp_path / "style.json"))
    assert learner2.load()
    assert learner2.profile.sample_count == learner.profile.sample_count
    # reset 清画像
    learner2.reset()
    assert learner2.profile.sample_count == 0


def test_style_profile_v1_migration(tmp_path):
    """旧 style.json（v1 无 profile）→ 加载不崩，参数保留。"""
    import json
    from veranima.core.learning import StyleLearner
    p = tmp_path / "style.json"
    p.write_text(json.dumps({
        "params": {"reply_length": 0.7, "formality": 0.4, "humor": 0.6, "topic_follow": 0.5},
        "bandits": {"reply_length": [0.1, 0.1, 0.1], "formality": [0.0, 0.0, 0.0],
                    "humor": [0.0, 0.0, 0.0], "topic_follow": [0.0, 0.0, 0.0]},
        "steps": 30,
    }, ensure_ascii=False), encoding="utf-8")
    learner = StyleLearner(persist_path=str(p))
    assert learner.load()
    assert learner.params.reply_length == 0.7  # 参数保留
    assert learner.profile.sample_count == 0  # 画像默认


def test_mirror_filters_stopwords(tmp_path):
    from veranima.core.learning import LanguageMirror
    m = LanguageMirror(persist_path=str(tmp_path / "m.json"))
    m.observe("我觉得这个那个其实还行吧")
    m.observe("我想买一只猫，猫很可爱")
    top = m.stats()["top"]
    assert "我想买一" in top  # 内容词保留（2-4 字组）
    assert "这个" not in top and "那个" not in top  # 停用词被过滤


# ---------- M-7 用户控制（MEMORY_SPEC 14/15） ----------

def test_list_and_export_memories(tmp_path):
    a = _agent_with_memory(tmp_path)
    a.memory.store("semantic", "用户喜欢喝咖啡", meta={"kind": "user_fact"})
    a.memory.store("episodic", "上次一起爬山", meta={"kind": "shared_episode"})
    listed = a.list_memories()
    assert any("喝咖啡" in m["content"] for m in listed)
    assert any("爬山" in m["content"] for m in listed)
    jl = a.export_memories("jsonl")
    assert "用户喜欢喝咖啡" in jl
    md = a.export_memories("markdown")
    assert "## semantic" in md
    assert "## episodic" in md


def test_forget_removes_memory_keeps_messages(tmp_path):
    a = _agent_with_memory(tmp_path)
    a.memory.store_message("user", "我家的猫叫咪咪", 80, "平静")
    a.memory.store("semantic", "用户家的猫叫咪咪", meta={"kind": "user_fact"})
    n = a.memory.erase(content_contains="咪咪")
    assert n >= 1
    assert a.list_memories() == []
    assert len(a.memory.recent_messages(10)) == 1  # 原文保留（单独删除）


# ---------- 模块联通（DESIGN 4.1 / MEMORY_SPEC 6.2 / R4_SPEC 4） ----------

def test_system_prompt_uses_brief(tmp_path):
    """build_system_prompt 走 Context Brief（MEMORY_SPEC 10.4 联通）。"""
    from veranima.core.prompts import build_system_prompt
    from veranima.core.state import AgentState
    s = _store(tmp_path)
    s.store_message("user", "我最近在喝手冲咖啡", 80, "平静")  # 提供查询提示
    s.store("semantic", "用户喜欢喝手冲咖啡", meta={"kind": "user_fact", "subject": "user"})
    card = CharacterCard(name="测试卡", veranima={})
    sp = build_system_prompt(card, AgentState(), s, channel="im")
    assert "【长期事实】" in sp  # brief 层标签
    assert "手冲咖啡" in sp


def test_turn_context_contract():
    """DESIGN 4.1 TurnContext 数据契约。"""
    from veranima.core.agent import TurnContext
    ctx = TurnContext(channel="im", user_text="你好", images=("a",), scene="normal",
                      current_time="2026-08-19T10:00:00", state={"mood": "平静"})
    assert ctx.channel == "im"
    assert ctx.images == ("a",)
    assert ctx.scene == "normal"


def test_llm_memory_candidates_consumed(tmp_path):
    """Reply.memory_candidates → validate → 写入（MEMORY_SPEC 6.2 联通）。"""
    a = _agent_with_memory(tmp_path)
    from veranima.core.reply import Reply, ReplySegment
    r = Reply(segments=[ReplySegment(text="嗯")],
              memory_candidates=[{"kind": "user_fact", "content": "用户喜欢猫", "confidence": 0.5}])
    a._store_llm_candidates(r, user_msg_id=1)
    assert any("猫" in e.content for e in a.memory.list_layer("semantic"))


def test_proactive_feedback_responded_flow(tmp_path):
    """R4_SPEC 4：反馈记录 + 响应标记 + 忽略退避链路。"""
    a = _agent_with_memory(tmp_path)
    a.memory.record_proactive_feedback(source="attention")
    a.memory.record_proactive_feedback(source="attention")
    fb = a.memory.recent_proactive_feedback(limit=3)
    assert len([f for f in fb if not f["responded"]]) == 2
    # 用户回应 → responded 标记 + note_responded 重置
    a.memory.record_proactive_feedback(source="attention", responded=True)
    a.gate.note_responded("attention")
    # 连续忽略 → note_ignored 退避
    a.gate.note_ignored("attention")
    a.gate.note_ignored("attention")
    assert a.gate._ignored_streak["attention"] == 2


