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


def _render(agent, text: str) -> str:
    """IM 通道统一出口（与 QQ _send_to_all 同构）：换行/波浪号/感叹号/emoji
    规则 + 内部提示词/思考痕迹剥离。渲染失败回退原文（宁可不修饰不能不发）。"""
    if not text:
        return ""
    try:
        from veranima.core.render import render_im
        emoji_freq = (agent.card.veranima or {}).get("emoji_frequency", "low") if agent.card else "low"
        return render_im(text, attachment=agent.state.attachment, emoji_frequency=emoji_freq) or text
    except Exception:
        log.exception("render_im failed, raw fallback")
        return text


def _advance_schedule(agent) -> str:
    """虚拟日程推进一格，返回迁移公告（sleep_preparing/woke）或空串。

    advance_schedule_async 内部是 to_thread 包装（无网络协程），bridge 无线
    程事件循环 → 用 asyncio.run 跑一次；缺该函数时退到 runtime.advance 同步版。
    """
    import asyncio
    import datetime
    runtime = getattr(agent, "schedule_runtime", None)
    if runtime is None:
        return ""
    advance = getattr(agent, "advance_schedule_async", None)
    try:
        if callable(advance):
            asyncio.run(advance())
        else:
            runtime.advance(datetime.datetime.now(datetime.timezone.utc))
        return runtime.pop_notice() or ""
    except Exception:
        log.exception("schedule advance failed")
        return ""


def _tick_loop(interval: float = 60.0) -> None:
    """后台每分钟周期循环，对齐 QQ adapter 的驱动面（2026-08-29 盘点补全）：
    日程推进公告 → ritual 问候/节庆/饭点（tick_proactive，饭点 2026-08 收编进
    问候引擎）→ 离线思考（late_reply 优先 / heartbeat 破冰）→ 夜间 digest（内部日去重）。
    quiet hours 已退役（睡眠模拟由虚拟日程承担）。消息进 _pending。

    同轮单发：安卓主动闸门按用户要求归零（随心发言），PC 端靠 min_gap=30
    天然错峰的 ritual 各路（问候/节庆/三餐/公告）在这里会同轮撞车（2026-08-29
    实测午饭提醒+中午问候连发两条）——一轮 tick 只放行第一条命中的。"""
    import time as _t
    while True:
        _t.sleep(interval)
        agent = getattr(boot, "agent", None)
        if agent is None or getattr(boot, "_shutdown", False):
            continue
        try:
            now = _t.time()
            sent = False  # 本轮已放行一条主动
            notice = _advance_schedule(agent)
            if notice:
                cand = agent.schedule_notice_candidate(notice, "im")
                decision = agent.gate.decide(
                    cand, scene=agent.scene_lock.current(),
                    character_sleeping=False,
                ) if cand else None
                text = agent.schedule_notice_text(notice) if decision and decision.allow else ""
                if text:
                    agent.gate.commit(cand)
                    agent.record_proactive_message(text, channel="im")
                    _pending.append(_render(agent, text))
                    sent = True
                    log.info("schedule notice queued: %s", text[:60])
            if not sent:
                for msg in agent.tick_proactive():
                    _pending.append(_render(agent, msg))
                    sent = True
                    log.info("proactive queued: %s", msg[:60])
            off = getattr(boot, "offline", None)
            if off is not None and not sent and off.due(now, getattr(boot, "_last_user_activity", None)):
                # late_reply/heartbeat 内部已落库（store_message），不再 record
                msg = agent.late_reply() or agent.heartbeat()
                if msg:
                    _pending.append(_render(agent, msg))
                    log.info("offline think queued: %s", msg[:60])
            if not sent:
                # 无人应答追问（期待过期后一句轻追问；每期待至多一次）
                fu = agent.followup_message()
                if fu:
                    _pending.append(_render(agent, fu))
                    log.info("followup queued: %s", fu[:60])
            digest = getattr(agent, "maybe_nightly_digest", None)
            if callable(digest):
                digest()
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


def sleep_summary_pending() -> str:
    """最近闭合周期若带未发送的苏醒总结 → 返回文本并标记已发（proactive_feedback 去重）。

    由 drain_pending 消费：用户报告「醒了」后，总结作为独立主动消息推送。
    """
    agent = getattr(boot, "agent", None)
    if agent is None:
        return ""
    try:
        cycle = agent.memory.latest_closed_cycle()
        if not cycle or not cycle.get("summary"):
            return ""
        cid = f"sleep_summary:{cycle['id']}"
        sent = agent.memory.recent_proactive_feedback(source="sleep_summary", limit=30)
        if any(str(r.get("candidate_id") or "") == cid for r in sent):
            return ""
        agent.memory.record_proactive_feedback(source="sleep_summary", channel="qq", candidate_id=cid)
        return str(cycle["summary"])
    except Exception as e:
        log.debug("sleep_summary_pending failed: %s", e)
        return ""


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
        # 历史 NULL 分类按 layer 回填（幂等；2026-08-30 用户反馈新记忆不分类）
        try:
            from veranima.memory.store import backfill_categories
            _n = backfill_categories(agent.memory.con)
            if _n:
                log.info("backfill_categories: %d 条历史记忆已分类", _n)
        except Exception as e:
            log.warning("backfill_categories failed: %s", e)
        boot.agent = agent
        # 周期调度器（tick 线程消费）：离线思考用默认参数（静默30min/概率0.3/
        # 日上限2），调参需求出现再进配置。饭点提醒已收编进 core tick_proactive。
        from veranima.core.proactive import OfflineThinkTimer
        boot.offline = OfflineThinkTimer()
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
    ("llm_vision_model", ("llm", "profiles", "default", "vision_model"), False),
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


def growth_report() -> str:
    """成长树只读数据（DESIGN §11-A）：关系七维+阶段 / 风格四维 / procedural 技能点 / 承诺。

    零 core 改动：全部来自 relationship.to_dict() + StyleLearner.params +
    list_layer(procedural) + PromiseBook.open_promises。
    """
    agent = getattr(boot, "agent", None)
    if agent is None:
        return json.dumps({"ok": False, "error": "未初始化"})
    try:
        rel = agent.relationship.to_dict()
        stage = derive_stage(agent.relationship)
        style = {}
        try:
            p = agent.style.params
            style = {k: round(float(getattr(p, k, 0.5)), 3) for k in
                     ("reply_length", "formality", "humor", "topic_follow")}
        except Exception as e:
            log.debug("style params unavailable: %s", e)
        skills = []
        try:
            for e in agent.memory.list_layer("procedural", limit=100, include_superseded=False):
                kind = (e.meta or {}).get("kind") or "other"
                skills.append({"id": e.id, "kind": kind, "content": e.content[:60],
                               "strength": round(e.strength, 2),
                               "updated_at": e.updated_at or ""})
        except Exception as e:
            log.debug("procedural list failed: %s", e)
        promises = []
        try:
            for p in agent.promises.open_promises(limit=8):
                promises.append({"content": str(p.get("content", ""))[:60],
                                 "status": str(p.get("status", ""))})
        except Exception as e:
            log.debug("promises list failed: %s", e)
        return json.dumps({"ok": True, "relationship": rel, "stage": stage,
                           "style": style, "skills": skills, "promises": promises},
                          ensure_ascii=False)
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        log.error("growth_report failed:\n%s", tb)
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


def derive_stage(model) -> str:
    """关系阶段（persona.derive_relationship_stage 的容错包装）。"""
    try:
        from veranima.core.persona import derive_relationship_stage
        return derive_relationship_stage(model)
    except Exception:
        return "初识"


def memories_list(layer: str = "", category: str = "", limit: int = 200) -> str:
    """记忆列表（DESIGN §11-B）：可选按层/分类过滤，带 category 供标签云聚合。"""
    agent = getattr(boot, "agent", None)
    if agent is None:
        return json.dumps({"ok": False, "error": "未初始化"})
    try:
        from veranima.memory.schema import LAYERS
        if layer:
            entries = agent.memory.list_layer(layer, limit=int(limit), include_superseded=False)
        else:
            entries = []
            for lyr in LAYERS:
                entries.extend(agent.memory.list_layer(lyr, limit=int(limit), include_superseded=False))
        if category:
            entries = [e for e in entries if (e.category or "未分类") == category]
        # 排除 tension 事件噪音（「用户认真回应了直接问题」类，2026-08-30 用户裁决
        # 默认不显示；数据保留，删除入口=记忆库删条目后此类不再重新提取）
        entries = [e for e in entries if ((e.meta or {}).get("kind") or "") != "relational_tension_event"]
        # 全量合并按 updated_at 倒序（最新在前）
        entries.sort(key=lambda e: e.updated_at or "", reverse=True)
        out = [{"id": e.id, "layer": e.layer, "category": e.category or "未分类",
                "content": e.content[:120], "strength": round(e.strength, 2),
                "created_at": e.created_at or "", "updated_at": e.updated_at or ""}
               for e in entries]
        # 标签云聚合：category → 计数
        tags: dict[str, int] = {}
        for it in out:
            tags[it["category"]] = tags.get(it["category"], 0) + 1
        return json.dumps({"ok": True, "memories": out, "tags": tags}, ensure_ascii=False)
    except Exception as e:
        tb = traceback.format_exc(limit=4)
        log.error("memories_list failed:\n%s", tb)
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


def sleep_cycles() -> str:
    """用户睡眠周期列表（DESIGN 2026-08-30 用户拍板）：入睡/苏醒/时长/总结。

    返回 [{fell_asleep_at, woke_at, sleep_minutes, awake_minutes, summary}]，
    按入睡时刻倒序。时间显示用本地时区 MM-dd HH:mm。
    """
    agent = getattr(boot, "agent", None)
    if agent is None:
        return json.dumps({"ok": False, "error": "未初始化"})
    try:
        import datetime
        out = []
        prev_woke = None
        for c in agent.memory.recent_sleep_cycles(limit=30):
            fell = c.get("fell_asleep_at") or ""
            woke = c.get("woke_at") or ""
            entry = {
                "fell_asleep_at": fell, "woke_at": woke,
                "sleep_minutes": 0, "awake_minutes": 0,
                "summary": c.get("summary") or "",
            }
            try:
                f = datetime.datetime.fromisoformat(fell)
                if woke:
                    w = datetime.datetime.fromisoformat(woke)
                    entry["sleep_minutes"] = int((w - f).total_seconds() / 60)
                if prev_woke:
                    entry["awake_minutes"] = int((f - prev_woke).total_seconds() / 60)
            except Exception:
                pass
            out.append(entry)
            if woke:
                prev_woke = datetime.datetime.fromisoformat(woke)
        return json.dumps({"ok": True, "cycles": out}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


def memories_erase(memory_id: int) -> str:
    """删除一条记忆（整条版本链+FTS+向量）。删除是幂等操作；core 后续可能重新提取同内容。"""
    agent = getattr(boot, "agent", None)
    if agent is None:
        return json.dumps({"ok": False, "error": "未初始化"})
    try:
        n = agent.memory.erase(int(memory_id))
        return json.dumps({"ok": True, "detail": f"已删除 {n} 条（含版本链）"})
    except Exception as e:
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})


_VISUAL_COOLDOWN_S = 20 * 60  # 视觉联想冷却 20 分钟（防每次 tick 都发 LLM）
_last_visual_at: float = 0.0
_last_visual_key: str = ""


def visual_note(pkg: str, label: str) -> str:
    """前台应用感知 → 联想主动消息（复用 R4 proactive_from_visual）。

    判断点哲学（用户 2026-08 拍板）：动作判断=一次低成本 LLM 调用
    （包名+app 名 → 动作短语，如「聊微信」「刷B站」），失败回退 app 名。
    冷却：20 分钟内同 pkg 不重复发；产出进 _pending（走既有通知+横幅链路）。
    """
    global _last_visual_at, _last_visual_key
    agent = getattr(boot, "agent", None)
    pkg = str(pkg or "").strip()
    label = str(label or "").strip() or pkg
    if agent is None or not pkg:
        return json.dumps({"ok": False, "error": "未就绪"})
    import time as _t
    now = _t.time()
    if pkg == _last_visual_key and now - _last_visual_at < _VISUAL_COOLDOWN_S:
        return json.dumps({"ok": True, "detail": "cooldown"})
    _last_visual_key = pkg
    _last_visual_at = now
    try:
        tag = _classify_foreground_action(pkg, label) or label
        log.info("visual_note: %s → %s (pkg=%s)", label, tag, pkg)
        reply, _ja = agent.proactive_from_visual(tag)
        if reply:
            _pending.append(_render(agent, reply))
            log.info("visual note queued: %s (%s → %s)", reply[:50], label, tag)
            return json.dumps({"ok": True, "detail": f"联想已排队: {reply[:30]}…"})
        return json.dumps({"ok": True, "detail": "无匹配记忆，未发起"})
    except Exception as e:
        log.warning("visual_note failed: %s", e)
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})


def _classify_foreground_action(pkg: str, label: str) -> str:
    """包名+app 名 → 动作短语（一次低成本 LLM 调用，失败回退 None→用 app 名）。"""
    try:
        ui = (boot.config or {}).get("ui") or {}
        if not ui.get("foreground_action_llm", True):
            return ""
        prompt = (
            f"用户正在使用安卓应用「{label}」（包名 {pkg}）。"
            "用 2-4 个字的中文动词短语描述他在干什么，比如「聊微信」「刷B站」"
            "「逛淘宝」「打游戏」。只输出短语本身，不要解释。"
        )
        a = getattr(boot, "agent", None)
        # 推理模型小预算烧 reasoning 返回空（R0_SPEC 6 教训）：256 起步
        raw = a.llm.chat([{"role": "user", "content": prompt}], max_tokens=256, temperature=0.2) if a else ""
        tag = str(raw or "").strip().strip('"').strip("'").strip()
        return tag[:16] if tag and len(tag) >= 2 else ""
    except Exception as e:
        log.debug("foreground action classify failed: %s", e)
        return ""


def portrait_path() -> str:
    """当前角色立绘的绝对路径（视觉小说舞台用；空串=无图，UI 回退纯色舞台）。

    assets/portraits/<char>.jpg 由 Kotlin 在 boot 时解到 filesDir/portraits/
    （见 sync_assets.py / MainActivity），这里按角色名匹配、目录唯一文件兜底。
    """
    try:
        root = Path(getattr(boot, "root", "."))
        d = root / "portraits"
        if not d.is_dir():
            return ""
        char = ""
        try:
            char = (boot.config or {}).get("active_character", "") or ""
        except Exception:
            pass
        for f in sorted(d.iterdir()):
            if char and char in f.stem:
                return str(f)
        files = [f for f in sorted(d.iterdir()) if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
        return str(files[0]) if files else ""
    except Exception:
        log.exception("portrait_path failed")
        return ""


def history(limit: int = 80) -> str:
    """最近 N 条对话（id 升序）。主动消息核心已落库（record_proactive_message），
    这里一起带出——聊天 UI 以库为准，无需单独通道。"""
    agent = getattr(boot, "agent", None)
    if agent is None:
        return json.dumps({"ok": False, "messages": []})
    try:
        rows = agent.memory.recent_messages(limit=int(limit))
        root = Path(getattr(boot, "root", "."))
        out = []
        for r in rows:
            try:
                att = [str(root / "photos" / n) for n in json.loads(r.get("attachments") or "[]")]
            except Exception:
                att = []
            out.append({"id": int(r["id"]), "me": r["role"] == "user",
                        "time": r["created_at"],
                        "tone": r.get("tone_at", ""), "mood": r.get("mood_at", "") or "",
                        # 有附件真图时剥掉 [图片] 占位（占位是给纯文本记忆用的，别渲染出来）
                        "text": r["content"].replace(" [图片]", "").strip() if att else r["content"],
                        "images": att})
        return json.dumps({"ok": True, "messages": out}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e), "messages": []})


def chat(text: str, image_paths: str = "[]") -> str:
    """一轮对话（同步阻塞——真 UI 阶段换协程+回调）。

    image_paths: JSON 数组字符串，本地图片文件路径（≤4 张）。核心管线
    make_image_payload 校验（类型/大小/炸弹检测），agent.handle 以
    OpenAI 多模态块发给 vision_model（llm.client 按含图自动切模型），
    历史/记忆落 [图片] 占位；附件文件名存 filesDir/photos/ 并写进
    messages.attachments（历史重载渲染用——cacheDir 随时会被系统清）。
    """
    agent = getattr(boot, "agent", None)
    if agent is None:
        return json.dumps({"ok": False, "error": "未初始化"})
    import time as _t
    boot._last_user_activity = _t.time()  # 离线思考的静默窗口锚点
    try:
        paths = [str(p) for p in json.loads(image_paths or "[]")][:4]
        images: list[str] = []
        names: list[str] = []
        if paths:
            from veranima.core.image_payload import make_image_payload
            photos = Path(getattr(boot, "root", ".")) / "photos"
            photos.mkdir(exist_ok=True)
            for pth in paths:
                raw = Path(pth).read_bytes()
                payload = make_image_payload(raw, source=pth)
                images.append(payload.data_url)
                # 文件名带扩展名（按 content_type 推；历史重载按文件读，扩展名只为人可读）
                ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
                       "image/webp": "webp"}.get(payload.content_type, "bin")
                name = f"{int(_t.time() * 1000000)}.{ext}"
                (photos / name).write_bytes(raw)
                names.append(name)
        attachments = json.dumps(names, ensure_ascii=False) if names else ""
        res = agent.handle(text, images=images or None, channel="im", attachments=attachments)
        # 与 QQ 统一出口一致：Reply 对象优先、渲染后才可见（防内部痕迹外漏）
        out = {"ok": True, "reply": _render(agent, res.reply_obj or res.reply), "portrait": res.portrait,
               "energy": round(res.energy, 2), "tone": res.tone or ""}
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        tb = traceback.format_exc(limit=6)
        log.error("chat failed:\n%s", tb)
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}", "trace": tb}, ensure_ascii=False)
