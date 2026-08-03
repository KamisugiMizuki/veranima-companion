"""CLI 适配器：交互命令 + rich 界面 + 主动问候（MVP1 验证形态）。"""

from __future__ import annotations

import logging
import sys

from rich.console import Console
from rich.markdown import Markdown

from ..core.agent import Agent

logger = logging.getLogger(__name__)

HELP_TEXT = """/help       显示帮助
/status     查看当前状态（精力/情绪/依恋度/记忆量）
/forget <词> 删除包含该词的所有记忆（隐私擦除）
/memory     查看记忆统计
/quit       退出
直接输入内容即对话"""


class CLIAdapter:
    def __init__(self, agent: Agent, console: Console | None = None):
        self.agent = agent
        self.console = console or Console()

    def run(self) -> None:
        c = self.console
        c.print(f"[bold cyan]Veranima[/] — 情感陪伴 agent（Ctrl+C 或 /quit 退出）")
        try:
            opening = self.agent.start()
            c.print(f"[cyan]{self.agent.card.name}[/]：{opening}")
            while True:
                try:
                    line = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    c.print("\n再见。")
                    break
                if not line:
                    continue
                if line.startswith("/"):
                    self._dispatch(line)
                    continue
                result = self.agent.handle(line)
                c.print(f"[cyan]{self.agent.card.name}[/]：{result.reply}")
                if result.proactive_msg:
                    c.print(f"[dim cyan]{self.agent.card.name}（主动）[/]：{result.proactive_msg}")
        except Exception as e:
            c.print(f"[red]运行时错误：{e}[/]")
            logger.exception("CLI crashed")
            sys.exit(1)

    def _dispatch(self, cmd: str) -> None:
        c = self.console
        parts = cmd.split(maxsplit=1)
        op = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if op == "/help":
            c.print(HELP_TEXT)
        elif op == "/status":
            s = self.agent.status()
            c.print(
                f"精力 {s['energy']}/100 | 情绪 {s['mood']} | "
                f"依恋度 {s['attachment']:.3f} | 本轮消息 {s['history_len']} 条"
            )
        elif op == "/memory":
            counts = self.agent.memory.curate().get("counts", {})
            c.print("记忆分布：" + "  ".join(f"{k}={v}" for k, v in counts.items()))
        elif op == "/forget":
            if not arg:
                c.print("用法：/forget <关键词>")
                return
            n = self.agent.forget(arg)
            c.print(f"已删除 {n} 条相关记忆。")
        elif op == "/quit":
            c.print("再见。")
            sys.exit(0)
        else:
            c.print(f"未知命令 {op}，/help 查看帮助")
