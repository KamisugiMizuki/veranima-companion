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
        if agent is None:
            continue
        try:
            for msg in agent.tick_proactive():
                _pending.append(msg)
                log.info("proactive queued: %s", msg[:60])
        except Exception:
            log.exception("proactive tick failed")


def start_ticks() -> str:
    """boot 后由 Kotlin 调一次：起 daemon tick 线程。"""
    if getattr(start_ticks, "_on", False):
        return json.dumps({"ok": True, "already": True})
    import threading
    threading.Thread(target=_tick_loop, daemon=True).start()
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
