"""官网深色改版的视觉与回归检查。

在仓库根目录执行：

    python website/tools/check_dark_theme.py

做四件事：
1. 三档宽度（320 / 768 / 1440）整页截图，输出到 website/tools/_shots/
2. 断言页面上没有残留的浅色底（米白纸质感），即浅色背景不应再出现
3. 回归：图集 tab 切换后主图与 caption 是否同步、复制命令按钮是否可用
4. 断言关键 id 与 class 仍在（app.js 依赖它们）
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_DIR = REPO_ROOT / "website" / "public"
SHOTS_DIR = Path(__file__).resolve().parent / "_shots"

# 改版前的米白纸质感主色，出现即视为「没改干净」
FORBIDDEN_BG = ["rgb(251, 248, 241)", "rgb(255, 252, 246)"]

WIDTHS = [(320, "mobile"), (768, "tablet"), (1440, "desktop")]

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
            # file:// 下 fetch("/api/latest-release") 必然失败，那是本地预览的固有现象，
            # 不算页面缺陷；线上有真接口。这里过滤掉它，只看真正的脚本错误。
            page.on(
                "console",
                lambda m: errors.append(m.text)
                if m.type == "error" and "latest-release" not in m.text
                else None,
            )
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.wait_for_load_state("networkidle")

            # 整页截图
            shot = SHOTS_DIR / f"{name}-{width}.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"[{name} {width}] 截图 -> {shot.name}")

            # 横向溢出
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            check(overflow <= 1, f"{name} 无横向溢出 (scrollWidth-clientWidth={overflow})")

            # 首屏截图单独出，便于看大图效果
            page.screenshot(path=str(SHOTS_DIR / f"{name}-hero.png"))

            # 浅色残留：body 与主要卡片区
            if width == 1440:
                # body 背景是渐变，backgroundColor 会是透明，要看 backgroundImage
                body_style = page.evaluate(
                    """() => {
                        const s = getComputedStyle(document.body);
                        return { color: s.backgroundColor, image: s.backgroundImage };
                    }"""
                )
                check(
                    "rgb(251, 248, 241)" not in body_style["image"]
                    and "rgb(251, 248, 241)" not in body_style["color"],
                    f"body 背景已深色化 (image={body_style['image'][:60]})",
                )
                light = page.evaluate(
                    """() => {
                        const bad = [];
                        for (const sel of ['.feature-card', '.download-card', '.workflow-card', '.shot-frame']) {
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

                # 首屏面板：工具栏条 + 状态栏
                check(
                    page.locator(".shot-toolbar").count() == 1,
                    "首屏工具栏条存在",
                )
                check(
                    page.locator(".shot-status").count() == 1,
                    "首屏状态栏存在",
                )
                check(
                    page.locator(".shot-dots i").count() == 3,
                    "工具栏三个窗口圆点",
                )

                # 区块编号
                indexes = page.locator(".section-index").count()
                check(indexes == 4, f"四个区块编号齐全 (实际 {indexes})")

                # 不变量：app.js 依赖的 id
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

                # 回归：图集切换
                page.locator(".tab-btn").nth(4).click()
                page.wait_for_timeout(400)
                src = page.locator("#galleryMainImg").get_attribute("src")
                check(src == "assets/paper-preview.webp", f"图集切换主图同步 (src={src})")
                caption = page.locator("#galleryCaption").inner_text()
                check(
                    "组卷" in caption and "A3" in caption,
                    f"图集切换 caption 同步 (含'组卷'与'A3')",
                )
                zoom_src = page.locator("#galleryZoomBtn").get_attribute("data-zoom-src")
                check(
                    zoom_src == "assets/paper-preview.webp",
                    f"灯箱 data-zoom-src 同步 (={zoom_src})",
                )

                # 回归：复制命令（剪贴板写权限已在 context 上授予）
                page.locator("#copyPsBtn").click()
                page.wait_for_timeout(400)
                btn_text = page.locator("#copyPsBtn").inner_text()
                check("复制" in btn_text or "已" in btn_text, f"复制按钮有反馈 (文本={btn_text})")
                try:
                    clip = page.evaluate("() => navigator.clipboard.readText()")
                    check(
                        "install.ps1" in clip,
                        f"剪贴板内容正确 (含 install.ps1)",
                    )
                except Exception as exc:  # noqa: BLE001
                    check(False, f"读取剪贴板失败: {exc}")

                # 回归：SEO 标签仍在
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
