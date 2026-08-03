"""应用组装工厂：CLI 与 QQ（NapCatQQ）入口共用。

职责：config → 角色卡/记忆/LLM → Agent。LLM 可用性检查由各入口负责
（CLI 检查失败退出；QQ 常驻只警告），工厂只做组装。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import load_config, resolve_path
from .core.agent import Agent
from .core.character import CharacterCard
from .core.state import AgentState
from .llm.client import LLMClient
from .memory.store import MemoryStore

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:12345/v1"
DEFAULT_CARD_NAME = "小V"
DEFAULT_CARD_FIRST_MES = "你好，我是小V。今天想聊点什么？"


def create_agent(config: dict | None = None) -> Agent:
    """按配置组装 Agent（角色卡 + 五层记忆 + LLM）。"""
    cfg = config or load_config()

    # 角色卡
    card_path = resolve_path(cfg, "character_card")
    if card_path and Path(card_path).exists():
        card = CharacterCard.from_file(card_path)
    else:
        card = CharacterCard(name=DEFAULT_CARD_NAME, first_mes=DEFAULT_CARD_FIRST_MES)
        logger.info("no character card found, using builtin default")

    # 记忆
    llm_cfg = cfg.get("llm", {})
    memory_cfg = {
        **cfg.get("memory", {}),
        "host": llm_cfg.get("base_url", DEFAULT_BASE_URL),
        "root": cfg.get("root", str(Path.cwd())),
    }
    db_path = resolve_path(memory_cfg, "db_path") or "data/veranima.db"
    memory = MemoryStore(db_path=db_path, config=memory_cfg, llm_config=llm_cfg)

    # LLM
    llm = LLMClient(cfg.get("llm", {}))

    return Agent(card=card, memory=memory, llm=llm, state=AgentState(), config=cfg)
