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
