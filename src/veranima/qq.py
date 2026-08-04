#!/usr/bin/env python
"""Veranima QQ 入口：python -m veranima.qq（NapCatQQ OneBot v11）。

前置：NapCatQQ 运行中，并配置"反向 WebSocket 客户端"连接
ws://<qq.ws_host>:<qq.ws_port>/ws（默认监听 127.0.0.1:8099；8080 被本机
SearXNG 占用，勿用）。白名单 qq.allowed_qq 必填（1v1 私聊，只填你的 QQ 号）。
"""

from __future__ import annotations

import logging
import sys

from .adapters.qq import OfflineThinkTimer, QQAdapter
from .app import create_agent
from .config import load_config


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config()
    qq_cfg = cfg.get("qq", {})

    if not qq_cfg.get("enabled", False):
        print("qq.enabled=false，未启用 QQ 形态。请在 config/config.yaml 配置 [qq] 段。")
        return 1
    allowed = qq_cfg.get("allowed_qq", [])
    if not allowed:
        print("qq.allowed_qq 为空（白名单必填，1v1 私聊）。请填写你的 QQ 号。")
        return 1

    agent = create_agent(cfg)
    llm = agent.llm
    if not llm.is_available():
        print(f"警告：无法连接 LLM 服务（{cfg.get('llm', {}).get('base_url', 'http://localhost:1234/v1')}）。")
        print("QQ 服务仍将启动；LLM 恢复后消息处理自动可用。")
    elif not llm.ensure_model():
        print(f"警告：模型 {cfg.get('llm', {}).get('model', '?')} 未加载，回复将提示唤醒。")

    think_cfg = qq_cfg.get("offline_think", {})
    offline = None
    if think_cfg.get("enabled", True):
        offline = OfflineThinkTimer(
            silence_minutes=int(think_cfg.get("silence_minutes", 30)),
            probability=float(think_cfg.get("probability", 0.3)),
            max_per_day=int(think_cfg.get("max_per_day", 2)),
            growth_factor=float(think_cfg.get("growth_factor", 0.08)),
            max_probability=float(think_cfg.get("max_probability", 0.95)),
        )

    host = qq_cfg.get("ws_host", "127.0.0.1")
    port = int(qq_cfg.get("ws_port", 8099))
    # 静默时段：默认 23:00-08:00 不主动发任何消息（问候/离线思考）
    qh = qq_cfg.get("quiet_hours", [23, 8])
    quiet_hours = (int(qh[0]), int(qh[1])) if qh else None
    adapter = QQAdapter(
        agent,
        ws_host=host,
        ws_port=port,
        access_token=qq_cfg.get("access_token", ""),
        allowed_qq=allowed,
        proactive=bool(qq_cfg.get("proactive", True)),
        offline_think=offline,
        quiet_hours=quiet_hours,
    )
    print(f"Veranima QQ 已启动（白名单: {', '.join(map(str, allowed))}）")
    print(f"监听 ws://{host}:{port}/ws —— 请在 NapCatQQ 配置反向 WebSocket 客户端连接此地址")
    adapter.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
