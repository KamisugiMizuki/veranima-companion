"""对话引擎错误容忍测试：LLM 不可用 / 生成失败时不崩溃，返回角色化兜底。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.llm.client import LLMError, LLMUnavailableError
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    """可编程假 LLM：按设定抛错或返回固定回复。"""

    def __init__(self, error=None, reply="正常回复", loaded=True):
        self.error = error
        self.reply = reply
        self.loaded = loaded
        self.calls = 0
        self.last_messages: list | None = None

    def chat(self, messages, **kw):
        self.calls += 1
        self.last_messages = messages
        if self.error:
            raise self.error
        return self.reply

    def is_model_loaded(self):
        return self.loaded

    low_energy_max_tokens = 256


@pytest.fixture
def agent(tmp_path):
    card = CharacterCard(name="小V", first_mes="你好")
    memory = MemoryStore(
        db_path=str(tmp_path / "t.db"),
        config={},
        provider=FakeEmbed(),
    )
    return card, memory


def _image_data_url(format_name="PNG"):
    import io
    from PIL import Image
    from veranima.core.image_payload import make_image_payload

    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buf, format=format_name)
    return make_image_payload(buf.getvalue()).data_url


def test_handle_llm_unavailable_returns_wakeup(agent, tmp_path):
    """服务不可用：前置检查拦截，返回唤醒文案，不发请求。"""
    card, memory = agent
    llm = FakeLLM(error=LLMUnavailableError("model not loaded"), loaded=False)
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={"chat": {"proactive_message_prob": 0.0}})
    r = a.handle("在吗？")
    assert "还没醒" in r.reply
    assert "API" in r.reply
    assert llm.calls == 0  # 关键：不可用时不发 chat 请求
    msgs = memory.recent_messages(limit=4)
    assert len(msgs) == 2  # user + assistant 兜底
    assert msgs[-1]["role"] == "assistant"


def test_prompt_preserves_message_times_across_restart(agent):
    card, memory = agent
    memory.con.executemany(
        "INSERT INTO messages(role, content, created_at, energy_at, mood_at) VALUES (?,?,?,?,?)",
        [
            ("user", "我觉得是该睡觉的时间了", "2026-08-21T10:40:48", 80, "平静"),
            ("assistant", "行了，赶紧躺着去，晚安。", "2026-08-21T10:40:52", 80, "平静"),
        ],
    )
    memory.con.commit()
    llm = FakeLLM(reply="知道了")
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={})

    a.handle("不对，我根本还没睡")

    assert llm.last_messages[1] == {
        "role": "user",
        "content": "[2026-08-21 10:40:48] 我觉得是该睡觉的时间了",
    }
    assert llm.last_messages[2] == {
        "role": "assistant",
        "content": "[2026-08-21 10:40:52] 行了，赶紧躺着去，晚安。",
    }
    assert llm.last_messages[-1]["content"].startswith("[")
    assert llm.last_messages[-1]["content"].endswith("不对，我根本还没睡")
    assert "不要仅凭晚安、睡觉或早安推断已经跨日" in llm.last_messages[0]["content"]
    assert all(item.get("created_at") for item in a._history[-2:])


def test_generated_reply_does_not_echo_prompt_time_prefixes(agent):
    card, memory = agent
    llm = FakeLLM(
        reply="[2026-08-21 13:20:30] [2026-08-21 13:20:34] [2026-08-21 13:20:37] 那就测试呗。"
    )
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={})

    result = a.handle("时间戳应该是加好了")

    assert result.reply == "那就测试呗。"
    assert memory.recent_messages(limit=1)[0]["content"] == "那就测试呗。"


def test_handle_model_loaded_but_chat_fails(agent):
    """模型已加载但生成异常：异常分类兜底。（proactive 关掉，避免随机触发影响断言）"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(error=LLMUnavailableError("server hiccup"), loaded=True), state=AgentState(), config={"chat": {"proactive_message_prob": 0.0}})
    r = a.handle("在吗？")
    assert "还没醒" in r.reply
    assert a.llm.calls == 1


def test_handle_llm_generic_error_returns_fallback(agent):
    """LLM 在线但生成失败：返回通用兜底。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(error=LLMError("server 500")), state=AgentState(), config={})
    r = a.handle("在吗？")
    assert "有点卡" in r.reply


def test_handle_proactive_does_not_need_llm(agent):
    """R4：handle 内不再触发无理由随机主动（R4_SPEC 3 idle/fatigue 关闭）。"""
    card, memory = agent
    llm = FakeLLM(error=LLMUnavailableError("down"))
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={"chat": {"proactive_message_prob": 1.0}})
    r = a.handle("在吗？")
    # 随机主动已移除：proactive 恒 False，LLM 只调 1 次（对话生成）
    assert r.proactive is False
    assert r.proactive_msg == ""
    assert llm.calls == 1


def test_start_greeting_without_llm(agent):
    """start() 问候是时间模板，不依赖 LLM。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(error=LLMUnavailableError("down")), state=AgentState(), config={})
    opening = a.start()
    assert opening  # 初遇开场白或时间问候


def test_extract_events_preference(agent):
    """'我特别喜欢X' 等偏好表达 → semantic 层记忆。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a.handle("我特别喜欢下雨天，下雨的时候心情会变好")
    sem = memory.list_layer("semantic")
    assert any("下雨天" in e.content for e in sem)


def test_extract_events_strong(agent):
    """'记住' 诉求 → episodic 层记忆。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a.handle("记住：我的生日是3月14日")
    eps = memory.list_layer("episodic")
    assert any("生日" in e.content for e in eps)


def test_extract_events_plain_no_duplicate(agent):
    """普通闲聊（无信号词）不产生记忆，也不把消息本身当记忆。"""
    card, memory = agent
    a = Agent(card=card, memory=memory, llm=FakeLLM(), state=AgentState(), config={})
    a.handle("今天天气不错")
    assert len(memory.list_layer("semantic")) == 0
    assert len(memory.list_layer("episodic")) == 0


# ---------- 8.6 多模态图像输入 ----------

def test_handle_with_images_uses_multimodal_content(agent):
    """带图消息：user content 组装为多模态数组（text + image_url）。"""
    card, memory = agent
    llm = FakeLLM()
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={"chat": {"proactive_message_prob": 0.0}})
    img = _image_data_url()
    r = a.handle("看看这张图", [img])
    assert r.reply
    user_msg = llm.last_messages[-1]
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][0]["text"].endswith("看看这张图")
    assert user_msg["content"][1] == {"type": "image_url", "image_url": {"url": img}}


def test_handle_image_only_message(agent):
    """纯图片消息（无文本）：仍处理，text 用占位。"""
    card, memory = agent
    llm = FakeLLM()
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={"chat": {"proactive_message_prob": 0.0}})
    r = a.handle("", [_image_data_url("JPEG")])
    assert r.reply
    user_msg = llm.last_messages[-1]
    assert user_msg["content"][0]["text"].startswith("[")
    assert user_msg["content"][0]["text"].endswith("[图片]")
    # 记忆用 [图片] 占位（不存 base64）
    recent = memory.recent_messages(limit=2)
    assert "[图片]" in recent[0]["content"]


def test_handle_drops_invalid_image_payload_at_shared_agent_boundary(agent):
    card, memory = agent
    llm = FakeLLM()
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={})

    a.handle("不要转发伪图片", ["data:image/png;base64,QUJD"])

    assert isinstance(llm.last_messages[-1]["content"], str)
    assert "[图片]" not in memory.recent_messages(limit=2)[0]["content"]


def test_handle_no_text_no_images_empty(agent):
    """空文本 + 无图：返回空 TurnResult，不发请求。"""
    card, memory = agent
    llm = FakeLLM()
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config={})
    r = a.handle("")
    assert r.reply == ""
    assert llm.calls == 0


def test_handle_consumes_active_style_brief_and_length_plan(agent, tmp_path):
    """生产 Agent 链：聚合 StyleBrief 与长度计划进同一 system prompt，不泄漏来源标识。"""
    from veranima.core.learning import UserStyleProfile

    card, memory = agent
    llm = FakeLLM()
    state = AgentState()
    a = Agent(
        card=card, memory=memory, llm=llm, state=state,
        config={"root": str(tmp_path), "chat": {"proactive_message_prob": 0.0}},
    )
    a.relationship.conflict_tension = 0.7  # 每轮同步到 state 的关系真值，触发复杂 ResponsePlan
    profile = UserStyleProfile(
        sample_count=100,
        char_count=10000,
        avg_message_chars=100.0,
        avg_sentence_chars=40.0,
        detail_preference=0.9,
        confidence=1.0,
        source_id="PRIVATE_CORPUS_ID",
        scene_count=3,
        reviewed_count=12,
        quality_score=1.0,
    )
    import json
    corpus_dir = tmp_path / "data" / "style_corpora" / "private-style"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "manifest.json").write_text(
        json.dumps({"corpus_id": "private-style", "version": 1, "status": "active"}),
        encoding="utf-8",
    )
    a.style.activate_corpus("private-style", profile)

    result = a.handle("请处理这个复杂问题", channel="im")
    system_prompt = llm.last_messages[0]["content"]

    assert result.reply
    assert "【表达适配】" in system_prompt
    assert "长度=long" in system_prompt
    assert "PRIVATE_CORPUS_ID" not in system_prompt

    a.handle("以后回复都简短点，只说结论。", channel="im")
    a = Agent(
        card=card, memory=memory, llm=llm, state=AgentState(),
        config={"root": str(tmp_path), "chat": {"proactive_message_prob": 0.0}},
    )
    a.relationship.conflict_tension = 0.7
    a.handle("继续处理另一个复杂问题", channel="im")
    assert "长度=short" in llm.last_messages[0]["content"]

    a.reset_style()
    manifest = json.loads((corpus_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "preview"
    assert a.style.active_corpus_id == ""


def test_handle_history_starting_with_assistant_normalized(agent):
    """孤立 assistant（proactive/late_reply 追加）被规范化：请求序列以 user 开头。

    回归 2026-08-04：_history 截断后第一条是 assistant 时，llama.cpp Qwen3 jinja
    模板报 400 "No user query found in messages"（跑若干轮后偶发）。
    """
    card, memory = agent
    llm = FakeLLM()
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(),
              config={"chat": {"proactive_message_prob": 0.0, "history_max_messages": 4}})
    # 模拟历史：proactive 留下的孤立 assistant（无配对 user），且截断边界使其成为第一条
    a._history = [
        {"role": "assistant", "content": "（想起一件事）对了，你上次说的那件事后来怎么样了？"},
        {"role": "user", "content": "嗯，那件事已经解决了"},
        {"role": "assistant", "content": "那就好。"},
    ]
    r = a.handle("今天天气不错")
    assert r.reply
    msgs = llm.last_messages
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"      # 开头的孤立 assistant 被丢弃
    assert msgs[-1]["role"] == "user"     # 结尾是当前用户消息
    assert msgs[-1]["content"].endswith("今天天气不错")


def test_handle_history_all_assistant_normalized(agent):
    """极端情况：历史只有孤立 assistant（如 start 开场白后未对话）也能正常请求。"""
    card, memory = agent
    llm = FakeLLM()
    a = Agent(card=card, memory=memory, llm=llm, state=AgentState(),
              config={"chat": {"proactive_message_prob": 0.0}})
    a._history = [{"role": "assistant", "content": "（敲完最后一行代码）嗯。具体问题？"}]
    r = a.handle("在吗？")
    assert r.reply
    msgs = llm.last_messages
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[-1]["role"] == "user"


# ---------- 重启续接（2026-08-04） ----------

def test_restart_restores_history_and_state(tmp_path):
    """重启续接：同 DB 新建 Agent 恢复对话上下文与内在状态（依恋度/计数）。"""
    from veranima.core.agent import Agent
    card = CharacterCard(name="小V", first_mes="你好")
    cfg = {"chat": {"proactive_message_prob": 0.0}}

    # 第一次运行：聊两轮，状态积累
    mem1 = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    a1 = Agent(card=card, memory=mem1, llm=FakeLLM(), state=AgentState(), config=cfg)
    a1.handle("你好呀")
    a1.handle("今天加班好累")
    before_attach = a1.state.attachment
    before_total = a1.state.total_messages
    assert before_total >= 4  # 2 user + 2 assistant

    # 模拟重启：同 DB 新建 agent（进程内存全部丢失）
    mem2 = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    a2 = Agent(card=card, memory=mem2, llm=FakeLLM(), state=AgentState(), config=cfg)

    # 状态恢复（不再回初始 0.5）
    assert a2.state.attachment == pytest.approx(before_attach, abs=1e-4)
    assert a2.state.total_messages == before_total
    # 对话上下文恢复
    assert len(a2._history) == len(a1._history) == 4
    assert a2._history[0]["role"] == "user"
    assert a2._history[-1]["role"] == "assistant"
    # 重启后直接接话：请求序列正常且能引用之前内容
    r = a2.handle("我们刚才聊到哪了？")
    assert r.reply
    msgs = a2.llm.last_messages
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"      # 恢复的历史以 user 开头
    assert msgs[-1]["role"] == "user"
    assert any("加班" in str(m.get("content")) for m in msgs)  # 上次话题在上下文中


def test_restart_fresh_db_keeps_defaults(tmp_path):
    """新库（无快照/无消息）：状态与历史保持默认，不报错。"""
    from veranima.core.agent import Agent
    card = CharacterCard(name="小V", first_mes="你好")
    mem = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    a = Agent(card=card, memory=mem, llm=FakeLLM(), state=AgentState(), config={})
    assert a.state.attachment == pytest.approx(0.5)
    assert a.state.total_messages == 0
    assert a._history == []
