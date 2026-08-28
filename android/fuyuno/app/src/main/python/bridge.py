"""冬乃安卓桥：Kotlin 侧唯一入口。spike 版 = 同步函数，无线程/无事件总线。

异常哲学：任何失败转 {"ok": false, "error": str}——让 UI 能显示真实原因，
不吞栈（用户调试偏好：日志宁可太多不可太少）。
"""
import json
import logging
import traceback
from pathlib import Path

log = logging.getLogger("fuyuno.bridge")


def boot(files_dir: str) -> str:
    """首次调用：配置 → create_agent（远程 LLM + 远程 embedding，全链无本地模型）。

    幂等：重复调用返回缓存状态。返回 {ok, ...诊断}。
    """
    if getattr(boot, "_done", False):
        return json.dumps({"ok": True, "already": True})
    try:
        root = Path(files_dir)
        (root / "data").mkdir(exist_ok=True)
        (root / "logs").mkdir(exist_ok=True)
        fh = logging.FileHandler(root / "logs" / "core.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)
        logging.getLogger().setLevel(logging.INFO)

        import yaml
        from veranima.app import create_agent

        cfg = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        cfg["root"] = str(root)
        # 路径全部锚定到应用私有目录
        cfg.setdefault("memory", {})["db_path"] = str(root / "data" / "veranima.db")
        agent = create_agent(cfg)
        boot.agent = agent
        boot._done = True
        probe = {
            "ok": True,
            "python": __import__("sys").version.split()[0],
            "role": agent.card.name,
            "memories": _mem_probe(agent),
        }
        log.info("boot ok: %s", probe)
        return json.dumps(probe, ensure_ascii=False)
    except Exception as e:
        tb = traceback.format_exc(limit=6)
        log.error("boot failed:\n%s", tb)
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}", "trace": tb}, ensure_ascii=False)


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
