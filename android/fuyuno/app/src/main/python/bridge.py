"""冬乃安卓桥：Kotlin 侧唯一入口。spike 版 = 同步函数，无线程/无事件总线。

异常哲学：任何失败转 {"ok": false, "error": str}——让 UI 能显示真实原因，
不吞栈（用户调试偏好：日志宁可太多不可太少）。
"""
import json
import logging
import shutil
import traceback
from pathlib import Path

log = logging.getLogger("fuyuno.bridge")

_pending: list[str] = []  # tick 产出的主动消息，Kotlin 轮询取走


def _tick_loop(interval: float = 60.0) -> None:
    """后台每分钟 tick_proactive（同 CLI adapter 模式）；消息进 _pending。"""
    import time as _t
    while True:
        _t.sleep(interval)
        agent = getattr(boot, "agent", None)
        if agent is None or getattr(boot, "_shutdown", False):
            continue
        try:
            for msg in agent.tick_proactive():
                _pending.append(msg)
                log.info("proactive queued: %s", msg[:60])
        except Exception:
            log.exception("proactive tick failed")


def start_ticks() -> str:
    """boot 后由 Kotlin 调一次：起 daemon tick 线程（固定 60s 检查；发送频率由
    proactive.min_gap_minutes 闸门控制，那才是设置页暴露的"主动发言频率"）。"""
    if getattr(start_ticks, "_on", False):
        return json.dumps({"ok": True, "already": True})
    import threading
    threading.Thread(target=_tick_loop, args=(60.0,), daemon=True).start()
    start_ticks._on = True
    return json.dumps({"ok": True})


def drain_pending() -> str:
    """取走全部待发主动消息（Kotlin 侧发通知）。"""
    out = list(_pending)
    _pending.clear()
    return json.dumps({"ok": True, "messages": out}, ensure_ascii=False)


def boot(files_dir: str) -> str:
    """首次调用：捡 inbox → 配置 → create_agent（远程 LLM + 远程 embedding，全链无本地模型）。

    inbox/ 是调试投递口（adb push /data/local/tmp + run-as cp 送进私有目录）：
    config.yaml / characters/ / backup.zip 各自捡到位，backup.zip 仅当本机库为空才导入，
    导入后改名 backup.zip.done 防重复。
    幂等：重复调用返回缓存状态。返回 {ok, ...诊断}。
    """
    if getattr(boot, "_done", False):
        return json.dumps({"ok": True, "already": True})
    try:
        root = Path(files_dir)
        inbox = root / "inbox"
        (root / "data").mkdir(exist_ok=True)
        (root / "logs").mkdir(exist_ok=True)
        fh = logging.FileHandler(root / "logs" / "core.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)
        logging.getLogger().setLevel(logging.INFO)

        # inbox 投递：config → characters → backup（顺序即依赖）
        cfg_in = inbox / "config.yaml"
        if cfg_in.exists():
            shutil.copy(cfg_in, root / "config.yaml")
            cfg_in.unlink()
            log.info("inbox: config.yaml 就位")
        chars_in = inbox / "characters"
        if chars_in.is_dir():
            for sub in chars_in.iterdir():
                if sub.is_dir():
                    dst = root / "characters" / sub.name
                    if not dst.exists():
                        shutil.copytree(sub, dst)
            shutil.rmtree(chars_in)
            log.info("inbox: characters 捡完")

        import yaml
        from veranima.app import create_agent

        cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        cfg["root"] = str(root)
        boot.root = root  # 设置页后端锚点
        # 路径全部锚定到应用私有目录
        cfg.setdefault("memory", {})["db_path"] = str(root / "data" / "veranima.db")

        imported = _pickup_backup(root, cfg, inbox)

        agent = create_agent(cfg)
        boot.agent = agent
        boot._done = True
        probe = {
            "ok": True,
            "python": __import__("sys").version.split()[0],
            "role": agent.card.name,
            "memories": _mem_probe(agent),
        }
        if imported:
            probe["imported"] = imported
        log.info("boot ok: %s", probe)
        return json.dumps(probe, ensure_ascii=False)
    except Exception as e:
        tb = traceback.format_exc(limit=6)
        log.error("boot failed:\n%s", tb)
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}", "trace": tb}, ensure_ascii=False)


def _pickup_backup(root: Path, cfg: dict, inbox: Path) -> int:
    """inbox 里的 backup.zip → 全量导入（仅当本机 memories 为空）。返回导入条数。"""
    src = inbox / "backup.zip" if inbox else None
    if not src or not src.exists():
        return 0
    db_path = Path(cfg["memory"]["db_path"])
    if db_path.exists():
        import sqlite3
        con = sqlite3.connect(db_path)
        try:
            n = con.execute("SELECT count(*) FROM memories").fetchone()[0]
        except Exception:
            n = 0
        finally:
            con.close()
        if n:
            log.info("backup.zip 存在但本机库非空（%d 条），跳过导入", n)
            return 0
    from veranima.core.backup import import_backup
    from veranima.memory.embedding import make_provider
    mem_cfg = cfg.get("memory") or {}
    prov = make_provider(mem_cfg, cfg.get("llm") or {})
    spec = (mem_cfg.get("embedding_model") or "").strip()
    res = import_backup(root, src, embedding_spec=spec, embedding_dim=prov.dim,
                        embed_fn=prov.embed)
    src.rename(src.with_suffix(".zip.done"))
    log.info("backup imported: %s memories, reembedded=%s",
             res.get("memories"), res.get("reembedded"))
    return int(res.get("memories") or 0)


def _mem_probe(agent) -> int:
    try:
        return agent.memory.con.execute("SELECT count(*) FROM memories").fetchone()[0]
    except Exception:
        return -1


# ---------- 设置页后端（全部读写私有目录 config.yaml，改后重启生效） ----------

# (设置键, 路径, 是否敏感)。敏感=key 打码显示、空值不写；非敏感=原文回显、可清空。
_CFG_FIELDS = (
    ("llm_api_key", ("llm", "profiles", "default", "api_key"), True),
    ("llm_base_url", ("llm", "profiles", "default", "base_url"), False),
    ("llm_model", ("llm", "profiles", "default", "model"), False),
    ("embedding_api_key", ("memory", "embedding_api_key"), True),
    ("embedding_base_url", ("memory", "embedding_base_url"), False),
    ("embedding_model", ("memory", "embedding_model"), False),
    ("search_api_key", ("search", "api_key"), True),
    ("search_base_url", ("search", "base_url"), False),
)


def _load_cfg(root: Path) -> dict:
    import yaml
    return yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))


def _save_cfg(root: Path, cfg: dict) -> None:
    import yaml
    with open(root / "config.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def _mask(v: str) -> str:
    v = str(v or "")
    return (v[:4] + "…" + v[-4:]) if len(v) > 12 else ("已设置" if v else "")


def get_settings() -> str:
    root = Path(getattr(boot, "root", "."))
    try:
        cfg = _load_cfg(root)
        fields = {}
        for name, path, secret in _CFG_FIELDS:
            node = cfg
            for p in path:
                node = (node or {}).get(p) if isinstance(node, dict) else None
            fields[name] = _mask(node) if secret else str(node or "")
        chars = sorted(p.parent.name for p in root.glob("characters/*/character.json"))
        cur = str(cfg.get("character_card") or "")
        active = Path(cur).parent.name if cur else ""
        return json.dumps({"ok": True, "fields": fields,
                           "search_provider": "bocha",
                           "characters": chars, "active_character": active},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


def set_setting(key: str, value: str) -> str:
    """白名单直写。敏感值：空字符串=不改动（防 UI 打码回显误清空）。"""
    root = Path(getattr(boot, "root", "."))
    try:
        cfg = _load_cfg(root)
        for name, path, secret in _CFG_FIELDS:
            if key == name:
                if value.strip() or not secret:
                    node = cfg
                    for p in path[:-1]:
                        node = node.setdefault(p, {})
                    node[path[-1]] = value.strip()
                break
        else:
            if key == "search_provider":
                cfg.setdefault("search", {})["provider"] = "bocha"  # 安卓唯一选项
            elif key == "active_character":
                card = root / "characters" / value / "character.json"
                if not card.exists():
                    raise ValueError(f"角色不存在: {value}")
                cfg["character_card"] = str(card)
            else:
                raise ValueError(f"未知设置键: {key}")
        _save_cfg(root, cfg)
        return json.dumps({"ok": True, "restart_required": True})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


def backup_export() -> str:
    """导出共享记忆备份 → inbox/backup_out.zip，adb pull 可取。"""
    root = Path(getattr(boot, "root", "."))
    try:
        agent = getattr(boot, "agent", None)
        if agent is None:
            raise RuntimeError("核心未启动")
        from veranima.core.backup import export_backup
        cfg = _load_cfg(root)
        spec = str((cfg.get("memory") or {}).get("embedding_model") or "")
        out = root / "inbox" / "backup_out.zip"
        out.parent.mkdir(exist_ok=True)
        # db 锚点与 boot/app 一致（config 里未必写了 db_path）
        db = Path(str((cfg.get("memory") or {}).get("db_path") or (root / "data" / "veranima.db")))
        if not db.is_absolute():
            db = root / db
        export_backup(root, db, embedding_spec=spec, out_path=out)
        return json.dumps({"ok": True, "path": str(out), "bytes": out.stat().st_size})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


def backup_import() -> str:
    """inbox/backup.zip → 全量导入（覆盖旧库留 .old）。完成后需重启。"""
    root = Path(getattr(boot, "root", "."))
    try:
        cfg = _load_cfg(root)
        inbox = root / "inbox"
        src = inbox / "backup.zip"
        if not src.exists():
            raise RuntimeError("inbox/backup.zip 不存在（adb push 后 cat 进来）")
        # 先摘掉活核心（关连接），tick 停摆，再动库文件
        boot._shutdown = True
        agent = getattr(boot, "agent", None)
        if agent is not None:
            try:
                agent.memory.con.close()
            except Exception:
                pass
            boot.agent = None
        from veranima.core.backup import import_backup
        from veranima.memory.embedding import make_provider
        mem_cfg = cfg.get("memory") or {}
        prov = make_provider(mem_cfg, cfg.get("llm") or {})
        spec = (mem_cfg.get("embedding_model") or "").strip()
        res = import_backup(root, src, embedding_spec=spec, embedding_dim=prov.dim,
                            embed_fn=prov.embed)
        src.rename(src.with_suffix(".zip.done"))
        return json.dumps({"ok": True, "memories": res.get("memories"),
                           "reembedded": res.get("reembedded"), "restart_required": True},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


def role_export(role_id: str) -> str:
    root = Path(getattr(boot, "root", "."))
    try:
        from veranima.core.character_archive import export_character
        out = root / "inbox" / "role_pending.char"  # 固定名：SAF 回调按约定名拷走
        export_character(root / "characters" / role_id, out,
                         include_portraits=False, include_voice=False)
        return json.dumps({"ok": True, "path": str(out), "bytes": out.stat().st_size})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


def role_import() -> str:
    """inbox/*.char → 解进 characters/（重名加 _2）。返回角色名列表。"""
    root = Path(getattr(boot, "root", "."))
    try:
        from veranima.core.character_archive import import_character
        imported = []
        for f in sorted((root / "inbox").glob("*.char")):
            dest = import_character(f, root / "characters")
            imported.append(dest.name)
            f.unlink()
        return json.dumps({"ok": True, "imported": imported}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


def chat(text: str) -> str:
    """一轮对话（同步阻塞——真 UI 阶段换协程+回调）。"""
    agent = getattr(boot, "agent", None)
    if agent is None:
        return json.dumps({"ok": False, "error": "未初始化"})
    try:
        res = agent.handle(text, channel="im")
        out = {"ok": True, "reply": res.reply, "portrait": res.portrait,
               "energy": round(res.energy, 2)}
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        tb = traceback.format_exc(limit=6)
        log.error("chat failed:\n%s", tb)
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}", "trace": tb}, ensure_ascii=False)
