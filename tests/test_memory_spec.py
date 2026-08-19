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

from veranima.memory.store import MemoryStore


def _store(tmp_path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "m.db"), config={"embedding_model": "none"})


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
