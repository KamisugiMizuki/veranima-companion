from __future__ import annotations

import asyncio
import datetime as dt

from veranima.adapters.qq import QQAdapter


def test_qq_background_tick_advances_schedule_runtime(monkeypatch):
    class Runtime:
        sleeping = False
        def __init__(self): self.calls = []
        def advance(self, when): self.calls.append(when)

    class Agent:
        schedule_runtime = Runtime()
        config = {"proactive": {"channels": {"qq": {}}}}
        memory = object()
        tension = type("T", (), {"proactive_allowed": lambda self: False})()

    adapter = object.__new__(QQAdapter)
    adapter.agent = Agent()
    adapter.proactive = False
    adapter._tick_interval = 0.01
    adapter._in_quiet_hours = lambda: True

    async def run_once():
        stop = asyncio.Event()
        task = asyncio.create_task(adapter._bg_loop_async(stop))
        await asyncio.sleep(0.03)
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_once())
    assert Agent.schedule_runtime.calls
