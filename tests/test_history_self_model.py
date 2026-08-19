"""历史搜索与 SelfModel 人生章节行为契约。"""
from __future__ import annotations

from veranima.memory.store import MemoryStore


def _store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "m.db"), config={"embedding_model": "none"})


def test_search_messages_uses_fts_and_paginates(tmp_path):
    s = _store(tmp_path)
    s.store_message("user", "我们讨论过屋顶和天空", 80, "平静")
    s.store_message("assistant", "那次风很大", 80, "平静")
    s.store_message("user", "后来又聊了产线", 80, "平静")
    rows = s.search_messages("屋顶")
    assert len(rows) == 1
    assert rows[0]["content"] == "我们讨论过屋顶和天空"
    assert s.search_messages("屋顶", before_id=rows[0]["id"]) == []


def test_self_model_chapter_crud(tmp_path):
    s = _store(tmp_path)
    chapter_id = s.store_self_model_chapter(
        title="第一次共同项目",
        self_interpretation="我开始把陪伴理解为共同处理问题",
        key_events=[1, 2],
        relationship_changes=["reciprocity"],
        open_threads=["继续合作"],
        period_start="2026-08-20",
    )
    rows = s.list_self_model_chapters()
    assert rows[0]["id"] == chapter_id
    assert rows[0]["title"] == "第一次共同项目"
    assert rows[0]["key_events"] == [1, 2]
    assert s.get_self_model_chapter(chapter_id)["self_interpretation"].startswith("我开始")


def test_self_model_chapter_updates_without_destroying_history(tmp_path):
    s = _store(tmp_path)
    cid = s.store_self_model_chapter(title="旧标题", self_interpretation="旧解释")
    s.update_self_model_chapter(cid, title="新标题", open_threads=["未完成"])
    row = s.get_self_model_chapter(cid)
    assert row["title"] == "新标题"
    assert row["self_interpretation"] == "旧解释"
    assert row["open_threads"] == ["未完成"]
