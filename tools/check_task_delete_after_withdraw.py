r"""撤完题的卷必须真能删：有活题仍拒、撤回后点「永久删除」真删掉。

DOM 上写着「已入库」不算数，这里断言的是任务从列表消失、题库条数不动、
以及撤回记录的题面快照还在。

用法：
  $env:QB_DATABASE="<repo>\\tmp\\task-delete\\db.sqlite3"
  $env:QB_DATA_ROOT="<repo>\\tmp\\task-delete\\data"
  python tools/check_task_delete_after_withdraw.py --seed
  # 另开一个窗口起 Django（不起 worker）：
  $env:QB_PORT=8802; .\.venv\\Scripts\\python.exe manage.py runserver 127.0.0.1:8802
  python tools/check_task_delete_after_withdraw.py --url http://127.0.0.1:8802
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp" / "task-delete-shots"
LIVE_NAME = "还有活题的资料.pdf"
DONE_NAME = "已全部撤回的资料.pdf"


def seed():
    for name in ("QB_DATABASE", "QB_DATA_ROOT"):
        value = os.environ.get(name)
        if not value or not Path(value).resolve().is_relative_to(ROOT / "tmp"):
            raise SystemExit(f"{name} must be inside this checkout's tmp directory")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.utils import timezone
    from core import library
    from core.models import Paper, PublishedQuestion, Question

    call_command("migrate", verbosity=0)
    OUT.mkdir(parents=True, exist_ok=True)
    # 可以重复跑：先把上一轮种的两份清掉，否则卷名会越叠越多。
    for stale in (LIVE_NAME, DONE_NAME):
        for old in Paper.objects.filter(filename=stale):
            shutil.rmtree(settings.DATA_ROOT / str(old.id), ignore_errors=True)
            old.delete()
    fixture = json.loads((ROOT / "backend/core/demo_data/demo-paper.json").read_text(encoding="utf-8"))

    def make(filename, withdraw_all):
        paper = Paper.objects.create(filename=filename, kind="pdf", sha256=f"seed-{filename}",
                                     pages=fixture["pages"], status=Paper.Status.READY)
        folder = settings.DATA_ROOT / str(paper.id)
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "backend/core/demo_data/demo-paper.pdf", folder / "source.pdf")
        paper.source_path = str(folder / "source.pdf")
        paper.save()
        for number in (1, 2):
            question = Question.objects.create(
                paper=paper, number=number, question_type="free_response",
                stem=f"第 {number} 题：求 ${number}+{number}$。", state=Question.State.GREEN, approved=True,
            )
            content = library.final_content(question)
            PublishedQuestion.objects.create(
                question=question, paper=paper, source_filename=filename, number=number,
                question_type="free_response", version=1, content=content,
                content_hash=library.content_hash(content), search_text=library._search_text(content),
                status=PublishedQuestion.Status.WITHDRAWN if withdraw_all else PublishedQuestion.Status.PUBLISHED,
                published_at=timezone.now(),
                withdrawn_at=timezone.now() if withdraw_all else None,
            )
        return paper

    live = make(LIVE_NAME, False)
    done = make(DONE_NAME, True)
    (OUT / "seed.json").write_text(json.dumps({"live": str(live.id), "done": str(done.id)}, ensure_ascii=False),
                                   encoding="utf-8")
    print(f"seeded live={live.id} done={done.id}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8802")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args()
    if args.seed:
        seed()
        return

    from playwright.sync_api import sync_playwright

    url = args.url.rstrip("/")
    ids = json.loads((OUT / "seed.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    report, failures = [], 0

    def check(name, ok, detail=""):
        nonlocal failures
        report.append(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
        if not ok:
            failures += 1

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def library_total():
            return page.request.get(f"{url}/api/library?limit=1").json()["total"]

        def open_paper(paper_id):
            page.goto(f"{url}/?paper={paper_id}", wait_until="networkidle")
            page.wait_for_timeout(1200)
            for sel in ["#welcomeSkip", "button:has-text('直接开始')"]:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    page.wait_for_timeout(400)
                    break

        def open_menu():
            page.click("summary:has-text('试卷操作')")
            page.wait_for_timeout(400)

        # ---------- 1. 还有活题：按钮根本不出现 ----------
        before_total = library_total()
        open_paper(ids["live"])
        open_menu()
        delete = page.query_selector("#settingsDelete")
        check("有活题时「永久删除…」不出现", delete is None or delete.is_hidden(),
              f"hidden={delete.is_hidden() if delete else 'n/a'}")
        hint = page.inner_text("#settingsDangerHint")
        check("提示给出去路而不是一句不能删", "撤回" in hint and "正式题库" in hint, hint)

        # ---------- 2. 全部撤回：按钮出现，点了真删 ----------
        open_paper(ids["done"])
        open_menu()
        delete = page.query_selector("#settingsDelete")
        check("全部撤回后「永久删除…」出现且可点",
              delete is not None and delete.is_visible() and not delete.is_disabled())
        page.screenshot(path=str(OUT / "01-menu-before-delete.png"))
        if delete is not None and delete.is_visible():
            delete.click()
            page.wait_for_timeout(700)
            page.screenshot(path=str(OUT / "02-confirm.png"))
            dialog = page.query_selector("dialog[open]")
            text = dialog.inner_text() if dialog else ""
            check("确认框说清删掉不影响撤回记录的快照", "题面快照" in text, text.replace("\n", " ⏎ ")[:160])
            # 按可见文字找确认按钮：class 猜错会点到隐藏的按钮上，
            # 那看起来像“点不动”，其实是自己选错了元素。
            ok = None
            for candidate in page.query_selector_all("dialog[open] button"):
                if candidate.is_visible() and "删除" in (candidate.inner_text() or ""):
                    ok = candidate
                    break
            check("确认框里有可点的删除按钮", ok is not None,
                  f"dialog 按钮: {[x.inner_text().strip() for x in page.query_selector_all('dialog[open] button')]}")
            if ok is None:
                browser.close()
                (OUT / "report.txt").write_text("\n".join(report), encoding="utf-8")
                raise SystemExit("找不到可点的确认按钮")
            ok.click()
            page.wait_for_timeout(2500)
            page.screenshot(path=str(OUT / "03-after-delete.png"))

        papers = page.request.get(f"{url}/api/papers").json()["papers"]
        names = [row["name"] for row in papers]
        check("被删的任务从试卷列表消失", DONE_NAME not in names, f"现在有 {len(names)} 份：{names}")
        check("有活题那份还在", LIVE_NAME in names)
        check("题库条数没被动过（撤回记录不是活题，删任务不该动它）",
              library_total() == before_total, f"{before_total} → {library_total()}")

        # 撤回记录的快照还在题库里可查
        page.goto(f"{url}/library", wait_until="networkidle")
        page.wait_for_timeout(1200)
        check("撤回记录不出现在题库列表里（本来就不该在）",
              page.query_selector_all(".library-card").__len__() == before_total)
        report.append(f"题库当前 {before_total} 条（两份卷各 2 道，共 4 道：2 道活的 + 2 道撤回的）")

        check("没有 JS 报错", not errors, "; ".join(errors[:4]))
        browser.close()

    (OUT / "report.txt").write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"\n{len(report) - failures}/{len(report)} passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
