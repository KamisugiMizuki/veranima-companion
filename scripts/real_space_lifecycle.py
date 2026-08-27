from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

from veranima.app import create_agent
from veranima.config import load_config


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="veranima-space-real-") as temp:
        root = Path(temp)
        cfg = copy.deepcopy(load_config())
        cfg["root"] = str(Path.cwd())
        cfg["character_card"] = str(Path.cwd() / "characters" / "zima" / "character.json")
        cfg.setdefault("memory", {})["db_path"] = str(root / "space.db")
        cfg.setdefault("search", {})["enabled"] = False
        cfg.setdefault("tasks", {})["enabled"] = False
        cfg["virtual_schedule"] = {"enabled": True, "space_enabled": True}
        agent = create_agent(cfg)
        runtime = agent.schedule_runtime
        if runtime is None or runtime.outline.space is None:
            raise AssertionError("role space runtime not loaded")
        when = dt.datetime(2026, 8, 28, 1, 0, tzinfo=dt.timezone.utc)
        runtime.advance(when)
        context = runtime.current_context(when)
        if not context.place_label or not context.ambient_context:
            raise AssertionError("current space context missing")
        prompt = agent._format_schedule_context(context)
        if context.place_label not in prompt:
            raise AssertionError("place label missing from prompt")
        if "活动环境=" not in prompt:
            raise AssertionError("ambient context missing from prompt")
        print(json.dumps({
            "ok": True,
            "role": runtime.outline.role_id,
            "place": context.place_label,
            "scene_state": context.scene_state,
            "ambient_keys": sorted(context.ambient_context),
            "prompt_has_place": context.place_label in prompt,
        }, ensure_ascii=False))
        agent.memory.con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
