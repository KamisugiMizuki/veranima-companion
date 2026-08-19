"""测试全局夹具：冻结 ambient 时钟到固定中午（避开 quiet hours 23:00-08:00）。

背景：ProactiveGate/SceneLock/Arbitrator 用 time.time() 判断静默时段；深夜跑测试时
主动类用例全部被 quiet hours 拦截（与代码回归无关）。冻结为固定中午后相对时间差
（如 _last_touch = now - 3h）不受影响。

注意：必须替换 ambient 的 time 模块引用为独立 fake 模块，不能 monkeypatch
"time.time"——time 是全局共享模块，改它会污染 store 的真实时钟（decay 等）。
"""
from __future__ import annotations

import datetime
import types

import pytest


@pytest.fixture(autouse=True)
def _freeze_ambient_clock(monkeypatch):
    """把 veranima.core.ambient 的 time.time 固定为 2026-08-03 12:00（白天）。"""
    fixed = datetime.datetime(2026, 8, 3, 12, 0).timestamp()
    fake_time = types.ModuleType("fake_ambient_time")
    fake_time.time = lambda: fixed
    monkeypatch.setattr("veranima.core.ambient.time", fake_time)
