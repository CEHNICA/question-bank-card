"""造一条「落在第（2）问」的待核对候选，给「用在第几问」的真机验收用。

**全是编的题。** 不碰用户的数据，只在隔离库里造。

它要能证明的事：建议说第（2）问，老师在界面上改成第（1）问，采纳后
落库就得是第（1）问 —— 而且范围键和小问外键都在（只写范围键的话，
按小问检索永远搜不到这条）。

用法：

    python tools/make_scope_fixture.py --database <db 路径>
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

STEM = "已知 a>0，b>0：\n（1）求证 a+b ≥ 2√(ab)；\n（2）当 a+b=4 时求 ab 的最大值。"


def build(args) -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
    os.environ["QB_DATABASE"] = str(Path(args.database).resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    import django

    django.setup()

    from core import library_concepts, library_tagging
    from core.models import (
        LibrarySolution,
        LibrarySolutionVariant,
        LibraryTag,
        LibraryTagSuggestion,
        Paper,
        PublishedQuestion,
        Question,
    )
    from core.textnorm import concept_key

    paper = Paper.objects.create(filename="虚构范围验收卷.pdf", kind="pdf", sha256="b" * 64)
    question = Question.objects.create(paper=paper, number=1, question_type="free_response", stem=STEM, state="green")
    content = {
        "stem": STEM,
        "options": {},
        "answer": "虚构答案",
        "analysis": "（1）虚构推导。（2）虚构结论。",
        "figures": [],
        "source_filename": "虚构范围验收卷.pdf",
        "section": {},
        "origin": "",
    }
    publication = PublishedQuestion.objects.create(
        question=question,
        paper=paper,
        source_filename="虚构范围验收卷.pdf",
        number=1,
        question_type="free_response",
        version=1,
        content=content,
        content_hash=("scope" * 16)[:64],
        search_text=STEM,
    )

    subquestions = library_concepts.ensure_subquestions(publication)
    if len(subquestions) != 2:
        print("小问没拆出来 —— 题面格式变了？")
        return 2

    variant = LibrarySolutionVariant.objects.create(
        publication=publication,
        name="解法一",
        origin=LibrarySolutionVariant.Origin.SAVED,
        status=LibrarySolutionVariant.Status.ACTIVE,
    )
    LibrarySolution.objects.create(
        publication=publication,
        variant=variant,
        fingerprint=("scopesol" * 8)[:64],
        answer="虚构答案",
        analysis="（1）虚构推导。（2）虚构结论。",
        figures=[],
    )

    tag, _ = LibraryTag.objects.get_or_create(
        name="配方法",
        kind=LibraryTag.Kind.METHOD,
        defaults={"canonical_key": concept_key("配方法"), "aliases": ["配方"], "description": "虚构的方法"},
    )

    catalogue = library_tagging.catalogue_state()
    suggestion = LibraryTagSuggestion.objects.create(
        publication=publication,
        status=LibraryTagSuggestion.Status.PENDING,
        taxonomy_revision=catalogue.revision,
        assignment_revision=publication.tag_revision,
        generation_source="虚构生成器",
        # 方法建议必须记下它依据的那一版解析 —— 采纳时服务端要核对这一版还在不在。
        # 用跟生成流程同一个函数取快照，别自己拼一份：拼的字段不一样，
        # 服务端会判成「解析版本已变化」。
        method_source=library_tagging.method_source_snapshot(publication),
        suggestions=[
            {
                "name": "配方法",
                "tag_id": str(tag.pk),
                "kind": "method",
                "role": "method",
                # 建议说的是第（2）问 —— 界面上要能改成第（1）问。
                "scope": {"subquestions": ["2"]},
                "rationale": "虚构建议：第（2）问看起来用的是配方法。",
                "evidence": "虚构依据：第（2）问的推导里有配方（需要你核对）。",
            }
        ],
    )

    print(
        "范围验收数据造好了：题 %s，小问 %s，建议 %s（建议说第（2）问）"
        % (publication.pk, [row.stable_key for row in subquestions], suggestion.pk)
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="造一条带小问范围的虚构候选")
    parser.add_argument("--database", required=True, help="写到哪个 sqlite 文件")
    args = parser.parse_args()
    return build(args)


if __name__ == "__main__":
    raise SystemExit(main())
