"""PetServer 协议测试（R3_SPEC 3 通信协议）：连接 / speak / bubble / poke / ping。"""
import asyncio
import io
import json

import pytest
import websockets
from PIL import Image

from veranima.pet_server import PetServer


@pytest.fixture
def server():
    return PetServer(host="127.0.0.1", port=0)


async def _client_connect(port):
    # 用实际端口连（port=0 时从 serve 拿真实端口——测试中固定用高位端口）
    return await websockets.connect(f"ws://127.0.0.1:{port}")


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sticker_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def test_sticker_review_ws_lists_approves_and_deletes_live_qq_library(tmp_path):
    from veranima.core.image_payload import make_image_payload
    from veranima.core.stickers import StickerLibrary

    port = _free_port()
    library = StickerLibrary(tmp_path / "stickers")
    pending = library.add_candidate(
        make_image_payload(_sticker_png()),
        owner_scope="qq:10001",
        meaning="待审核",
        moods=["开心"],
    )

    class QQStub:
        allowed = {"10001"}
        stickers = library

        async def run_task(self):
            await asyncio.Future()

    async def scenario():
        server = PetServer(host="127.0.0.1", port=port)
        server.connect_qq(QQStub())
        task = asyncio.create_task(server.run())
        await asyncio.sleep(0.2)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "sticker_list", "id": 1}))
                listed = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                await ws.send(json.dumps({
                    "type": "sticker_action",
                    "id": 2,
                    "data": {"action": "approve", "entry_id": pending.id},
                }))
                approved = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                await ws.send(json.dumps({
                    "type": "sticker_action",
                    "id": 3,
                    "data": {"action": "delete", "entry_id": pending.id},
                }))
                deleted = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                return listed, approved, deleted
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    listed, approved, deleted = asyncio.run(scenario())
    assert listed["type"] == "sticker_list"
    assert listed["data"]["entries"][0]["status"] == "pending"
    assert approved["type"] == "sticker_action_result"
    assert approved["id"] == 2 and approved["ok"] is True
    assert deleted["type"] == "sticker_action_result"
    assert deleted["id"] == 3 and deleted["ok"] is True
    assert library.list_entries() == []


def test_sticker_action_rejects_other_owner(tmp_path):
    from veranima.core.image_payload import make_image_payload
    from veranima.core.stickers import StickerLibrary

    port = _free_port()
    library = StickerLibrary(tmp_path / "stickers")
    entry = library.add_candidate(
        make_image_payload(_sticker_png()),
        owner_scope="qq:10001",
        meaning="私有",
    )

    class QQStub:
        allowed = {"10001", "20002"}
        stickers = library

        async def run_task(self):
            await asyncio.Future()

    async def scenario():
        server = PetServer(host="127.0.0.1", port=port)
        server.connect_qq(QQStub())
        task = asyncio.create_task(server.run())
        await asyncio.sleep(0.2)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({
                    "type": "sticker_action", "id": 4,
                    "data": {"action": "delete", "entry_id": entry.id, "owner_scope": "qq:20002"},
                }))
                return json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    result = asyncio.run(scenario())
    assert result["ok"] is False
    assert library.list_entries()[0].id == entry.id


def test_sticker_action_rejects_legacy_global_entry(tmp_path):
    from veranima.core.image_payload import make_image_payload
    from veranima.core.stickers import StickerLibrary

    port = _free_port()
    library = StickerLibrary(tmp_path / "stickers")
    entry = library.add_candidate(make_image_payload(_sticker_png()), owner_scope="legacy_global")

    class QQStub:
        allowed = {"10001"}
        stickers = library

        async def run_task(self):
            await asyncio.Future()

    async def scenario():
        server = PetServer(host="127.0.0.1", port=port)
        server.connect_qq(QQStub())
        task = asyncio.create_task(server.run())
        await asyncio.sleep(0.2)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "sticker_action", "id": 5, "data": {
                    "action": "approve", "entry_id": entry.id,
                }}))
                return json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    result = asyncio.run(scenario())
    assert result["ok"] is False
    assert library.list_entries()[0].status == "pending"


def test_sticker_settings_roundtrip_and_runtime_payload(tmp_path):
    from veranima.config import save_config

    port = _free_port()
    cfg_path = tmp_path / "config" / "config.yaml"
    sticker_dir = tmp_path / "stickers"
    image_root = tmp_path / "napcat"
    sticker_dir.mkdir()
    image_root.mkdir()
    save_config({"qq": {"enabled": True, "allowed_qq": [10001], "stickers": {
        "enabled": True, "dir": str(sticker_dir), "learning_mode": "review",
        "send_rate": "normal", "min_reply_gap": 3, "pending_ttl_days": 7,
        "max_items": 100,
    }}}, cfg_path)

    async def scenario():
        server = PetServer(host="127.0.0.1", port=port)
        task = asyncio.create_task(server.run())
        await asyncio.sleep(0.2)
        import veranima.config as cfgmod
        cfgmod.ROOT = tmp_path
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "save_config", "id": 10, "data": {
                    "qq": {
                        "allowed": [10001],
                        "stickers": {
                            "enabled": True, "dir": str(sticker_dir),
                            "learning_mode": "auto", "send_rate": "frequent",
                            "min_reply_gap": 5, "pending_ttl_days": 30, "max_items": 300,
                        },
                        "image_roots": [str(image_root)],
                        "trusted_image_proxy": True,
                    },
                }}))
                saved = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                await ws.send(json.dumps({"type": "get_config", "id": 11}))
                loaded = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                return saved, loaded
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    saved, loaded = asyncio.run(scenario())
    assert saved["ok"] is True
    stickers = loaded["data"]["qq"]["stickers"]
    assert stickers["learning_mode"] == "auto"
    assert stickers["send_rate"] == "frequent"
    assert stickers["min_reply_gap"] == 5
    assert stickers["pending_ttl_days"] == 30
    assert stickers["max_items"] == 300
    assert loaded["data"]["qq"]["image_roots"] == [str(image_root)]
    assert loaded["data"]["qq"]["trusted_image_proxy"] is True


def test_sticker_settings_reject_unknown_enum(tmp_path):
    from veranima.config import save_config

    port = _free_port()
    cfg_path = tmp_path / "config" / "config.yaml"
    cfg_path.parent.mkdir()
    save_config({"qq": {"allowed_qq": [10001]}}, cfg_path)

    async def scenario():
        server = PetServer(host="127.0.0.1", port=port)
        task = asyncio.create_task(server.run())
        await asyncio.sleep(0.2)
        import veranima.config as cfgmod
        cfgmod.ROOT = tmp_path
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "save_config", "id": 12, "data": {
                    "qq": {"stickers": {"learning_mode": "invented"}},
                }}))
                return json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    result = asyncio.run(scenario())
    assert result["ok"] is False
    assert "learning_mode" in result["error"]


def test_sticker_settings_reject_invalid_directories(tmp_path):
    from veranima.config import save_config

    port = _free_port()
    cfg_path = tmp_path / "config" / "config.yaml"
    cfg_path.parent.mkdir()
    save_config({"qq": {"allowed_qq": [10001]}}, cfg_path)
    file_path = tmp_path / "file"
    file_path.write_text("not a directory", encoding="utf-8")

    async def scenario():
        server = PetServer(host="127.0.0.1", port=port)
        task = asyncio.create_task(server.run())
        await asyncio.sleep(0.2)
        import veranima.config as cfgmod
        cfgmod.ROOT = tmp_path
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "save_config", "id": 13, "data": {
                    "qq": {"stickers": {"learning_mode": "review", "send_rate": "normal", "dir": str(file_path)},
                           "image_roots": [str(file_path)]},
                }}))
                return json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    result = asyncio.run(scenario())
    assert result["ok"] is False
    assert "目录" in result["error"]


def test_sticker_list_requires_one_valid_scope_for_multi_user(tmp_path):
    from veranima.core.stickers import StickerLibrary

    port = _free_port()
    library = StickerLibrary(tmp_path / "stickers")

    class QQStub:
        allowed = {"10001", "20002"}
        stickers = library

        async def run_task(self):
            await asyncio.Future()

    async def scenario():
        server = PetServer(host="127.0.0.1", port=port)
        server.connect_qq(QQStub())
        task = asyncio.create_task(server.run())
        await asyncio.sleep(0.2)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                await ws.send(json.dumps({"type": "sticker_list", "id": 14}))
                return json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    result = asyncio.run(scenario())
    assert result["ok"] is False
    assert "作用域" in result["error"]


def test_protocol_roundtrip():
    """起真实 WS 服务：壳客户端连接 → 收 reply_*/bubble → 发 poke → 收回复（R3 协议）。"""
    port = _free_port()
    results = {}

    async def scenario():
        srv = PetServer(host="127.0.0.1", port=port)
        task = asyncio.create_task(srv.run())
        await asyncio.sleep(0.3)  # 等服务就绪
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            # 核心 → 壳：speak（R3 协议：reply_start → reply_segment → reply_end）
            assert await srv.speak("你好呀", tags=["<低声>"])
            msgs = [json.loads(await asyncio.wait_for(ws.recv(), timeout=3)) for _ in range(3)]
            results["reply_types"] = [m["type"] for m in msgs]
            results["segment"] = next(m for m in msgs if m["type"] == "reply_segment")
            # 核心 → 壳：bubble
            assert await srv.bubble("在想你")
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            results["bubble"] = msg
            # 壳 → 核心：poke → 先 reply_cancelled（TTS 打断）再回 reply_*
            await ws.send(json.dumps({"type": "poke"}))
            poke_seg = None
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if msg["type"] == "reply_segment":
                    poke_seg = msg
                if msg["type"] == "reply_end":
                    break  # 消费完整回复序列
            results["poke_reply"] = poke_seg
            # 壳 → 核心：ping → pong
            await ws.send(json.dumps({"type": "ping"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            results["pong"] = msg
        task.cancel()

    asyncio.run(scenario())
    assert results["reply_types"] == ["reply_start", "reply_segment", "reply_end"]
    assert results["segment"]["payload"]["text"] == "你好呀"
    assert results["bubble"]["type"] == "bubble"
    assert results["poke_reply"]["type"] == "reply_segment"
    assert "叫我干嘛" in results["poke_reply"]["payload"]["text"]
    assert results["pong"]["type"] == "pong"


def test_send_without_client_returns_false():
    """无客户端连接时 send 返回 False（不抛异常）。"""
    srv = PetServer()
    assert asyncio.run(srv.speak("hi")) is False


def test_poke_with_agent_uses_agent_reply():
    """poke 接入 agent 后：回复来自 agent 生成（channel=tts）。"""
    port = _free_port()
    results = {}

    class FakeAgent:
        def handle(self, text, channel="im"):
            assert channel == "tts"
            class R:
                reply = "（抬起头）怎么了？"
                portrait = "开心脸红"
                tone = "温柔"
                ja_text = ""
            return R()

    async def scenario():
        srv = PetServer(host="127.0.0.1", port=port)
        srv.connect_agent(FakeAgent())
        task = asyncio.create_task(srv.run())
        await asyncio.sleep(0.3)
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"type": "poke"}))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if msg["type"] == "reply_segment":
                    break
            results["reply"] = msg
        task.cancel()

    asyncio.run(scenario())
    assert results["reply"]["type"] == "reply_segment"
    assert results["reply"]["payload"]["text"] == "（抬起头）怎么了？"


def test_config_roundtrip(tmp_path):
    """get_config 返回打码 key；save_config 写回 yaml 并可再读。"""
    from veranima.config import save_config
    port = _free_port()
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    cfg_path = cfg_dir / "config.yaml"
    save_config({"llm": {"base_url": "https://a.example/v1", "model": "m1", "api_key": "test-key-value"},
                 "qq": {"allowed_qq": [10001]}}, cfg_path)
    results = {}

    async def scenario():
        srv = PetServer(host="127.0.0.1", port=port)
        task = asyncio.create_task(srv.run())
        await asyncio.sleep(0.3)
        import veranima.config as cfgmod
        cfgmod.ROOT = tmp_path  # 让 load_config 找到 tmp 下的 config.yaml
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            # get_config
            await ws.send(json.dumps({"type": "get_config", "id": 1}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            results["get"] = msg
            # save_config（改 model）
            await ws.send(json.dumps({"type": "save_config", "id": 2,
                                      "data": {"llm": {"model": "m2"}, "qq": {"allowed": [10002]}}}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            results["save"] = msg
        task.cancel()

    asyncio.run(scenario())
    assert results["get"]["type"] == "config"
    assert results["get"]["id"] == 1
    assert results["get"]["data"]["llm"]["api_key"] == "test****alue"  # 打码
    assert results["save"]["type"] == "config_saved"
    assert results["save"]["id"] == 2
    # 写回验证
    import yaml
    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert saved["llm"]["model"] == "m2"
    assert saved["qq"]["allowed_qq"] == [10002]


def test_llm_profile_actions_over_ws(tmp_path):
    from veranima.config import save_config
    port = _free_port()
    cfg_path = tmp_path / "config" / "config.yaml"
    cfg_path.parent.mkdir()
    save_config({"llm": {"base_url": "https://a.example/v1", "model": "m1", "api_key": "test-key-value"}}, cfg_path)

    async def scenario():
        srv = PetServer(host="127.0.0.1", port=port)
        task = asyncio.create_task(srv.run())
        await asyncio.sleep(0.2)
        import veranima.config as cfgmod
        cfgmod.ROOT = tmp_path
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"type": "save_config", "id": 10, "data": {
                "llm_profile_action": "add", "llm_profile": {
                    "name": "LM Studio", "base_url": "http://localhost:12345/v1",
                    "model": "local-model", "temperature": 0.8, "max_tokens": 4096,
                    "timeout": 180, "api_key": "",
                },
            }}))
            added = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            pid = added["llm"]["profile_id"]
            await ws.send(json.dumps({"type": "save_config", "id": 11, "data": {
                "llm_profile_action": "switch", "llm_profile": {"profile_id": pid},
            }}))
            switched = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        task.cancel()
        return pid, added, switched

    pid, added, switched = asyncio.run(scenario())
    assert pid == "lm-studio"
    assert added["ok"] is True
    assert switched["llm"]["active_profile"] == "lm-studio"
    import yaml
    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert saved["llm"]["model"] == "local-model"
