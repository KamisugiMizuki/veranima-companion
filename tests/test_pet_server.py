"""PetServer 协议测试（R3_SPEC 3 通信协议）：连接 / speak / bubble / poke / ping。"""
import asyncio
import json

import pytest
import websockets

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
