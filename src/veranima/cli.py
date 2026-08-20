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
            out = export_character(roles_dir() / args.role_id, Path(args.new_id or f"{args.role_id}.charpkg"))
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


def _creation_store(cfg):
    from pathlib import Path
    from .core.shared_creation import SharedCreationStore
    from .memory.store import MemoryStore

    root = Path(cfg.get("root", "."))
    llm_cfg = cfg.get("llm") or {}
    memory_cfg = {**(cfg.get("memory") or {}), "root": str(root), "host": llm_cfg.get("base_url", "")}
    db_path = memory_cfg.get("db_path") or str(root / "data" / "veranima.db")
    if not Path(db_path).is_absolute():
        db_path = str(root / db_path)
    return SharedCreationStore(MemoryStore(db_path=db_path, config=memory_cfg, llm_config=llm_cfg))


def _create_cmd(args) -> int:
    """共同创作 CLI：消费项目、场景、决策、产物、线程和确认事件。"""
    store = _creation_store(load_config())
    if args.create_action == "list":
        for project in store.list_projects():
            print(f"{project.project_id} [{project.status}] {project.title} — {project.purpose}")
        return 0
    if args.create_action == "project":
        project = store.create_project(kind=args.kind, title=args.title, purpose=args.purpose, confirmed=True)
        print(f"项目已创建 {project.project_id}")
        return 0
    if args.create_action == "confirm":
        event = store.confirm_shared_event(
            args.project_id, summary=args.summary,
            evidence_message_ids=[int(args.evidence_id)],
        )
        print(f"共同经历已确认 {event.event_id} memory_id={event.memory_id}")
        return 0
    if args.create_action == "scene":
        scene = store.create_scene(args.project_id, title=args.title, goal=args.goal)
        print(f"场景已创建 {scene.scene_id}")
        return 0
    if args.create_action == "decision":
        decision = store.record_decision(
            args.project_id, args.scene_id, question=args.question,
            options=[{"id": args.chosen, "label": args.chosen}], chosen=args.chosen,
            decided_by="user", evidence_message_ids=[int(args.evidence_id)],
        )
        print(f"决策已记录 {decision.decision_id}")
        return 0
    if args.create_action == "artifact":
        artifact = store.save_artifact(
            args.project_id, args.scene_id, title=args.title, content=args.content,
            evidence_message_ids=[int(args.evidence_id)],
        )
        print(f"产物已保存 {artifact.artifact_id} v{artifact.version}")
        return 0
    if args.create_action == "thread":
        thread = store.open_thread(
            args.project_id, summary=args.summary, next_action=args.next_action,
        )
        print(f"线程已记录 {thread.thread_id}")
        return 0
    return 1


def _style_cmd(args) -> int:
    """离线 Style Learning：本地语料 → 抽样复核 → 聚合画像。"""
    import json
    from pathlib import Path
    from .core.learning import StyleLearner
    from .core.style_corpus import StyleCorpusStore

    root = Path(load_config().get("root", "."))
    store = StyleCorpusStore(root / "data" / "style_corpora")
    learner = StyleLearner(persist_path=str(root / "data" / "style.json"))
    learner.load()
    try:
        if args.style_action == "import":
            manifest = store.ingest(
                args.corpus_id, args.files, source=args.source, owner=args.owner,
                license=args.license, consent=args.consent,
                delete_scope=args.delete_scope, retention_until=args.retention_until,
                replace=args.replace,
            )
            print(json.dumps({"corpus_id": args.corpus_id, **manifest["stats"]}, ensure_ascii=False))
            return 0
        if args.style_action == "review-export":
            queue = store.export_review(args.corpus_id, limit=args.limit)
            print(f"已导出 {len(queue)} 条复核样本: {store.review_path(args.corpus_id).resolve()}")
            return 0
        if args.style_action == "review-apply":
            print(json.dumps(store.apply_reviews(args.corpus_id), ensure_ascii=False))
            return 0
        if args.style_action == "activate":
            profile = store.activate(args.corpus_id, learner)
            print(json.dumps(profile.snapshot(), ensure_ascii=False))
            return 0
        if args.style_action == "deactivate":
            if not store.deactivate(args.corpus_id, learner):
                print(f"语料集不存在: {args.corpus_id}", file=sys.stderr)
                return 1
            print(f"已停用风格画像，语料集保留为 preview: {args.corpus_id}")
            return 0
        if args.style_action == "status":
            for manifest in store.status(args.corpus_id):
                print(json.dumps(manifest, ensure_ascii=False))
            return 0
        if args.style_action == "delete":
            if not store.delete(args.corpus_id, learner):
                print(f"语料集不存在: {args.corpus_id}", file=sys.stderr)
                return 1
            print(f"已删除语料集及运行时画像: {args.corpus_id}")
            return 0
    except (ValueError, FileNotFoundError, FileExistsError, json.JSONDecodeError) as exc:
        print(f"Style Learning 操作失败: {exc}", file=sys.stderr)
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
    tp = sub.add_parser("task", help="R5 任务管道：模糊指令 → 工单 → dsh")
    tp.add_argument("text", nargs="+", help="任务描述")
    cp = sub.add_parser("create", help="共同创作")
    cp.add_argument("create_action", choices=["list", "project", "scene", "decision", "artifact", "thread", "confirm"])
    cp.add_argument("args", nargs="*")
    sp = sub.add_parser("style", help="离线文风语料处理与复核")
    style_sub = sp.add_subparsers(dest="style_action", required=True)
    si = style_sub.add_parser("import", help="导入并自动清洗/分句/弱标注")
    si.add_argument("corpus_id")
    si.add_argument("files", nargs="+")
    si.add_argument("--source", required=True)
    si.add_argument("--owner", required=True)
    si.add_argument("--license", required=True)
    si.add_argument("--consent", action="store_true", help="确认有权在本地分析这些文本")
    si.add_argument("--retention-until", default="")
    si.add_argument("--delete-scope", choices=["corpus"], default="corpus")
    si.add_argument("--replace", action="store_true")
    se = style_sub.add_parser("review-export", help="导出少量代表/冲突样本")
    se.add_argument("corpus_id")
    se.add_argument("--limit", type=int, default=24)
    sa = style_sub.add_parser("review-apply", help="应用人工 accept/reject 复核")
    sa.add_argument("corpus_id")
    sx = style_sub.add_parser("activate", help="质量门禁通过后启用聚合画像")
    sx.add_argument("corpus_id")
    sdx = style_sub.add_parser("deactivate", help="停用聚合画像但保留语料集")
    sdx.add_argument("corpus_id")
    ss = style_sub.add_parser("status", help="查看语料集状态")
    ss.add_argument("corpus_id", nargs="?")
    sd = style_sub.add_parser("delete", help="删除语料集并撤销其运行时画像")
    sd.add_argument("corpus_id")
    args = ap.parse_args(argv)

    if args.cmd == "roles":
        return _roles_cmd(args)
    if args.cmd == "task":
        return _task_cmd(args)
    if args.cmd == "style":
        return _style_cmd(args)
    if args.cmd == "create":
        if args.create_action == "list" and not args.args:
            pass
        elif args.create_action == "project" and len(args.args) == 3:
            args.kind, args.title, args.purpose = args.args
        elif args.create_action == "scene" and len(args.args) == 3:
            args.project_id, args.title, args.goal = args.args
        elif args.create_action == "decision" and len(args.args) == 5:
            args.project_id, args.scene_id, args.question, args.chosen, args.evidence_id = args.args
        elif args.create_action == "artifact" and len(args.args) == 5:
            args.project_id, args.scene_id, args.title, args.content, args.evidence_id = args.args
        elif args.create_action == "thread" and len(args.args) == 3:
            args.project_id, args.summary, args.next_action = args.args
        elif args.create_action == "confirm" and len(args.args) == 3:
            args.project_id, args.summary, args.evidence_id = args.args
        else:
            ap.error("create 参数不匹配；使用 -h 查看动作参数")
        return _create_cmd(args)

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
    """R5 任务管道（R5_SPEC 2/3）：指令 → 工单 → 追问或转交 dsh。"""
    from .core.workorder import build_workorder, build_workorder_llm, clarification_question, is_task_request
    from .tools.dsh_bridge import dsh_available, run_dsh_task

    text = " ".join(args.text)
    if not is_task_request(text):
        print("（这是闲聊，不转交任务管道）")
        return 0
    if not dsh_available():
        print("桌面助手（dsh）未安装：")
        print("  1. cd dsh")
        print("  2. npm install @deepseek-ai/dsh@0.1.0-rc.6")
        print("  3. 设置 DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY 环境变量（独立于 veranima 配置）")
        print("  4. 重新执行本命令")
        return 1
    # LLM 版意图补全（无 LLM 时自动降级规则版）
    try:
        from .app import create_agent
        cfg = load_config()
        llm = create_agent(cfg).llm
        wo = build_workorder_llm(llm, text) if llm.is_available() else build_workorder(text)
    except Exception:
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
