#!/usr/bin/env python
"""Veranima CLI 入口：python -m veranima.cli [roles|...]"""

from __future__ import annotations

import argparse
import logging
import sys

from .adapters.cli import CLIAdapter
from .app import create_agent
from .config import load_config


def _roles_cmd(args) -> int:
    """多角色管理（DESIGN 4.11）：list / switch / clone。"""
    from .core.roles import active_role, clone_role, list_roles, roles_dir, switch_role

    cfg = load_config()
    if args.roles_action == "list":
        roles = list_roles()
        active = active_role(cfg)
        print(f"角色列表（{len(roles)} 个，目录 {cfg.get('root', '.')}/characters/）：")
        for r in roles:
            mark = " *" if active and r["id"] == active["id"] else ""
            print(f"  {r['id']:<20} {r['name']}{mark}")
        if active:
            print(f"当前激活: {active['id']}（{active['name']}）")
        return 0
    if args.roles_action == "switch":
        ok, msg = switch_role(args.role_id, cfg)
        print(msg)
        return 0 if ok else 1
    if args.roles_action == "clone":
        ok, msg = clone_role(args.role_id, args.new_id)
        print(msg)
        return 0 if ok else 1
    if args.roles_action == "export":
        from .core.character_archive import export_character
        from pathlib import Path
        try:
            out = export_character(roles_dir() / args.role_id, Path(args.new_id or f"{args.role_id}.char"))
            print(f"已导出: {out}")
            return 0
        except Exception as e:
            print(f"导出失败: {e}")
            return 1
    if args.roles_action == "import":
        from .core.character_archive import import_character
        from pathlib import Path
        try:
            target = import_character(Path(args.role_id), roles_dir())
            print(f"已导入: {target}")
            return 0
        except Exception as e:
            print(f"导入失败: {e}")
            return 1
    return 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser(prog="veranima")
    sub = ap.add_subparsers(dest="cmd")
    rp = sub.add_parser("roles", help="多角色管理")
    rp.add_argument("roles_action", choices=["list", "switch", "clone", "export", "import"])
    rp.add_argument("role_id", nargs="?")
    rp.add_argument("new_id", nargs="?")
    tp = sub.add_parser("task", help="M5 任务管道：模糊指令 → 工单 → dsh")
    tp.add_argument("text", nargs="+", help="任务描述")
    args = ap.parse_args(argv)

    if args.cmd == "roles":
        return _roles_cmd(args)
    if args.cmd == "task":
        return _task_cmd(args)

    # 默认：进入 CLI 对话
    cfg = load_config()
    agent = create_agent(cfg)

    llm = agent.llm
    if not llm.is_available():
        print(f"无法连接 LLM 服务（{cfg.get('llm', {}).get('base_url', '未配置')}）。请检查网络与 API 配置。")
        return 1
    if not llm.ensure_model():
        print(f"模型 {cfg.get('llm', {}).get('model', '?')} 不可用，请在 config 中检查模型名")
        return 1

    CLIAdapter(agent).run()
    return 0


def _task_cmd(args) -> int:
    """M5 任务管道（M5_SPEC 2/3）：指令 → 工单 → 追问或转交 dsh。"""
    from .core.workorder import build_workorder, clarification_question, is_task_request
    from .tools.dsh_bridge import run_dsh_task

    text = " ".join(args.text)
    if not is_task_request(text):
        print("（这是闲聊，不转交任务管道）")
        return 0
    wo = build_workorder(text)
    print(f"工单: {wo.task_id} [{wo.task_type}]")
    q = clarification_question(wo)
    if q:
        print(f"需澄清: {q}")
        print("（回复补充信息后重新发起即可；演示模式直接转交 dsh）")
    print("已安排，任务交给桌面助手处理中……")
    result = run_dsh_task(wo.to_json())
    print(f"结果: exit={result['exit_code']}")
    out = result["output"]
    print((out[:600] + "…") if len(out) > 600 else out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
