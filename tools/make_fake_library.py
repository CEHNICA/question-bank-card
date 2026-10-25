"""造一套虚构题库，给验收和实机演示用。

**全是编的题。** 不复制真实题库的题，不复制原卷，不碰用户的数据。
用它验证的是「界面和流程能不能走通」，不是数学内容对不对 ——
数学对不对要人核对，系统替代不了。

用法：

    python tools/make_fake_library.py --database <db 路径> --count 24

跑完再 ``manage.py migrate``，然后起服务就能看见这些题。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 虚构题面：一句话一个考点，够看清流程，不装成真卷。
FAKE_STEMS = [
    "已知函数 f(x)=x^2-2x+3，求它的最小值，并说明理由。",
    "在△ABC 中，AB=AC，D 是 BC 的中点，求证 AD⊥BC。",
    "已知 a>0，b>0，且 1/a + 1/b = 1，求证 a+b ≥ 4。",
    "求圆 x^2+y^2=4 与直线 y=x 的交点坐标。",
    "已知等差数列 {a_n} 的前 n 项和 S_n=100，求 a_1。",
    "在△ABC 中，∠C=90°，AC=3，BC=4，求 AB 的长。",
    "已知向量 a=(1,2)，b=(-2,1)，求 a·b 的值。",
    "过点 P(1,2) 作圆 x^2+y^2=5 的切线，求切线方程。",
    "已知 f(x)=x^3-3x^2+2x，求 f(x) 的极值点。",
    "在梯形 ABCD 中，AD∥BC，AB=CD，证明它是等腰梯形。",
    "解不等式组：x-1>0 且 x+2≤5。",
    "已知 sinα+cosα=1/2，求 sin2α 的值。",
    "抛物线 y^2=4x 的焦点坐标是什么？它到准线的距离是多少？",
    "在△ABC 中，AD 是 BC 边上的中线，求证 AB=AC 时 AD⊥BC。",
    "已知数列 {a_n} 满足 a_1=1，a_{n+1}=2a_n，求通项公式。",
]

# 一题两问的小问题面。
FAKE_SUBQUESTION_STEMS = [
    "已知 a>0，b>0：\n（1）求证 a+b ≥ 2√(ab)；\n（2）当 a+b=4 时求 ab 的最大值。",
    "在△ABC 中，∠C=90°：\n（1）求证 AB 的中点 D 到 AC 的距离等于 BC 的一半；\n（2）若 AC=3，BC=4，求 AD。",
]

# 两个方法分属两个解法的题（用来看「同解法 AND」不误命中）。
FAKE_TWO_VARIANT_STEMS = [
    "已知四边形 ABCD 中，AB∥CD，AD=BC。\n（1）说明这个四边形是等腰梯形；\n（2）若 AD=2，AB=3，CD=5，求面积。"
]

CONCEPTS = [
    ("二次函数的最值", "knowledge", ["二次函数最值", "抛物线顶点"]),
    ("等腰三角形", "knowledge", []),
    ("基本不等式", "knowledge", ["均值不等式"]),
    ("直线与圆的位置关系", "knowledge", []),
    ("等差数列通项", "knowledge", ["等差数列"]),
    ("勾股定理", "knowledge", []),
    ("向量数量积", "knowledge", ["点积"]),
    ("圆的切线", "knowledge", ["切线方程"]),
    ("导数与极值", "knowledge", ["函数极值"]),
    ("等腰梯形", "knowledge", []),
    ("一元一次不等式组", "knowledge", []),
    ("二倍角公式", "knowledge", []),
    ("抛物线的焦点", "knowledge", ["焦点坐标"]),
    ("中位线定理", "knowledge", ["三角形中位线"]),
    ("构造中位线", "method", ["构造法"]),
    ("全等三角形", "method", []),
    ("配方法", "method", []),
    ("分类讨论", "method", ["分类"]),
    ("反证法", "method", []),
]


def build(args) -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
    os.environ["QB_DATABASE"] = str(Path(args.database).resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    import django

    django.setup()

    from core.models import (
        LibrarySolution,
        LibrarySolutionVariant,
        LibrarySubquestion,
        LibraryTag,
        LibraryTagAssignment,
        LibraryTagSuggestion,
        Paper,
        PublishedQuestion,
        Question,
    )
    from core.textnorm import concept_key

    paper = Paper.objects.create(filename="虚构验收卷.pdf", kind="pdf", sha256="a" * 64)

    tags = {}
    for name, kind, aliases in CONCEPTS:
        tag, _ = LibraryTag.objects.get_or_create(
            name=name,
            kind=kind,
            defaults={"canonical_key": concept_key(name), "aliases": aliases, "description": f"虚构的{kind}：{name}"},
        )
        tags[name] = tag

    def make_publication(stem, number, *, analysis="", answer=""):
        question = Question.objects.create(
            paper=paper, number=number, question_type="free_response", stem=stem, state="green"
        )
        content = {
            "stem": stem,
            "options": {},
            "answer": answer,
            "analysis": analysis,
            "figures": [],
            "source_filename": "虚构验收卷.pdf",
            "section": {},
            "origin": "",
        }
        return PublishedQuestion.objects.create(
            question=question,
            paper=paper,
            source_filename="虚构验收卷.pdf",
            number=number,
            question_type="free_response",
            version=1,
            content=content,
            content_hash=(f"fake{number:04d}" * 16)[:64],
            search_text=stem + " " + analysis,
        )

    number = 0
    publications = []
    for stem in FAKE_STEMS:
        number += 1
        publications.append(make_publication(stem, number, analysis="这是虚构解析，只为看流程。"))
    for stem in FAKE_SUBQUESTION_STEMS:
        number += 1
        publications.append(make_publication(stem, number, analysis="（1）虚构推导。（2）虚构结论。"))
    for stem in FAKE_TWO_VARIANT_STEMS:
        number += 1
        publications.append(make_publication(stem, number, analysis="解法一：虚构。解法二：虚构。"))

    # 给一部分题贴上正式关联，覆盖「主要考查 / 辅助用到 / 解法采用」三种用途。
    pairs = [
        ("二次函数的最值", "primary"),
        ("导数与极值", "primary"),
        ("等腰三角形", "primary"),
        ("基本不等式", "primary"),
        ("勾股定理", "supporting"),
        ("向量数量积", "primary"),
        ("圆的切线", "primary"),
        ("等差数列通项", "primary"),
        ("抛物线的焦点", "primary"),
        ("中位线定理", "primary"),
    ]
    for index, (concept_name, role) in enumerate(pairs):
        publication = publications[index % len(publications)]
        tag = tags[concept_name]
        LibraryTagAssignment.objects.get_or_create(
            publication=publication,
            tag=tag,
            role=role,
            scope_key="whole",
            defaults={
                "scope": {"whole": True},
                "source": LibraryTagAssignment.Source.HUMAN,
                "rationale": "虚构演示数据：这道题主要考它。",
                "evidence": "虚构依据：题面里出现了对应的条件。",
                "review_status": LibraryTagAssignment.ReviewStatus.CONFIRMED,
                "evidence_status": LibraryTagAssignment.EvidenceStatus.CURRENT,
            },
        )

    # 两小问的题：拆出小问，给两个小问各记一个方法。
    sub_publication = publications[len(FAKE_STEMS)]
    for index, label in enumerate(["（1）", "（2）"]):
        LibrarySubquestion.objects.get_or_create(
            publication=sub_publication,
            stable_key=f"sq:{index + 1}",
            defaults={"display_label": label, "position": index},
        )
    variant = LibrarySolutionVariant.objects.create(
        publication=sub_publication,
        name="解法一",
        origin=LibrarySolutionVariant.Origin.SAVED,
        status=LibrarySolutionVariant.Status.ACTIVE,
    )
    LibrarySolution.objects.create(
        publication=sub_publication,
        variant=variant,
        fingerprint=(f"sol{sub_publication.pk}" * 8)[:64],
        answer="虚构答案",
        analysis="（1）虚构推导。（2）虚构结论。",
        figures=[],
    )
    for index, concept_name in enumerate(("配方法", "分类讨论")):
        sub = LibrarySubquestion.objects.get(publication=sub_publication, stable_key=f"sq:{index + 1}")
        LibraryTagAssignment.objects.get_or_create(
            publication=sub_publication,
            tag=tags[concept_name],
            role="method",
            scope_key=f"sub:{sub.pk}+variant:{variant.pk}",
            defaults={
                "scope": {"whole": False},
                "source": LibraryTagAssignment.Source.HUMAN,
                "rationale": "虚构演示数据：这个方法在这个小问里用。",
                "evidence": "虚构依据：解析里写了这一步。",
                "review_status": LibraryTagAssignment.ReviewStatus.CONFIRMED,
                "evidence_status": LibraryTagAssignment.EvidenceStatus.CURRENT,
                "variant": variant,
                "subquestion": sub,
            },
        )

    # 一个方法分属两个解法的题 —— 用来验证「同解法 AND」不会误命中。
    two_variant_publication = publications[-1]
    variant_one = LibrarySolutionVariant.objects.create(
        publication=two_variant_publication,
        name="解法一",
        origin=LibrarySolutionVariant.Origin.SAVED,
        status=LibrarySolutionVariant.Status.ACTIVE,
    )
    variant_two = LibrarySolutionVariant.objects.create(
        publication=two_variant_publication,
        name="解法二",
        origin=LibrarySolutionVariant.Origin.OTHER_CONFIRMED,
        status=LibrarySolutionVariant.Status.ALTERNATIVE,
    )
    for variant_row, concept_name in ((variant_one, "全等三角形"), (variant_two, "分类讨论")):
        LibraryTagAssignment.objects.get_or_create(
            publication=two_variant_publication,
            tag=tags[concept_name],
            role="method",
            scope_key=f"variant:{variant_row.pk}",
            defaults={
                "scope": {"whole": True},
                "source": LibraryTagAssignment.Source.HUMAN,
                "rationale": "虚构演示数据：这个方法在这个解法里用。",
                "evidence": "虚构依据：解析里写了这一步。",
                "review_status": LibraryTagAssignment.ReviewStatus.CONFIRMED,
                "evidence_status": LibraryTagAssignment.EvidenceStatus.CURRENT,
                "variant": variant_row,
            },
        )

    # 一条待核对候选：只进候选，不进正式关联。
    LibraryTagSuggestion.objects.get_or_create(
        publication=publications[0],
        status=LibraryTagSuggestion.Status.PENDING,
        defaults={
            "suggestions": [
                {
                    "name": "函数的单调性",
                    "kind": "knowledge",
                    "role": "primary",
                    "scope": {"whole": True},
                    "rationale": "虚构建议：这道题可能在考单调性。",
                    "evidence": "虚构依据：题面里出现了递减的描述（需要你核对）。",
                },
                {
                    "name": "配方法",
                    "kind": "method",
                    "role": "method",
                    "scope": {"whole": True},
                    "rationale": "虚构建议：也许用的是配方法。",
                    "evidence": "虚构依据：需要你回到解析里核对。",
                },
            ]
        },
    )

    print(
        "虚构题库造好了：%d 道题，%d 个概念，%d 条正式关联，%d 条待核对候选。"
        % (
            PublishedQuestion.objects.count(),
            LibraryTag.objects.count(),
            LibraryTagAssignment.objects.count(),
            LibraryTagSuggestion.objects.count(),
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="造虚构题库，用于验收和演示")
    parser.add_argument("--database", required=True, help="写到哪个 sqlite 文件")
    parser.add_argument("--count", type=int, default=0, help="保留参数位，题量由上面的表决定")
    args = parser.parse_args()
    return build(args)


if __name__ == "__main__":
    raise SystemExit(main())
