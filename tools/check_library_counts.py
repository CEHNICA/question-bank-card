"""Library list stress: every number the interface shows must match the data behind it.

The list pages 40 at a time and says 「已显示 N / 共 M 题」.  Numbers off by one at a
page boundary, a filter chip that counts a different set than the list, or a deep link
that filters differently from what the rail shows all read as 「题库坏了」.

The expected total is decided by the stub that serves /api/library in this file, so the
assertion is 「界面从不和数据打架」, not 「两份筛选实现碰巧一致」.  The library is laid
out on page boundaries (39/40/41/79/80/81) because that is where off-by-one hides, and
every case is also reached through the same deep link a person's URL would be.

--run writes only under checkout/tmp/library-counts.  No Django, worker, cloud service
or user data is touched.
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import sys
import threading
from urllib.parse import parse_qs, quote, urlencode, urlparse

from playwright.sync_api import expect, sync_playwright

from check_library_preview_browser import FrontendHandler, fixture_item

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp" / "library-counts"
PAGE = 40

# (count, question_type, tag, has_answer, review source)
LAYOUT = [
    (40, "single_choice", None, True, "human"),
    (40, "fill_blank", "代数", True, "human"),
    (40, "free_response", "代数", False, "ai"),
]


def build() -> list[dict]:
    items = []
    for block, (count, kind, tag, has_answer, review) in enumerate(LAYOUT):
        for index in range(count):
            item = fixture_item(f"b{block}-{index:03d}", index + 1)
            item["question_type"] = item["content"]["question_type"] = kind
            if kind not in ("single_choice", "multiple_choice"):
                item["content"]["options"] = {}
            if has_answer:
                item["content"]["answer"] = "A"
            else:
                item["content"]["answer"] = ""
            item["has_answer"] = has_answer
            item["review"] = {"source": review}
            item["tags"] = [] if tag is None else [tag]
            items.append(item)
    # Some questions in the middle block carry a second tag, so one question matches
    # two tags at once and a 「标签+题型」 slice is strictly smaller than either alone.
    for index in range(0, 40, 7):
        items[40 + index]["tags"] = ["代数", "函数"]
    # One unique stem so the search case has an exact answer of one.
    items[5]["content"]["stem"] = "唯一检索词 ZZTOPZZ 在这里。"
    return items


ITEMS = build()


def _one(params: dict[str, list[str]], key: str) -> str:
    return (params.get(key, [""])[0] or "").strip()


def filtered(params: dict[str, list[str]]) -> list[dict]:
    """Serve the list: AND across kinds, OR inside one tag list."""
    wanted_type = _one(params, "type")
    wanted_review = _one(params, "review")
    wanted_answer = _one(params, "answer")
    wanted_tag = [part for part in _one(params, "tag").split(",") if part]
    needle = _one(params, "q")
    result = []
    for item in ITEMS:
        if wanted_type and item["question_type"] != wanted_type:
            continue
        if wanted_review and item["review"]["source"] != wanted_review:
            continue
        if wanted_answer and item["has_answer"] != (wanted_answer == "yes"):
            continue
        if wanted_tag and not set(item["tags"]) & set(wanted_tag):
            continue
        if needle and needle not in json.dumps(item["content"], ensure_ascii=False):
            continue
        result.append(item)
    return result


def facets() -> dict:
    types: dict[str, int] = {}
    tags: dict[str, int] = {}
    answers = {"yes": 0, "no": 0}
    reviews = {"human": 0, "ai": 0}
    for item in ITEMS:
        types[item["question_type"]] = types.get(item["question_type"], 0) + 1
        for tag in item["tags"]:
            tags[tag] = tags.get(tag, 0) + 1
        answers["yes" if item["has_answer"] else "no"] += 1
        reviews[item["review"]["source"]] += 1
    return {"sources": [], "types": types, "answers": answers, "reviews": reviews,
            "tags": sorted(tags)}


def library_body(query: str) -> dict:
    params = parse_qs(query, keep_blank_values=True)
    matches = filtered(params)
    limit = int(_one(params, "limit") or PAGE)
    offset = int(_one(params, "offset") or 0)
    return {"items": matches[offset:offset + limit], "total": len(matches),
            "features": {"ai_answer": False}, "facets": facets()}


def status_numbers(page) -> tuple:
    """Read 已显示 N / 共 M out of the status line, without guessing the wording."""
    text = page.locator("#libraryStatus").inner_text().replace(",", "")
    match = re.search(r"已显示\s*(\d+)\s*/\s*(?:共\s*)?(\d+)\s*题", text)
    if not match:
        return (None, None, text)
    return (int(match.group(1)), int(match.group(2)), text)


def card_ids(page) -> list[str]:
    return page.locator("#libraryList .library-card").evaluate_all(
        "ns=>ns.map(n=>(n.id||'').replace(/^q-/,''))")


def drain(page) -> dict:
    """Click 加载更多 the way a person does, until the button goes away."""
    steps = []
    for _ in range(12):
        shown, total, text = status_numbers(page)
        more = page.locator("#moreButton")
        visible = more.is_visible()
        steps.append({"cards": len(card_ids(page)), "shown": shown, "total": total, "more": visible})
        if not visible:
            break
        before = len(card_ids(page))
        more.click()
        # 「还有下一页」时按钮本来就一直亮着，拿它消失当「加载完了」的信号是错的。
        # 要等的是卡片真的多出来。
        page.wait_for_function(
            "n=>document.querySelectorAll('#libraryList .library-card').length>n",
            arg=before, timeout=15000)
    shown, total, text = status_numbers(page)
    return {"steps": steps, "final": [shown, total], "status": text}


def run(port: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"cases": [], "page_errors": [], "requests": []}
    with ThreadingHTTPServer(("127.0.0.1", port),
                             partial(FrontendHandler, directory=str(ROOT / "frontend"))) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with sync_playwright() as pw:
            browser_path = next((str(v) for v in (Path(pw.chromium.executable_path),
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if v.is_file()), None)
            browser = pw.chromium.launch(headless=True,
                **({"executable_path": browser_path} if browser_path else {}))
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_init_script(
                "localStorage.setItem('qb-welcome-seen','1');localStorage.setItem('qb-lens','0');")
            page = context.new_page()
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))

            def stub(route):
                parsed = urlparse(route.request.url)
                if parsed.path == "/api/library" and route.request.method == "GET":
                    report["requests"].append(parsed.query)
                    route.fulfill(json=library_body(parsed.query))
                    return
                if parsed.path == "/api/settings/library-ai":
                    route.fulfill(json={"features": {"knowledge_tags": False, "ai_answer": False},
                                        "mode": "assistant", "provider": "deepseek", "ready": True})
                    return
                if parsed.path.startswith("/api/"):
                    route.fulfill(json={})
                    return
                route.continue_()

            context.route("**/*", stub)

            def case(name: str, params: dict[str, str]) -> None:
                # 手写百分号转义会写错字（%E4%BB%8B 是「介」不是「代」），
                # 而筛不出东西时界面显示「没有找到」是完全正确的 —— 那种假红最费时间。
                query = "?" + urlencode(params) if params else ""
                page.goto(f"http://127.0.0.1:{port}/library{query}", wait_until="networkidle")
                page.wait_for_selector("#libraryList .library-card, #libraryList .library-empty",
                                       timeout=15000)
                page.wait_for_timeout(400)
                wanted = [item["id"] for item in filtered(parse_qs(query.lstrip("?"), keep_blank_values=True))]
                expected = len(wanted)
                first_page = len(card_ids(page))
                steps = drain(page)
                seen = card_ids(page)
                entry = {"name": name, "query": query, "expected": expected, "first_page": first_page,
                         "cards": len(seen), "unique": len(set(seen)),
                         "missing": sorted(set(wanted) - set(seen))[:5],
                         "extra": sorted(set(seen) - set(wanted))[:5], **steps}
                report["cases"].append(entry)
                # 每一页的「已显示」都要等于该页真实的卡片数，末页的「共」要等于总数。
                pages_ok = all(step["shown"] == step["cards"] for step in entry["steps"])
                more_ok = all(step["more"] == (step["cards"] < (step["total"] or 0)) for step in entry["steps"][:-1])
                if expected == 0:
                    # 筛不出东西时不写「已显示 0 / 共 0 题」，给的是空状态提示 ——
                    # 那是对的。要盯的是「别一边说空、一边报个数」。
                    empty = page.locator("#libraryList .library-empty")
                    ok = (entry["cards"] == 0 and not entry["missing"] and not entry["extra"]
                          and entry["final"][1] in (None, 0) and empty.is_visible()
                          and empty.inner_text().strip())
                    entry["empty"] = empty.inner_text().strip().split("\n")[0][:60] if empty.count() else None
                else:
                    ok = (entry["final"] == [expected, expected] and entry["unique"] == expected
                          and not entry["missing"] and not entry["extra"] and pages_ok and more_ok)
                shown_text = entry["status"] or f"空状态：{entry['empty']}"
                entry["ok"] = bool(ok)
                print(("PASS  " if ok else "FAIL  ") + f"{name}：数据 {expected} 道 → {shown_text}", flush=True)
                if not ok:
                    print("      " + json.dumps(entry, ensure_ascii=False)[:1000], flush=True)

            algebra, function = "代数", "函数"
            case("全部 120 道", {})
            case("单选 40 道（正好一页，不该有「加载更多」）", {"type": "single_choice"})
            case("填空 40 道（有答案、人工核对）", {"type": "fill_blank"})
            case("解答 40 道（无答案、AI 审核）", {"type": "free_response"})
            case("代数 80 道（正好两页）", {"tag": algebra})
            case("代数 + 单选（真的没有交集）", {"tag": algebra, "type": "single_choice"})
            case("代数 + 填空（交集正好一页）", {"tag": algebra, "type": "fill_blank"})
            case("代数+函数（并集仍是 80 道：带函数的题本来也带代数）", {"tag": f"{algebra},{function}"})
            case("代数 + 有答案 + 人工核对（三层嵌套 40 道）",
                 {"tag": algebra, "answer": "yes", "review": "human"})
            case("代数 + AI 审核（40 道，全无答案）", {"tag": algebra, "review": "ai"})
            case("筛不出任何一道", {"type": "multiple_choice"})
            case("搜一个只有一道命中的词", {"q": "ZZTOPZZ"})
            page.screenshot(path=str(OUT / "library-final.png"), full_page=True)
            browser.close()
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [c for c in report["cases"] if not c.get("ok")]
    print(f"\n{len(report['cases']) - len(bad)}/{len(report['cases'])} 案例通过", flush=True)
    if report["page_errors"]:
        print("页面报错：" + " | ".join(report["page_errors"][:3]), flush=True)
    if bad or report["page_errors"]:
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--port", type=int, default=8994)
    args = parser.parse_args()
    if not args.run:
        parser.error("Choose --run")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            print("端口被占，先换一个", file=sys.stderr)
            return 1
    run(args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())