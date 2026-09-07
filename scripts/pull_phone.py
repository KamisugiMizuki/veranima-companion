# -*- coding: utf-8 -*-
"""一次性全量导出设备端应用数据（真机/MuMu 通用）。

tar 流式打包 files/（排除 chaquopy 运行时——那是 APK 自带的，每次一样），
exec-out 单管道拉回本地解开。db 先 checkpoint 进主文件、三件套一起带走。
用法: python scripts/pull_phone.py [serial]   （缺省=唯一在线非 emu 设备）
     python scripts/pull_phone.py 127.0.0.1:16448   （指定 MuMu）
"""
import os
import subprocess
import sys
import tarfile
import time

ADB = r"D:\Android-sdk\platform-tools\adb.exe"
PKG = "io.github.kamisugimizuki.veranima"


def pick_serial():
    out = subprocess.run([ADB, "devices"], capture_output=True, text=True).stdout
    devs = [l.split("\t")[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
    if len(devs) == 1:
        return devs[0]
    real = [d for d in devs if ":" not in d]
    if real:
        return real[0]  # 多台时真机优先（emu 数据我们刚造过，不稀罕）
    return devs[0]


def main():
    serial = sys.argv[1] if len(sys.argv) > 1 else pick_serial()
    dst = os.path.join(os.path.dirname(__file__), "..", "exports",
                       f"phone_{serial.split(':')[0]}_{time.strftime('%m%d_%H%M')}")
    os.makedirs(dst, exist_ok=True)
    # 不 checkpoint（设备无 sqlite3，老坑）：tar 直接带走 db+wal+shm 三件套，
    # 本地 sqlite 自动回放 WAL。撕裂风险=极小窗口，triage 报错就重拉。
    r = subprocess.run(
        [ADB, "-s", serial, "exec-out",
         f"run-as {PKG} tar cf - -C files --exclude chaquopy ."],
        capture_output=True)
    blob = r.stdout
    tf = os.path.join(dst, "_data.tar")
    open(tf, "wb").write(blob)
    with tarfile.open(tf) as t:
        t.extractall(dst, filter="data")
    os.remove(tf)
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(dst) for f in fs)
    print(f"serial={serial}  {len(blob)} bytes tar -> {os.path.normpath(dst)}  "
          f"({total/1e6:.1f} MB unpacked)")
    for dp, _, fs in os.walk(dst):
        for f in sorted(fs):
            p = os.path.relpath(os.path.join(dp, f), dst)
            print(f"  {os.path.getsize(os.path.join(dp, f)):>10}  {p}")


if __name__ == "__main__":
    main()
