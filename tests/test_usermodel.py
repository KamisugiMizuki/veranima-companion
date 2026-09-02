"""UserModel 行为测试（2026-09-02 设计：usermodel.json 单一真源 + 设置页编辑）。

- 文件读写 / pinned 闸 / source 冲突链 / 空值清除
- 旧 user_profile 表一次性迁移
- nightly digest 顺带产出 portrait（角色写）；缺 portrait 不覆盖旧值
- _profile_block 注入「我眼中的你」
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from veranima.memory.store import MemoryStore
from veranima.memory.usermodel import UserModel


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


@pytest.fixture
def um(tmp_path):
    return UserModel(tmp_path / "usermodel.json")


def test_profile_roundtrip_and_rules(um):
    assert um.set_profile("city", "杭州", source="dialog") is True
    assert um.get_profile("city")["value"] == "杭州"
    assert um.set_profile("gender_guess", "x") is False        # 闭集外丢弃
    assert um.set_profile("city", "上海", source="dialog") is True
    assert um.get_profile("city")["value"] == "上海"           # 同级可更新
    um.set_profile("city", "北京", source="user", confidence=1.0)
    assert um.set_profile("city", "深圳", source="dialog", confidence=0.9) is False
    assert um.get_profile("city")["value"] == "北京"           # user 不被 dialog 覆写
    # pinned：非 user 来源一律拒；user 仍可写
    um.set_pinned("occupation", True)
    um.set_profile("occupation", "嵌入式", source="user", confidence=1.0)
    assert um.set_profile("occupation", "学生", source="dialog") is False
    assert um.set_profile("occupation", "程序员", source="user", confidence=1.0) is True
    # 空值：user 侧清除键；dialog 侧丢弃
    assert um.set_profile("city", "", source="dialog") is False
    assert um.set_profile("city", "", source="user") is True
    assert um.get_profile("city") is None
    assert um.all_profile() == {} or "city" not in um.all_profile()


def test_portrait_per_role(um):
    um.set_portrait("xumian", "嘴硬心软，深夜活跃。")
    assert um.get_portrait("xumian") == "嘴硬心软，深夜活跃。"
    assert um.get_portrait("lin") == ""
    um.set_portrait("xumian", "")                              # 空=清除
    assert um.get_portrait("xumian") == ""


def test_external_edit_visible_on_next_read(um):
    """adb push / 手改文件后（mtime 变化）下一次读必须见新值。"""
    um.set_profile("city", "杭州", source="user")
    doc = json.loads(Path(um.path).read_text(encoding="utf-8"))
    doc["profile"]["city"]["value"] = "苏州"
    Path(um.path).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    import os, time as _t
    os.utime(um.path, (_t.time() + 2, _t.time() + 2))
    assert um.get_profile("city")["value"] == "苏州"


def test_migrate_from_old_table(tmp_path):
    """旧库 user_profile 表 → 首次打开时并入 json（已有键不回退）。"""
    db = str(tmp_path / "m.db")
    s1 = MemoryStore(db_path=db, config={}, provider=FakeEmbed())
    s1.con.execute(
        "INSERT INTO user_profile(key,value,source,confidence,updated_at)"
        " VALUES ('city','杭州','user',1.0,'2026-09-01T00:00:00+00:00')")
    s1.con.commit()
    s1.con.close()
    assert not (tmp_path / "usermodel.json").exists()         # json 尚未诞生、表里有数据
    s2 = MemoryStore(db_path=db, config={}, provider=FakeEmbed())
    assert s2.profile_get("city")["value"] == "杭州"
    s2.profile_set("city", "北京", source="dialog", confidence=0.9)
    assert s2.profile_get("city")["value"] == "杭州"           # 迁移后规则照旧
    s2.con.close()


def test_profile_api_delegates_to_json(tmp_path):
    s = MemoryStore(db_path=str(tmp_path / "d.db"), config={}, provider=FakeEmbed())
    s.profile_set("real_name", "林晓", source="dialog", confidence=0.7)
    doc = json.loads((tmp_path / "usermodel.json").read_text(encoding="utf-8"))
    assert doc["profile"]["real_name"]["value"] == "林晓"
    n = s.con.execute("SELECT count(*) FROM user_profile").fetchone()[0]
    assert n == 0                                              # 表不再被写
    s.con.close()


# ---------- digest portrait + prompt 注入 ----------

class FakeLLM:
    base_url = "http://fake"

    def __init__(self, raw):
        self.raw = raw

    def chat(self, messages, **kw):
        return self.raw


def _digest_agent(tmp_path, raw):
    from veranima.core.agent import Agent
    from veranima.core.character import CharacterCard
    from veranima.core.state import AgentState
    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(db_path=str(tmp_path / "a.db"),
                         config={"decay_enabled": False}, provider=FakeEmbed())
    agent = Agent(card=card, memory=memory, llm=FakeLLM(raw),
                  state=AgentState(),
                  config={"chat": {"proactive_message_prob": 0.0},
                          "proactive": {"enabled": False}})
    for txt in ("周一加班到十一点", "周二继续改方案", "周三终于提测了"):
        mid = agent.memory.store_message("user", txt)
        agent._store_candidate({
            "kind": "shared_episode", "content": txt,
            "source_message_id": mid, "confidence": 0.9,
            "subject": "user", "source": "rule_extract"})
    return agent


def test_digest_stores_portrait(tmp_path):
    raw = json.dumps({"content": "这周在赶项目。",
                      "portrait": "他嘴上说没事，连加三天班就是在硬撑。"},
                     ensure_ascii=False)
    agent = _digest_agent(tmp_path, raw)
    out = agent.maybe_nightly_digest()
    assert out.get("created") is True
    rid = agent._schedule_role_id() or agent.card.name
    assert agent.memory.usermodel.get_portrait(rid) == "他嘴上说没事，连加三天班就是在硬撑。"
    # prompt 注入（含新增的 current_goal 标签行也在同一块）
    agent.memory.profile_set("current_goal", "veranima 安卓化", source="user", confidence=1.0)
    block = agent._profile_block()
    assert "我眼中的你" in block and "硬撑" in block and "近期在忙" in block


def test_digest_without_portrait_keeps_old(tmp_path):
    """老格式输出（只有 content）：portrait 不被清空。"""
    agent = _digest_agent(tmp_path, json.dumps({"content": "在赶项目。"}))
    rid = agent._schedule_role_id() or agent.card.name
    agent.memory.usermodel.set_portrait(rid, "旧解读")
    assert agent.maybe_nightly_digest().get("created") is True
    assert agent.memory.usermodel.get_portrait(rid) == "旧解读"


# ---------- 安卓 bridge 端点（设置页编辑页的数据面；PC 可直接加载 bridge.py） ----------

def _load_bridge():
    import importlib.util
    p = Path(__file__).resolve().parents[1] / "android/fuyuno/app/src/main/python/bridge.py"
    spec = importlib.util.spec_from_file_location("fuyuno_bridge_um", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bridge_usermodel_endpoints(tmp_path):
    import types
    bridge = _load_bridge()
    agent = _digest_agent(tmp_path, "{}")
    bridge.boot = types.SimpleNamespace(agent=agent, root=tmp_path)
    r = json.loads(bridge.usermodel_get())
    assert r["ok"] and r["path"].endswith("usermodel.json")
    assert bridge.usermodel_set("city", "杭州", "1") and json.loads(
        bridge.usermodel_get())["profile"]["city"]["value"] == "杭州"
    assert agent.memory.profile_get("city")["pinned"] is True
    # pinned 键：对话提取端（_apply_profile_facts→dialog）拒写，锁定真生效
    class _J:
        profile = {"city": "上海"}
    agent._apply_profile_facts(_J())
    assert agent.memory.profile_get("city")["value"] == "杭州"
    bridge.boot = None
    assert json.loads(bridge.usermodel_get())["ok"] is False
