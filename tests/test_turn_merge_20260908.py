"""连发合并与不阻塞输入（docs/android/TURN_MERGE_SPEC.md，2026-09-08 用户裁决）。

钉死两件事：
1) core.handle(pre_stored_msg_id=...) 不再重复落库调用方已存的用户消息，
   副作用（tension/info gap）沿用该 id；
2) bridge.chat_batch 逐条落库 N 行 + 合并成一次 handle（一次 LLM、一条回复），
   pre_stored_msg_id = 末条 user 行 id。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.memory.store import MemoryStore

_BRIDGE = Path(__file__).resolve().parents[1] / "android/fuyuno/app/src/main/python/bridge.py"


class Embed:
    dim = 8

    def embed(self, texts):
        return [[0.0] * self.dim for _ in texts]


class FakeLLM:
    def __init__(self, reply="嗯。"):
        self.reply = reply
        self.calls = 0

    def is_model_loaded(self):
        return True

    def chat(self, messages, **kwargs):
        self.calls += 1
        return self.reply


@pytest.fixture(scope="module")
def bridge():
    spec = importlib.util.spec_from_file_location("fuyuno_bridge_0908", _BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 1) core：预存消息不重复落库 ----------

def test_pre_stored_msg_id_skips_duplicate_store(tmp_path):
    store = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    llm = FakeLLM()
    agent = Agent(CharacterCard(name="凛"), store, llm, AgentState(), config={})
    mid = store.store_message("user", "第一句", 0.7, "平静")
    before = len(store.recent_messages(limit=50))

    r = agent.handle("第一句\n第二句", pre_stored_msg_id=mid)

    rows = store.recent_messages(limit=50)
    users = [m for m in rows if m["role"] == "user"]
    assert len(users) == 1, "调用方已落库的用户消息不得再存一遍"
    assert users[0]["id"] == mid
    assert len(rows) == before + 1  # 只多了一条 assistant 回复
    assert rows[-1]["role"] == "assistant" and rows[-1]["content"] == r.reply
    assert llm.calls == 1


# ---------- 2) bridge：逐条落库 + 一轮 handle ----------

def test_chat_batch_stores_each_message_and_calls_handle_once(bridge, monkeypatch, tmp_path):
    store = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    seen = {}

    class FakeAgent:
        role_key = "lin"
        message_channel = "im"

        def __init__(self):
            self.memory = store
            self.card = CharacterCard(name="凛")
            self.state = AgentState()

        def handle(self, text, images=None, channel="im", attachments="",
                   now=None, pre_stored_msg_id=None):
            seen["calls"] = seen.get("calls", 0) + 1
            seen["text"] = text
            seen["images"] = images
            seen["pre"] = pre_stored_msg_id
            seen["users_at_handle"] = [m["id"] for m in store.recent_messages(limit=50)
                                       if m["role"] == "user"]
            return SimpleNamespace(reply="嗯，都看到了。", reply_obj=None,
                                   portrait="", tone="平静", energy=0.7)

    monkeypatch.setattr(bridge, "_agent_for", lambda role: FakeAgent())
    out = json.loads(bridge.chat_batch(json.dumps([
        {"text": "今天好累"},
        {"text": "不想做饭了"},
        {"text": "要不点外卖"},
    ]), "lin"))

    assert out["ok"] is True, out
    rows = store.recent_messages(limit=50)
    users = [m for m in rows if m["role"] == "user"]
    assert [m["content"] for m in users] == ["今天好累", "不想做饭了", "要不点外卖"]
    assert seen["users_at_handle"] == [m["id"] for m in users]  # 落库先于 handle
    assert seen["text"] == "今天好累\n不想做饭了\n要不点外卖"      # 合并成一轮喂 LLM
    assert seen["pre"] == users[-1]["id"]                        # 末条 user 行 id
    assert seen["images"] is None
    assert out["ids"] == [m["id"] for m in users]
    assert "都看到了" in out["reply"]
    assert seen["calls"] == 1  # 一条回复，不是三条


def test_chat_batch_empty_batch_is_rejected(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "_agent_for", lambda role: SimpleNamespace())
    out = json.loads(bridge.chat_batch("[]", "lin"))
    assert out["ok"] is False and out["error"] == "空消息"


# ---------- 3) 漏回补回：复用已落库的 user 行 ----------

def test_catch_up_reuses_stored_row_without_duplicate(bridge, monkeypatch, tmp_path):
    """进程死在 handle 中间（chat_batch 已落 user 行）→ 补回走 pre_stored_msg_id，
    不得再落一条重复 user（09-08 导出筛查「重复用户消息」同型病灶）。"""
    from datetime import datetime, timedelta, timezone

    store = MemoryStore(str(tmp_path / "db.sqlite"), config={}, provider=Embed())
    mid = store.store_message("user", "在吗", 0.7, "平静", role_id="lin")
    store.con.execute("UPDATE messages SET created_at=? WHERE id=?",
                      ((datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(), mid))
    store.con.commit()
    seen = {}

    class FakeAgent:
        role_key = "lin"

        def __init__(self):
            self.memory = store

        def handle(self, text, **kw):
            seen["text"] = text
            seen.update(kw)
            return SimpleNamespace(reply="嗯。")

    monkeypatch.setattr(bridge.boot, "agents", {"lin": FakeAgent()}, raising=False)
    out = json.loads(bridge.catch_up_replies())

    assert out["handled"] == 1
    assert seen["pre_stored_msg_id"] == mid
    users = [m for m in store.recent_messages(limit=50) if m["role"] == "user"]
    assert len(users) == 1
