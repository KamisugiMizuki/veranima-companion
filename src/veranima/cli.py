#!/usr/bin/env python
"""Veranima CLI 入口：python -m veranima.cli"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .adapters.cli import CLIAdapter
from .config import load_config, resolve_path
from .core.agent import Agent
from .core.character import CharacterCard
from .core.state import AgentState
from .llm.client import LLMClient
from .memory.store import MemoryStore


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config()

    # 角色卡
    card_path = resolve_path(cfg, "character_card")
    if card_path and Path(card_path).exists():
        card = CharacterCard.from_file(card_path)
    else:
        card = CharacterCard(name="小V", first_mes="你好，我是小V。今天想聊点什么？")
        logging.info("no character card found, using builtin default")

    # 记忆
    memory_cfg = {**cfg.get("memory", {}), "host": cfg.get("llm", {}).get("base_url", "http://localhost:1234/v1")}
    db_path = resolve_path(memory_cfg, "db_path") or "data/veranima.db"
    memory = MemoryStore(db_path=db_path, config=memory_cfg)

    # LLM
    llm = LLMClient(cfg.get("llm", {}))
    if not llm.is_available():
        print(f"无法连接 LLM 服务（{cfg.get('llm', {}).get('base_url', 'http://localhost:1234/v1')}）。请先启动 LM Studio 并开启本地服务器。")
        return 1
    if not llm.ensure_model():
        print(f"模型 {cfg.get('llm', {}).get('model', '?')} 未加载，请在 LM Studio 中加载后重试")
        return 1

    # Agent
    agent = Agent(card=card, memory=memory, llm=llm, state=AgentState(), config=cfg)
    CLIAdapter(agent).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
