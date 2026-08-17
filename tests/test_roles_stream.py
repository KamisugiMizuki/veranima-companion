"""DESIGN 4.11 多角色 + 4.13 流式输出测试。"""
import json

import pytest

from veranima.core.roles import active_role, list_roles, switch_role
from veranima.llm.client import LLMClient, _split_sentences


def test_llm_unconfigured_returns_hint():
    """LLM 未配置 base_url → 返回缺省提示文案（不报错）。"""
    c = LLMClient({})
    reply = c.chat([{"role": "user", "content": "hi"}])
    assert "未配置" in reply or "config.yaml" in reply
    chunks = c.stream_chat([{"role": "user", "content": "hi"}])
    assert isinstance(chunks, list) and len(chunks) == 1


# ---------- 多角色注册表 ----------

def _make_role(tmp_path, role_id, name):
    d = tmp_path / "characters" / role_id
    d.mkdir(parents=True)
    (d / "character.json").write_text(json.dumps({
        "name": name, "first_mes": "hi",
    }, ensure_ascii=False), encoding="utf-8")
    return d


def test_list_roles(monkeypatch, tmp_path):
    _make_role(tmp_path, "vera", "Vera")
    _make_role(tmp_path, "zima", "Зима")
    from veranima import core
    monkeypatch.setattr(core.roles, "roles_dir", lambda: tmp_path / "characters")
    roles = list_roles()
    assert len(roles) == 2
    assert {r["id"] for r in roles} == {"vera", "zima"}
    assert roles[0]["name"] in ("Vera", "Зима")


def test_switch_role_writes_config(monkeypatch, tmp_path):
    _make_role(tmp_path, "vera", "Vera")
    from veranima import core
    monkeypatch.setattr(core.roles, "roles_dir", lambda: tmp_path / "characters")
    # 拦截 save_config 写盘（验证调用）
    written = {}
    def fake_save(data, path=None):
        written["data"] = data
    monkeypatch.setattr(core.roles, "save_config", fake_save)
    ok, _ = switch_role("vera", {"character_card": "config/character.json"})
    assert ok is True
    assert written["data"]["character_card"] == "characters/vera/character.json"


def test_switch_role_missing(monkeypatch, tmp_path):
    from veranima import core
    monkeypatch.setattr(core.roles, "roles_dir", lambda: tmp_path / "characters")
    ok, msg = switch_role("nope", {})
    assert ok is False
    assert "不存在" in msg


def test_active_role(monkeypatch, tmp_path):
    _make_role(tmp_path, "vera", "Vera")
    from veranima import core
    monkeypatch.setattr(core.roles, "roles_dir", lambda: tmp_path / "characters")
    monkeypatch.setattr(core.roles, "ROOT", tmp_path)
    active = active_role({"character_card": "characters/vera/character.json"})
    assert active["id"] == "vera"
    assert active["name"] == "Vera"


# ---------- 流式输出 ----------

def test_split_sentences():
    assert _split_sentences("你好。今天好吗？很好！") == ["你好。", "今天好吗？", "很好！"]
    assert _split_sentences("无标点的一句话") == ["无标点的一句话"]
    assert _split_sentences("嗯…然后呢？") == ["嗯…", "然后呢？"]


def test_split_sentences_empty():
    assert _split_sentences("") == []
    assert _split_sentences("   ") == []
