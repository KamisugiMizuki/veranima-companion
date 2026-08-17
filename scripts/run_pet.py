"""启动桌宠双端（开发模式）：Python 核心 WS 服务 + Electron 壳。

用法：python scripts/run_pet.py
先启动核心 WS（8765），再启动 Electron 壳；Ctrl+C 一起退出。
"""
import os
import signal
import subprocess
import sys
import time

V = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(V, ".venv", "Scripts", "python.exe")
NODE = r"C:\Program Files\nodejs\node.exe"
PET_DIR = os.path.join(V, "pet")
NODE_MODULES = os.path.join(PET_DIR, "node_modules")
ELECTRON_CLI = os.path.join(NODE_MODULES, "electron", "cli.js")

procs = []


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


def main() -> None:
    check_node_modules()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    core = subprocess.Popen([PY, "-m", "veranima.pet_server", "--port", "8765"],
                            cwd=V, env=env)
    procs.append(core)
    time.sleep(1.5)  # 等核心 WS 就绪
    shell = subprocess.Popen([NODE, ELECTRON_CLI, "."], cwd=os.path.join(V, "pet"), env=env)
    procs.append(shell)
    print("桌宠双端已启动（Ctrl+C 退出）")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    main()
