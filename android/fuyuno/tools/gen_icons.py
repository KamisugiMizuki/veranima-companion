"""从 C:/Users/Kamisugi/Downloads/Veranima.png 生成安卓 launcher 图标（一次性脚本，产物入库）。
旧源 windows_2k_hime.jpg（桌宠时代）；2026-09-01 品牌统一为 Veranima。

中心裁方 → 各尺寸：安卓 mipmap mdpi~xxxhdpi(48..192) + 512 playstore；
Electron：256px PNG + 多尺寸 .ico。
"""
from pathlib import Path
from PIL import Image

SRC = Path("C:/Users/Kamisugi/Downloads/Veranima.png")
ROOT = Path(__file__).resolve().parent.parent  # android/fuyuno
PROJ = ROOT.parent.parent                      # veranima 根

im = Image.open(SRC).convert("RGB")
side = min(im.size)
box = ((im.width - side) // 2, (im.height - side) // 2,
       (im.width + side) // 2, (im.height + side) // 2)
im = im.crop(box)
print("source:", SRC, "->", im.size)

for dpi, sz in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)):
    d = ROOT / ("app/src/main/res/mipmap-" + dpi)
    d.mkdir(parents=True, exist_ok=True)
    im.resize((sz, sz), Image.LANCZOS).save(d / "ic_launcher.png")

im.resize((512, 512), Image.LANCZOS).save(ROOT / "app/src/main/res/mipmap-xxxhdpi/ic_launcher_playstore.png")

assets = PROJ / "pet/assets"
assets.mkdir(parents=True, exist_ok=True)
png256 = im.resize((256, 256), Image.LANCZOS)
png256.save(assets / "icon.png")
png256.save(assets / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("done:", sorted(p.relative_to(PROJ) for p in (ROOT / "app/src/main/res").rglob("ic_launcher*") if p.is_file()),
      "pet/assets/icon.png/.ico")
