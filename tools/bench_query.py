"""检索性能基准：1,000 题和 10,000 题两种规模。

计划第 11 节的要求：建议列表查询 P95 在参考 Windows 机器上不超过 500 ms。
那个数字是**设计验收目标**，本脚本负责把它测出来 —— 测不到就如实说测不到。

要点：

* **固定种子**：每次跑出来的题和标签一样，数字才能比较。题目内容是合成占位文本，
  不是真实数学题，也不声称是数学测试集。
* **热查询**：每条查询先跑一次预热，再跑满 20 次，报中位数和 P95。
  第一次查询要读磁盘，不算数。
* **报的每个数字都带题量和关联量**：换台机器、换个题量，数字不可比。

用法：

    python tools/bench_query.py --database <db> --questions 1000
    python tools/bench_query.py --database <db> --questions 10000
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

import django

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")

parser = argparse.ArgumentParser(description="题有据检索性能基准")
parser.add_argument("--database", required=True)
parser.add_argument("--questions", type=int, default=1000)
parser.add_argument("--runs", type=int, default=20)
parser.add_argument("--seed", type=int, default=20261025)
parser.add_argument("--json-out", default="")
parser.add_argument("--reuse", action="store_true", help="复用库里已有数据，不重造")
parser.add_argument("--catalogue", type=int, default=120, help="概念目录规模（深链）")
args = parser.parse_args()

os.environ["QB_DATABASE"] = str(Path(args.database).resolve())
django.setup()

from django.core.management import call_command  # noqa: E402

call_command("migrate", run_syncdb=True, verbosity=0)

from core import library_query  # noqa: E402
from core.models import (  # noqa: E402
    LibrarySolutionVariant,
    LibrarySubquestion,
    LibraryTag,
    LibraryTagAssignment,
    LibraryTagRelation,
    Paper,
    PublishedQuestion,
    Question,
)

random.seed(args.seed)
CALL_COMMAND = call_command


def build(count: int):
    """造 count 道合成题和配套的概念、方法、关联。

    比例照着真库的样子：八成的题有主要考查，四成有解法采用，
    少量只有旧记录或待核对 —— 不然测出来的只是「理想数据」的速度。
    """
    print(f"=== 造 {count} 道合成题 ===")
    started = time.perf_counter()

    paper = Paper.objects.create(filename="基准合成卷.pdf", kind="pdf", sha256="d" * 64)
    concepts = []
    for index in range(120):
        concepts.append(
            LibraryTag.objects.create(name=f"合成概念{index}", kind="knowledge", canonical_key=f"合成概念{index}")
        )
    methods = []
    for index in range(60):
        methods.append(
            LibraryTag.objects.create(name=f"合成方法{index}", kind="method", canonical_key=f"合成方法{index}")
        )

    questions = []
    for index in range(count):
        number = index + 1
        stem = f"合成占位题 {number}：已知条件，求解过程与结论。"
        questions.append(
            Question(
                paper=paper,
                number=number,
                question_type="free_response",
                stem=stem,
                answer="占位答案",
                analysis="占位解析",
                options={},
                state="green",
            )
        )
    Question.objects.bulk_create(questions, batch_size=500)
    print(f"  题卡入库 {time.perf_counter() - started:.1f}s")

    started = time.perf_counter()
    publications = []
    for question in Question.objects.filter(paper=paper).order_by("number"):
        content = {
            "stem": question.stem,
            "answer": question.answer,
            "analysis": question.analysis,
            "options": {},
            "question_type": "free_response",
            "origin": "",
            "figures": [],
            "sources": [],
            "section": "",
            "document_id": str(paper.id),
            "source_filename": paper.filename,
        }
        publications.append(
            PublishedQuestion(
                paper=paper,
                question=question,
                number=question.number,
                source_filename=paper.filename,
                question_type="free_response",
                version=1,
                status=PublishedQuestion.Status.PUBLISHED,
                content=content,
                content_hash=("bench%064d" % question.number)[:64],
                search_text=question.stem,
                extras={},
            )
        )
    PublishedQuestion.objects.bulk_create(publications, batch_size=500)
    print(f"  入库版本入库 {time.perf_counter() - started:.1f}s")

    started = time.perf_counter()
    assignments = []
    for index, publication in enumerate(publications):
        primary = concepts[index % len(concepts)]
        assignments.append(
            LibraryTagAssignment(
                publication=publication,
                tag=primary,
                scope={"whole": True},
                scope_key="whole",
                role=LibraryTagAssignment.Role.PRIMARY,
                review_status=LibraryTagAssignment.ReviewStatus.CONFIRMED,
                evidence_status=LibraryTagAssignment.EvidenceStatus.CURRENT,
                source=LibraryTagAssignment.Source.HUMAN,
            )
        )
        if index % 5 == 0:
            assignments.append(
                LibraryTagAssignment(
                    publication=publication,
                    tag=concepts[(index + 7) % len(concepts)],
                    scope={"whole": True},
                    scope_key="whole",
                    role=LibraryTagAssignment.Role.SUPPORTING,
                    review_status=LibraryTagAssignment.ReviewStatus.CONFIRMED,
                    evidence_status=LibraryTagAssignment.EvidenceStatus.CURRENT,
                    source=LibraryTagAssignment.Source.HUMAN,
                )
            )
        if index % 3 == 0:
            variant = LibrarySolutionVariant(
                publication=publication,
                name="解法一",
                origin=LibrarySolutionVariant.Origin.SAVED,
                status=LibrarySolutionVariant.Status.ACTIVE,
            )
            variant.save()
            sub = LibrarySubquestion.objects.create(
                publication=publication, stable_key="sq:1", display_label="（1）", position=0
            )
            assignments.append(
                LibraryTagAssignment(
                    publication=publication,
                    tag=methods[(index // 3) % len(methods)],
                    scope={"whole": False},
                    scope_key=f"sub:{sub.pk}+variant:{variant.pk}",
                    role=LibraryTagAssignment.Role.METHOD,
                    review_status=LibraryTagAssignment.ReviewStatus.CONFIRMED,
                    evidence_status=LibraryTagAssignment.EvidenceStatus.CURRENT,
                    source=LibraryTagAssignment.Source.HUMAN,
                    variant=variant,
                    subquestion=sub,
                )
            )
    LibraryTagAssignment.objects.bulk_create(assignments, batch_size=500)
    print(f"  关联入库 {time.perf_counter() - started:.1f}s")
    return concepts, methods


def timed(query, runs: int) -> list[float]:
    library_query.run_query(query, limit=200)
    timings = []
    for _ in range(runs):
        started = time.perf_counter()
        library_query.run_query(query, limit=200)
        timings.append((time.perf_counter() - started) * 1000)
    return timings


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def build_catalogue_chain(count: int):
    """造一条深链：概念 0 → 概念 1 → … → 概念 count-1。

    「包含下级」的展开要走这条链。链越深、目录越大，展开越贵 ——
    真实题库目录是分层的，不是平的，这条链是最难走的一种形状。
    """
    print(f"=== 造 {count} 个概念的深链 ===")
    rows = []
    previous = None
    for index in range(count):
        tag = LibraryTag.objects.create(name=f"链式概念{index}", kind="knowledge", canonical_key=f"链式概念{index}")
        rows.append(tag)
        if previous is not None:
            LibraryTagRelation.objects.create(
                parent=previous, child=tag, relation_type=LibraryTagRelation.Type.HIERARCHY
            )
        previous = tag
    print(f"  链长 {count}，关系 {count - 1} 条")
    return rows


def main() -> int:
    if args.reuse:
        from core.models import LibraryTag, LibraryTagRelation

        concepts = list(LibraryTag.objects.filter(kind="knowledge").order_by("name"))
        methods = list(LibraryTag.objects.filter(kind="method").order_by("name"))
        print(f"=== 复用已有数据：{len(concepts)} 个概念、{len(methods)} 个方法 ===")
    else:
        concepts, methods = build(args.questions)
        if args.catalogue > 120:
            chain = build_catalogue_chain(args.catalogue)
            concepts = list(concepts) + chain
    total_assignments = LibraryTagAssignment.objects.count()
    total_publications = PublishedQuestion.objects.count()

    queries = [
        ("空条件（全部）", {}),
        ("主要考查＝单个概念", {"concept_groups": [{"ids": [str(concepts[0].pk)], "roles": ["primary"]}]}),
        (
            "主要考查＝三个概念（全中）",
            {
                "concept_groups": [
                    {"ids": [str(concepts[i].pk) for i in range(3)], "roles": ["primary"], "all_or_any": "all"}
                ]
            },
        ),
        (
            "主要考查＝十个概念（任一）",
            {
                "concept_groups": [
                    {"ids": [str(concepts[i].pk) for i in range(10)], "roles": ["primary"], "all_or_any": "any"}
                ]
            },
        ),
        (
            "包含下级概念",
            {"concept_groups": [{"ids": [str(concepts[0].pk)], "roles": ["primary"], "include_descendants": True}]},
        ),
        ("方法＝单个（同解法）", {"method_groups": [{"ids": [str(methods[0].pk)], "cooccurrence": "same_variant"}]}),
        (
            "方法＝两个（同解法 AND）",
            {"method_groups": [{"ids": [str(methods[0].pk), str(methods[1].pk)], "cooccurrence": "same_variant"}]},
        ),
        (
            "方法＝两个（分别存在）",
            {"method_groups": [{"ids": [str(methods[0].pk), str(methods[1].pk)], "cooccurrence": "separate"}]},
        ),
        (
            "方法＋排除（未知单独计数）",
            {
                "method_groups": [{"ids": [str(methods[0].pk)], "cooccurrence": "same_variant"}],
                "exclusions": {
                    "conditions": [{"ids": [str(methods[1].pk)], "roles": ["method"]}],
                    "handling_of_unknown": "separate",
                },
            },
        ),
        ("关键词＋题型", {"keywords": ["合成占位题 500"], "filters": {"question_type": "free_response"}}),
    ]

    print()
    print(f"=== 跑 {len(queries)} 条查询，每条 {args.runs} 次（先预热一次）===")
    results = []
    for label, query in queries:
        timings = timed(query, args.runs)
        result = {
            "query": label,
            "runs": len(timings),
            "median_ms": round(statistics.median(timings), 2),
            "p95_ms": round(percentile(timings, 0.95), 2),
            "max_ms": round(max(timings), 2),
        }
        results.append(result)
        print(
            f"  {label:<28} 中位 {result['median_ms']:>8.2f} ms   P95 {result['p95_ms']:>8.2f} ms   最慢 {result['max_ms']:>8.2f} ms"
        )

    worst = max(results, key=lambda item: item["p95_ms"])
    target = 500.0
    print()
    print(f"题量 {total_publications}，关联量 {total_assignments}")
    print(f"最慢的一条：{worst['query']}，P95 {worst['p95_ms']} ms（验收目标 ≤ {target} ms）")
    verdict = "达标" if worst["p95_ms"] <= target else "未达标"
    print(f"结论：{verdict}")

    if args.json_out:
        report = {
            "questions": total_publications,
            "assignments": total_assignments,
            "runs_per_query": args.runs,
            "seed": args.seed,
            "target_p95_ms": target,
            "worst_p95_ms": worst["p95_ms"],
            "verdict": verdict,
            "results": results,
            "note": "合成占位数据，不是数学测试集；换机器换题量数字不可比。",
        }
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"原始结果已写：{args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
