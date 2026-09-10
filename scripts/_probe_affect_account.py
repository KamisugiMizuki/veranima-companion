"""情绪账审计探针（2026-09-11）：负向传染 → 她 PAD →【当下语气】档位的真实链路实测。

只读真实常量与函数（Agent._AFFECT_DELTA / _affect_block / apply_emotion_event），
不写 DB、不调 LLM。回答两个问题：
 ①用户连发重情绪消息，几条会把她的【当下语气】推进「偏冷 / 带刺」档？
 ②她自己的生活事件（路上不顺等）累积到什么程度会冒同样档位？

跑法：.venv/Scripts/python scripts/_probe_affect_account.py
"""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veranima.core.agent import Agent  # noqa: E402
from veranima.core.persona import apply_emotion_event  # noqa: E402
from veranima.core.state import AgentState  # noqa: E402


def tone(state: AgentState) -> str:
    return Agent._affect_block(SimpleNamespace(state=state)) or "（无语气块）"


def user_turn(state: AgentState, emotion: str) -> None:
    """复刻 _update_affect：先 decay，再叠该情绪的 delta。"""
    apply_emotion_event(state, {"type": "decay"})
    delta = dict(Agent._AFFECT_DELTA.get(emotion, {}))
    if delta:
        apply_emotion_event(state, {"type": "user_turn", "cause": emotion, "delta": delta})


def life_event(state: AgentState, kind: str) -> None:
    """复刻 _apply_life_affect：向靶点拉一半。"""
    v, a, cause = Agent._LIFE_MOOD[kind]
    apply_emotion_event(state, {"type": f"life:{kind}", "cause": cause, "delta": {
        "valence": (v - state.valence) * Agent._LIFE_PULL,
        "arousal": (a - state.arousal) * Agent._LIFE_PULL,
    }})


def show(tag: str, st: AgentState) -> None:
    print(f"{tag:24s} v={st.valence:.3f} a={st.arousal:.3f} → {tone(st)}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    st = AgentState()
    show("基线", st)
    for i in range(1, 4):
        user_turn(st, "sad")
        show(f"用户第{i}条 sad", st)
    for i in range(1, 5):
        user_turn(st, "none")
        show(f"  中性第{i}条", st)
    print("---")
    st = AgentState()
    for i in range(1, 4):
        user_turn(st, "angry")
        show(f"用户第{i}条 angry", st)
    print("---")
    st = AgentState()
    for i in range(1, 7):
        life_event(st, "transition_interrupted")
        show(f"路上不顺×{i}", st)
    print("---")
    st = AgentState()
    life_event(st, "sleep_debt")
    show("没睡够×1", st)
    life_event(st, "day_interrupted")
    show("被打断×1", st)
    user_turn(st, "none")
    show("  +中性×1", st)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
