"""官网版本号一致性核对：静态兜底、页面显示、真实最新版必须是同一个。

背景：首页 HTML 里写死的版本号每发一次版就过期一次，实测已落后真实版本 3 个小版本
（爬虫与不跑 JS 的分享卡片看到的都是那个写死的值）。
现在 worker 会在运行时把真实最新版注入首页，静态值退化成最后一层兜底。

本尺子检查三件事：
1. 线上首页显示的版本号 == /api/latest-release 返回的真实最新版
2. 仓库里 website/public/index.html 的静态兜底 >= 真实最新版（绝不能比真实版本旧）
3. 线上响应带 X-TiYouJu-Version 头（说明 worker 注入已生效）

在仓库根目录执行：
    python website/tools/check_version_sync.py                    # 查线上
    python website/tools/check_version_sync.py --require-injected # 部署后验收：注入必须生效
    python website/tools/check_version_sync.py --file website/public/index.html --expect v2.3.16
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX = REPO_ROOT / "website" / "public" / "index.html"

DEFAULT_TARGET = "https://tiyouju.com/"

BADGE_RE = re.compile(r'<span\b[^>]*\bid="versionBadge"[^>]*>([^<]*)</span>', re.I)
META_BLOCK_RE = re.compile(r'<p\b[^>]*\bid="releaseMeta"[^>]*>(.*?)</p>', re.I | re.S)
META_STRONG_RE = re.compile(r"<strong>([^<]*)</strong>", re.I)
VERSION_RE = re.compile(r"^v?\d+(?:\.\d+)*$")

USER_AGENT = "tiyouju-version-check"


def extract_badge(html: str) -> str:
    match = BADGE_RE.search(html)
    return match.group(1).strip() if match else ""


def extract_meta(html: str) -> str:
    block = META_BLOCK_RE.search(html)
    if not block:
        return ""
    strong = META_STRONG_RE.search(block.group(1))
    return strong.group(1).strip() if strong else ""


def version_tuple(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(text or ""))
    return tuple(int(p) for p in parts)


def fetch(url: str, timeout: float = 20.0) -> tuple[str, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        body = response.read().decode("utf-8", errors="replace")
        headers = {k.lower(): v for k, v in response.headers.items()}
    return body, headers


def main() -> int:
    parser = argparse.ArgumentParser(description="核对官网版本号一致性")
    parser.add_argument("--target", default=DEFAULT_TARGET, help=f"站点根地址（默认 {DEFAULT_TARGET}）")
    parser.add_argument("--file", default=None, help="只核对本地 HTML 文件（离线模式，配合 --expect）")
    parser.add_argument("--expect", default=None, help="离线模式下期望的版本号")
    parser.add_argument("--require-injected", action="store_true", help="要求线上响应带 X-TiYouJu-Version 头")
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []

    # ---------- 离线模式：只核对本地文件 ----------
    if args.file:
        path = Path(args.file)
        if not path.is_absolute():
            path = REPO_ROOT / path
        html = path.read_text(encoding="utf-8")
        badge = extract_badge(html)
        meta = extract_meta(html)
        expect = args.expect or ""
        print(f"本地文件: {path}")
        print(f"  静态徽章      : {badge}")
        print(f"  静态 hero 文案: {meta}")
        if not badge or not meta:
            failures.append("本地 HTML 里找不到版本号落点")
        elif badge != meta:
            failures.append(f"本地两处版本号不一致：{badge} vs {meta}")
        if expect:
            print(f"  期望版本号    : {expect}")
            if badge != expect:
                failures.append(f"本地版本号 {badge} != 期望 {expect}")
        if failures:
            print("\nFAILED: " + "; ".join(failures))
            return 1
        print("\nALL CHECKS PASSED")
        return 0

    # ---------- 线上模式 ----------
    target = args.target if args.target.endswith("/") else args.target + "/"
    api_url = target + "api/latest-release"

    try:
        api_body, _ = fetch(api_url)
        release = json.loads(api_body)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"读不到 {api_url}：{exc}")
        print("\nFAILED: 无法确认真实最新版本")
        return 1

    api_tag = str(release.get("tag") or "").strip()
    print(f"真实最新版（/api/latest-release）: {api_tag or '(空)'}")

    try:
        html, headers = fetch(target)
    except (urllib.error.URLError, OSError) as exc:
        print(f"读不到 {target}：{exc}")
        print("\nFAILED: 无法读取线上首页")
        return 1

    badge = extract_badge(html)
    meta = extract_meta(html)
    injected_header = headers.get("x-tiyouju-version", "").strip()

    print(f"线上首页徽章              : {badge or '(没找到)'}")
    print(f"线上首页 hero 文案版本号  : {meta or '(没找到)'}")
    print(f"X-TiYouJu-Version 响应头  : {injected_header or '(无 —— worker 注入未生效)'}")

    if not api_tag:
        failures.append("/api/latest-release 没返回 tag")
    else:
        if badge != api_tag:
            failures.append(f"首页徽章 {badge or '(空)'} != 真实最新版 {api_tag}")
        else:
            print("\n  OK   首页徽章与真实最新版一致")
        if meta != api_tag:
            failures.append(f"首页 hero 文案 {meta or '(空)'} != 真实最新版 {api_tag}")
        else:
            print("  OK   首页 hero 文案与真实最新版一致")

        if args.require_injected and not injected_header:
            failures.append("线上没有 X-TiYouJu-Version 头，worker 注入未生效")
        elif not injected_header:
            warnings.append("worker 注入未生效（当前靠静态兜底恰好对上）")

        # 仓库里的静态兜底不能比真实版本旧，否则老问题复发。
        static_html = INDEX.read_text(encoding="utf-8")
        static_badge = extract_badge(static_html)
        print(f"仓库静态兜底              : {static_badge or '(没找到)'}")
        if static_badge and version_tuple(static_badge) < version_tuple(api_tag):
            failures.append(f"仓库静态兜底 {static_badge} 比真实最新版 {api_tag} 还旧")
        elif static_badge:
            print("  OK   仓库静态兜底不落后于真实最新版")

    for w in warnings:
        print(f"  WARN {w}")

    if failures:
        print("\nFAILED: " + "; ".join(failures))
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
