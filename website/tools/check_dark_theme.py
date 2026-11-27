"""官网深色旗舰改版的视觉与回归检查。

在仓库根目录执行：

    .venv/Scripts/python.exe website/tools/check_dark_theme.py

检查项：
1. 四档宽度（320 / 390 / 768 / 1440）整页与首屏截图，输出到 website/tools/_shots/
2. 断言页面全篇仅保留 1 处 app-icon.png 图标，不再出现手机端或首屏重复图标
3. 断言移动端首屏窗口工具栏/状态栏与主标题零重叠、四档宽度零横向溢出
4. 断言没有浅色纸质感背景残留、所有关键 ID 与 SEO 标签完整、图集切换与复制命令可用
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = REPO_ROOT / "website" / "public"
SHOTS_DIR = Path(__file__).resolve().parent / "_shots"

FORBIDDEN_BG = ["rgb(251, 248, 241)", "rgb(255, 252, 246)"]

WIDTHS = [(320, "mobile"), (390, "mobile390"), (768, "tablet"), (1440, "desktop")]

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  OK   {label}")
    else:
        print(f"  FAIL {label}")
        failures.append(label)


def main() -> int:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    url = PUBLIC_DIR.resolve().as_uri() + "/index.html"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        browser_context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        for width, name in WIDTHS:
            page = browser_context.new_page()
            page.set_viewport_size({"width": width, "height": 900})
            errors: list[str] = []
            page.on(
                "console",
                lambda m: errors.append(m.text)
                if m.type == "error" and "latest-release" not in m.text
                else None,
            )
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(450)

            shot = SHOTS_DIR / f"{name}-{width}.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"[{name} {width}] 截图 -> {shot.name}")

            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            check(overflow <= 1, f"{name} 无横向溢出 (scrollWidth-clientWidth={overflow})")

            icon_count = page.locator('img[src*="app-icon.png"]').count()
            check(icon_count == 1, f"{name} 仅保留 1 个题有据图标 (实际 {icon_count})")

            if width <= 420:
                overlap = page.evaluate(
                    """() => {
                        const h1 = document.querySelector('h1')?.getBoundingClientRect();
                        const tb = document.querySelector('.shot-toolbar')?.getBoundingClientRect();
                        const st = document.querySelector('.shot-status')?.getBoundingClientRect();
                        if (!h1 || !tb || !st) return 'missing';
                        const hit = (a, b) => !(a.bottom <= b.top || a.top >= b.bottom || a.right <= b.left || a.left >= b.right);
                        return hit(h1, tb) || hit(h1, st);
                    }"""
                )
                check(overlap is False, f"{name} 首屏窗口条与主标题无重叠 (overlap={overlap})")

                # 手机端菜单按钮可展开
                menu_btn = page.locator("#mobileMenuBtn")
                check(menu_btn.is_visible(), f"{name} 移动端菜单按钮可见")
                menu_btn.click()
                page.wait_for_timeout(150)
                check(page.locator("#mobileNav").is_visible(), f"{name} 点击菜单按钮可展开导航")
                menu_btn.click()
                page.wait_for_timeout(100)

            page.screenshot(path=str(SHOTS_DIR / f"{name}-hero.png"))

            if width == 1440:
                body_style = page.evaluate(
                    """() => {
                        const s = getComputedStyle(document.body);
                        return { color: s.backgroundColor, image: s.backgroundImage };
                    }"""
                )
                check(
                    "rgb(251, 248, 241)" not in body_style["image"]
                    and "rgb(251, 248, 241)" not in body_style["color"],
                    f"body 背景已深色化 (bg={body_style['color']})",
                )
                light = page.evaluate(
                    """() => {
                        const bad = [];
                        for (const sel of ['.feature-card', '.download-card', '.spotlight-card', '.shot-frame']) {
                            for (const el of document.querySelectorAll(sel)) {
                                const bg = getComputedStyle(el).backgroundColor;
                                if (bg === 'rgb(251, 248, 241)' || bg === 'rgb(255, 252, 246)') {
                                    bad.push(sel + '=' + bg);
                                }
                            }
                        }
                        return bad;
                    }"""
                )
                check(not light, f"卡片无浅色纸质感残留 {light if light else ''}")

                hidden_cards = page.evaluate(
                    """() => {
                        const bad = [];
                        for (const sel of ['.metrics-strip', '.spotlight-card', '.feature-card', '.download-card', '.community-banner']) {
                            for (const el of document.querySelectorAll(sel)) {
                                if (Number(getComputedStyle(el).opacity) < 0.99) {
                                    bad.push(sel);
                                }
                            }
                        }
                        return bad;
                    }"""
                )
                check(not hidden_cards, f"全页卡片直接可见无 opacity:0 空白 {hidden_cards if hidden_cards else ''}")

                spotlights_count = page.locator(".spotlight-card").count()
                check(spotlights_count == 3, f"三大核心卖点卡片齐全 (实际 {spotlights_count})")

                broken_imgs = page.evaluate(
                    """() => Array.from(document.images)
                        .filter(img => img.getAttribute('src') && (!img.complete || img.naturalWidth === 0))
                        .map(img => img.getAttribute('src'))"""
                )
                check(not broken_imgs, f"页面全部图片加载完整 {broken_imgs if broken_imgs else ''}")

                check(page.locator(".shot-toolbar").count() == 1, "首屏工具栏条存在")
                check(page.locator(".shot-status").count() == 1, "首屏状态栏存在")
                check(page.locator(".shot-dots i").count() >= 3, "工具栏窗口圆点存在")

                indexes = page.locator(".section-index").count()
                check(indexes == 4, f"四个区块编号齐全 (实际 {indexes})")

                ids = [
                    "versionBadge",
                    "downloadInstallerBtn",
                    "downloadPortableBtn",
                    "releaseMeta",
                    "downloadInstallerCardBtn",
                    "downloadPortableCardBtn",
                    "downloadCardMeta",
                    "copyPsBtn",
                    "psInstallCmd",
                    "galleryZoomBtn",
                    "galleryMainImg",
                    "galleryCaption",
                    "lightbox",
                    "lightboxImg",
                    "lightboxCaption",
                    "lightboxClose",
                    "install-cli",
                ]
                missing = [i for i in ids if page.locator(f"#{i}").count() == 0]
                check(not missing, f"app.js 依赖的 id 全部保留 {missing if missing else ''}")

                page.locator(".tab-btn").nth(4).click()
                page.wait_for_timeout(300)
                src = page.locator("#galleryMainImg").get_attribute("src")
                check(src == "assets/paper-preview.webp", f"图集切换主图同步 (src={src})")
                caption = page.locator("#galleryCaption").inner_text()
                check("组卷" in caption and "A3" in caption, "图集切换 caption 同步 (含'组卷'与'A3')")
                zoom_src = page.locator("#galleryZoomBtn").get_attribute("data-zoom-src")
                check(zoom_src == "assets/paper-preview.webp", f"灯箱 data-zoom-src 同步 (={zoom_src})")

                page.locator("#copyPsBtn").click()
                page.wait_for_timeout(300)
                btn_text = page.locator("#copyPsBtn").inner_text()
                check("复制" in btn_text or "已" in btn_text, f"复制按钮有反馈 (文本={btn_text})")
                try:
                    clip = page.evaluate("() => navigator.clipboard.readText()")
                    check("install.ps1" in clip, "剪贴板内容正确 (含 install.ps1)")
                except Exception as exc:  # noqa: BLE001
                    check(False, f"读取剪贴板失败: {exc}")

                for meta in [
                    'meta[property="og:image"]',
                    'meta[property="og:title"]',
                    'meta[name="twitter:card"]',
                    'link[rel="canonical"]',
                    'link[rel="sitemap"]',
                ]:
                    check(page.locator(meta).count() == 1, f"SEO 标签保留 {meta}")

            check(not errors, f"{name} 无 console 报错 {errors if errors else ''}")
            page.close()

        browser.close()

    print()
    if failures:
        print(f"FAILED {len(failures)} 项：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
