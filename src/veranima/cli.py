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
    args = ap.parse_args(argv)

    if args.cmd == "roles":
        return _roles_cmd(args)

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


if __name__ == "__main__":
    sys.exit(main())
