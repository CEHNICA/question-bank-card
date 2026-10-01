"""题有据 real-API benchmark harness (developer tool, not shipped).

Runs the real pipeline (MinerU -> segmentation -> vision reading) on sample
files in an isolated data directory and records per-stage / per-call timings
plus every card's readings and crops, so accuracy can be judged afterwards.

Usage (Windows PowerShell, from the bench folder):
  python tools\\benchmark\\qb_bench.py --repo . --out D:\\qb-bench\\runs\\v1 --secrets D:\\qb-bench\\secrets.json FILE [FILE ...]
A photo set is given as "a.jpg|b.jpg|c.jpg".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

EVENTS: list[dict] = []
EVENT_LOCK = threading.Lock()
T0 = time.monotonic()


def now() -> float:
    return round(time.monotonic() - T0, 3)


def record(**event) -> None:
    with EVENT_LOCK:
        EVENTS.append(event)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--secrets", required=True)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--material", default="exam")
    ap.add_argument("--env", action="append", default=[], help="extra KEY=VALUE for the run")
    ap.add_argument("--no-overlap", action="store_true", help="with --worker: no overlap lane")
    ap.add_argument("--worker", action="store_true",
                    help="queue every input first and run them like the app's worker (parse lane on)")
    ap.add_argument("inputs", nargs="+")
    args = ap.parse_args()

    secrets = json.loads(Path(args.secrets).read_text(encoding="utf-8"))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["QB_DATABASE"] = str(out / "db.sqlite3")
    os.environ["QB_DATA_ROOT"] = str(out / "data")
    os.environ["QB_MODEL_PREFERENCES_FILE"] = str(out / "model-preferences.json")
    os.environ["MINERU_TOKEN"] = secrets["mineru"]
    os.environ["MINIMAX_API_KEY"] = secrets["minimax"]
    if secrets.get("siliconflow"):
        os.environ["SILICONFLOW_API_KEY"] = secrets["siliconflow"]
    os.environ["QB_PARALLEL"] = str(args.parallel)
    for item in args.env:
        key, _, value = item.partition("=")
        os.environ[key] = value
    backend = Path(args.repo).resolve() / "backend"
    sys.path.insert(0, str(backend))
    os.environ["DJANGO_SETTINGS_MODULE"] = "qb_server.settings"
    import django

    django.setup()
    from django.core.management import call_command

    call_command("migrate", verbosity=0)
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    from core import imaging, pipeline, readers
    from core.models import Paper, Question

    # ---------------------------------------------------------- instrumentation
    original_post = readers._post

    def timed_post(url, key, payload, timeout=(10, 150)):
        text = ""
        try:
            text = payload["messages"][0]["content"][0]["text"]
        except Exception:
            pass
        kind = ("spotcheck" if "每一处空位上印的是甲还是乙" in text else
                "arbiter" if "已经被独立誊录了两次" in text else
                "locate" if "编号刻度" in text else
                "read_fig" if "蓝色框" in text else "read")
        start = now()
        status = None
        try:
            response = original_post(url, key, payload, timeout)
            status = response.status_code
            usage = {}
            try:
                usage = response.json().get("usage") or {}
            except Exception:
                pass
            return response
        finally:
            record(type="api", provider=url.split("/")[2], model=payload.get("model"), kind=kind,
                   start=start, end=now(), status=status,
                   usage=locals().get("usage", {}), thread=threading.get_ident())

    readers._post = timed_post

    def wrap_stage(name):
        original = getattr(pipeline, name)

        def inner(*a, **k):
            start = now()
            try:
                return original(*a, **k)
            finally:
                record(type="stage", name=name, start=start, end=now())

        setattr(pipeline, name, inner)

    for stage in ("parse", "segment_paper", "read_questions", "request_extract_file_from_pool",
                  "_chunk_blocks", "prepare_photos"):
        if hasattr(pipeline, stage):
            wrap_stage(stage)

    client = Client()
    summary = []

    def upload(spec):
        files = [Path(p) for p in spec.split("|")]
        label = files[0].stem if len(files) == 1 else f"{files[0].stem}+{len(files) - 1}"
        uploads = [SimpleUploadedFile(f.name, f.read_bytes()) for f in files]
        response = client.post(
            "/api/papers", {"file": uploads, "material_type": args.material},
            HTTP_X_QB_REQUEST="1",
        )
        if response.status_code not in (200, 201):
            print("upload failed", spec, response.status_code, response.content[:300])
            return None
        paper = Paper.objects.get(pk=response.json()["paper"]["id"])
        print(f"[{now():7.1f}s] {label}: uploaded as {paper.id}", flush=True)
        return paper, label, files

    def unblock(paper):
        if paper.status == Paper.Status.NEEDS_GROUPING:
            r = client.post(f"/api/papers/{paper.id}/confirm-structure", "{}",
                            content_type="application/json", HTTP_X_QB_REQUEST="1")
            print(f"  confirm structure -> {r.status_code}", flush=True)
            paper.refresh_from_db()
            if paper.status == Paper.Status.NEEDS_GROUPING:
                paper.status = Paper.Status.SEGMENTING
                paper.save()

    def run_one(paper):
        guard = 0
        while guard < 10:
            guard += 1
            paper.refresh_from_db()
            if paper.status in (Paper.Status.READY, Paper.Status.FAILED):
                break
            if paper.status == Paper.Status.NEEDS_GROUPING:
                unblock(paper)
                continue
            try:
                pipeline.process_paper(paper)
            except Exception:
                traceback.print_exc()
                break

    def dump(paper, label, files, started, finished):
        paper.refresh_from_db()
        folder = out / "cards" / label
        folder.mkdir(parents=True, exist_ok=True)
        store = pipeline.PageStore(paper)
        cards = []
        for q in Question.objects.filter(paper=paper).order_by("group__sequence", "number", "id"):
            crop_name = f"q{q.number:02d}_{q.id}.jpg"
            try:
                if q.regions:
                    image, _ = imaging.stack_regions(q.regions, store.load)
                    image = image.convert("RGB")
                    image.thumbnail((1600, 1600))
                    image.save(folder / crop_name, quality=85)
            except Exception as error:
                crop_name = f"ERROR {error}"
            cards.append({
                "id": q.id, "number": q.number, "group": q.group_id, "section": q.section,
                "type": q.question_type, "state": q.state, "flags": q.flags, "error": q.error,
                "text_source": q.text_source, "stem": q.stem, "options": q.options,
                "regions": q.regions, "figure_candidates": q.figure_candidates, "figures": q.figures,
                "figure_review": q.figure_review, "read_a": q.read_a, "read_b": q.read_b, "read_c": q.read_c,
                "source_kind": q.source_kind, "crop": crop_name,
            })
        for index in range(len(paper.pages)):
            try:
                preview = store.preview(index)
                (folder / f"page_{index + 1:02d}.jpg").write_bytes(Path(preview).read_bytes())
            except Exception:
                pass
        result = {
            "label": label, "files": [str(f) for f in files], "paper_id": str(paper.id),
            "status": paper.status, "error": paper.error, "pages": len(paper.pages),
            "notes": paper.notes, "structure": paper.structure,
            "elapsed": round(finished - started, 1), "cards": cards,
        }
        (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        states = {}
        for card in cards:
            states[card["state"]] = states.get(card["state"], 0) + 1
        line = f"[{now():7.1f}s] {label}: {paper.status} in {finished - started:.1f}s, {len(cards)} cards {states} {paper.error[:120]}"
        print(line, flush=True)
        summary.append(line)

    if args.worker:
        # The whole batch is queued at once, as a user dropping several files
        # would; the worker's main lane reads while its parse lane runs ahead.
        from core.management.commands import run_worker
        from core.account_pool import lease_priority
        batch = [item for item in (upload(spec) for spec in args.inputs) if item]
        started = now()
        stop = threading.Event()
        run_worker.SETTINGS_APPLIED["signature"] = run_worker.settings_signature()
        overlap_done = {}
        original_process = run_worker.process_paper

        def timed_process(paper):
            try:
                original_process(paper)
            finally:
                overlap_done[paper.pk] = now()
                print(f"[{now():7.1f}s] overlap lane finished {paper.pk}", flush=True)

        run_worker.process_paper = timed_process
        lanes = [threading.Thread(target=run_worker.parse_lane, args=(stop, 0.5), daemon=True)]
        if not args.no_overlap:
            lanes.append(threading.Thread(target=run_worker.overlap_lane, args=(stop, 0.5), daemon=True))
        for lane in lanes:
            lane.start()
        finished_at = {}
        while len(finished_at) < len(batch):
            progressed = False
            for paper, label, files in batch:
                if paper.pk in finished_at:
                    continue
                paper.refresh_from_db()
                if paper.status in (Paper.Status.READY, Paper.Status.FAILED):
                    finished_at[paper.pk] = overlap_done.get(paper.pk, now())
                    continue
                if not run_worker.claim_for_main_lane(paper):
                    continue
                try:
                    paper.refresh_from_db()
                    if paper.status in (Paper.Status.READY, Paper.Status.FAILED):
                        finished_at[paper.pk] = overlap_done.get(paper.pk, now())
                        continue
                    print(f"[{now():7.1f}s] main lane starts {label}", flush=True)
                    with lease_priority(run_worker.paper_priority(paper)):
                        run_one(paper)
                finally:
                    run_worker.release_main_lane()
                finished_at[paper.pk] = now()
                progressed = True
            if not progressed:
                time.sleep(0.5)
        stop.set()
        for paper, label, files in batch:
            dump(paper, label, files, started, finished_at[paper.pk])
        print(f"[{now():7.1f}s] batch finished in {max(finished_at.values()) - started:.1f}s", flush=True)
    else:
        for spec in args.inputs:
            item = upload(spec)
            if not item:
                continue
            paper, label, files = item
            started = now()
            run_one(paper)
            dump(paper, label, files, started, now())
    (out / "events.json").write_text(json.dumps(EVENTS, ensure_ascii=False, indent=0), encoding="utf-8")
    (out / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    api = [e for e in EVENTS if e["type"] == "api"]
    if api:
        durations = sorted(e["end"] - e["start"] for e in api)
        print(f"API calls: {len(api)}, median {durations[len(durations)//2]:.1f}s, "
              f"p90 {durations[int(len(durations)*0.9)]:.1f}s, max {durations[-1]:.1f}s, "
              f"non-200: {sum(1 for e in api if e['status'] != 200)}")
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
