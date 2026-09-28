"""Probe a MiniMax key: available models, latency and how much concurrency it tolerates.

python probe_minimax.py --secrets secrets.json --image some_crop.jpg [--levels 1,2,4,8] [--models MiniMax-M3]
Writes probe.json next to this script.
"""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

HOSTS = ["api.minimaxi.com", "api.minimax.io"]
PROMPT = ("你是数学资料誊录员。只誊录图中印刷体内容，数学式用 LaTeX（$...$），忽略手写。\n"
          "只按格式输出：\n【题号】\n【题型】单选题/多选题/填空题/解答题\n【题干】\n…\n【A】…\n【B】…\n【C】…\n【D】…")


def call(host, key, model, data_url, max_tokens=1500, thinking=False):
    payload = {"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": PROMPT}, {"type": "image_url", "image_url": {"url": data_url}}]}],
        "temperature": 0, "stream": False, "max_completion_tokens": max_tokens}
    if not thinking:
        payload["thinking"] = {"type": "disabled"}
        payload["reasoning_split"] = True
    start = time.monotonic()
    try:
        r = requests.post(f"https://{host}/v1/chat/completions", json=payload, timeout=(10, 180),
                          headers={"Authorization": f"Bearer {key}"})
        elapsed = time.monotonic() - start
        body = {}
        try:
            body = r.json()
        except Exception:
            pass
        text = ""
        try:
            text = body["choices"][0]["message"]["content"]
        except Exception:
            pass
        err = "" if r.status_code == 200 else json.dumps(body, ensure_ascii=False)[:300]
        return {"status": r.status_code, "elapsed": round(elapsed, 2), "usage": body.get("usage"),
                "text": text[:800], "error": err, "retry_after": r.headers.get("Retry-After")}
    except Exception as e:
        return {"status": None, "elapsed": round(time.monotonic() - start, 2), "error": repr(e)[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secrets", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--levels", default="1,2,4,8")
    ap.add_argument("--models", default="MiniMax-M3")
    ap.add_argument("--out", default="probe.json")
    args = ap.parse_args()
    key = json.loads(Path(args.secrets).read_text(encoding="utf-8"))["minimax"]
    raw = Path(args.image).read_bytes()
    data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    report = {"models": {}, "hosts": {}, "levels": {}}

    for host in HOSTS:
        try:
            r = requests.get(f"https://{host}/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
            report["hosts"][host] = {"status": r.status_code, "body": r.text[:3000]}
        except Exception as e:
            report["hosts"][host] = {"error": repr(e)}
        print(host, report["hosts"][host].get("status"), flush=True)
    host = next((h for h in HOSTS if report["hosts"][h].get("status") == 200), HOSTS[0])
    single = call(host, key, "MiniMax-M3", data_url)
    if single["status"] != 200:
        for alt in HOSTS:
            if alt != host:
                test = call(alt, key, "MiniMax-M3", data_url)
                if test["status"] == 200:
                    host, single = alt, test
                    break
    report["host"] = host
    print("host", host, single["status"], single["elapsed"], single.get("error", "")[:200], flush=True)

    for model in args.models.split(","):
        results = [call(host, key, model, data_url) for _ in range(2)]
        report["models"][model] = results
        print("model", model, [(r["status"], r["elapsed"]) for r in results], flush=True)

    for level in [int(x) for x in args.levels.split(",")]:
        count = max(4, level * 2)
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=level) as pool:
            results = list(pool.map(lambda _: call(host, key, "MiniMax-M3", data_url), range(count)))
        wall = time.monotonic() - start
        ok = [r["elapsed"] for r in results if r["status"] == 200]
        report["levels"][level] = {"wall": round(wall, 2), "count": count,
                                   "statuses": [r["status"] for r in results],
                                   "median": statistics.median(ok) if ok else None,
                                   "max": max(ok) if ok else None,
                                   "errors": [r["error"] for r in results if r["status"] != 200][:3],
                                   "texts_identical": len({r.get("text") for r in results if r["status"] == 200})}
        print("level", level, report["levels"][level], flush=True)
        time.sleep(3)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
