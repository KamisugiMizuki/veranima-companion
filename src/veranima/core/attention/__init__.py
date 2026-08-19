"""视觉注意力模块（VISION_SPEC）：扫视-注视状态机 + 显著度地图。

对外唯一入口：AttentionScheduler.tick() → list[AttentionEvent]。
替代 vision.py（过渡实现），迁移完成删除。
"""
from .scheduler import AttentionScheduler, AttentionConfig
from .events import AttentionEvent, AttentionInput, Observation
from .observer import observe
from .visibility_policy import VisibilityPolicy

__all__ = [
    "AttentionScheduler", "AttentionConfig",
    "AttentionEvent", "AttentionInput", "Observation",
    "observe", "VisibilityPolicy",
]
