"""M4 L0 在场检测（M4_SPEC 1.3）：零 token 系统状态。

Windows 实现（ctypes，零依赖）：
- 用户在场 = GetLastInputInfo 空闲时间 < 阈值
- 前台应用 = GetForegroundWindow + GetWindowTextW
非 Windows 降级：presence=True（恒在场），foreground=""
"""
from __future__ import annotations

import sys

PRESENCE_IDLE_THRESHOLD_MS = 5 * 60 * 1000  # 5min 无输入 = 离开（M4_SPEC L0）

_is_windows = sys.platform == "win32"
if _is_windows:
    import ctypes
    from ctypes import wintypes as _wt

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", _wt.UINT), ("dwTime", _wt.DWORD)]


def input_idle_ms() -> int:
    """距上次用户输入的空闲毫秒数（非 Windows 返回 0）。"""
    if not _is_windows:
        return 0
    try:
        li = _LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return 0
        tick = ctypes.windll.kernel32.GetTickCount()
        return max(0, tick - li.dwTime)
    except Exception:
        return 0


def presence() -> bool:
    """用户是否在场（空闲 < 阈值）。"""
    return input_idle_ms() < PRESENCE_IDLE_THRESHOLD_MS


def foreground_app() -> str:
    """当前前台窗口标题（用于锚点 tag 匹配）。非 Windows 返回空。"""
    if not _is_windows:
        return ""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        return buf.value.strip()
    except Exception:
        return ""


def foreground_tag(anchors_tags: list[str] | None = None) -> str:
    """前台窗口 → 锚点 tag（M4_SPEC 1.4 联想式匹配）。

    简化：标题包含关键词 → 返回 tag；无匹配返回 ""。
    """
    title = foreground_app().lower()
    if not title:
        return ""
    for tag in anchors_tags or ("游戏", "办公", "浏览器", "聊天"):
        if tag.lower() in title:
            return tag
    return ""
