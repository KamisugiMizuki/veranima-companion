"""从 PC 的 config/config.yaml 生成冬乃安卓的 filesDir/config.yaml（adb push 用）。

密钥只进设备不进 git。剥离：qq/tts/stt/hermes/dsh/search(searxng 段整删)、
input_device_id 等 Windows 专有键。
"""
import sys
import yaml
from pathlib import Path

KEEP_TOP = {"llm", "memory", "proactive", "output", "appearance", "companion_continuity",
            "relationship_tension", "virtual_life", "tension", "promise", "creation",
            "stickers", "web", "date_api", "holiday"}


def main(root: Path, out: Path):
    cfg = yaml.safe_load((root / "config" / "config.yaml").read_text(encoding="utf-8"))
    a = {k: v for k, v in cfg.items() if k in KEEP_TOP}
    # LLM profiles 只留 default（lm-studio 等 PC 专属组不进设备）
    prof = (a.get("llm") or {}).get("profiles")
    if isinstance(prof, dict):
        keep = {k: v for k, v in prof.items() if k == str((a.get("llm") or {}).get("active_profile", "default"))}
        a["llm"]["profiles"] = keep or prof
    # 角色卡：设备上的相对路径
    a["character_card"] = "characters/lin/character.json"  # 默认卡（用户可在设置页切换）
    # embedding 保持 openai 远程（dashscope），专用 key 条目原样带走
    # search 段换成 bocha（安卓专用），key 也带走
    s = cfg.get("search", {})
    if s.get("provider") == "bocha":
        a["search"] = {"enabled": True, "provider": "bocha", "api_key": s.get("api_key", ""),
                       "base_url": "https://api.bochaai.com/v1/web-search",
                       "timeout_seconds": 10, "cache_ttl_seconds": 900,
                       "allow_implicit_freshness_search": True, "semantic_locator_enabled": True}
    else:
        a["search"] = {"enabled": False}
    # memory：路径由 bridge 注入 db_path；本地模型键全删
    mem = dict(a.get("memory") or {})
    mem.pop("db_path", None)
    a["memory"] = mem
    # 主动发言：安卓端不设人为频率闸门（2026-08-29 用户拍板"随心发言"）。
    # gate 语义：max_per_day<=0 / min_gap_minutes<=0 / quiet_hours_enabled=false = 不限制。
    # quiet hours 的睡眠模拟职责已由虚拟日程（blocks 睡眠段）承担，不重复设闸。
    p = dict(a.get("proactive") or {})
    p["max_per_day"] = 0
    p["min_gap_minutes"] = 0
    p["quiet_hours_enabled"] = False
    ch = {k: {**v, "max_per_day": 0, "min_gap_minutes": 0}
          for k, v in (p.get("channels") or {}).items()}
    ch["im"] = {"enabled": True, "max_per_day": 0, "min_gap_minutes": 0}
    p["channels"] = ch
    a["proactive"] = p
    # 落库通道标签（2026-08-31 用户裁决）：安卓无 QQ，messages/feedback 的
    # channel 列写 "im" 而非 core 默认的 "qq"（仅显示标签，闸门分桶不变）
    a["channel_tag"] = "im"
    out.write_text(yaml.safe_dump(a, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"生成 {out}（llm={a['llm'].get('model')} emb={mem.get('embedding_model')} "
          f"search={a['search']['provider']}）")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3],
         Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "device-config.yaml")
