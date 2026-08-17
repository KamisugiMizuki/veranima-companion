#!/usr/bin/env python
"""Veranima CLI 入口：python -m veranima.cli"""

from __future__ import annotations

import logging
import sys

from .adapters.cli import CLIAdapter
from .app import create_agent
from .config import load_config


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config()
    agent = create_agent(cfg)

    llm = agent.llm
    if not llm.is_available():
        print(f"无法连接 LLM 服务（{cfg.get('llm', {}).get('base_url', 'http://localhost:1234/v1')}）。请先启动 LM Studio 并开启本地服务器。")
        return 1
    if not llm.ensure_model():
        print(f"模型 {cfg.get('llm', {}).get('model', '?')} 未加载，请在 LM Studio 中加载后重试")
        return 1

    CLIAdapter(agent).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
