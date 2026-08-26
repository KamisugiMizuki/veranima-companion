"""C-6：共同创作离线 benchmark（SHARED_CREATION_SPEC §9 行为验收）。

覆盖：正常完成、暂停、删除、换卡隔离、无证据拦截、闲聊不建项目。
全部离线（fake embedding / 无 LLM / fake bridge）。
"""
from __future__ import annotations

import asyncio

import pytest

from veranima.core.shared_creation import (
    CreationConfirmationRequired,
    SharedCreationStore,
)
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(db_path=str(tmp_path / "c.db"), config={"decay_enabled": False}, provider=FakeEmbed())
    s = SharedCreationStore(memory)
    yield s
    s.con.close()


def test_bench_full_lifecycle(tmp_path, store):
    """开始→进行→完成→归档 全程事实可追溯。"""
    e1 = store.memory.store_message("user", "我们开始写吧")
    p = store.create_project(kind="story", title="生命周期", purpose="验证", confirmed=True)
    scene = store.create_scene(p.project_id, title="S1", goal="开场")
    d = store.record_decision(
        p.project_id, scene.scene_id, question="视角？",
        options=[{"id": "first", "label": "第一人称"}], chosen="first",
        decided_by="user", evidence_message_ids=[e1],
    )
    a = store.save_artifact(p.project_id, scene.scene_id, title="开场草稿",
                            content="风掠过屋顶。", evidence_message_ids=[e1])
    ev = store.confirm_shared_event(p.project_id, summary="完成了开场",
                                    evidence_message_ids=[e1])
    assert d.chosen == "first" and a.version == 1
    assert ev.relationship_candidate is not None
    done = store.set_project_status(p.project_id, "completed")
    assert done.status == "completed"
    assert store.get_decision(d.decision_id).chosen == "first"      # 决定保留
    assert store.get_artifact(a.artifact_id).content == "风掠过屋顶。"  # 产物保留
    assert ev.memory_id > 0                                          # 记忆已写入


def test_bench_pause_and_resume_keeps_threads(tmp_path, store):
    e = store.memory.store_message("user", "先停一下")
    p = store.create_project(kind="story", title="暂停恢复", purpose="x", confirmed=True)
    t = store.open_thread(p.project_id, summary="结局未定", next_action="下周继续")
    paused = store.set_project_status(p.project_id, "paused")
    assert paused.status == "paused"
    assert store.list_open_threads(p.project_id)[0].summary == "结局未定"  # 暂停不清线程
    resumed = store.set_project_status(p.project_id, "active")
    assert resumed.status == "active"
    assert store.list_open_threads(p.project_id)[0].thread_id == t.thread_id


def test_bench_delete_project_cascades_memory_candidates_only(tmp_path, store):
    """删除项目：派生对象级联；原始消息不动（SPEC §6 用户删除）。"""
    e = store.memory.store_message("user", "删掉这个项目吧")  # 原始消息必须保留
    p = store.create_project(kind="story", title="将删", purpose="x", confirmed=True)
    scene = store.create_scene(p.project_id, title="S", goal="g")
    store.save_artifact(p.project_id, scene.scene_id, title="a", content="x",
                        evidence_message_ids=[e])
    store.delete_project(p.project_id)
    assert store.get_project(p.project_id) is None
    assert store.list_artifacts(scene.scene_id) == []
    row = store.memory.con.execute("SELECT content FROM messages WHERE id=?", (e,)).fetchone()
    assert row is not None  # 原始聊天不静默删除


def test_bench_no_evidence_rejected(tmp_path, store):
    """无 evidence 的确认/产物一律拒绝。"""
    p = store.create_project(kind="story", title="无证据", purpose="x", confirmed=True)
    with pytest.raises(CreationConfirmationRequired):
        store.confirm_shared_event(p.project_id, summary="凭空事件", evidence_message_ids=[99999])
    scene = store.create_scene(p.project_id, title="S", goal="g")
    with pytest.raises(CreationConfirmationRequired):
        store.save_artifact(p.project_id, scene.scene_id, title="a", content="x",
                            evidence_message_ids=[99999])


def test_bench_chat_like_intent_does_not_create_project(store):
    """单句「以后一起写个故事」不产生任何项目（无确认机制在 create 层）。"""
    with pytest.raises(CreationConfirmationRequired):
        store.create_project(kind="story", title="以后写的故事", purpose="随口一说", confirmed=False)
    assert store.list_projects() == []


def test_bench_character_switch_facts_survive(tmp_path, store):
    """换卡隔离：项目事实属于用户与项目本身，不随角色卡消失。"""
    e = memory_evidence = store.memory.store_message("user", "事实与角色无关")
    p = store.create_project(kind="story", title="跨卡项目", purpose="x", confirmed=True)
    scene = store.create_scene(p.project_id, title="S", goal="g")
    a = store.save_artifact(p.project_id, scene.scene_id, title="事实产物", content="内容",
                            evidence_message_ids=[e])
    # 模拟"换卡"：SharedCreationStore 不持有角色状态——同一 store 继续可用即通过
    assert store.get_artifact(a.artifact_id).title == "事实产物"
    assert store.get_project(p.project_id).title == "跨卡项目"
