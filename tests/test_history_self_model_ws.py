"""WS 历史搜索与 SelfModel 查询连通测试。"""
from __future__ import annotations

import asyncio
import json

from veranima.pet_server import PetServer


def test_ws_history_and_self_model_requests(tmp_path):
    async def run():
        srv = PetServer()
        from veranima.memory.store import MemoryStore
        class Agent: pass
        agent = Agent()
        agent.memory = MemoryStore(db_path=str(tmp_path / "m.db"), config={"embedding_model": "none"})
        agent.memory.store_message("user", "我们讨论过屋顶", 80, "平静")
        srv.connect_agent(agent)
        sent = []

        class Client:
            async def send(self, data): sent.append(json.loads(data))
            def __aiter__(self): return self
            async def __anext__(self):
                if self.messages: return self.messages.pop(0)
                raise StopAsyncIteration

        client = Client()
        client.messages = [
            json.dumps({"type": "search_history", "id": 1, "data": {"query": "屋顶"}}),
            json.dumps({"type": "get_self_model", "id": 2}),
        ]
        await srv._handle(client)
        assert sent[0]["type"] == "history_search_results"
        assert sent[0]["data"][0]["content"] == "我们讨论过屋顶"
        assert sent[1]["type"] == "self_model"

    asyncio.run(run())
