from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from veranima.app import create_agent
from veranima.config import load_config
from veranima.core import agent as agent_module
from veranima.core.reply import is_failure_fallback_reply, is_internal_reply


_REAL_DATETIME = dt.datetime


class _FrozenDateTime(_REAL_DATETIME):
    current = _REAL_DATETIME.now(dt.timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class _FrozenDatetimeModule:
    datetime = _FrozenDateTime

    def __getattr__(self, name):
        return getattr(dt, name)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="veranima-space-api-") as temp:
        root = Path(temp)
        project = Path.cwd()
        cfg = copy.deepcopy(load_config())
        cfg["root"] = str(project)
        cfg["character_card"] = str(project / "characters" / "zima" / "character.json")
        cfg.setdefault("memory", {})["db_path"] = str(root / "space.db")
        cfg.setdefault("search", {})["enabled"] = False
        cfg.setdefault("tasks", {})["enabled"] = False
        cfg["virtual_schedule"] = {"enabled": True, "space_enabled": True}
        agent = create_agent(cfg)
        try:
            runtime = agent.schedule_runtime
            assert runtime and runtime.outline.space
            times = [
                dt.datetime(2026, 8, 28, 5, 0, tzinfo=dt.timezone.utc),
                dt.datetime(2026, 8, 28, 7, 0, tzinfo=dt.timezone.utc),
                dt.datetime(2026, 8, 28, 7, 10, tzinfo=dt.timezone.utc),
                dt.datetime(2026, 8, 28, 7, 21, tzinfo=dt.timezone.utc),
            ]
            observations = []
            llm_calls = 0
            chat_name = "chat_structured" if callable(getattr(agent.llm, "chat_structured", None)) else "chat"
            real_chat = getattr(agent.llm, chat_name)

            def counted_chat(*args, **kwargs):
                nonlocal llm_calls
                llm_calls += 1
                return real_chat(*args, **kwargs)

            setattr(agent.llm, chat_name, counted_chat)
            for when in times:
                runtime.advance(when)
                ctx = runtime.current_context(when)
                answer = agent.current_space_answer(when)
                _FrozenDateTime.current = when
                with mock.patch.object(agent_module, "datetime", _FrozenDatetimeModule()):
                    result = agent.handle("用一句话说说你此刻周围是什么样子。", channel="im")
                visible = result.reply
                assert visible and not is_failure_fallback_reply(visible) and not is_internal_reply(visible)
                assert not any(place_id in visible for place_id in runtime.outline.space.places)
                recent = agent.memory.recent_messages(limit=2, channel="qq")
                assert recent and recent[-1]["role"] == "assistant" and recent[-1]["content"] == visible
                if when == times[1]:
                    assert ctx.scene_state == "in_transition"
                    assert ctx.target_place_id == "zima_home"
                if when == times[3]:
                    assert ctx.scene_state == "at_place"
                    assert ctx.place_label == "一居室"
                if ctx.place_label and ctx.scene_state == "at_place":
                    assert ctx.place_label in answer
                observations.append({"when": when.isoformat(), "place": ctx.place_label, "scene_state": ctx.scene_state, "answer_chars": len(visible)})
            assert llm_calls >= len(times)
            print(json.dumps({"ok": True, "llm_calls": llm_calls, "observations": observations}, ensure_ascii=False))
        finally:
            agent.memory.con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
