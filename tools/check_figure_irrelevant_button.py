"""配图冲突面板：一键把「其余候选图」都标成无关，零配图也要能走通。

1.13.3：面板文案一直承诺「或明确确认其余候选均与本题无关」，但题面本来就没有图的题
（AI 多框了几张手写或别题的图）在界面上点不到这个动作 —— 第三个按钮被「有配图才
显示」挡住，前端的确认函数遇到零配图又直接弹回去。文案承诺了动作、按钮不画。

这个脚本走完整往返：点按钮 → 确认 → 读 API 核对落到了哪个状态 → 再标记通过，
断言题库条数真的 +1。断言全部落在可观测的外部状态上，不看 DOM 属性。

用法：先起一个开发服务器（QB_DATABASE / QB_DATA_ROOT 指到一份可改的副本），再
.\\backend\\.venv\\Scripts\\python.exe tools\\check_figure_irrelevant_button.py --base http://127.0.0.1:8803
"""

import argparse
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# The review card is two columns above 1100 px. The in-app Browser panel is 901 px,
# so wide-screen acceptance has to go through Playwright with an explicit viewport.
VIEWPORT = {"width": 1400, "height": 950}

BUTTON_ADD = "\u52a0\u5165\u8bd5\u9898\u7bee"  # 加入试题篮
BUTTON_ALL_IRRELEVANT = "\u90fd\u4e0e\u672c\u9898\u65e0\u5173"  # 都与本题无关
CONFIRM_ALL = "\u786e\u8ba4\u90fd\u65e0\u5173"  # 确认都无关
CONFIRM_YES = "\u786e\u8ba4"  # 确认

# Every figure-review menu currently on the page, with its buttons' text.
PANELS = """() => [...document.querySelectorAll('.card .figure-review')].map((panel) => ({
  question: (panel.closest('.card').querySelector('.qnum') || {}).textContent || '',
  questionId: panel.closest('.card').dataset.id,
  status: (panel.className.match(/figure-review-([a-z_]+)/) || [])[1] || '',
  copy: (panel.querySelector('.figure-review-copy') || {}).textContent || '',
  buttons: [...panel.querySelectorAll('button')].map((b) => (b.textContent || '').trim()),
  hasFigure: !!panel.closest('.card').querySelector('.rendered .qb-figure, .rendered .qb-figures'),
}))"""


def fail(message):
    print(f"  FAIL {message}")
    return 1


async def find_target(page):
    """A question with no figure of its own but candidates nobody has placed.

    That is exactly the case the missing button used to hide: the copy promises
    the action, the panel has nothing to preserve, so there is nothing to show.
    """
    return await page.evaluate("""() => {
        const panels = [...document.querySelectorAll('.card .figure-review-conflict')];
        for (const panel of panels) {
            const card = panel.closest('.card');
            if (card.querySelector('.rendered .qb-figure, .rendered .qb-figures')) continue;
            if (!/\\u5019\\u9009\\u56fe\\u5c1a\\u672a\\u5f52\\u7c7b/.test(panel.querySelector('.figure-review-copy')?.textContent || '')) continue;
            return {
              question: (card.querySelector('.qnum') || {}).textContent || '',
              id: card.dataset.id,
              buttons: [...panel.querySelectorAll('button')].map((b) => (b.textContent || '').trim()),
            };
        }
        return null;
    }""")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8803")
    args = parser.parse_args()

    failures = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        page = await browser.new_page(viewport=VIEWPORT)
        try:
            await page.goto(f"{args.base}/", wait_until="domcontentloaded")
            await page.wait_for_selector(".paper-link", timeout=30000)
            await page.wait_for_timeout(2500)
            for dialog in await page.evaluate(
                "() => [...document.querySelectorAll('dialog[open]')].map((d) => d.id)"
            ):
                await page.evaluate("(id) => document.getElementById(id)?.close()", dialog)

            papers = await page.evaluate(
                "() => [...document.querySelectorAll('.paper-link')].map((b) => b.title)"
            )
            print(f"试卷 {len(papers)} 份")

            # Open the paper that still has work to do.
            await page.evaluate("""() => {
                const link = [...document.querySelectorAll('.paper-link')]
                  .find((b) => /\\u770b|\\u6838\\u67e5|\\u5f85/.test(b.querySelector('.paper-meta')?.textContent || ''))
                  || document.querySelector('.paper-link');
                link.click();
            }""")
            await page.wait_for_selector(".card", timeout=30000)
            await page.wait_for_timeout(3000)
            for dialog in await page.evaluate(
                "() => [...document.querySelectorAll('dialog[open]')].map((d) => d.id)"
            ):
                await page.evaluate("(id) => document.getElementById(id)?.close()", dialog)

            # Scroll the whole list: cards are content-visibility:auto, so a panel
            # that is not laid out yet reports no buttons at all.
            height = await page.evaluate("() => document.body.scrollHeight")
            for _ in range(0, height, 500):
                await page.mouse.wheel(0, 500)
                await page.wait_for_timeout(110)
            await page.wait_for_timeout(600)

            target = await find_target(page)
            if not target:
                print("SKIP 这份卷里没有「零配图 + 候选图未归类」的题，换一份数据再跑。")
                return 0
            print(f"目标：{target['question']}（零配图、候选图未归类）")
            print(f"  面板现有按钮：{target['buttons']}")

            if not any(BUTTON_ALL_IRRELEVANT in b for b in target["buttons"]):
                failures += fail(
                    f"面板上没有「{BUTTON_ALL_IRRELEVANT}」按钮，文案承诺了动作但界面上没有")
                return failures

            paper_id = await page.evaluate(
                "() => new URL(location.href).searchParams.get('paper')")
            # page.request.get is a coroutine — await the response before .json().
            before = await (await page.request.get(f"{args.base}/api/papers/{paper_id}")).json()
            before_q = next(q for q in before["questions"] if str(q["id"]) == target["id"])
            # /api/library caps limit at 100 server-side and reports the real size in
            # "total". Counting items would compare two truncated lists of 100.
            before_total = (await (await page.request.get(
                f"{args.base}/api/library?limit=500")).json())["total"]
            print(f"  操作前：status={before_q['figure_review'].get('status')} "
                  f"figures={len(before_q.get('figures') or [])} 题库={before_total}")

            # Click the real button, then really confirm.
            await page.evaluate("""(id) => {
                const panel = document.querySelector(`.card[data-id="${id}"] .figure-review`);
                panel.scrollIntoView({ block: 'center' });
                const button = [...panel.querySelectorAll('button')]
                  .find((b) => (b.textContent || '').includes('%s'));
                button.click();
            }""" % BUTTON_ALL_IRRELEVANT, target["id"])
            await page.wait_for_timeout(700)
            # Only the confirm dialog: the preview also opens a teaching panel, and a
            # bare "dialog[open] .confirm-text" can land on that instead.
            dialog_text = await page.evaluate("""() => {
                const dialogs = [...document.querySelectorAll('dialog[open]')];
                const target = dialogs.find((d) => d.querySelector('.confirm-text'));
                return target ? (target.querySelector('.confirm-text').textContent || '').trim() : '';
            }""")
            print(f"  确认框：{dialog_text[:90]}")
            if not dialog_text:
                failures += fail("点了按钮没有出现确认框")
                return failures
            await page.evaluate("""() => {
                const dialogs = [...document.querySelectorAll('dialog[open]')];
                const target = dialogs.find((d) => d.querySelector('.confirm-text'));
                const button = [...target.querySelectorAll('button')]
                  .find((b) => (b.textContent || '').trim() === '%s')
                  || target.querySelector('.confirm-actions .button.primary');
                button.click();
            }""" % CONFIRM_ALL)
            await page.wait_for_timeout(2000)

            # Read the API directly: page.evaluate(fetch) right after a re-render
            # can hand back the response the page itself already had.
            after = await (await page.request.get(f"{args.base}/api/papers/{paper_id}")).json()
            after_q = next(q for q in after["questions"] if str(q["id"]) == target["id"])
            review = after_q["figure_review"]
            print(f"  操作后：status={review.get('status')} figures={len(after_q.get('figures') or [])} "
                  f"ignored={len(review.get('ignored_candidates') or [])}")

            if review.get("status") != "confirmed_no_figure":
                failures += fail(f"落到了 {review.get('status')}，不是 confirmed_no_figure")
            if after_q.get("figures"):
                failures += fail("仍然留着配图")
            if not review.get("ignored_candidates"):
                failures += fail("没有记录任何无关候选图，下次重算又会把它们当成漏网的")

            panel = await page.evaluate(
                """(id) => {
                    const el = document.querySelector(`.card[data-id="${id}"] .figure-review`);
                    return el ? { status: (el.className.match(/figure-review-([a-z_]+)/) || [])[1],
                                  buttons: [...el.querySelectorAll('button')].map((b) => b.textContent.trim()) } : null;
                }""", target["id"])
            print(f"  面板现在：{json.dumps(panel, ensure_ascii=False)}")
            if panel and panel["status"] == "conflict":
                failures += fail("界面上仍然是冲突状态，没有变成「已人工确认本题无图」")

            # Now it must be passable, and passing must reach the bank.
            await page.evaluate("""(id) => {
                const tick = document.querySelector(`.card[data-id="${id}"] .card-tick`);
                tick.scrollIntoView({ block: 'center' });
                tick.click();
            }""", target["id"])
            await page.wait_for_timeout(2500)
            after_total = (await (await page.request.get(
                f"{args.base}/api/library?limit=500")).json())["total"]
            print(f"  题库：{before_total} → {after_total}")
            if after_total != before_total + 1:
                failures += fail(f"标记通过后台库条数是 {before_total} → {after_total}，不是 +1")
        finally:
            await browser.close()

    print("FAIL" if failures else "PASS", f"- {failures} 个问题")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
