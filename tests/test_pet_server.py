"""PetServer 协议测试（M3_SPEC 3 通信协议）：连接 / speak / bubble / poke / ping。"""
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
    """起真实 WS 服务：壳客户端连接 → 收 speak/bubble → 发 poke → 收回复。"""
    port = _free_port()
    results = {}

    async def scenario():
        srv = PetServer(host="127.0.0.1", port=port)
        task = asyncio.create_task(srv.run())
        await asyncio.sleep(0.3)  # 等服务就绪
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            # 核心 → 壳：speak
            assert await srv.speak("你好呀", tags=["<低声>"])
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            results["speak"] = msg
            # 核心 → 壳：bubble
            assert await srv.bubble("在想你")
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            results["bubble"] = msg
            # 壳 → 核心：poke → 核心回 speak
            await ws.send(json.dumps({"type": "poke"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            results["poke_reply"] = msg
            # 壳 → 核心：ping → pong
            await ws.send(json.dumps({"type": "ping"}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            results["pong"] = msg
        task.cancel()

    asyncio.run(scenario())
    assert results["speak"]["type"] == "speak"
    assert results["speak"]["text"] == "你好呀"
    assert results["bubble"]["type"] == "bubble"
    assert results["poke_reply"]["type"] == "speak"
    assert "叫我干嘛" in results["poke_reply"]["text"]
    assert results["pong"]["type"] == "pong"


def test_send_without_client_returns_false():
    """无客户端连接时 send 返回 False（不抛异常）。"""
    srv = PetServer()
    assert asyncio.run(srv.speak("hi")) is False
