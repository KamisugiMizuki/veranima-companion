"""启动桌宠（开发模式）：只启动 Electron 壳——壳会自动 spawn Python 核心。

用法：python scripts/run_pet.py
Ctrl+C 退出（壳退出时核心一起停）。
"""
import os
import subprocess
import sys
import time

V = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = r"C:\Program Files\nodejs\node.exe"
ELECTRON_CLI = os.path.join(V, "pet", "node_modules", "electron", "cli.js")
NODE_MODULES = os.path.join(V, "pet", "node_modules")


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
    shell = subprocess.Popen([NODE, ELECTRON_CLI, "."], cwd=os.path.join(V, "pet"), env=env)
    print("桌宠已启动（壳会自动拉起 Python 核心；Ctrl+C 退出）")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if shell.poll() is None:
            shell.terminate()


if __name__ == "__main__":
    main()
