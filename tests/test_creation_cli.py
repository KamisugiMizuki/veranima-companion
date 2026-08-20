"""角色包与共同创作 CLI 消费路径。"""
from __future__ import annotations

import json
from pathlib import Path

import veranima.cli as cli
from veranima.memory.store import MemoryStore


def _config(tmp_path: Path) -> dict:
    return {
        "root": str(tmp_path),
        "memory": {"embedding_model": "none"},
    }


def _role(tmp_path: Path) -> None:
    role = tmp_path / "characters" / "demo"
    role.mkdir(parents=True)
    (role / "character.json").write_text(json.dumps({
        "spec": "chara_card_v3", "data": {"name": "Demo", "extensions": {"veranima": {}}},
    }), encoding="utf-8")


def test_roles_export_defaults_to_charpkg(monkeypatch, tmp_path, capsys):
    _role(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda: _config(tmp_path))
    monkeypatch.setattr("veranima.core.roles.roles_dir", lambda: tmp_path / "characters")

    assert cli.main(["roles", "export", "demo", str(tmp_path / "demo.charpkg")]) == 0
    assert (tmp_path / "demo.charpkg").exists()
    assert "已导出" in capsys.readouterr().out


def test_create_project_and_confirm_event(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: _config(tmp_path))

    assert cli.main(["create", "project", "story", "屋顶", "写完短篇"]) == 0
    project_id = capsys.readouterr().out.strip().split()[-1]
    assert project_id.startswith("project_")
    memory = MemoryStore(db_path=str(tmp_path / "data" / "veranima.db"), config={"embedding_model": "none"})
    evidence_id = memory.store_message("user", "这个开场可以定稿", 80, "平静")
    assert cli.main(["create", "confirm", project_id, "完成了开场", str(evidence_id)]) == 0
    assert "共同经历已确认" in capsys.readouterr().out


def test_create_cli_consumes_scene_decision_artifact_and_thread(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_config", lambda: _config(tmp_path))
    memory = MemoryStore(db_path=str(tmp_path / "data" / "veranima.db"), config={"embedding_model": "none"})
    evidence_id = memory.store_message("user", "选择屋顶开场", 80, "平静")
    assert cli.main(["create", "project", "story", "屋顶", "写完短篇"]) == 0
    project_id = capsys.readouterr().out.strip().split()[-1]
    assert cli.main(["create", "scene", project_id, "第一幕", "决定开场"]) == 0
    scene_id = capsys.readouterr().out.strip().split()[-1]
    assert cli.main(["create", "decision", project_id, scene_id, "是否回屋顶", "roof", str(evidence_id)]) == 0
    assert "决策已记录" in capsys.readouterr().out
    assert cli.main(["create", "artifact", project_id, scene_id, "开场", "风吹过屋顶", str(evidence_id)]) == 0
    assert "产物已保存" in capsys.readouterr().out
    assert cli.main(["create", "thread", project_id, "决定风声线索", "比较两个版本"]) == 0
    assert "线程已记录" in capsys.readouterr().out
    assert cli.main(["create", "list"]) == 0
    assert "屋顶" in capsys.readouterr().out
