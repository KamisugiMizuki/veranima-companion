"""VISION_SPEC V1/V2 测试：显著度三通道 + 扫视-注视状态机（替代旧 vision.py 测试）。"""
import numpy as np

from veranima.core.attention.saliency import compute_saliency
from veranima.core.attention.scheduler import AttentionScheduler


def test_saliency_static_first_frame():
    """首帧（无运动历史）：对比/结构通道主导，无 motion 源。"""
    frame = np.full((100, 160), 240, dtype=np.uint8)
    frame[20:40, 30:70] = 30  # 高对比黑块
    regions = compute_saliency(frame, None)
    assert regions, "应有显著区域"
    assert all(r["source"] != "motion" for r in regions)
    assert regions[0]["score"] > 0.01


def test_saliency_motion_dominant():
    """帧差：移动块 → motion 通道主导。"""
    frame = np.full((100, 160), 240, dtype=np.uint8)
    frame[20:40, 30:70] = 30
    frame2 = frame.copy()
    frame2[30:50, 40:80] = 30  # 块移动
    regions = compute_saliency(frame2, frame)
    assert regions[0]["source"] == "motion"
    assert regions[0]["score"] > 0.1


def test_scheduler_tick_events(monkeypatch):
    """tick 状态机：窗口切换事件 + 注视转移事件（截屏 mock 为稳定图）。"""
    import veranima.core.attention.perception as perc

    frame = np.full((100, 160), 240, dtype=np.uint8)
    frame[20:40, 30:70] = 30
    monkeypatch.setattr(perc, "grab_gray_downsampled", lambda scale=8: frame)
    monkeypatch.setattr(perc, "_CAN_CAPTURE", True)

    att = AttentionScheduler(llm=None, config={"global_scan_sec": 0.0})
    att._foreground = lambda: "测试窗口"  # 固定前台窗口
    events = att.tick()
    kinds = {e.kind for e in events}
    assert "window_switch" in kinds
    assert "fixation_shift" in kinds
    # 无 LLM → 观察不产生 tag（静默降级）
    for e in events:
        assert e.tag == ""
