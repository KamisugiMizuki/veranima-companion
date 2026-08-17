"""启动桌宠（开发模式）：只启动 Electron 壳——壳会自动 spawn Python 核心。

桌宠启动时若 config.yaml 的 qq.enabled=true，会一并拉起 QQ bot（后台无窗口）。
退出（托盘退出 / Ctrl+C）时全部进程一起停。

用法：python scripts/run_pet.py
"""
import os
import subprocess
import sys
import time

V = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = r"C:\Program Files\nodejs\node.exe"
ELECTRON_CLI = os.path.join(V, "pet", "node_modules", "electron", "cli.js")
NODE_MODULES = os.path.join(V, "pet", "node_modules")
PY = os.path.join(V, ".venv", "Scripts", "python.exe")

# Windows：子进程不弹控制台窗口
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def check_node_modules() -> None:
    """新 clone 环境没有 node_modules（gitignore 排除）→ 提示建 junction 复用。"""
    missing = []
    for pkg, probe in (("electron", "electron/cli.js"), ("ws", "ws/package.json")):
        if not os.path.exists(os.path.join(NODE_MODULES, probe)):
            missing.append(pkg)
    if missing:
        print("缺少桌宠壳依赖:", ", ".join(missing))
        print("请先建立 junction 复用 koodo-reader 的 node_modules：")
        print('  cmd /c mklink /J "%s\\electron" "%s"' % (
            NODE_MODULES, r"D:\Hermes_workspace\koodo-reader\node_modules\electron"))
        print('  cmd /c mklink /J "%s\\ws" "%s"' % (
            NODE_MODULES, r"D:\Hermes_workspace\koodo-reader\node_modules\ws"))
        sys.exit(1)


def qq_enabled() -> bool:
    """config.yaml 的 qq.enabled（桌宠启动时是否连带拉起 QQ bot）。"""
    try:
        import yaml
        cfg = yaml.safe_load(open(os.path.join(V, "config", "config.yaml"), encoding="utf-8"))
        return bool((cfg.get("qq") or {}).get("enabled"))
    except Exception:
        return False


def main() -> None:
    check_node_modules()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    # 1. Electron 壳（GUI 进程，无控制台）
    shell = subprocess.Popen([NODE, ELECTRON_CLI, "."], cwd=os.path.join(V, "pet"), env=env)
    procs = [shell]

    # 2. QQ bot（config qq.enabled=true 时一并拉起；后台无窗口）
    qq_proc = None
    if qq_enabled():
        if os.path.exists(PY):
            qq_proc = subprocess.Popen(
                [PY, "-m", "veranima.qq"],
                cwd=V, env=env, creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            procs.append(qq_proc)
            print("QQ bot 已随桌宠启动（config qq.enabled=true）")
        else:
            print("[warn] .venv 缺失，QQ bot 未启动")

    print("桌宠已启动（壳会自动拉起 Python 核心；托盘退出后全部进程随之退出）")
    try:
        # 等待壳退出（托盘退出 = 真正退出）；pythonw 下无 Ctrl+C
        while shell.poll() is None:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        # 全部一起停（壳 → QQ）
        for p in procs:
            if p.poll() is None:
                p.terminate()
        time.sleep(1)
        for p in procs:
            if p.poll() is None:
                p.kill()


if __name__ == "__main__":
    main()
