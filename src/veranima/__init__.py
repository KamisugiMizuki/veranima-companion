"""Veranima Companion — 情感陪伴 agent 内核。

分层依赖：adapters → core → memory/llm（禁止反向依赖）。
记忆系统对外只暴露五个原语：store / recall / decay / curate / erase。
"""

__version__ = "0.1.0"
