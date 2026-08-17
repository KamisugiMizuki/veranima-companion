"""生成桌宠四态占位图（M3_SPEC 3.4 MVP）：idle/speaking/thinking/sleeping。

Pillow 程序化绘制：圆脸角色 + 眼睛/嘴形四态区分。
- idle：睁眼、微笑
- speaking：张嘴（说话）
- thinking：侧视、嘴微张（省略号气泡）
- sleeping：闭眼（曲线）、嘴角放松

输出到 assets/pet/*.png，200x200 透明底。
"""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "pet")
SIZE = 200
CENTER = (100, 100)


def _face(draw, r=70):
    """圆脸 + 腮红 + 刘海。"""
    x, y = CENTER
    draw.ellipse([x - r, y - r + 10, x + r, y + r + 10], fill=(255, 235, 220, 255))
    # 刘海（半圆）
    draw.pieslice([x - r, y - r - 20, x + r, y + r - 40], 180, 360, fill=(120, 90, 200, 255))
    # 腮红
    draw.ellipse([x - 55, y + 10, x - 35, y + 30], fill=(255, 180, 180, 180))
    draw.ellipse([x + 35, y + 10, x + 55, y + 30], fill=(255, 180, 180, 180))


def _eyes(draw, mode):
    x, y = CENTER
    if mode == "sleeping":
        # 闭眼：向下弯曲的弧线
        draw.arc([x - 45, y - 15, x - 15, y + 15], 180, 360, fill=(60, 60, 80, 255), width=4)
        draw.arc([x + 15, y - 15, x + 45, y + 15], 180, 360, fill=(60, 60, 80, 255), width=4)
    elif mode == "thinking":
        # 侧视：左右不对称（右眼看向一边）
        draw.ellipse([x - 48, y - 20, x - 18, y + 10], fill=(60, 60, 80, 255))
        draw.ellipse([x + 22, y - 14, x + 46, y + 8], fill=(60, 60, 80, 255))  # 半闭
    else:
        # 睁眼
        draw.ellipse([x - 48, y - 20, x - 18, y + 10], fill=(60, 60, 80, 255))
        draw.ellipse([x + 18, y - 20, x + 48, y + 10], fill=(60, 60, 80, 255))
        # 高光
        draw.ellipse([x - 42, y - 16, x - 34, y - 8], fill=(255, 255, 255, 255))
        draw.ellipse([x + 24, y - 16, x + 32, y - 8], fill=(255, 255, 255, 255))


def _mouth(draw, mode):
    x, y = CENTER
    if mode == "speaking":
        # 张嘴（椭圆）
        draw.ellipse([x - 14, y + 42, x + 14, y + 62], fill=(180, 90, 90, 255))
    elif mode == "thinking":
        # 微张嘴 + 省略号气泡
        draw.ellipse([x - 8, y + 44, x + 8, y + 56], fill=(180, 90, 90, 255))
        for i, dx in enumerate((-20, 0, 20)):
            draw.ellipse([x + dx - 4, y - 58, x + dx + 4, y - 50], fill=(120, 120, 140, 255))
    elif mode == "sleeping":
        # 放松小嘴
        draw.arc([x - 10, y + 40, x + 10, y + 52], 0, 180, fill=(180, 90, 90, 255), width=3)
    else:
        # 微笑
        draw.arc([x - 16, y + 34, x + 16, y + 56], 20, 160, fill=(180, 90, 90, 255), width=4)


def main():
    os.makedirs(OUT, exist_ok=True)
    for mode in ("idle", "speaking", "thinking", "sleeping"):
        img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        _face(draw)
        _eyes(draw, mode)
        _mouth(draw, mode)
        path = os.path.join(OUT, f"{mode}.png")
        img.save(path)
        print("wrote", path)


if __name__ == "__main__":
    main()
