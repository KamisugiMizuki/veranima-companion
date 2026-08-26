"""OneBot v11（NapCatQQ）适配器测试：白名单 / 文本提取 / 消息管线 / 离线思考。

不连真实 WS：替换 bot 为 FakeBot，直接调用 adapter._handle_private。
"""

from __future__ import annotations

import asyncio
import datetime
import random
import time

import pytest
from aiocqhttp import Message

from veranima.adapters.qq import OfflineThinkTimer, QQAdapter
from veranima.core.agent import Agent, TurnResult
from veranima.core.character import CharacterCard
from veranima.core.image_payload import make_image_payload
from veranima.core.state import AgentState
from veranima.core.qq_advisor import QQMaterial
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


class FakeLLM:
    def __init__(self, reply="你好呀"):
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **kw):
        self.calls += 1
        return self.reply

    def is_model_loaded(self):
        return True

    low_energy_max_tokens = 256


class FakeBot:
    """替换 adapter.bot：记录 send/send_private_msg 调用。"""

    def __init__(self):
        self.sent: list = []

    async def send(self, event, message, **kw):
        self.sent.append(("send", str(message)))

    async def send_private_msg(self, **kw):
        self.sent.append(("private", kw))


@pytest.fixture
def agent(tmp_path):
    card = CharacterCard(name="小V", description="测试", personality="温柔")
    return Agent(
        card=card,
        memory=MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed()),
        llm=FakeLLM(),
        state=AgentState(),
        config={"chat": {"proactive_message_prob": 0.0}},
    )


@pytest.fixture
def adapter(agent):
    # quiet_hours=None：默认测试不关心静默时段（时段判定单独测）
    a = QQAdapter(agent, allowed_qq=["10001"], quiet_hours=None)
    a.bot = FakeBot()
    return a


def run(coro):
    return asyncio.run(coro)


def run_and_drain(adapter, coro):
    async def wrapped():
        await coro
        pending = list(getattr(adapter, "_sticker_tasks", ()))
        if pending:
            await asyncio.gather(*pending)
    return asyncio.run(wrapped())


# ---------- 白名单 ----------

def test_non_whitelist_ignored(adapter, agent):
    """非白名单 QQ 消息：不调用 agent.handle，不发送。"""
    orig = agent.handle

    def fail(text):
        raise AssertionError("agent.handle must not be called")

    agent.handle = fail
    run(adapter._handle_private({"user_id": 99999, "message_type": "private", "message": "你好"}))
    assert adapter.bot.sent == []
    agent.handle = orig


def test_whitelist_processed(adapter, agent):
    """白名单 QQ：handle 被调用，回复发送。"""
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "在吗"}))
    assert adapter.bot.sent == [("send", "你好呀")]


def test_mixed_model_reply_reaches_qq_as_last_visible_text(adapter, agent):
    mixed = (
        "1. **分析输入**：用户又修了代码。\n"
        '`{"segments":[{"text":"草稿回复","tone":"调侃"}]}\n'
        "说明文字。\n"
        '`{"segments":[{"text":"最后一版给用户看的话","tone":"调侃"}]}\n'
    )
    agent.llm.reply = mixed
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "又修了一个半小时代码"}))
    assert adapter.bot.sent == [("send", "最后一版给用户看的话")]


def test_busy_mixed_reply_preserves_complete_visible_text(adapter, agent):
    visible = "哟，终于舍得把高数书翻开了？看你今天状态还行，继续保持，别又看两页就钻回代码里了啊。先把手机放远一点，认真学一会儿。"
    agent.llm.reply = (
        "1. **分析输入**：用户开始学习。\n"
        f'`{{"segments":[{{"text":"{visible}","tone":"调侃"}}]}}\n'
    )
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "已严肃开始学习"}))
    sent = adapter.bot.sent[0][1]
    assert sent == visible
    assert "（我这边有点忙，回头细说）" not in sent
    assert len(sent) > 40


def test_busy_reply_obj_preserves_complete_structured_text(adapter, agent):
    full_text = "哟，终于舍得把高数书翻开了？看你今天状态还行，继续保持，今天先认真学一会儿，别又看两页就钻回代码里了啊。"
    agent.llm.reply = '{"segments":[{"text":"' + full_text + '"}]}'
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "已严肃开始学习"}))
    sent = adapter.bot.sent[0][1]
    assert sent == full_text


def test_qq_proactive_rejects_unanchored_reference(adapter):
    adapter.agent._short_task = lambda *args, **kwargs: "醒了。之前那事后来搞定没？"
    material = QQMaterial("memory", "用户今天继续学高数", 0.9, 1)
    assert adapter._generate_qq_proactive(material, datetime.datetime.now().astimezone()) == ""


def test_qq_proactive_rejects_message_without_material_topic(adapter):
    adapter.agent._short_task = lambda *args, **kwargs: "后来搞定没？"
    material = QQMaterial("memory", "用户今天继续学高数", 0.9, 1)
    assert adapter._generate_qq_proactive(material, datetime.datetime.now().astimezone()) == ""


def test_qq_proactive_keeps_explicit_topic_anchor(adapter):
    adapter.agent._short_task = lambda *args, **kwargs: "醒了。今天高数进度怎么样了？"
    material = QQMaterial("memory", "用户今天继续学高数", 0.9, 1, source_memory_id=1, source_message_id=9)
    assert adapter._generate_qq_proactive(material, datetime.datetime.now().astimezone()) == "醒了。今天高数进度怎么样了？"


def test_qq_proactive_requires_historical_source_anchor(adapter):
    material = QQMaterial("presence", "", 0.0, None)

    assert adapter._generate_qq_proactive(material, datetime.datetime.now().astimezone()) == ""


def test_qq_proactive_candidate_uses_historical_source_id(adapter):
    material = QQMaterial("memory", "用户有一项待跟进安排", 0.9, 31)

    candidate = adapter._qq_candidate(material)

    assert candidate.context["source_id"] == 31


def test_qq_proactive_feedback_id_is_stable_for_source_and_day(adapter):
    import datetime
    candidate = adapter._qq_candidate(QQMaterial("memory", "待跟进安排", 0.9, 31))
    now = datetime.datetime(2026, 8, 26, 10, tzinfo=datetime.timezone.utc)

    assert adapter._qq_feedback_id(candidate, now) == "qq:shared_episode:31:2026-08-26"
    assert adapter._qq_feedback_id(candidate, now) == adapter._qq_feedback_id(candidate, now)


def test_qq_meal_reminder_uses_gate_and_persists_after_send(adapter, monkeypatch):
    now = datetime.datetime(2026, 8, 24, 12, 0).astimezone()
    monkeypatch.setattr(adapter.meal_scheduler, "due", lambda **kwargs: (
        "lunch", "到饭点了，先去吃午饭。", "meal:2026-08-24:lunch",
    ))
    assert run(adapter._send_due_meal_reminder_async(now)) is True
    assert adapter.bot.sent[-1][1]["message"] == "到饭点了，先去吃午饭。"
    feedback = adapter.agent.memory.recent_proactive_feedback(source="meal", channel="qq", limit=1)
    assert feedback and feedback[0]["candidate_id"] == "meal:2026-08-24:lunch"


def test_whitelist_str_and_int(agent):
    """白名单支持字符串/数字混写。"""
    a = QQAdapter(agent, allowed_qq=[10001, "20002"])
    assert a.allowed == {"10001", "20002"}


def test_build_adapter_wires_trusted_image_proxy(agent):
    from veranima.qq import build_adapter

    built = build_adapter({"qq": {
        "enabled": True, "allowed_qq": [10001], "trusted_image_proxy": True,
        "image_proxy_hosts": ["multimedia.nt.qq.com"],
    }}, agent)
    assert built is not None and built.trusted_image_proxy
    assert built.image_proxy_hosts == ("multimedia.nt.qq.com",)


def test_build_adapter_consumes_sticker_lifecycle_config(agent, tmp_path):
    from veranima.qq import build_adapter

    built = build_adapter({"qq": {
        "enabled": True,
        "allowed_qq": [10001],
        "stickers": {
            "enabled": True,
            "dir": str(tmp_path / "stickers"),
            "learning_mode": "review",
            "send_rate": "frequent",
            "min_reply_gap": 5,
            "pending_ttl_days": 30,
            "max_items": 25,
        },
    }}, agent)

    assert built.sticker_learning_mode == "review"
    assert built.sticker_send_rate == "frequent"
    assert built.sticker_min_reply_gap == 5
    assert built.stickers.pending_ttl_days == 30
    assert built.stickers.max_items == 25


def test_sticker_legacy_global_entries_are_not_sent(adapter, agent, tmp_path):
    from veranima.core.stickers import StickerLibrary

    library = StickerLibrary(root=tmp_path / "stickers")
    library.add(_png_bytes(), meaning="开心", moods=["开心"])
    adapter.stickers = library
    adapter.sticker_send_rate = "always"
    agent.handle = lambda text, images=None, channel="im": TurnResult(
        reply="哈哈太好了", energy=80, mood="平静",
    )

    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "耶"}))

    assert [m for _, m in adapter.bot.sent if str(m).startswith("[CQ:image")] == []


# ---------- 文本提取 ----------

def test_plain_text_from_message_object():
    """CQ 码剥离：图片段被忽略，文本保留。"""
    msg = Message("[CQ:image,file=ab.png]你好呀[CQ:face,id=1]")
    assert QQAdapter._plain_text({"message": msg}) == "你好呀[QQ表情：id=1]"


def test_plain_text_preserves_native_qq_face_meaning():
    msg = Message([{"type": "face", "data": {"id": "66", "summary": "[比心]"}}])
    assert QQAdapter._plain_text({"message": msg}) == "[QQ表情：比心]"


def test_plain_text_accepts_napcat_face_placeholder():
    assert QQAdapter._plain_text({"message": "[表情 [比心] ]"}) == "[QQ表情：比心]"


def test_plain_text_preserves_text_around_napcat_face_placeholder():
    assert QQAdapter._plain_text({"message": "你好 [表情 [比心] ] 世界"}) == "你好 [QQ表情：比心] 世界"


def test_plain_text_preserves_text_around_face_placeholder_in_message_object():
    assert QQAdapter._plain_text({"message": Message("你好 [表情 [比心] ] 世界")}) == "你好 [QQ表情：比心] 世界"


def test_plain_text_accepts_structured_face_list():
    assert QQAdapter._plain_text({"message": [{"type": "face", "data": {"summary": "[比心]"}}]}) == "[QQ表情：比心]"


def test_native_qq_face_only_message_reaches_agent(adapter, agent):
    seen = []
    agent.handle = lambda text, images=None, channel="im": (
        seen.append((text, images, channel)) or TurnResult(reply="收到你的比心了", energy=80, mood="平静")
    )
    run(adapter._handle_private({
        "user_id": 10001,
        "message_type": "private",
        "message": Message([{"type": "face", "data": {"id": "66", "summary": "[比心]"}}]),
    }))
    assert seen == [("[QQ表情：比心]", [], "im")]
    assert adapter.bot.sent == [("send", "收到你的比心了")]


def test_napcat_face_placeholder_reaches_agent(adapter, agent):
    seen = []
    agent.handle = lambda text, images=None, channel="im": (
        seen.append((text, images, channel)) or TurnResult(reply="看到了", energy=80, mood="平静")
    )
    run(adapter._handle_private({
        "user_id": 10001,
        "message_type": "private",
        "message": "[表情 [比心] ]",
    }))
    assert seen == [("[QQ表情：比心]", [], "im")]
    assert adapter.bot.sent == [("send", "看到了")]


def test_plain_text_from_raw_string():
    assert QQAdapter._plain_text({"message": " 直接文本 "}) == "直接文本"


def test_build_adapter_quiet_hours_can_be_disabled(agent):
    from veranima.qq import build_adapter
    adapter = build_adapter({
        "qq": {"enabled": True, "allowed_qq": [10001], "quiet_hours": [23, 8]},
        "proactive": {"quiet_hours_enabled": False},
    }, agent)
    assert adapter.quiet_hours is None


def test_image_segments_accept_cq_string_and_file_or_image_refs():
    segments = QQAdapter._image_segments(
        "[CQ:image,file=sticker%2Fabc.png,url=https%3A%2F%2Fexample.com%2Fa.png]"
    )
    assert segments == [{"file": "sticker/abc.png", "url": "https://example.com/a.png"}]


def test_image_collection_falls_back_to_raw_message(adapter, monkeypatch):
    payload = make_image_payload(_png_bytes(), source="qq")

    async def resolved(self, data):
        return payload

    monkeypatch.setattr(QQAdapter, "_payload_from_segment", resolved)
    async def collect():
        return await adapter._collect_images({
            "message_id": 42,
            "message": "[图片]",
            "raw_message": "[CQ:image,file=sticker.png]",
        })

    images = run(collect())
    assert images[0][0:2] == (payload.data_url, payload.raw)
    assert images[0][2]["channel"] == "qq"
    assert images[0][2]["platform_message_id"] == "42"
    assert images[0][2]["received_at"]


def test_cq_image_only_message_reaches_agent_when_image_resolves(adapter, agent, monkeypatch):
    payload = make_image_payload(_png_bytes(), source="qq")
    async def resolved(self, data):
        return payload
    monkeypatch.setattr(QQAdapter, "_payload_from_segment", resolved)
    called = []
    agent.handle = lambda text, images=None, channel="im": (
        called.append((text, images, channel)) or TurnResult(reply="收到", energy=80, mood="平静")
    )
    run(adapter._handle_private({
        "user_id": 10001,
        "message": "[CQ:image,file=sticker.png]",
    }))
    assert called == [("", [payload.data_url], "im")]


def test_image_only_message_skipped(adapter, agent):
    """纯图片消息但无可下载 url（file 只是本地名）：跳过，不调用 handle。"""
    def fail(text, images=None):
        raise AssertionError("agent.handle must not be called")

    agent.handle = fail
    run(adapter._handle_private(
        {"user_id": 10001, "message_type": "private", "message": Message("[CQ:image,file=a.png]")}
    ))
    assert adapter.bot.sent == []


def test_image_message_with_url_passed_to_handle(adapter, agent, monkeypatch):
    """带 http url 的图片消息：下载为 data URL 传给 handle（8.6.2）。"""
    monkeypatch.setattr(
        QQAdapter, "_download_image",
        lambda self, url: _image_tuple(),
    )
    seen = {}

    def spy(text, images=None, channel="im"):
        seen["text"] = text
        seen["images"] = images
        return TurnResult(reply="看到了", energy=80, mood="平静")

    agent.handle = spy
    run(adapter._handle_private(
        {"user_id": 10001, "message_type": "private",
         "message": Message("[CQ:image,file=a.png,url=http://127.0.0.1:8099/img/1.png]看看这张")}
    ))
    assert seen["text"] == "看看这张"
    assert seen["images"][0].startswith("data:image/png;base64,")
    assert adapter.bot.sent == [("send", "看到了")]


def test_qq_image_message_is_capped_at_four(adapter, agent, monkeypatch):
    monkeypatch.setattr(QQAdapter, "_download_image", lambda self, url: _image_tuple())
    seen = {}

    def spy(text, images=None, channel="im"):
        seen["images"] = images
        return TurnResult(reply="收到", energy=80, mood="平静")

    agent.handle = spy
    cq = "".join(f"[CQ:image,file={i}.png,url=https://example.com/{i}.png]" for i in range(5))
    run(adapter._handle_private({
        "user_id": 10001, "message_type": "private", "message": Message(cq),
    }))
    assert len(seen["images"]) == 4


def test_image_download_failure_falls_back_to_text(adapter, agent, monkeypatch):
    """图片下载失败：降级为纯文本对话，不阻塞（8.6.4 边界）。"""
    monkeypatch.setattr(QQAdapter, "_download_image", lambda self, url: None)
    agent.handle = lambda text, images=None, channel="im": TurnResult(reply="文字也能聊", energy=80, mood="平静")
    run(adapter._handle_private(
        {"user_id": 10001, "message_type": "private",
         "message": Message("[CQ:image,file=a.png,url=http://127.0.0.1:8099/img/x.png]你好")}
    ))
    assert adapter.bot.sent == [("send", "文字也能聊")]


def test_local_image_must_stay_under_configured_roots(agent, tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    inside = allowed / "inside.png"
    outside = tmp_path / "outside.png"
    inside.write_bytes(_png_bytes())
    outside.write_bytes(_png_bytes())
    local_adapter = QQAdapter(agent, allowed_qq=[10001], image_roots=[allowed])

    assert local_adapter._resolve_local_image(str(inside)) == inside.resolve()
    assert local_adapter._resolve_local_image(str(outside)) is None
    assert local_adapter._resolve_local_image(str(allowed / ".." / "outside.png")) is None


def test_get_image_response_cannot_recurse_forever(adapter):
    calls = 0

    async def call_action(action, **kwargs):
        nonlocal calls
        calls += 1
        return {"data": {"file": kwargs["file"]}}

    adapter.bot.call_action = call_action
    assert run(adapter._payload_from_segment({"file": "loop.png"})) is None
    assert calls == 1


def test_private_http_image_is_rejected_before_network(adapter, monkeypatch):
    called = False

    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            nonlocal called
            called = True
            return self
        def __exit__(self, *args):
            return False
        def get(self, url):
            raise AssertionError("private URL must not reach httpx")

    monkeypatch.setattr("veranima.adapters.qq.httpx.Client", Client)
    assert adapter._download_image("http://127.0.0.1/private.png") is None
    assert not called


def test_fake_ip_requires_explicit_proxy_and_qq_cdn_allowlist(agent, adapter, monkeypatch):
    monkeypatch.setattr(
        "veranima.adapters.qq.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("198.18.2.135", 443))],
    )
    url = "https://multimedia.nt.qq.com/download/image.png"
    assert adapter._pinned_http_url(url) is None

    trusted = QQAdapter(
        agent, allowed_qq=[10001], trusted_image_proxy=True,
        image_proxy_hosts=["multimedia.nt.qq.com"],
    )
    pinned = trusted._pinned_http_url(url)
    assert pinned and pinned[0] == "https://multimedia.nt.qq.com:443/download/image.png"
    assert trusted._pinned_http_url("https://evil.example/image.png") is None


def test_http_image_uses_streaming_size_guard(adapter, monkeypatch):
    stream_called = False
    streamed = {}

    class Response:
        headers = {"content-type": "image/png", "content-length": str(10 * 1024 * 1024 + 1)}
        def raise_for_status(self):
            return None
        def iter_bytes(self):
            raise AssertionError("oversized content-length must reject before body read")

    class Stream:
        def __enter__(self):
            return Response()
        def __exit__(self, *args):
            return False

    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def stream(self, method, url, **kwargs):
            nonlocal stream_called
            stream_called = True
            streamed.update(method=method, url=url, **kwargs)
            return Stream()

    monkeypatch.setattr("veranima.adapters.qq.httpx.Client", Client)
    monkeypatch.setattr(
        "veranima.adapters.qq.socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    assert adapter._download_image("https://example.com/too-large.png") is None
    assert stream_called
    assert streamed["url"] == "https://93.184.216.34:443/too-large.png"
    assert streamed["headers"] == {"Host": "example.com"}
    assert streamed["extensions"] == {"sni_hostname": "example.com"}


# ---------- 8.6.3 表情包库 ----------

def _png_bytes() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _image_tuple() -> tuple[str, bytes]:
    payload = make_image_payload(_png_bytes())
    return payload.data_url, payload.raw


def test_sticker_ingest_new_image(adapter, agent, tmp_path, monkeypatch):
    """没见过的新图：LLM 标注 → 入库（adapter 层链路）。"""
    from veranima.core.stickers import StickerLibrary
    lib = StickerLibrary(root=tmp_path / "stickers")
    adapter.stickers = lib
    raw = _png_bytes()
    monkeypatch.setattr(QQAdapter, "_download_image",
                        lambda self, url: _image_tuple())
    monkeypatch.setattr(agent, "annotate_sticker",
                        lambda data_url: {"is_sticker": True, "meaning": "红色", "moods": ["开心"], "scenarios": ["测试"]})
    agent.handle = lambda text, images=None, channel="im": TurnResult(reply="看到图了", energy=80, mood="平静")
    run_and_drain(adapter, adapter._handle_private(
        {"user_id": 10001, "message_type": "private",
         "message": Message("[CQ:image,file=a.png,url=http://127.0.0.1:8099/img/1.png]")}
    ))
    assert len(lib) == 1
    assert lib._entries[0].meaning == "红色"


def test_sticker_review_ingest_is_pending_and_scoped_to_sender(adapter, agent, tmp_path):
    from veranima.core.stickers import StickerLibrary

    adapter.stickers = StickerLibrary(root=tmp_path / "stickers")
    adapter.sticker_learning_mode = "review"
    data_url, raw = _image_tuple()
    agent.annotate_sticker = lambda _data_url: {
        "is_sticker": True,
        "kind": "sticker",
        "confidence": 0.93,
        "meaning": "惊讶",
        "moods": ["惊讶"],
        "scenario_tags": ["surprise"],
        "scenarios": ["突然得知消息"],
    }

    adapter._ingest_stickers(
        [(data_url, raw, {"channel": "qq", "platform_message_id": "m1"})],
        owner_scope="qq:10001",
    )

    entries = adapter.stickers.list_entries()
    assert len(entries) == 1
    assert entries[0].status == "pending"
    assert entries[0].owner_scope == "qq:10001"
    assert entries[0].source["platform_message_id"] == "m1"


def test_sticker_ingest_duplicate_skipped(adapter, agent, tmp_path, monkeypatch):
    """见过的图：不重复标注入库。"""
    from veranima.core.stickers import StickerLibrary
    lib = StickerLibrary(root=tmp_path / "stickers")
    adapter.stickers = lib
    raw = _png_bytes()
    lib.add(raw, meaning="已有", moods=["开心"])
    monkeypatch.setattr(QQAdapter, "_download_image",
                        lambda self, url: _image_tuple())
    monkeypatch.setattr(agent, "annotate_sticker", lambda data_url: (_ for _ in ()).throw(AssertionError("不应标注已见过的图")))
    agent.handle = lambda text, images=None, channel="im": TurnResult(reply="嗯", energy=80, mood="平静")
    run_and_drain(adapter, adapter._handle_private(
        {"user_id": 10001, "message_type": "private",
         "message": Message("[CQ:image,file=a.png,url=http://127.0.0.1:8099/img/1.png]")}
    ))
    assert len(lib) == 1


def test_sticker_annotation_does_not_block_first_reply(adapter, agent, tmp_path, monkeypatch):
    import threading
    from veranima.core.stickers import StickerLibrary

    adapter.stickers = StickerLibrary(root=tmp_path / "stickers")
    monkeypatch.setattr(QQAdapter, "_download_image", lambda self, url: _image_tuple())
    started = threading.Event()
    release = threading.Event()

    def annotate(data_url):
        started.set()
        release.wait(timeout=2)
        return {"is_sticker": True, "meaning": "慢标注", "moods": ["开心"], "scenarios": ["测试"]}

    agent.annotate_sticker = annotate
    agent.handle = lambda text, images=None, channel="im": TurnResult(reply="先回复", energy=80, mood="平静")

    async def scenario():
        task = asyncio.create_task(adapter._handle_private({
            "user_id": 10001,
            "message_type": "private",
            "message": Message("[CQ:image,file=a.png,url=https://example.com/a.png]"),
        }))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            await asyncio.sleep(0.02)
            completed_before_release = task.done()
            sent_before_release = list(adapter.bot.sent)
        finally:
            release.set()
            await task
            pending = list(getattr(adapter, "_sticker_tasks", ()))
            if pending:
                await asyncio.gather(*pending)
        return completed_before_release, sent_before_release

    completed, sent = asyncio.run(scenario())
    assert completed
    assert sent == [("send", "先回复")]
    assert len(adapter.stickers) == 1


def test_run_task_waits_for_background_sticker_worker_on_exit(adapter):
    async def scenario():
        async def stopped_bot(**kwargs):
            return None

        async def worker():
            async with adapter._lock:
                await asyncio.to_thread(time.sleep, 0.03)
                return "finished"

        adapter.bot.run_task = stopped_bot
        sticker_task = asyncio.create_task(worker())
        adapter._sticker_tasks.add(sticker_task)
        await adapter.run_task()
        assert sticker_task.done() and not sticker_task.cancelled()
        assert sticker_task.result() == "finished"
        assert not adapter._lock.locked()

    asyncio.run(scenario())


def test_sticker_sent_on_happy_reply(adapter, agent, tmp_path):
    """回复带开心情绪：宽松匹配 → 发送表情包。"""
    from veranima.core.stickers import StickerLibrary
    lib = StickerLibrary(root=tmp_path / "stickers")
    adapter.stickers = lib
    adapter.sticker_send_rate = "always"
    entry = lib.add(
        _png_bytes(),
        meaning="开心",
        moods=["开心"],
        owner_scope="qq:10001",
    )
    agent.handle = lambda text, images=None, channel="im": TurnResult(reply="哈哈太好了！", energy=80, mood="平静")
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "耶"}))
    cq = [m for m in adapter.bot.sent if isinstance(m[1], str) and m[1].startswith("[CQ:image")]
    assert len(cq) == 1
    assert ".png" in cq[0][1]
    assert entry.uses == 1
    assert entry.last_used_at


def test_sticker_not_sent_without_mood(adapter, agent, tmp_path):
    """回复无情绪：不发表情包。"""
    from veranima.core.stickers import StickerLibrary
    lib = StickerLibrary(root=tmp_path / "stickers")
    adapter.stickers = lib
    lib.add(_png_bytes(), meaning="开心", moods=["开心"])
    agent.handle = lambda text, images=None, channel="im": TurnResult(reply="收到，好的", energy=80, mood="平静")
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "嗯"}))
    assert adapter.bot.sent == [("send", "收到，好的")]


def test_failed_sticker_send_keeps_usage_unchanged(adapter, agent, tmp_path):
    from veranima.core.stickers import StickerLibrary

    class FailingStickerBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.sticker_attempts = 0

        async def send(self, event, message, **kw):
            if str(message).startswith("[CQ:image"):
                self.sticker_attempts += 1
                raise RuntimeError("onebot image failed")
            await super().send(event, message, **kw)

    library = StickerLibrary(root=tmp_path / "stickers")
    entry = library.add(
        _png_bytes(),
        meaning="惊讶",
        moods=["惊讶"],
        owner_scope="qq:10001",
    )
    adapter.stickers = library
    adapter.sticker_send_rate = "always"
    adapter.sticker_min_reply_gap = 1
    adapter.bot = FailingStickerBot()
    agent.handle = lambda text, images=None, channel="im": TurnResult(
        reply="居然会这样？", energy=80, mood="平静",
    )

    run(adapter._handle_private({
        "user_id": 10001,
        "message_type": "private",
        "message": "我也没想到",
    }))

    assert adapter.bot.sent == [("send", "居然会这样？")]
    assert adapter.bot.sticker_attempts == 1
    assert entry.uses == 0
    assert entry.last_used_at is None


def test_low_sticker_rate_uses_fifteen_percent_boundary(adapter, agent, tmp_path):
    from veranima.core.stickers import StickerLibrary

    class FixedRandom:
        def __init__(self, value):
            self.value = value

        def random(self):
            return self.value

    library = StickerLibrary(root=tmp_path / "stickers")
    library.add(_png_bytes(), moods=["开心"], owner_scope="qq:10001")
    adapter.stickers = library
    adapter.sticker_send_rate = "low"
    adapter.sticker_min_reply_gap = 1
    agent.handle = lambda text, images=None, channel="im": TurnResult(
        reply="哈哈，太好了", energy=80, mood="平静",
    )

    adapter._sticker_rand = FixedRandom(0.149)
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "一"}))
    adapter._last_sticker_reply_count = None
    adapter._sticker_rand = FixedRandom(0.15)
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "二"}))

    stickers = [message for _, message in adapter.bot.sent if str(message).startswith("[CQ:image")]
    assert len(stickers) == 1


# ---------- 消息管线 ----------

def test_proactive_msg_deferred_not_sent_immediately(adapter, agent):
    """主动消息不立即发送：进 pending，等待对话静默（防双连发）。"""
    agent.handle = lambda text, images=None, channel="im": TurnResult(
        reply="普通回复", proactive_msg="（主动）对了，你上次说的事",
        energy=80, mood="平静",
    )
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "hi"}))
    assert adapter.bot.sent == [("send", "普通回复")]  # 只有回复，主动消息未发
    assert adapter._pending_proactive == "（主动）对了，你上次说的事"


def test_pending_proactive_flushed_after_silence(adapter, agent, monkeypatch):
    """对话静默满 proactive_delay_minutes 后，后台 tick 发送 pending 主动消息。"""
    adapter._pending_proactive = "（主动）对了，你上次说的事"
    adapter._last_user_activity = time.time() - adapter.proactive_delay_minutes * 60 - 10
    loop, t = _run_loop_thread()
    try:
        adapter._flush_pending_proactive(loop)
    finally:
        _stop_loop_thread(loop, t)
    assert adapter.bot.sent[0][0] == "private"
    assert "（主动）对了，你上次说的事" in adapter.bot.sent[0][1]["message"]
    assert adapter._pending_proactive is None  # 发送后清空


def test_pending_proactive_not_flushed_before_silence(adapter, agent):
    """对话还没冷下来：pending 不发送。"""
    adapter._pending_proactive = "（主动）等等再说"
    adapter._last_user_activity = time.time() - 10  # 10 秒前刚发过
    loop, t = _run_loop_thread()
    try:
        adapter._flush_pending_proactive(loop)
    finally:
        _stop_loop_thread(loop, t)
    assert adapter.bot.sent == []
    assert adapter._pending_proactive == "（主动）等等再说"  # 保留等待


def test_pending_proactive_discarded_on_new_user_message(adapter, agent):
    """用户又发消息：旧 pending 作废，不插话。"""
    adapter._pending_proactive = "（主动）旧话题"
    agent.handle = lambda text, images=None, channel="im": TurnResult(reply="新回复", energy=80, mood="平静")
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "hi"}))
    assert adapter.bot.sent == [("send", "新回复")]
    assert adapter._pending_proactive is None  # 作废


def test_empty_reply_not_sent(adapter, agent):
    """回复为空（空白输入）不发送。"""
    agent.handle = lambda text, images=None, channel="im": TurnResult(reply="", energy=80, mood="平静")
    run(adapter._handle_private({"user_id": 10001, "message_type": "private", "message": "hi"}))
    assert adapter.bot.sent == []


def test_messages_serialized(adapter, agent):
    """并发消息串行处理（asyncio.Lock），handle 无并发调用。"""
    import threading
    active = []
    seen = []

    def slow_handle(text, images=None, channel="im"):
        active.append(1)
        assert len(active) == 1, "handle 并发调用！"
        import time
        time.sleep(0.05)
        active.pop()
        seen.append(text)
        return TurnResult(reply=f"reply:{text}", energy=80, mood="平静")

    agent.handle = slow_handle

    async def concurrent():
        ev = {"user_id": 10001, "message_type": "private", "message": "m1"}
        await asyncio.gather(
            adapter._handle_private(dict(ev, message="m1")),
            adapter._handle_private(dict(ev, message="m2")),
            adapter._handle_private(dict(ev, message="m3")),
        )

    run(concurrent())
    assert seen == ["m1", "m2", "m3"]  # 顺序保持


# ---------- 8.7.4 离线思考 ----------

def test_offline_not_due_before_silence():
    t = OfflineThinkTimer(silence_minutes=30, probability=1.0, rand=random.Random(0))
    assert not t.due(now=1000.0, last_activity=None)          # 从未对话
    assert not t.due(now=1000.0, last_activity=1000.0 - 29 * 60)  # 未满静默窗口


def test_offline_due_after_silence():
    t = OfflineThinkTimer(silence_minutes=30, probability=1.0, rand=random.Random(0))
    assert t.due(now=1000.0, last_activity=1000.0 - 31 * 60)


def test_offline_probability_gate():
    t = OfflineThinkTimer(silence_minutes=30, probability=0.0, rand=random.Random(0))
    assert not t.due(now=1000.0, last_activity=1000.0 - 31 * 60)


def test_offline_window_dedup():
    """触发后需再静默满一个窗口才可能再触发（防刷屏）。"""
    t = OfflineThinkTimer(silence_minutes=30, probability=1.0, rand=random.Random(0))
    assert t.due(now=1000.0, last_activity=1000.0 - 60 * 60)       # 触发
    assert not t.due(now=1010.0, last_activity=1000.0 - 60 * 60)   # 窗口内不重复
    assert t.due(now=1000.0 + 31 * 60, last_activity=1000.0 - 60 * 60)  # 再满窗口可再触发


def test_offline_max_per_day_limit():
    """每日上限：当天触发满 max_per_day 后不再触发（防整夜轰炸）。"""
    t = OfflineThinkTimer(
        silence_minutes=30, probability=1.0, max_per_day=2, rand=random.Random(0)
    )
    # 基准 now 需距 last_activity 超过静默窗口（30 分钟 = 1800s）
    base = 2000.0
    assert t.due(now=base, last_activity=0.0)                       # 第 1 次
    assert t.due(now=base + 31 * 60, last_activity=0.0)             # 第 2 次
    assert not t.due(now=base + 62 * 60, last_activity=0.0)         # 当日额度用尽


def test_offline_max_per_day_resets_next_day():
    """跨天重置每日计数。"""
    t = OfflineThinkTimer(
        silence_minutes=30, probability=1.0, max_per_day=1, rand=random.Random(0)
    )
    base = 2000.0
    assert t.due(now=base, last_activity=0.0)
    assert not t.due(now=base + 31 * 60, last_activity=0.0)         # 同日已满
    assert t.due(now=base + 24 * 3600, last_activity=0.0)           # 次日重置


def test_offline_probability_grows_on_miss():
    """渴望度积累：未命中时概率增长，命中后重置回基础值。"""
    class SeqRand:
        def __init__(self, vals):
            self._vals = list(vals)
        def random(self):
            return self._vals.pop(0)

    t = OfflineThinkTimer(
        silence_minutes=30, probability=0.3, growth_factor=0.1, max_probability=0.9,
        rand=SeqRand([0.8, 0.5, 0.9, 0.2]),
    )
    # 窗口1：0.8 >= 0.3 → miss，概率 0.3 → 0.4
    assert not t.due(now=2000.0, last_activity=0.0)
    assert abs(t.probability - 0.4) < 1e-9
    # 窗口2：0.5 >= 0.4 → miss，概率 0.4 → 0.5
    assert not t.due(now=2000.0 + 31 * 60, last_activity=0.0)
    assert abs(t.probability - 0.5) < 1e-9
    # 窗口3：0.9 >= 0.5 → miss，概率 0.5 → 0.6
    assert not t.due(now=2000.0 + 62 * 60, last_activity=0.0)
    assert abs(t.probability - 0.6) < 1e-9
    # 窗口4：0.2 < 0.6 → hit，概率重置回 0.3
    assert t.due(now=2000.0 + 93 * 60, last_activity=0.0)
    assert abs(t.probability - 0.3) < 1e-9


def test_offline_probability_capped_at_max():
    """渴望度积累封顶：不超过 max_probability。"""
    class SeqRand:
        def __init__(self, vals):
            self._vals = list(vals)
        def random(self):
            return self._vals.pop(0)

    t = OfflineThinkTimer(
        silence_minutes=30, probability=0.85, growth_factor=0.1, max_probability=0.9,
        rand=SeqRand([0.99, 0.99, 0.99]),
    )
    for i in range(3):
        assert not t.due(now=2000.0 + i * 31 * 60, last_activity=0.0)
    assert abs(t.probability - 0.9) < 1e-9  # 0.85+0.1 → 封顶 0.9


def test_offline_probability_zero_never_grows():
    """概率 0 = 关闭：永不触发，也不增长。"""
    t = OfflineThinkTimer(silence_minutes=30, probability=0.0, rand=random.Random(0))
    for i in range(5):
        assert not t.due(now=2000.0 + i * 31 * 60, last_activity=0.0)
    assert t.probability == 0.0


def test_quiet_hours_judgment(adapter):
    """静默时段判定：跨午夜区间（23:00-08:00）。"""
    adapter.quiet_hours = (23, 8)
    assert adapter._in_quiet_hours(datetime.datetime(2026, 8, 3, 23, 30))
    assert adapter._in_quiet_hours(datetime.datetime(2026, 8, 4, 3, 0))
    assert adapter._in_quiet_hours(datetime.datetime(2026, 8, 4, 7, 59))
    assert not adapter._in_quiet_hours(datetime.datetime(2026, 8, 3, 22, 59))
    assert not adapter._in_quiet_hours(datetime.datetime(2026, 8, 4, 8, 0))
    adapter.quiet_hours = None
    assert not adapter._in_quiet_hours(datetime.datetime(2026, 8, 4, 3, 0))


def test_offline_think_skipped_in_quiet_hours(adapter, agent, monkeypatch):
    """静默时段内 _tick_offline_think 直接短路，不发消息。"""
    agent.memory.store_message("user", "下周要去面试了", 80, "平静")
    adapter._last_user_activity = 0.0
    adapter.offline = OfflineThinkTimer(
        silence_minutes=1, probability=1.0, rand=random.Random(0)
    )
    monkeypatch.setattr(adapter, "_in_quiet_hours", lambda now=None: True)
    loop, t = _run_loop_thread()
    try:
        adapter._tick_offline_think(loop)
    finally:
        _stop_loop_thread(loop, t)
    assert adapter.bot.sent == []


def _run_loop_thread():
    """启动一个真实运行的事件循环线程（run_coroutine_threadsafe 需要）。"""
    import threading

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    return loop, t


def _stop_loop_thread(loop, t):
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)


def test_offline_think_generates_late_reply(adapter, agent):
    """离线思考 tick：静默命中 → late_reply → 发送给白名单 QQ。"""
    agent.memory.store_message("user", "下周要去面试了", 80, "平静")
    adapter._last_user_activity = 0.0  # 很久没活动
    adapter.offline = OfflineThinkTimer(
        silence_minutes=1, probability=1.0, rand=random.Random(0)
    )
    loop, t = _run_loop_thread()
    try:
        adapter._tick_offline_think(loop)
    finally:
        _stop_loop_thread(loop, t)
    assert adapter.bot.sent, "应发送离线思考消息"
    assert adapter.bot.sent[0][0] == "private"
    assert adapter.bot.sent[0][1]["user_id"] == 10001
    assert adapter.bot.sent[0][1]["message"]


def test_offline_think_skips_when_model_unloaded(adapter, agent):
    """模型不可用：离线思考不发送（不打扰）。"""
    agent.memory.store_message("user", "下周要去面试了", 80, "平静")
    agent.llm = FakeLLM()
    agent.llm.is_model_loaded = lambda: False
    adapter._last_user_activity = 0.0
    adapter.offline = OfflineThinkTimer(
        silence_minutes=1, probability=1.0, rand=random.Random(0)
    )
    loop, t = _run_loop_thread()
    try:
        adapter._tick_offline_think(loop)
    finally:
        _stop_loop_thread(loop, t)
    assert adapter.bot.sent == []
