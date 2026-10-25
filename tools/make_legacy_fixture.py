"""造一个「旧结构」的虚构题库，用来演练迁移。

**这是旧结构**：跑之前先 ``manage.py migrate core 0029``，把数据库退回到
1.26.29 迁移之后的样子 —— 那时候还没有「用途」「小问」「解法分支」这些东西，
标签是贴在一道题上的一个名字，范围只有整题。

造的东西覆盖迁移里要处理的每一种情况：

* 章节容器（带 ``section:`` 系统来源键）及其下级
* 章节上的直接人工贴标
* 旧知识点正式关联（旧人工编辑）
* 旧方法正式关联
* 同名的历史标签和正式概念（要归并的那一组）
* 旧 AI 候选（待核对、排队中、已拒绝）
* 自定义标签
* 不明确的上下级（父子类型不一致）

用法::

    python manage.py migrate core 0029
    python tools/make_legacy_fixture.py --database <db>
    python manage.py migrate core 0030
    python manage.py shell -c "from core import library_tag_migration as m; print(m.plan_migration())"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def build(database: str) -> int:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
    os.environ["QB_DATABASE"] = str(Path(database).resolve())
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    import django

    django.setup()

    # 这个脚本在**新结构**（0030 已应用）下造数据，但把新字段填成**旧语义** ——
    # 这正是真实迁移的中间状态：0030 先给旧数据补上默认值，
    # 迁移工具再把「用途未细分」「没绑定具体依据」这些语义落实下去。
    # 手工回退到 0029 的表结构反而不对：那样 ORM 写不进去（新字段没列）。
    from django.db import connection

    from core.models import (
        LibraryJob,
        LibraryTag,
        LibraryTagAssignment,
        LibraryTagGroup,
        LibraryTagRelation,
        LibraryTagSuggestion,
        Paper,
        PublishedQuestion,
        Question,
    )

    with connection.cursor() as cursor:
        columns = {column.name for column in connection.introspection.get_table_description(cursor, "core_librarytag")}
    if "canonical_key" not in columns:
        print("这个库还没跑到 0030。先 manage.py migrate core 0030。")
        return 2

    # 新字段一律填旧语义：没有规范身份键、用途未细分、没绑定具体依据。
    legacy_tag_fields = {"canonical_key": "", "scope_note": "", "type_hints": [], "merged_into": None}

    paper = Paper.objects.create(filename="虚构旧卷.pdf", kind="pdf", sha256="b" * 64)

    def make_publication(number: int, stem: str):
        question = Question.objects.create(
            paper=paper, number=number, question_type="free_response", stem=stem, state="green"
        )
        content = {
            "stem": stem,
            "options": {},
            "answer": "",
            "analysis": "虚构旧解析。",
            "figures": [],
            "source_filename": "虚构旧卷.pdf",
            "section": {},
            "origin": "",
        }
        return PublishedQuestion.objects.create(
            question=question,
            paper=paper,
            source_filename="虚构旧卷.pdf",
            number=number,
            question_type="free_response",
            version=1,
            content=content,
            content_hash=(f"old{number:04d}" * 16)[:64],
            search_text=stem,
        )

    # ---- 旧分组（0028 之后会变成章节容器） --------------------------------
    group = LibraryTagGroup.objects.create(name="初中 · 函数", system_key="seed-group:3", position=0, color="#3a7bd5")

    # ---- 章节容器：带 section: 系统来源键 ---------------------------------
    section = LibraryTag.objects.create(
        name="初中 · 函数",
        kind="knowledge",
        system_key="section:seed-group:3",
        description="教材章节，用来给标签目录分层。",
        levels=["初中"],
        **legacy_tag_fields,
    )
    child = LibraryTag.objects.create(
        name="函数单调性", kind="knowledge", system_key="knowledge:函数单调性", levels=["初中"], **legacy_tag_fields
    )
    LibraryTagRelation.objects.create(parent=section, child=child, relation_type=LibraryTagRelation.Type.HIERARCHY)
    group.tags.add(section, child)

    # ---- 正式概念 --------------------------------------------------------
    formal = LibraryTag.objects.create(name="三角函数", kind="knowledge", levels=["初中", "高中"], **legacy_tag_fields)
    method = LibraryTag.objects.create(name="配方法", kind="method", levels=["初中", "高中"], **legacy_tag_fields)
    # 同名的历史标签：迁移时应该归并到正式概念，旧 ID 保留。
    legacy_twin = LibraryTag.objects.create(
        name="三角函数",
        kind="legacy",
        description="从旧版题库迁入；没有对应的细分目录项，等待人工整理。",
        active=False,
        **legacy_tag_fields,
    )
    custom = LibraryTag.objects.create(name="周五小测", kind="custom", **legacy_tag_fields)
    # 父子类型不一致：迁移后要标成「语义待整理」。
    mixed_parent = LibraryTag.objects.create(name="反证法", kind="method", **legacy_tag_fields)
    mixed_child = LibraryTag.objects.create(name="奇偶性", kind="knowledge", **legacy_tag_fields)
    LibraryTagRelation.objects.create(
        parent=mixed_parent, child=mixed_child, relation_type=LibraryTagRelation.Type.HIERARCHY
    )

    # ---- 旧正式关联：范围只有整题 ----------------------------------------
    first = make_publication(1, "虚构旧题一：已知 a>0，b>0，求证 1/a+1/b≥1。")
    second = make_publication(2, "虚构旧题二：求函数 f(x)=x^2-2x+3 的最小值。")
    third = make_publication(3, "虚构旧题三：AB∥CD，AD=BC，判断四边形形状。")

    LibraryTagAssignment.objects.create(
        publication=first,
        tag=formal,
        source=LibraryTagAssignment.Source.LEGACY_HUMAN,
        scope={"whole": True},
        rationale="旧版标签保留；未记录逐标签核对状态。",
        needs_review=False,
        role=LibraryTagAssignment.Role.LEGACY,
        scope_key="whole",
        evidence_status=LibraryTagAssignment.EvidenceStatus.UNBOUND,
        review_status=LibraryTagAssignment.ReviewStatus.CONFIRMED,
    )
    LibraryTagAssignment.objects.create(
        publication=second,
        tag=method,
        source=LibraryTagAssignment.Source.LEGACY_HUMAN,
        scope={"whole": True},
        rationale="旧版标签保留；未记录逐标签核对状态。",
        needs_review=False,
        role=LibraryTagAssignment.Role.LEGACY,
        scope_key="whole",
        evidence_status=LibraryTagAssignment.EvidenceStatus.UNBOUND,
        review_status=LibraryTagAssignment.ReviewStatus.CONFIRMED,
    )
    # 同名的历史标签上也有旧关联：迁移归并之后，这条关联要跟着走到正式概念，
    # 不能变成悬空引用。
    LibraryTagAssignment.objects.create(
        publication=second,
        tag=legacy_twin,
        source=LibraryTagAssignment.Source.LEGACY_UNKNOWN,
        scope={"whole": True},
        rationale="旧标签待核对。",
        needs_review=True,
        role=LibraryTagAssignment.Role.LEGACY,
        scope_key="whole",
        evidence_status=LibraryTagAssignment.EvidenceStatus.UNBOUND,
        review_status=LibraryTagAssignment.ReviewStatus.CONFIRMED,
    )
    # 自定义标签：可能是老师自己的专题用法，也可能是概念，等人工决定。
    LibraryTagAssignment.objects.create(
        publication=first,
        tag=custom,
        source=LibraryTagAssignment.Source.LEGACY_HUMAN,
        scope={"whole": True},
        rationale="旧版标签保留；未记录逐标签核对状态。",
        needs_review=False,
        role=LibraryTagAssignment.Role.LEGACY,
        scope_key="whole",
        evidence_status=LibraryTagAssignment.EvidenceStatus.UNBOUND,
        review_status=LibraryTagAssignment.ReviewStatus.CONFIRMED,
    )
    # 章节上的直接人工贴标
    LibraryTagAssignment.objects.create(
        publication=third,
        tag=section,
        source=LibraryTagAssignment.Source.LEGACY_HUMAN,
        scope={"whole": True},
        rationale="旧版标签保留；未记录逐标签核对状态。",
        needs_review=False,
        role=LibraryTagAssignment.Role.LEGACY,
        scope_key="whole",
        evidence_status=LibraryTagAssignment.EvidenceStatus.UNBOUND,
        review_status=LibraryTagAssignment.ReviewStatus.CONFIRMED,
    )

    # ---- 旧 AI 候选：待核对、排队中、已拒绝 --------------------------------
    job = LibraryJob.objects.create(publication=first, kind="tags", executor="api", status="done", result={})
    LibraryTagSuggestion.objects.create(
        job=job,
        publication=first,
        status=LibraryTagSuggestion.Status.PENDING,
        question_fingerprint="old" * 16,
        suggestions=[
            {
                "name": "基本不等式",
                "kind": "knowledge",
                "scope": {"whole": True},
                "rationale": "旧版 AI 标签，没有逐条理由。",
                "evidence": "请依据原题题干、选项和配图重新核对。",
            }
        ],
    )
    LibraryTagSuggestion.objects.create(
        publication=second, status=LibraryTagSuggestion.Status.QUEUED, question_fingerprint="old" * 16, suggestions=[]
    )
    LibraryTagSuggestion.objects.create(
        publication=third,
        status=LibraryTagSuggestion.Status.REJECTED,
        question_fingerprint="old" * 16,
        suggestions=[{"name": "已被拒绝的旧建议", "kind": "knowledge", "scope": {"whole": True}}],
        resolved_at=None,
    )

    print(
        "虚构旧库造好了：%d 个标签、%d 条正式关联、%d 条候选、%d 个章节容器。"
        % (
            LibraryTag.objects.count(),
            LibraryTagAssignment.objects.count(),
            LibraryTagSuggestion.objects.count(),
            LibraryTag.objects.filter(system_key__startswith="section:").count(),
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="造虚构旧结构的题库，用于迁移演练")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    return build(args.database)


if __name__ == "__main__":
    raise SystemExit(main())
