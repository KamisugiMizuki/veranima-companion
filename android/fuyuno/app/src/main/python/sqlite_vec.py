"""sqlite-vec 的安卓侧替身：pip 包无 Android wheel，改加载 jniLibs 里的 loadable .so。

libvec0.so 路径经 FUYUNO_VECDIR 环境变量传入（bridge.boot 在 create_agent 之前写入；
chaquopy 解包目录只读所以不能用 side-file）。load() 显式指定入口点 sqlite3_vec_init
（文件名 libvec0.so 的默认推导 sqlite3_vec0_init 与实际导出符号不符，strings 实测）。
仅在 APK 内存在（app pythonpath 优先于 assets 里的核心 src），Windows 端走 pip 包。
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)
_DIR_FILE = Path(__file__).resolve().parent / ".vecdir"
_VEC_ENV = "FUYUNO_VECDIR"
ENTRY = "sqlite3_vec_init"


def load(connection):
    import os
    native_dir = os.environ.get("FUYUNO_VECDIR", "")
    if not native_dir:
        raise RuntimeError("sqlite_vec 未初始化（FUYUNO_VECDIR 未设置——boot 未传 nativeLibraryDir？）")
    so = Path(native_dir) / "libvec0.so"
    if not so.exists():
        raise RuntimeError(f"sqlite-vec so 不存在: {so}")
    connection.enable_load_extension(True)
    connection.load_extension(str(so), ENTRY)
    logger.info("sqlite-vec loaded from %s (entry=%s)", so, ENTRY)
