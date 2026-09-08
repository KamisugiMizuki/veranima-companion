"""真实角色卡验收：日程事件 → 情绪 + 提示块给人话（09-09）。

跑法：.venv/Scripts/python scripts/_probe_life_affect.py [role]
看两件事：①真实场景事件（出门/到地方/路上不顺）是否让 PAD 动且带归因；
②【当前虚拟活动交互资源】里活动是中文标签而非 model_training_work 这类机器键。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from veranima.core.agent import Agent  # noqa: E402
from veranima.core.character import CharacterCard as Card  # noqa: E402
from veranima.core.state import AgentState  # noqa: E402
from veranima.core.virtual_schedule import ScheduleOutline, ScheduleRuntime  # noqa: E402
from veranima.memory.store import MemoryStore  # noqa: E402


class Embed:
    dim = 8

    def embed(self, texts):
        return [[0.0] * self.dim for _ in texts]


class LLM:
    def is_model_loaded(self): return True
    def chat(self, messages, **kwargs): return "{}"


def main(role: str = "xumian") -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    card_path = root / "characters" / role / "character.json"
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="life_affect_"))
    mem = MemoryStore(str(tmp / "db.sqlite"), config={}, provider=Embed())
    agent = Agent(Card.from_file(card_path), mem, LLM(), AgentState(),
                  config={"character_card": str(card_path), "root": str(root),
                          "virtual_schedule": {"enabled": True}})
    agent.schedule_runtime = ScheduleRuntime(ScheduleOutline.from_role_dir(card_path.parent))

    base = dt.datetime(2026, 9, 8, 22, 0, tzinfo=dt.timezone.utc)  # 北京 06:00
    last = None
    hits = 0
    ctx = None
    for minutes in range(0, 18 * 60, 5):
        when = base + dt.timedelta(minutes=minutes)
        asyncio.run(agent.advance_schedule_async(when))
        cur_ctx = agent.schedule_runtime.current_context(when)
        if cur_ctx.activity_key:
            ctx = cur_ctx          # 留一个有活动的样本验标签
        cause = getattr(agent.state, "last_cause", "")
        cur = (round(agent.state.valence, 3), round(agent.state.arousal, 3), cause)
        if cur != last and cause:
            hits += 1
            print(f"{when.astimezone().strftime('%H:%M')} v={cur[0]:.2f} a={cur[1]:.2f} "
                  f"cause={cause} | scene={cur_ctx.scene_state} act={cur_ctx.activity_key}")
            last = cur
    block = Agent._format_schedule_context(ctx)
    print(f"\n情绪被日程事件驱动的次数={hits}")
    print("提示块活动字段：" + [s for s in block.split("，") if s.startswith("活动=")][0])
    machine = [k for k in ("model_training_work", "meme_archiving", "commute_transit") if k in block]
    print("机器键泄漏=" + (",".join(machine) if machine else "无"))
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "xumian"))
