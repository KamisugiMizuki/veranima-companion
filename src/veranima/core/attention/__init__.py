"""视觉注意力模块（VISION_SPEC）：扫视-注视状态机 + 显著度地图。

对外唯一入口：AttentionScheduler.tick() → list[AttentionEvent]。
替代 vision.py（过渡实现），迁移完成删除。
"""
from .scheduler import AttentionScheduler, AttentionEvent

__all__ = ["AttentionScheduler", "AttentionEvent"]
