"""生成桌宠立绘（R4_SPEC 2.2/2.3）：四态基础图 + 表情词表图 + 立绘说明.txt。

Pillow 程序化绘制：圆脸角色 + 眼睛/嘴形区分。
- 四态（渲染回退链基础态）：idle（睁眼微笑）/ speaking（张嘴）/ thinking（侧视+省略号）/ sleeping（闭眼）
- 表情（avatar.expressions 词表）：stand（站立待机=idle 同款）/ happy（开心脸红）/ puzzled（疑惑）/
  sad（难过）/ surprised（惊讶）
- 立绘说明.txt：每行「文件前缀 标签」，供批量映射（R4_SPEC 2.3）

输出到 assets/pet/*.png + assets/pet/立绘说明.txt，200x200 透明底。
"""
import os
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "assets", "pet")
SIZE = 200
CENTER = (100, 100)

# 立绘说明.txt 内容（R4_SPEC 2.3：每行「文件前缀 标签」）
PORTRAIT_DESCRIPTION = """stand 站立待机
happy 开心脸红
puzzled 疑惑
sad 难过
surprised 惊讶
"""


def _face(draw, r=70):
    """圆脸 + 腮红 + 刘海。"""
    x, y = CENTER
    draw.ellipse([x - r, y - r + 10, x + r, y + r + 10], fill=(255, 235, 220, 255))
    draw.pieslice([x - r, y - r - 20, x + r, y + r - 40], 180, 360, fill=(120, 90, 200, 255))


def _cheeks(draw, strong=False):
    """腮红；strong=明显（开心脸红用）。"""
    x, y = CENTER
    alpha = 220 if strong else 140
    draw.ellipse([x - 55, y + 10, x - 35, y + 30], fill=(255, 160, 160, alpha))
    draw.ellipse([x + 35, y + 10, x + 55, y + 30], fill=(255, 160, 160, alpha))


def _eyes(draw, mode):
    x, y = CENTER
    if mode == "sleeping":
        draw.arc([x - 45, y - 15, x - 15, y + 15], 180, 360, fill=(60, 60, 80, 255), width=4)
        draw.arc([x + 15, y - 15, x + 45, y + 15], 180, 360, fill=(60, 60, 80, 255), width=4)
    elif mode == "happy":
        # 眯眼笑（弯月）
        draw.arc([x - 48, y - 10, x - 18, y + 20], 180, 360, fill=(60, 60, 80, 255), width=4)
        draw.arc([x + 18, y - 10, x + 48, y + 20], 180, 360, fill=(60, 60, 80, 255), width=4)
    elif mode == "puzzled":
        # 一高一低（疑惑）
        draw.ellipse([x - 48, y - 20, x - 18, y + 10], fill=(60, 60, 80, 255))
        draw.ellipse([x + 18, y - 8, x + 48, y + 20], fill=(60, 60, 80, 255))  # 右眼低
    elif mode == "sad":
        # 下垂眼 + 泪光
        draw.arc([x - 48, y - 8, x - 18, y + 22], 180, 360, fill=(60, 60, 80, 255), width=4)
        draw.arc([x + 18, y - 8, x + 48, y + 22], 180, 360, fill=(60, 60, 80, 255), width=4)
        draw.ellipse([x - 40, y + 2, x - 32, y + 10], fill=(160, 200, 255, 255))  # 泪光
    elif mode == "surprised":
        # 大圆眼
        draw.ellipse([x - 50, y - 24, x - 16, y + 10], fill=(60, 60, 80, 255))
        draw.ellipse([x + 16, y - 24, x + 50, y + 10], fill=(60, 60, 80, 255))
        draw.ellipse([x - 43, y - 19, x - 35, y - 11], fill=(255, 255, 255, 255))
        draw.ellipse([x + 23, y - 19, x + 31, y - 11], fill=(255, 255, 255, 255))
    elif mode == "thinking":
        draw.ellipse([x - 48, y - 20, x - 18, y + 10], fill=(60, 60, 80, 255))
        draw.ellipse([x + 22, y - 14, x + 46, y + 8], fill=(60, 60, 80, 255))  # 半闭
    else:
        # 睁眼（idle/stand/speaking）
        draw.ellipse([x - 48, y - 20, x - 18, y + 10], fill=(60, 60, 80, 255))
        draw.ellipse([x + 18, y - 20, x + 48, y + 10], fill=(60, 60, 80, 255))
        draw.ellipse([x - 42, y - 16, x - 34, y - 8], fill=(255, 255, 255, 255))
        draw.ellipse([x + 24, y - 16, x + 32, y - 8], fill=(255, 255, 255, 255))


def _mouth(draw, mode):
    x, y = CENTER
    if mode == "speaking":
        draw.ellipse([x - 14, y + 42, x + 14, y + 62], fill=(180, 90, 90, 255))
    elif mode == "surprised":
        # 惊讶张嘴（大椭圆）
        draw.ellipse([x - 16, y + 40, x + 16, y + 64], fill=(180, 90, 90, 255))
    elif mode == "happy":
        # 开心大笑（圆弧 + 上翘）
        draw.arc([x - 18, y + 32, x + 18, y + 60], 200, 340, fill=(180, 90, 90, 255), width=4)
    elif mode == "sad":
        # 难过下弯
        draw.arc([x - 12, y + 44, x + 12, y + 62], 20, 160, fill=(180, 90, 90, 255), width=3)
    elif mode == "puzzled":
        draw.arc([x - 10, y + 40, x + 10, y + 54], 0, 180, fill=(180, 90, 90, 255), width=3)  # 小o
    elif mode == "thinking":
        draw.ellipse([x - 8, y + 44, x + 8, y + 56], fill=(180, 90, 90, 255))
        for i, dx in enumerate((-20, 0, 20)):
            draw.ellipse([x + dx - 4, y - 58, x + dx + 4, y - 50], fill=(120, 120, 140, 255))
    elif mode == "sleeping":
        draw.arc([x - 10, y + 40, x + 10, y + 52], 0, 180, fill=(180, 90, 90, 255), width=3)
    else:
        draw.arc([x - 16, y + 34, x + 16, y + 56], 20, 160, fill=(180, 90, 90, 255), width=4)


# 图 → (眼睛模式, 嘴模式, 强腮红)
CONFIGS = {
    "idle": ("idle", "idle", False),
    "speaking": ("idle", "speaking", False),
    "thinking": ("thinking", "thinking", False),
    "sleeping": ("sleeping", "sleeping", False),
    # 表情词表（R4_SPEC 2.2）
    "stand": ("idle", "idle", False),          # 站立待机 = 默认状态
    "happy": ("happy", "happy", True),         # 开心脸红
    "puzzled": ("puzzled", "puzzled", False),  # 疑惑
    "sad": ("sad", "sad", False),              # 难过
    "surprised": ("surprised", "surprised", False),  # 惊讶
}


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, (eye, mouth, strong) in CONFIGS.items():
        img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        _face(draw)
        _cheeks(draw, strong=strong)
        _eyes(draw, eye)
        _mouth(draw, mouth)
        path = os.path.join(OUT, f"{name}.png")
        img.save(path)
        print("wrote", path)
    # 立绘说明.txt（R4_SPEC 2.3）
    desc = os.path.join(OUT, "立绘说明.txt")
    with open(desc, "w", encoding="utf-8") as f:
        f.write(PORTRAIT_DESCRIPTION)
    print("wrote", desc)


if __name__ == "__main__":
    main()
