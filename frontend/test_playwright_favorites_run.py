import asyncio
import os
from playwright.async_api import async_playwright

ARTIFACT_DIR = r"C:\Users\17321\.gemini\antigravity\brain\a2ce0b73-30f5-4cc1-b686-e97bda223f5a"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        print("正在打开 Eagle 页面 http://127.0.0.1:8769/eagle/ ...")
        await page.goto("http://127.0.0.1:8769/eagle/", wait_until="networkidle")

        await page.wait_for_selector("#questionListContainer", timeout=10000)
        await asyncio.sleep(1)

        # 1. 初始状态截图（未收藏任何题目，左侧置顶树显示“我收藏的试题 0”，顶栏显示“我的收藏 (0)”）
        path1 = os.path.join(ARTIFACT_DIR, "favorites_initial_state.png")
        await page.screenshot(path=path1)
        print("已截图：初始状态", path1)

        # 2. 点击第一道题目的爱心进行收藏
        first_card_heart = page.locator('#questionListContainer [id^="star_btn_"]').first
        await first_card_heart.click()
        await asyncio.sleep(0.6)

        path2 = os.path.join(ARTIFACT_DIR, "favorites_after_collect.png")
        await page.screenshot(path=path2)
        print("已截图：收藏后状态（卡片红心、左侧树=1、顶栏=1、右侧属性=已收藏）", path2)

        # 3. 点击顶栏“我的收藏 (1)”
        await page.locator('#headerFavBtn').click()
        await asyncio.sleep(0.6)

        path3 = os.path.join(ARTIFACT_DIR, "favorites_filtered_view.png")
        await page.screenshot(path=path3)
        print("已截图：收藏筛选视图（顶栏高亮激活、列表仅显示收藏题目、面包屑呈现）", path3)

        # 4. 在收藏筛选视图下再次点击爱心（取消收藏）
        first_in_filter = page.locator('#questionListContainer [id^="star_btn_"]').first
        await first_in_filter.click()
        await asyncio.sleep(0.6)

        path4 = os.path.join(ARTIFACT_DIR, "favorites_empty_guidance.png")
        await page.screenshot(path=path4)
        print("已截图：取消收藏后即时移出与友好空态引导", path4)

        await browser.close()
        print("Playwright 自动化实机测试执行成功！")

if __name__ == "__main__":
    asyncio.run(main())
