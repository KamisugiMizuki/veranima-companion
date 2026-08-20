"""PetServer 单客户端替换与迟到回复隔离。"""
from __future__ import annotations

import asyncio
import json

import websockets

from veranima.pet_server import PetServer


def test_replaced_ws_cannot_clear_or_contaminate_new_client(free_tcp_port):
    async def scenario():
        server = PetServer(port=free_tcp_port)
        release = asyncio.Event()

        class Result:
            reply = "旧连接的迟到回复"
            portrait = ""
            ja_text = ""
            reply_obj = None

        class Agent:
            memory = type("Memory", (), {
                "recent_proactive_feedback": lambda self, limit=3: [],
            })()
            gate = type("Gate", (), {})()

            async def delayed(self):
                await release.wait()
                return Result()

        agent = Agent()
        server._agent = agent

        async def fake_call_agent(text):
            return await agent.delayed()

        server._call_agent = fake_call_agent
        ws_server = await websockets.serve(server._handle, "127.0.0.1", free_tcp_port)
        try:
            first = await websockets.connect(f"ws://127.0.0.1:{free_tcp_port}")
            await first.send(json.dumps({"type": "stream_talk", "text": "first", "request_id": "old"}))
            await asyncio.sleep(0.05)
            second = await websockets.connect(f"ws://127.0.0.1:{free_tcp_port}")
            await asyncio.sleep(0.05)
            assert server._client is not None
            release.set()
            await asyncio.sleep(0.1)
            assert server._client is not None
            try:
                data = await asyncio.wait_for(second.recv(), timeout=0.1)
            except asyncio.TimeoutError:
                data = ""
            assert "旧连接的迟到回复" not in data
            await second.close()
            await first.close()
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(scenario())
