"""启动桌宠（开发模式）：只启动 Electron 壳——壳会自动 spawn Python 核心。

桌宠核心会在同一 Python 进程内挂载 QQ adapter，共用 Agent/记忆/锁。
退出（托盘退出 / Ctrl+C）时壳及其核心/TTS 一起停。

用法：推荐双击项目根目录的 run_pet.vbs（无控制台窗口）；开发调试可运行：python scripts/run_pet.py
"""
import os
import subprocess
import sys
import time

V = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = r"C:\Program Files\nodejs\node.exe"
ELECTRON_CLI = os.path.join(V, "pet", "node_modules", "electron", "cli.js")
ELECTRON_EXE = os.path.join(V, "pet", "node_modules", "electron", "dist", "electron.exe")
NODE_MODULES = os.path.join(V, "pet", "node_modules")
PY = os.path.join(V, ".venv", "Scripts", "python.exe")

# Windows：子进程不弹控制台窗口
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_INSTANCE_MUTEX = None


def acquire_single_instance() -> bool:
    """入口级单实例保护；Electron 锁之前阻止 QQ/诊断子进程重复启动。"""
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _INSTANCE_MUTEX = kernel32.CreateMutexW(None, False, "Local\\VeranimaPetLauncher")
    return bool(_INSTANCE_MUTEX) and kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _startupinfo():
    """隐藏 Windows 子进程窗口；Linux/macOS 返回 None。"""
    if os.name != "nt":
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return info


def _run_hidden(args, **kwargs):
    """运行诊断命令且不创建 console window。"""
    kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    kwargs.setdefault("startupinfo", _startupinfo())
    return subprocess.run(args, **kwargs)


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


def preflight_ports() -> None:
    """启动前检查核心/TTS 端口：残留孤儿进程（壳被强杀后 Windows 不回收子进程）
    会占住 8765/9880 导致核心 bind 失败死循环——这里自动清理。

    ponytail: 按端口找 PID，再按命令行确认是 pet_server/tts.server 才杀（防误杀）。
    """
    import re

    def find_listener(port: int) -> list[str]:
        try:
            out = _run_hidden(["netstat", "-ano"], capture_output=True, timeout=30).stdout
            return [l.split()[-1] for l in out.decode("gbk", errors="replace").splitlines()
                    if f":{port}" in l and "LISTENING" in l]
        except Exception:
            return []

    def is_our_process(pid: str) -> bool:
        try:
            # wmic 在新 Windows 已弃用，用 PowerShell CIM（同样零依赖）
            out = _run_hidden(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
                capture_output=True, timeout=30).stdout.decode("gbk", errors="replace")
            return "pet_server" in out or "tts.server" in out
        except Exception:
            return False

    for port, name in ((8765, "核心"), (9880, "TTS 服务")):
        for pid in find_listener(port):
            if is_our_process(pid):
                _run_hidden(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=30)
                print(f"已清理残留{name}进程 (PID {pid}, 端口 {port})")


def main() -> None:
    if not acquire_single_instance():
        return
    check_node_modules()
    preflight_ports()  # 清理残留核心/TTS 进程（防端口占用死循环）
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    # 1. Electron 壳（GUI 进程，无控制台）
    # 直接启动 Electron GUI 可执行文件，避免 node.exe CLI 中间层产生 console。
    electron_cmd = [ELECTRON_EXE, "."] if os.path.exists(ELECTRON_EXE) else [NODE, ELECTRON_CLI, "."]
    shell = subprocess.Popen(
        electron_cmd, cwd=os.path.join(V, "pet"), env=env,
        creationflags=CREATE_NO_WINDOW, startupinfo=_startupinfo(),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    procs = [shell]

    print("桌宠已启动（核心内共用 Agent 挂载 QQ；托盘退出后全部进程随之退出）")
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
        # Electron 正常退出应已清理核心/TTS；这里按端口再兜底一次，确保
        # uv launcher 的孙进程不会在托盘图标消失后继续占用 8765/9880。
        preflight_ports()


if __name__ == "__main__":
    main()
