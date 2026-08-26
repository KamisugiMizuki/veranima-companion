from __future__ import annotations

import asyncio

from veranima.pet_server import PetServer


def test_pet_background_presence_loop_advances_schedule_runtime(monkeypatch):
    class Runtime:
        def __init__(self): self.calls = []
        def advance(self, when): self.calls.append(when)

    class Agent:
        schedule_runtime = Runtime()

    server = PetServer(port=0)
    server._agent = Agent()

    async def fake_presence():
        return False

    monkeypatch.setattr("veranima.pet_server.presence", fake_presence, raising=False)

    async def run_once():
        task = asyncio.create_task(server._schedule_tick_once())
        await task

    asyncio.run(run_once())
    assert Agent.schedule_runtime.calls


def test_pet_background_never_consumes_or_sends_schedule_notice():
    source = __import__("pathlib").Path("src/veranima/pet_server.py").read_text(encoding="utf-8")
    block = source[source.index("async def _schedule_tick_once"):source.index("async def tick_presence")]
    assert "pop_notice" not in block
    assert "schedule_notice_text" not in block
