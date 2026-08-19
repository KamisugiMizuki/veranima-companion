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
/style      查看学习到的风格参数与语言镜像
/reset --style  回滚风格参数（核心人格不受影响）
/review     生成"我们一起走过的日子"回顾
/memory     查看记忆统计
/forget <词> 删除包含该词的所有记忆（隐私擦除）
/quit       退出
直接输入内容即对话"""


class CLIAdapter:
    def __init__(self, agent: Agent, console: Console | None = None):
        self.agent = agent
        self.console = console or Console()

    def run(self) -> None:
        c = self.console
        c.print(f"[bold cyan]Veranima[/] — 情感陪伴 agent（Ctrl+C 或 /quit 退出）")
        # MVP3 主动触发：后台线程每分钟检查定时问候与节庆纪念
        import threading
        stop = threading.Event()

        def _proactive_loop():
            while not stop.wait(60):
                try:
                    self._tick_proactive()
                except Exception:
                    logger.exception("proactive tick failed")

        threading.Thread(target=_proactive_loop, daemon=True).start()
        try:
            opening = self.agent.start()
            c.print(f"[cyan]{self.agent.card.name}[/]：{opening}")
            # 启动时也检查一次节庆（例如当天是纪念日）
            self._tick_proactive()
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

    def _tick_proactive(self) -> None:
        """定时问候 + 节庆纪念检查（复用 agent.tick_proactive，每日去重）。"""
        for msg in self.agent.tick_proactive():
            self.console.print(f"[dim cyan]{self.agent.card.name}（主动）[/]：{msg}")

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
            # M-7：列出各层记忆（不只是分布）
            parts = arg.split()
            layer = parts[0] if parts and parts[0] in ("semantic", "episodic", "procedural", "core_profile", "session") else None
            mems = self.agent.list_memories(layer=layer, limit=20)
            if not mems:
                c.print("（暂无记忆）" if layer is None else f"（{layer} 层暂无记忆）")
            else:
                for m in mems:
                    st = f"[{m['status']}]" if m["status"] != "active" else ""
                    c.print(f"#{m['id']} {m['layer']} v{m['version']} {st} {m['content'][:60]}")
                c.print(f"共显示 {len(mems)} 条；/memory <层名> 只看一层")
        elif op == "/export":
            fmt = arg.strip() or "jsonl"
            if fmt not in ("jsonl", "markdown"):
                c.print("用法：/export [jsonl|markdown]")
                return
            data = self.agent.export_memories(fmt=fmt)
            out_path = f"data/memory_export.{fmt}"
            import pathlib
            pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            pathlib.Path(out_path).write_text(data, encoding="utf-8")
            c.print(f"已导出 {len(data.splitlines())} 行 → {out_path}")
        elif op == "/style":
            ls = self.agent.learning_summary()
            params = ls["params"]
            c.print(
                f"风格参数（{ls['steps']} 轮学习）："
                f"长度 {params['reply_length']:.2f} | 正式度 {params['formality']:.2f} | "
                f"幽默 {params['humor']:.2f} | 话题跟随 {params['topic_follow']:.2f}"
            )
            if ls["mirror_top"]:
                c.print("用户高频词：" + " ".join(f"{w}×{n}" for w, n in list(ls["mirror_top"].items())[:6]))
            # M-6：文风画像统计
            prof = ls["profile"]
            if prof["sample_count"] >= 20:
                c.print(
                    f"文风画像（{prof['sample_count']} 样本，置信 {prof['confidence']:.2f}）："
                    f"均长 {prof['avg_message_chars']:.0f} 字 | 问句 {prof['question_ratio']:.2f} | "
                    f"正式度 {prof['formality']:.2f} | 直接度 {prof['directness']:.2f}"
                )
            else:
                c.print(f"文风画像收集中（{prof['sample_count']}/20 样本）")
            c.print(f"未兑现承诺：{ls['open_promises']} 条")
        elif op == "/review":
            c.print("（小V 在想……）")
            text = self.agent.monthly_review()
            c.print(text)
        elif op == "/reset":
            if arg == "--style":
                ls = self.agent.reset_style()
                c.print(f"风格已回滚为默认（{ls['steps']} 轮学习清零）。核心人格不受影响。")
            else:
                c.print("用法：/reset --style  （回滚学习到的风格参数与语言镜像）")
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
