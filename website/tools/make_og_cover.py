"""生成 tiyouju.com 的 Open Graph 分享图（1200x630 PNG）。

用法（在仓库根目录执行）：

    python website/tools/make_og_cover.py

产物：website/public/assets/og-cover.png

为什么单独生成一张 PNG，而不是直接拿 assets/review-workspace.webp 当 og:image：
微信、微博、QQ 等分享卡片大多只认 PNG / JPEG，不吃 webp；
而且 og:image 需要固定 1200x630 的比例，截图原图是 1360x737。
所以这里用截图作底图，左侧压一块深绿面板写文案，输出标准分享图。

改完文案或换了主截图后重跑本脚本，再 `wrangler deploy` 即可。
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS = REPO_ROOT / "website" / "public" / "assets"
OUT = ASSETS / "og-cover.png"

W, H = 1200, 630

TEAL_DEEP = (20, 58, 54)
TEAL_MID = (30, 84, 76)
GOLD = (214, 166, 93)
SURFACE = (255, 252, 246)
MUTED_LIGHT = (198, 222, 215)
FAINT = (176, 206, 198)

FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def cover_fit(im: Image.Image, w: int, h: int) -> Image.Image:
    """等比缩放铺满画布，超出部分居中裁掉。"""
    scale = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    left = (im.width - w) // 2
    top = (im.height - h) // 2
    return im.crop((left, top, left + w, top + h))


def main() -> None:
    # 底图：产品实拍截图铺满
    base = Image.open(ASSETS / "review-workspace.webp").convert("RGB")
    base = cover_fit(base, W, H)

    # 左侧实心面板 + 向右柔和过渡，保证文字清晰
    solid_end = 628
    fade_end = 930
    mask = Image.new("L", (W, H))
    for x in range(W):
        if x <= solid_end:
            a = 252
        elif x >= fade_end:
            a = 0
        else:
            t = (x - solid_end) / (fade_end - solid_end)
            a = int(252 * (1 - t) ** 1.5)
        for y in range(H):
            mask.putpixel((x, y), a)

    canvas = Image.composite(Image.new("RGB", (W, H), TEAL_DEEP), base, mask)

    # 面板内细微竖向渐变，避免死板
    sheen = Image.new("L", (W, H), 0)
    for y in range(H):
        v = int(16 * (y / (H - 1)))
        for x in range(solid_end):
            sheen.putpixel((x, y), v)
    canvas = Image.composite(Image.new("RGB", (W, H), TEAL_MID), canvas, sheen)

    d = ImageDraw.Draw(canvas)

    # 顶部细金线
    d.rectangle((0, 0, W, 6), fill=GOLD)

    # 左上：图标 + 站名
    icon = Image.open(ASSETS / "app-icon.png").convert("RGBA").resize((72, 72), Image.LANCZOS)
    canvas.paste(icon, (60, 46), icon)
    d.text((150, 54), "题有据  TiYouJu", font=font(FONT_BOLD, 38), fill=SURFACE)
    d.text((152, 100), "Windows 10 / 11 · 本地优先 · AGPL-3.0 开源免费",
           font=font(FONT_REG, 20), fill=MUTED_LIGHT)

    # 主标题
    d.text((60, 196), "把零散试卷，", font=font(FONT_BOLD, 52), fill=SURFACE)
    d.text((60, 258), "变成你的私家题库。", font=font(FONT_BOLD, 52), fill=GOLD)

    d.text((62, 348), "Word / PDF / 拍照题直收本机", font=font(FONT_REG, 25), fill=MUTED_LIGHT)
    d.text((62, 386), "每道题都留原卷出处，随时一键组卷导出",
           font=font(FONT_REG, 25), fill=MUTED_LIGHT)

    d.text((62, 448), "免 OCR 直读 · 毫秒级离线查重 · 8 层目录万题秒搜",
           font=font(FONT_REG, 21), fill=FAINT)
    d.text((62, 482), "分节赋分 · A4 / A3 双栏密封线 · 题库始终留在本机",
           font=font(FONT_REG, 21), fill=FAINT)

    # 网址
    d.text((60, 534), "tiyouju.com", font=font(FONT_BOLD, 34), fill=GOLD)

    canvas.save(OUT, "PNG", optimize=True)
    print(f"已生成 {OUT}  {OUT.stat().st_size} bytes  {W}x{H}")


if __name__ == "__main__":
    main()
