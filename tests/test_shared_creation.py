"""共同创作 C-1/C-2/C-3 行为契约。"""
from __future__ import annotations

import hashlib

import pytest

from veranima.core.shared_creation import (
    CreationConfirmationRequired,
    SharedCreationStore,
)
from veranima.memory.store import MemoryStore


def _service(tmp_path):
    memory = MemoryStore(db_path=str(tmp_path / "creation.db"), config={"embedding_model": "none"})
    return SharedCreationStore(memory), memory


def test_project_requires_explicit_confirmation(tmp_path):
    service, _ = _service(tmp_path)

    with pytest.raises(CreationConfirmationRequired):
        service.create_project(
            kind="story", title="屋顶上的短篇", purpose="共同完成初稿", confirmed=False
        )
    assert service.list_projects() == []


def test_project_scene_decision_artifact_and_thread_roundtrip(tmp_path):
    service, memory = _service(tmp_path)
    evidence_id = memory.store_message("user", "回到屋顶", 80, "平静")
    project = service.create_project(
        kind="story", title="屋顶上的短篇", purpose="共同完成初稿", confirmed=True
    )
    scene = service.create_scene(project.project_id, title="第一幕", goal="决定故事开场")
    decision = service.record_decision(
        project.project_id,
        scene.scene_id,
        question="开场是否回到屋顶？",
        options=[{"id": "roof", "label": "回到屋顶"}],
        chosen="roof",
        decided_by="user",
        evidence_message_ids=[evidence_id],
    )
    artifact = service.save_artifact(
        project.project_id,
        scene.scene_id,
        title="第一版开场",
        content="风从屋顶边缘掠过去。",
        evidence_message_ids=[evidence_id],
    )
    thread = service.open_thread(
        project.project_id,
        summary="还要决定风声是否作为线索",
        next_action="比较两个版本",
    )

    assert service.get_project(project.project_id).status == "active"
    assert service.get_scene(scene.scene_id).goal == "决定故事开场"
    assert service.get_decision(decision.decision_id).chosen == "roof"
    assert service.get_artifact(artifact.artifact_id).sha256 == hashlib.sha256(
        "风从屋顶边缘掠过去。".encode()
    ).hexdigest()
    assert service.list_open_threads(project.project_id)[0].thread_id == thread.thread_id
    service.resolve_thread(thread.thread_id)
    assert service.list_open_threads(project.project_id) == []
    updated = service.set_project_status(project.project_id, "completed")
    assert updated.status == "completed"


def test_confirmed_event_writes_shared_episode_with_evidence(tmp_path):
    service, memory = _service(tmp_path)
    evidence_id = memory.store_message("user", "这个版本可以定稿", 80, "平静")
    project = service.create_project(
        kind="software", title="小工具", purpose="共同完成一个可运行原型", confirmed=True
    )
    event = service.confirm_shared_event(
        project.project_id,
        summary="共同完成了第一个可运行原型",
        evidence_message_ids=[evidence_id],
        user_interpretation="一起把模糊想法变成了能运行的东西",
        character_interpretation="分工和反复试错让这件事真正成立",
    )

    assert event.confirmed is True
    assert event.memory_id is not None
    stored = memory.get(event.memory_id)
    assert stored is not None
    assert stored.layer == "episodic"
    assert stored.meta["kind"] == "shared_episode"
    assert stored.meta["project_id"] == project.project_id
    assert stored.meta["evidence_message_ids"] == [evidence_id]
    assert "用户解释" in stored.content
    assert "角色解释" in stored.content
    meanings = [e for e in memory.list_layer("episodic", include_superseded=True)
                if e.meta.get("kind") == "shared_meaning"]
    assert len(meanings) == 1
    assert meanings[0].meta["project_id"] == project.project_id


def test_unconfirmed_event_does_not_write_memory(tmp_path):
    service, memory = _service(tmp_path)
    project = service.create_project(
        kind="story", title="草稿", purpose="试写", confirmed=True
    )

    with pytest.raises(CreationConfirmationRequired):
        service.confirm_shared_event(
            project.project_id,
            summary="没有证据的共同完成",
            evidence_message_ids=[],
            user_interpretation="感觉完成了",
        )
    assert memory.list_layer("episodic", include_superseded=True) == []


def test_unknown_evidence_id_is_rejected(tmp_path):
    service, _ = _service(tmp_path)
    project = service.create_project(kind="story", title="草稿", purpose="试写", confirmed=True)
    with pytest.raises(CreationConfirmationRequired, match="证据消息不存在"):
        service.confirm_shared_event(
            project.project_id,
            summary="伪造完成",
            evidence_message_ids=[999999],
        )


def test_confirm_shared_event_is_idempotent(tmp_path):
    service, memory = _service(tmp_path)
    evidence_id = memory.store_message("user", "确认完成", 80, "平静")
    project = service.create_project(kind="story", title="短篇", purpose="完成初稿", confirmed=True)
    first = service.confirm_shared_event(
        project.project_id, summary="完成初稿", evidence_message_ids=[evidence_id]
    )
    second = service.confirm_shared_event(
        project.project_id, summary="完成初稿", evidence_message_ids=[evidence_id]
    )
    assert second.event_id == first.event_id
    assert second.memory_id == first.memory_id
    assert len(memory.list_layer("episodic", include_superseded=True)) == 1


def test_existing_shared_events_schema_migrates(tmp_path):
    memory = MemoryStore(db_path=str(tmp_path / "old.db"), config={"embedding_model": "none"})
    memory.con.execute("""
        CREATE TABLE shared_projects (
            project_id TEXT PRIMARY KEY, kind TEXT, title TEXT, purpose TEXT,
            status TEXT, created_at TEXT, updated_at TEXT
        )
    """)
    memory.con.execute("""
        CREATE TABLE shared_events (
            event_id TEXT PRIMARY KEY,
            project_id TEXT,
            summary TEXT,
            evidence_json TEXT,
            user_interpretation TEXT,
            character_interpretation TEXT,
            confirmed INTEGER,
            memory_id INTEGER,
            created_at TEXT
        )
    """)
    memory.con.commit()
    service = SharedCreationStore(memory)
    evidence_id = memory.store_message("user", "确认完成", 80, "平静")
    project = service.create_project(kind="story", title="旧库项目", purpose="完成", confirmed=True)
    event = service.confirm_shared_event(
        project.project_id, summary="完成", evidence_message_ids=[evidence_id]
    )
    assert event.memory_id is not None
