"""VISION_SPEC 4：敏感窗口策略——纯函数，不依赖 UI。

命中敏感分类时：
1. 不截取/不发送图像
2. foreground_app 只保存 sensitive 类别
3. 产出 away/privacy_block 事件
4. attention.paused=true 全局暂停
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 默认敏感分类关键词（VISION_SPEC 4）
DEFAULT_SENSITIVE_KEYWORDS = (
    "密码", "支付", "银行", "私聊", "会议", "锁屏", "安全", "验证码",
    "password", "payment", "bank", "login", "verify", "pay",
)

SENSITIVE_CATEGORY = "sensitive"


class VisibilityPolicy:
    """视觉隐私策略（VISION_SPEC 4/9）：纯函数集合。"""

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.paused = bool(cfg.get("paused", False))
        self.save_raw_images = bool(cfg.get("save_raw_images", False))
        self.sensitive_categories = list(cfg.get("sensitive_categories", []))
        self._keywords = tuple(DEFAULT_SENSITIVE_KEYWORDS)

    def is_sensitive(self, app_name: str, window_title: str = "") -> bool:
        """窗口/标题命中敏感关键词（VISION_SPEC 4 默认分类）。"""
        hay = f"{app_name} {window_title}".lower()
        return any(k in hay for k in self._keywords)

    def paused_now(self) -> bool:
        """attention.paused=true → 全局暂停（VISION_SPEC 4 第 4 条）。"""
        return self.paused

    def policy_action(self, app_name: str, window_title: str = "") -> dict:
        """对一次感知请求给出策略裁决。

        返回 {"action": "capture|block", "category": "sensitive|normal", "reason": "..."}
        """
        if not self.enabled:
            return {"action": "block", "category": "normal", "reason": "attention disabled"}
        if self.paused:
            return {"action": "block", "category": "normal", "reason": "paused by user"}
        if self.is_sensitive(app_name, window_title):
            return {"action": "block", "category": SENSITIVE_CATEGORY,
                    "reason": f"sensitive window: {app_name}"}
        return {"action": "capture", "category": "normal", "reason": "ok"}
