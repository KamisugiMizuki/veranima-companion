"""Phone evidence regression: context probes must use exact time facts."""
from __future__ import annotations

import datetime as dt

from veranima.core.agent import Agent


def test_time_instruction_rejects_guessed_daylight():
    text = Agent._time_context_instruction()
    assert "完整日期和时分" in text
    assert "没有日落、天气或环境传感器数据时" in text


def test_precise_local_clock_preserves_date_weekday_and_minute():
    assert Agent._precise_local_clock(dt.datetime(2026, 9, 13, 15, 0)) == "2026-09-13 15:00 周日"


