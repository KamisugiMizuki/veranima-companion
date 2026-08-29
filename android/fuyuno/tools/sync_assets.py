"""把当前角色立绘同步进 APK assets（构建前跑一次）。

chaquopy 的 python 读不了 Android assets，故立绘的解包在 Kotlin 侧
（MainActivity boot 时 assets → filesDir/portraits/），本脚本只负责
把源图放进 assets，文件名=角色名（bridge.portrait_path 按目录唯一文件返回）。
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # .../veranima（tools→fuyuno→android→root）
SRC = ROOT / "characters" / "lin" / "portraits" / "lin_halfbody.jpg"
DST_DIR = Path(__file__).resolve().parents[1] / "app" / "src" / "main" / "assets" / "portraits"

def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"立绘源不存在: {SRC}")
    DST_DIR.mkdir(parents=True, exist_ok=True)
    dst = DST_DIR / "lin.jpg"
    shutil.copyfile(SRC, dst)
    print(f"portrait synced: {dst} ({dst.stat().st_size} bytes)")

if __name__ == "__main__":
    main()
