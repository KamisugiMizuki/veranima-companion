from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

from veranima.app import create_agent
from veranima.config import load_config


def main():
    with tempfile.TemporaryDirectory(prefix="veranima-space-real-") as temp:
        root = Path(temp)
        cfg = copy.deepcopy(load_config())
        project = Path.cwd()
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
            now = dt.datetime(2026, 8, 28, 1, tzinfo=dt.timezone.utc)
            runtime.advance(now)
            context = runtime.current_context(now)
            assert context.place_label and context.ambient_context
            prompt = agent._format_schedule_context(context)
            assert context.place_label in prompt and "活动环境=" in prompt
            answer = agent.current_space_answer(now)
            assert context.place_label in answer
            print(json.dumps({"ok": True, "role": runtime.outline.role_id, "place": context.place_label, "scene_state": context.scene_state, "ambient": context.ambient_context, "answer": answer}, ensure_ascii=False))
        finally:
            agent.memory.con.close()


if __name__ == "__main__":
    main()
