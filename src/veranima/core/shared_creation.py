"""共同创作最小持久化核心。

项目/场景/决策/产物/未完成线程属于协作状态；共同经历仍由 MemoryStore
统一写入。这里不直接修改关系值，避免把项目数量误当成亲密度。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..memory.store import MemoryStore


PROJECT_KINDS = {"story", "software", "learning", "game", "research", "custom"}
PROJECT_STATUS = {"draft", "active", "paused", "blocked", "completed", "abandoned", "archived"}
SCENE_STATUS = {"planned", "active", "paused", "blocked", "resolved"}
THREAD_STATUS = {"open", "snoozed", "resolved", "dropped"}


class CreationConfirmationRequired(ValueError):
    """长期项目或共同经历缺少用户确认/证据。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class Project:
    project_id: str
    kind: str
    title: str
    purpose: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Scene:
    scene_id: str
    project_id: str
    title: str
    goal: str
    status: str


@dataclass(frozen=True)
class Decision:
    decision_id: str
    project_id: str
    scene_id: str
    question: str
    options: list[dict]
    chosen: str
    decided_by: str
    evidence_message_ids: list[int]


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    project_id: str
    scene_id: str
    title: str
    content: str
    sha256: str
    version: int


@dataclass(frozen=True)
class OpenThread:
    thread_id: str
    project_id: str
    summary: str
    next_action: str
    status: str


@dataclass(frozen=True)
class SharedEvent:
    event_id: str
    project_id: str
    summary: str
    evidence_message_ids: list[int]
    confirmed: bool
    memory_id: int | None
    relationship_candidate: dict | None = None  # C-4：待审核关系候选


class SharedCreationStore:
    """C-1 至 C-3：用现有 MemoryStore SQLite 连接保存共同创作状态。"""

    def __init__(self, memory: MemoryStore):
        self.memory = memory
        self.con = memory.con
        self._init_schema()

    def _init_schema(self) -> None:
        self.con.executescript(
            """
            CREATE TABLE IF NOT EXISTS shared_projects (
                project_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                purpose TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shared_scenes (
                scene_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES shared_projects(project_id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                goal TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shared_decisions (
                decision_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES shared_projects(project_id) ON DELETE CASCADE,
                scene_id TEXT NOT NULL REFERENCES shared_scenes(scene_id) ON DELETE CASCADE,
                question TEXT NOT NULL,
                options_json TEXT NOT NULL,
                chosen TEXT NOT NULL,
                decided_by TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shared_artifacts (
                artifact_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES shared_projects(project_id) ON DELETE CASCADE,
                scene_id TEXT NOT NULL REFERENCES shared_scenes(scene_id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                version INTEGER NOT NULL,
                evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shared_open_threads (
                thread_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES shared_projects(project_id) ON DELETE CASCADE,
                summary TEXT NOT NULL,
                next_action TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shared_events (
                event_id TEXT PRIMARY KEY,
                event_key TEXT UNIQUE,
                project_id TEXT NOT NULL REFERENCES shared_projects(project_id) ON DELETE CASCADE,
                summary TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                user_interpretation TEXT NOT NULL,
                character_interpretation TEXT NOT NULL,
                confirmed INTEGER NOT NULL,
                memory_id INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_shared_scenes_project ON shared_scenes(project_id);
            CREATE INDEX IF NOT EXISTS idx_shared_threads_project ON shared_open_threads(project_id, status);
            """
        )
        columns = {row["name"] for row in self.con.execute("PRAGMA table_info(shared_events)")}
        if "event_key" not in columns:
            self.con.execute("ALTER TABLE shared_events ADD COLUMN event_key TEXT")
        self.con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_shared_events_key "
            "ON shared_events(event_key) WHERE event_key IS NOT NULL"
        )
        self.con.commit()

    @staticmethod
    def _require_project_kind(kind: str) -> str:
        if kind not in PROJECT_KINDS:
            raise ValueError(f"未知项目类型: {kind}")
        return kind

    @staticmethod
    def _project(row) -> Project:
        return Project(**dict(row))

    def create_project(self, *, kind: str, title: str, purpose: str, confirmed: bool) -> Project:
        if not confirmed:
            raise CreationConfirmationRequired("创建长期共同项目需要用户确认")
        kind = self._require_project_kind(kind)
        title, purpose = title.strip(), purpose.strip()
        if not title or not purpose:
            raise ValueError("项目标题和目标不能为空")
        now, project_id = _now(), _id("project")
        self.con.execute(
            "INSERT INTO shared_projects VALUES (?,?,?,?,?,?,?)",
            (project_id, kind, title[:120], purpose[:500], "active", now, now),
        )
        self.con.commit()
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> Project | None:
        row = self.con.execute("SELECT * FROM shared_projects WHERE project_id=?", (project_id,)).fetchone()
        return self._project(row) if row else None

    def list_projects(self, *, status: str | None = None) -> list[Project]:
        sql, params = "SELECT * FROM shared_projects", []
        if status:
            sql += " WHERE status=?"; params.append(status)
        sql += " ORDER BY updated_at DESC"
        return [self._project(row) for row in self.con.execute(sql, params).fetchall()]

    def set_project_status(self, project_id: str, status: str) -> Project:
        if status not in PROJECT_STATUS:
            raise ValueError(f"未知项目状态: {status}")
        if not self.get_project(project_id):
            raise KeyError(project_id)
        self.con.execute(
            "UPDATE shared_projects SET status=?,updated_at=? WHERE project_id=?",
            (status, _now(), project_id),
        )
        self.con.commit()
        return self.get_project(project_id)

    def create_scene(self, project_id: str, *, title: str, goal: str) -> Scene:
        if not self.get_project(project_id):
            raise KeyError(project_id)
        title, goal = title.strip(), goal.strip()
        if not title or not goal:
            raise ValueError("场景标题和目标不能为空")
        now, scene_id = _now(), _id("scene")
        self.con.execute(
            "INSERT INTO shared_scenes VALUES (?,?,?,?,?,?,?)",
            (scene_id, project_id, title[:120], goal[:500], "active", now, now),
        )
        self._touch(project_id)
        self.con.commit()
        return self.get_scene(scene_id)

    def get_scene(self, scene_id: str) -> Scene | None:
        row = self.con.execute("SELECT scene_id,project_id,title,goal,status FROM shared_scenes WHERE scene_id=?", (scene_id,)).fetchone()
        return Scene(**dict(row)) if row else None

    def record_decision(self, project_id: str, scene_id: str, *, question: str,
                        options: list[dict], chosen: str, decided_by: str,
                        evidence_message_ids: list[int]) -> Decision:
        self._require_scene(project_id, scene_id)
        if decided_by not in {"user", "character", "both"}:
            raise ValueError("decided_by 必须为 user/character/both")
        if not question.strip() or not chosen or not evidence_message_ids:
            raise CreationConfirmationRequired("共同决策需要问题、选择和消息证据")
        self._require_evidence(evidence_message_ids, require_user=decided_by in {"user", "both"})
        option_ids = {str(option.get("id")) for option in options if isinstance(option, dict)}
        if chosen not in option_ids:
            raise ValueError("chosen 不在 options 中")
        decision_id, now = _id("decision"), _now()
        self.con.execute(
            "INSERT INTO shared_decisions VALUES (?,?,?,?,?,?,?,?,?)",
            (decision_id, project_id, scene_id, question.strip()[:500],
             json.dumps(options, ensure_ascii=False), chosen, decided_by,
             json.dumps(evidence_message_ids), now),
        )
        self._touch(project_id); self.con.commit()
        return self.get_decision(decision_id)

    def get_decision(self, decision_id: str) -> Decision | None:
        row = self.con.execute("SELECT * FROM shared_decisions WHERE decision_id=?", (decision_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        return Decision(
            decision_id=data["decision_id"], project_id=data["project_id"], scene_id=data["scene_id"],
            question=data["question"], options=json.loads(data["options_json"]), chosen=data["chosen"],
            decided_by=data["decided_by"], evidence_message_ids=json.loads(data["evidence_json"]),
        )

    def save_artifact(self, project_id: str, scene_id: str, *, title: str, content: str,
                      evidence_message_ids: list[int]) -> Artifact:
        self._require_scene(project_id, scene_id)
        if not title.strip() or not content or not evidence_message_ids:
            raise CreationConfirmationRequired("产物需要标题、内容和消息证据")
        self._require_evidence(evidence_message_ids)
        version = self.con.execute(
            "SELECT count(*) FROM shared_artifacts WHERE project_id=? AND scene_id=? AND title=?",
            (project_id, scene_id, title.strip()),
        ).fetchone()[0] + 1
        artifact_id = _id("artifact")
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.con.execute(
            "INSERT INTO shared_artifacts VALUES (?,?,?,?,?,?,?,?,?)",
            (artifact_id, project_id, scene_id, title.strip()[:120], content, sha256,
             version, json.dumps(evidence_message_ids), _now()),
        )
        self._touch(project_id); self.con.commit()
        return self.get_artifact(artifact_id)

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        row = self.con.execute("SELECT * FROM shared_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        return Artifact(
            artifact_id=data["artifact_id"], project_id=data["project_id"], scene_id=data["scene_id"],
            title=data["title"], content=data["content"], sha256=data["sha256"], version=data["version"],
        )

    def open_thread(self, project_id: str, *, summary: str, next_action: str) -> OpenThread:
        if not self.get_project(project_id):
            raise KeyError(project_id)
        if not summary.strip() or not next_action.strip():
            raise ValueError("开放线程需要摘要和下一步")
        thread_id, now = _id("thread"), _now()
        self.con.execute(
            "INSERT INTO shared_open_threads VALUES (?,?,?,?,?,?,?)",
            (thread_id, project_id, summary.strip()[:500], next_action.strip()[:500], "open", now, now),
        )
        self._touch(project_id); self.con.commit()
        return OpenThread(thread_id, project_id, summary.strip()[:500], next_action.strip()[:500], "open")

    def list_open_threads(self, project_id: str) -> list[OpenThread]:
        rows = self.con.execute(
            "SELECT thread_id,project_id,summary,next_action,status FROM shared_open_threads "
            "WHERE project_id=? AND status='open' ORDER BY created_at", (project_id,)
        ).fetchall()
        return [OpenThread(**dict(row)) for row in rows]

    def resolve_thread(self, thread_id: str) -> None:
        cur = self.con.execute(
            "UPDATE shared_open_threads SET status='resolved',updated_at=? WHERE thread_id=?",
            (_now(), thread_id),
        )
        if cur.rowcount != 1:
            raise KeyError(thread_id)
        self.con.commit()

    def confirm_shared_event(self, project_id: str, *, summary: str, evidence_message_ids: list[int],
                             user_interpretation: str = "", character_interpretation: str = "") -> SharedEvent:
        if not self.get_project(project_id):
            raise KeyError(project_id)
        if not summary.strip() or not evidence_message_ids:
            raise CreationConfirmationRequired("共同经历写入需要用户确认、摘要和消息证据")
        self._require_evidence(evidence_message_ids, require_user=True)
        normalized_summary = " ".join(summary.split())
        evidence_ids = sorted(set(int(mid) for mid in evidence_message_ids))
        event_key = hashlib.sha256(
            f"{project_id}\n{normalized_summary}\n{','.join(map(str, evidence_ids))}".encode("utf-8")
        ).hexdigest()
        existing = self.con.execute(
            "SELECT event_id,project_id,summary,evidence_json,confirmed,memory_id "
            "FROM shared_events WHERE event_key=?", (event_key,),
        ).fetchone()
        if existing:
            return SharedEvent(
                existing["event_id"], existing["project_id"], existing["summary"],
                json.loads(existing["evidence_json"]), bool(existing["confirmed"]), existing["memory_id"],
            )
        prior_count = self.con.execute(
            "SELECT count(*) FROM shared_events WHERE project_id=?", (project_id,),
        ).fetchone()[0]
        event_id = _id("event")
        from .creation_relationship import build_shared_creation_relationship_candidate
        rel_candidate = build_shared_creation_relationship_candidate(
            project_id=project_id,
            project_title=self.get_project(project_id).title,
            summary=normalized_summary,
            event_id=event_id,
            evidence_message_ids=evidence_ids,
            completed=(self.get_project(project_id).status == "completed"),
            prior_events_in_project=int(prior_count),
        )
        content = f"共同项目：{self.get_project(project_id).title}。共同事件：{normalized_summary}。"
        if user_interpretation.strip():
            content += f"用户解释：{user_interpretation.strip()}。"
        if character_interpretation.strip():
            content += f"角色解释：{character_interpretation.strip()}。"
        entry = self.memory.store(
            "shared_episode", content,
            importance=0.7, confidence=0.8,
            provenance=str(evidence_ids[0]),
            meta={
                "kind": "shared_episode", "project_id": project_id,
                "evidence_message_ids": evidence_ids,
                "user_confirmed": True, "subject": "shared",
                "event_time": _now(),
            },
        )
        if user_interpretation.strip() or character_interpretation.strip():
            from .persona import (
                build_shared_meaning_candidate,
                persona_candidate_to_memory,
                validate_persona_candidate,
            )

            candidate = build_shared_meaning_candidate(
                event_summary=normalized_summary,
                user_interpretation=user_interpretation,
                character_interpretation=character_interpretation,
                evidence_ids=evidence_ids,
                user_confirmed=True,
            )
            if candidate is not None and not validate_persona_candidate(candidate, None):
                memory_candidate = persona_candidate_to_memory(candidate, evidence_ids[0])
                if memory_candidate is not None:
                    self.memory.store(
                        memory_candidate["layer"], memory_candidate["content"],
                        importance=memory_candidate["importance"],
                        confidence=memory_candidate["confidence"],
                        provenance=str(evidence_ids[0]),
                        meta={**memory_candidate["meta"], "project_id": project_id},
                    )
        self.con.execute(
            """INSERT INTO shared_events
               (event_id,event_key,project_id,summary,evidence_json,user_interpretation,
                character_interpretation,confirmed,memory_id,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (event_id, event_key, project_id, normalized_summary[:500], json.dumps(evidence_ids),
             user_interpretation.strip()[:500], character_interpretation.strip()[:500], 1, entry.id, _now()),
        )
        self._touch(project_id); self.con.commit()
        return SharedEvent(event_id, project_id, normalized_summary[:500], evidence_ids, True, entry.id,
                           relationship_candidate=rel_candidate)

    def _require_scene(self, project_id: str, scene_id: str) -> None:
        row = self.con.execute("SELECT 1 FROM shared_scenes WHERE scene_id=? AND project_id=?", (scene_id, project_id)).fetchone()
        if not row:
            raise KeyError(f"scene {scene_id} 不属于项目 {project_id}")

    def _require_evidence(self, evidence_message_ids: list[int], *, require_user: bool = False) -> None:
        ids = [int(mid) for mid in evidence_message_ids]
        placeholders = ",".join("?" for _ in ids)
        rows = self.con.execute(
            f"SELECT id, role FROM messages WHERE id IN ({placeholders})", ids,
        ).fetchall()
        if len(rows) != len(set(ids)):
            raise CreationConfirmationRequired("证据消息不存在")
        if require_user and not any(row["role"] == "user" for row in rows):
            raise CreationConfirmationRequired("共同确认需要用户消息证据")

    def _touch(self, project_id: str) -> None:
        self.con.execute("UPDATE shared_projects SET updated_at=? WHERE project_id=?", (_now(), project_id))
