"""题库备份包：把整库打包带走，在另一台电脑上并进来。

只做两件事：

* **导出** —— 一个 zip（清单 + 题库快照 + 原卷），能带到别的电脑上；
* **合并** —— 把另一份备份并进本机，**只增不减**：本机已有的题一道不删，
  两边都有答案时两份解析都留着（题有据的解析本来就是一串版本）。

三条不肯让步的规矩：

1. **不联网、不上传**：包里只有题库和原卷，密钥和设置都不进包。
2. **只增不减**：合并永远不删本机已有的东西；老师做过的活儿不会因为并了一次库
   就没了。
3. **先看再动**：合并前给一份"会发生什么"的清单，合并后给一份人话报告，
   并且留下"这次动了什么"的记录，可以撤销。

识别"同一样东西"的规则（见 docs/同步方案.md 第五节）：

============  ==========================================
对象          身份
============  ==========================================
试卷          原文件内容的 sha256
题            (试卷 sha256, 题号)，题干前 30 字再核一遍
标签          名字 + 类型
标签上下级    (子, 父, 关系类型)
题上的标签    (题, 标签)
入库快照      归属的题 + 版本号
============  ==========================================
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

BUNDLE_FORMAT = 1
MANIFEST_NAME = "题有据备份.json"
DATABASE_NAME = "题库.sqlite3"
ORIGINALS = "原卷"
MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024

PAPER_TABLE = "core_paper"
BLOCK_TABLE = "core_block"
QUESTION_TABLE = "core_question"
PUBLICATION_TABLE = "core_publishedquestion"
SOLUTION_TABLE = "core_librarysolution"
REGION_TABLE = "core_regionread"
TAG_GROUP_TABLE = "core_librarytaggroup"
TAG_TABLE = "core_librarytag"
RELATION_TABLE = "core_librarytagrelation"
ASSIGNMENT_TABLE = "core_librarytagassignment"
TAG_MEMBERSHIP_TABLE = "core_librarytag_groups"
# 题组（一份资料里的题号作用域）和删除批次（一次删除动作）：
# **题目本身的外键就指着这两张表**。不跟着走，搬过来的题就指向了不存在的行 ——
# SQLite 要到提交时才检查外键，那时整次合并会被退回（1.26.3 真机上撞过：
# 599 道题指向没搬过来的题组、69 道题指向没搬过来的删除批次，合并报
# "FOREIGN KEY constraint failed"，预览却看不出问题）。
QUESTION_GROUP_TABLE = "core_questiongroup"
DELETION_BATCH_TABLE = "core_questiondeletionbatch"

# 能重新生成的中间产物：带上只是让包变大、冲突变多。
# 1.26.19：加上 backups 和 imports —— 备份包本身**绝不能**打进另一个备份包里。
# 以前没排除 backups，结果是：自动备份把上一次的备份包也打了进去，每打一次
# 包就大一倍（63 GB 的教训），最后把磁盘塞满，整个题库都打不开了。
SKIP_DIRECTORIES = {"chunks", "pages", "backups", "imports"}
SKIP_NAMES = {
    "features.json",
    "mineru_engine.json",
    "knowledge-points.txt",
    "library-drafts.json",
    "worker.lock",
    "library-drafts.lock",
}


class TransferError(ValueError):
    """A problem the teacher can act on; the message says what to do next."""


def _uuid_text(value) -> str:
    """把编号统一成"带横杠"的写法。

    数据库里存的是 32 位连写（e5899a0411c94a73a6c26a6efcb5ba1d），磁盘上的文件夹
    和备份包里的路径用的却是带横杠的（e5899a04-11c9-4a73-a6c2-6a6efcb5ba1d）。
    两边对不上，备份恢复就会一个文件都回不来：清单里的那份卷子找不到，原卷文件
    也按错的名字去包里找。统一之后，两边说的是同一件事。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError, TypeError):
        return text


def _looks_like_uuid(value) -> bool:
    """这段文字像一个编号吗。"""
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _uuid_hex(value) -> str:
    """把编号统一成 SQLite 库内使用的 32 位小写无横杠写法。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return uuid.UUID(text).hex
    except (ValueError, AttributeError, TypeError):
        return text


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _safe_bundle_stem(name: str) -> str:
    """把资料显示名整理成适合放进备份压缩包文件名的片段。"""
    text = str(name or "").strip()
    for suffix in (".pdf", ".docx", ".doc", ".zip"):
        if text.lower().endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    text = _INVALID_FILENAME_CHARS.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    if len(text) > 40:
        text = text[:40].strip(" ._")
    return text or "精选资料"


def data_root() -> Path:
    return Path(settings.DATA_ROOT)


def database_path() -> Path:
    return Path(settings.DATABASES["default"]["NAME"])


def records_root() -> Path:
    return data_root() / "imports"


def backups_root() -> Path:
    """软件本机备份锁与历史备份所在目录（打包导出时已由 SKIP_DIRECTORIES 排除）。"""
    return data_root() / "backups"


# ---------------------------------------------------------------- 导出


def list_exportable_papers() -> list[dict]:
    """列出可单独打包分享的资料（含已归档但仍有在库题目的书/卷，不含回收站废卷与示例练习卷）。"""
    from django.db.models import Count, Q

    from .demo import is_demo
    from .models import Paper, PublishedQuestion, Question

    live_pub_paper_ids = set(
        PublishedQuestion.objects.filter(
            status=PublishedQuestion.Status.PUBLISHED, question__deleted_at__isnull=True, paper_id__isnull=False
        ).values_list("paper_id", flat=True)
    )
    papers = [
        paper
        for paper in Paper.objects.filter(Q(archived=False) | Q(pk__in=live_pub_paper_ids)).order_by(
            "-created_at", "-id"
        )
        if not is_demo(paper)
    ]
    if not papers:
        return []
    paper_ids = [paper.pk for paper in papers]
    question_counts = {
        str(row["paper_id"]): int(row["total"])
        for row in Question.objects.filter(paper_id__in=paper_ids).values("paper_id").annotate(total=Count("id"))
    }
    pub_counts = {
        str(row["paper_id"]): int(row["total"])
        for row in PublishedQuestion.objects.filter(paper_id__in=paper_ids, status=PublishedQuestion.Status.PUBLISHED)
        .values("paper_id")
        .annotate(total=Count("id"))
    }
    return [
        {
            "id": str(paper.pk),
            "title": paper.display_name,
            "filename": paper.filename,
            "material_type": paper.material_type,
            "questions": question_counts.get(str(paper.pk), 0),
            "publications": pub_counts.get(str(paper.pk), 0),
        }
        for paper in papers
    ]


def list_exportable_folders() -> list[dict]:
    """列出包含已入库题目的目录文件夹（支持按知识点/方法/自定义目录整支打包分享）。"""
    from .demo import is_demo
    from .library_tagging import descendant_ids, relation_context
    from .models import LibraryTag, LibraryTagAssignment, PublishedQuestion

    context = relation_context()
    live = LibraryTagAssignment.objects.filter(
        publication__status=PublishedQuestion.Status.PUBLISHED,
        publication__question__isnull=False,
        publication__question__deleted_at__isnull=True,
        publication__paper__isnull=False,
        needs_review=False,
    ).select_related("publication__paper")
    publications_by_tag: dict[str, set[str]] = {}
    for assignment in live:
        if is_demo(assignment.publication.paper):
            continue
        publications_by_tag.setdefault(str(assignment.tag_id), set()).add(str(assignment.publication_id))

    if not publications_by_tag:
        return []

    tags = list(LibraryTag.objects.filter(active=True).order_by("kind", "name", "id"))
    by_id = {str(tag.pk): tag for tag in tags}
    kind_labels = {"knowledge": "知识点", "method": "解题方法", "custom": "自定义"}
    items: list[dict] = []
    for tag in tags:
        pub_ids: set[str] = set()
        for child_id in descendant_ids([tag.pk], context=context):
            pub_ids.update(publications_by_tag.get(child_id, set()))
        if not pub_ids:
            continue
        # 沿着第一条父级链向上拼出完整路径，方便在导出列表中区分同名子节点。
        chain = [tag.name]
        seen = {str(tag.pk)}
        current_id = str(tag.pk)
        for _ in range(4):
            parents = context.get(current_id, {}).get("parents") or []
            parent_id = next((str(pid) for pid in parents if str(pid) in by_id and str(pid) not in seen), None)
            if not parent_id:
                break
            seen.add(parent_id)
            chain.append(by_id[parent_id].name)
            current_id = parent_id
        chain.reverse()
        kind_label = kind_labels.get(tag.kind, "文件夹")
        items.append(
            {
                "id": str(tag.pk),
                "name": tag.name,
                "kind": tag.kind,
                "kind_label": kind_label,
                "path_label": f"{kind_label} · {' / '.join(chain)}",
                "questions": len(pub_ids),
            }
        )
    return items


def _resolve_export_folders(tag_ids: list[str] | None) -> dict:
    """校验并展开所选目录文件夹（含下级子文件夹）对应的已入库题目与所属卷子。"""
    if not isinstance(tag_ids, (list, tuple)) or not tag_ids:
        raise TransferError("先勾选至少一个要导出的目录文件夹。")

    from .demo import is_demo
    from .library_tagging import descendant_ids, relation_context
    from .models import LibraryTag, LibraryTagAssignment, PublishedQuestion

    selected_tags: list = []
    seen_ids: set[str] = set()
    for raw in tag_ids:
        text = str(raw or "").strip()
        if not text:
            continue
        tag = None
        try:
            uid = str(uuid.UUID(text))
        except (ValueError, AttributeError, TypeError):
            tag = LibraryTag.objects.filter(name=text).order_by("-active", "kind", "id").first()
        else:
            tag = LibraryTag.objects.filter(pk=uid).first()
        if tag is None:
            raise TransferError(f"找不到目录文件夹「{text}」，请刷新列表后再试。")
        if str(tag.pk) not in seen_ids:
            seen_ids.add(str(tag.pk))
            selected_tags.append(tag)

    if not selected_tags:
        raise TransferError("先勾选至少一个要导出的目录文件夹。")

    context = relation_context()
    subtree_tag_ids = descendant_ids([tag.pk for tag in selected_tags], context=context)
    matched_assignments = LibraryTagAssignment.objects.filter(
        tag_id__in=subtree_tag_ids,
        needs_review=False,
        publication__status=PublishedQuestion.Status.PUBLISHED,
        publication__question__isnull=False,
        publication__question__deleted_at__isnull=True,
        publication__paper__isnull=False,
    ).select_related("publication__paper")

    matched_pubs = []
    seen_pub_ids = set()
    for assignment in matched_assignments:
        pub = assignment.publication
        if pub.pk in seen_pub_ids or is_demo(pub.paper):
            continue
        seen_pub_ids.add(pub.pk)
        matched_pubs.append(pub)

    if not matched_pubs:
        raise TransferError("选中的目录文件夹里还没有已入库的题目，先给题目贴上该文件夹的标签再导出。")

    question_ids = {int(pub.question_id) for pub in matched_pubs if pub.question_id is not None}
    papers_by_id = {pub.paper_id: pub.paper for pub in matched_pubs if pub.paper is not None}
    papers = sorted(papers_by_id.values(), key=lambda paper: (paper.created_at, str(paper.pk)))
    return {
        "tags": selected_tags,
        "subtree_tag_hexes": {_uuid_hex(tid) for tid in subtree_tag_ids},
        "question_ids": question_ids,
        "papers": papers,
        "paper_hexes": {_uuid_hex(paper.pk) for paper in papers},
    }


def _resolve_export_papers(paper_ids: list[str] | None) -> list | None:
    """校验并按顺序取出要单独打包的资料；传入 None 表示整库导出。"""
    if paper_ids is None:
        return None
    if not isinstance(paper_ids, (list, tuple)) or not paper_ids:
        raise TransferError("先勾选至少一套要导出的卷子或书。")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_id in paper_ids:
        text = str(raw_id or "").strip()
        if not text:
            continue
        try:
            uid = str(uuid.UUID(text))
        except (ValueError, AttributeError, TypeError):
            raise TransferError("选中的资料编号不正确，请刷新列表后再试。") from None
        if uid not in seen:
            seen.add(uid)
            normalized.append(uid)
    if not normalized:
        raise TransferError("先勾选至少一套要导出的卷子或书。")

    from .demo import is_demo
    from .models import Paper

    by_id = {str(paper.pk): paper for paper in Paper.objects.filter(pk__in=normalized)}
    missing = [uid for uid in normalized if uid not in by_id]
    if missing:
        raise TransferError("选中的部分卷子或书已经不在题库里，请刷新列表后再试。")
    papers = [by_id[uid] for uid in normalized]
    if any(is_demo(paper) for paper in papers):
        raise TransferError("示例练习卷不能当作题包分享，请改选你自己的卷子或书。")
    return papers


def _table_exists(connect: sqlite3.Connection, table: str) -> bool:
    return (
        connect.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone()
        is not None
    )


def _prune_snapshot_database(
    writer: sqlite3.Connection,
    keep_paper_hexes: set[str],
    *,
    keep_question_ids: set[int] | None = None,
    extra_keep_tag_hexes: set[str] | None = None,
) -> None:
    """按选定资料或目录文件夹裁剪临时快照库：只留目标题目、解析、配图归属与相关标签。"""
    placeholders = ",".join("?" for _ in keep_paper_hexes)
    paper_params = tuple(sorted(keep_paper_hexes))

    writer.execute(f'DELETE FROM "{PAPER_TABLE}" WHERE id NOT IN ({placeholders})', paper_params)
    for table_name in (BLOCK_TABLE, QUESTION_GROUP_TABLE, "core_importchunk"):
        if _table_exists(writer, table_name):
            writer.execute(f'DELETE FROM "{table_name}" WHERE paper_id NOT IN ({placeholders})', paper_params)

    # 分享给网友的题包剔除本机已删进回收站的废题，并清理本机删除批次关联。
    writer.execute(
        f'DELETE FROM "{QUESTION_TABLE}" WHERE paper_id NOT IN ({placeholders}) OR deleted_at IS NOT NULL', paper_params
    )
    if keep_question_ids is not None:
        writer.execute("CREATE TEMP TABLE IF NOT EXISTS _keep_questions (id INTEGER PRIMARY KEY)")
        writer.execute("DELETE FROM _keep_questions")
        writer.executemany(
            "INSERT OR IGNORE INTO _keep_questions (id) VALUES (?)", [(int(qid),) for qid in keep_question_ids]
        )
        writer.execute(f'DELETE FROM "{QUESTION_TABLE}" WHERE id NOT IN (SELECT id FROM _keep_questions)')
        if _table_exists(writer, QUESTION_GROUP_TABLE):
            writer.execute(
                f'DELETE FROM "{QUESTION_GROUP_TABLE}" '
                f'WHERE id NOT IN (SELECT DISTINCT group_id FROM "{QUESTION_TABLE}" WHERE group_id IS NOT NULL)'
            )

    writer.execute(f'UPDATE "{QUESTION_TABLE}" SET deletion_batch_id = NULL WHERE deletion_batch_id IS NOT NULL')
    if _table_exists(writer, QUESTION_GROUP_TABLE):
        writer.execute(
            f'UPDATE "{QUESTION_TABLE}" SET group_id = NULL '
            f'WHERE group_id IS NOT NULL AND group_id NOT IN (SELECT id FROM "{QUESTION_GROUP_TABLE}")'
        )
    if _table_exists(writer, DELETION_BATCH_TABLE):
        writer.execute(f'DELETE FROM "{DELETION_BATCH_TABLE}"')

    writer.execute(
        f'DELETE FROM "{PUBLICATION_TABLE}" '
        f"WHERE paper_id IS NULL OR paper_id NOT IN ({placeholders}) "
        f"OR status != ? OR question_id IS NULL "
        f'OR question_id NOT IN (SELECT id FROM "{QUESTION_TABLE}")',
        (*paper_params, "published"),
    )
    if _table_exists(writer, SOLUTION_TABLE):
        writer.execute(
            f'DELETE FROM "{SOLUTION_TABLE}" WHERE publication_id NOT IN (SELECT id FROM "{PUBLICATION_TABLE}")'
        )
        writer.execute(
            f'UPDATE "{SOLUTION_TABLE}" SET parent_id = NULL '
            f'WHERE parent_id IS NOT NULL AND parent_id NOT IN (SELECT id FROM "{SOLUTION_TABLE}")'
        )
    if _table_exists(writer, REGION_TABLE):
        writer.execute(f'DELETE FROM "{REGION_TABLE}" WHERE question_id NOT IN (SELECT id FROM "{QUESTION_TABLE}")')

    for transient_table in (
        "core_libraryjob",
        "core_librarytagsuggestion",
        "core_librarytagoperation",
        "core_assistantproposal",
        "core_teacheractivity",
        "core_questionlayoutoperation",
    ):
        if _table_exists(writer, transient_table):
            writer.execute(f'DELETE FROM "{transient_table}"')

    keep_tags: set[str] = set(extra_keep_tag_hexes or set())
    if _table_exists(writer, ASSIGNMENT_TABLE):
        writer.execute(
            f'DELETE FROM "{ASSIGNMENT_TABLE}" WHERE publication_id NOT IN (SELECT id FROM "{PUBLICATION_TABLE}")'
        )
        keep_tags.update(
            str(row[0])
            for row in writer.execute(
                f'SELECT DISTINCT tag_id FROM "{ASSIGNMENT_TABLE}" WHERE tag_id IS NOT NULL'
            ).fetchall()
            if row[0]
        )

    # 沿标签上下级与关联解题方法闭包向上补齐祖先标签，避免标签树断裂。
    if _table_exists(writer, RELATION_TABLE) and keep_tags:
        relations = writer.execute(f'SELECT parent_id, child_id, relation_type FROM "{RELATION_TABLE}"').fetchall()
        changed = True
        while changed:
            changed = False
            for parent_id, child_id, relation_type in relations:
                parent_hex = str(parent_id or "")
                child_hex = str(child_id or "")
                if child_hex in keep_tags and parent_hex and parent_hex not in keep_tags:
                    keep_tags.add(parent_hex)
                    changed = True
                if (
                    str(relation_type or "").lower() == "related_method"
                    and parent_hex in keep_tags
                    and child_hex
                    and child_hex not in keep_tags
                ):
                    keep_tags.add(child_hex)
                    changed = True

    if _table_exists(writer, TAG_TABLE):
        if keep_tags:
            writer.execute("CREATE TEMP TABLE IF NOT EXISTS _keep_tags (id TEXT PRIMARY KEY)")
            writer.execute("DELETE FROM _keep_tags")
            writer.executemany("INSERT OR IGNORE INTO _keep_tags (id) VALUES (?)", [(tid,) for tid in keep_tags])
            writer.execute(f'DELETE FROM "{TAG_TABLE}" WHERE id NOT IN (SELECT id FROM _keep_tags)')
        else:
            writer.execute(f'DELETE FROM "{TAG_TABLE}"')

    if _table_exists(writer, RELATION_TABLE) and _table_exists(writer, TAG_TABLE):
        writer.execute(
            f'DELETE FROM "{RELATION_TABLE}" '
            f'WHERE parent_id NOT IN (SELECT id FROM "{TAG_TABLE}") '
            f'OR child_id NOT IN (SELECT id FROM "{TAG_TABLE}")'
        )
    if _table_exists(writer, TAG_MEMBERSHIP_TABLE) and _table_exists(writer, TAG_TABLE):
        writer.execute(
            f'DELETE FROM "{TAG_MEMBERSHIP_TABLE}" WHERE librarytag_id NOT IN (SELECT id FROM "{TAG_TABLE}")'
        )
    if _table_exists(writer, TAG_GROUP_TABLE):
        if _table_exists(writer, TAG_MEMBERSHIP_TABLE):
            writer.execute(
                f'DELETE FROM "{TAG_GROUP_TABLE}" '
                f'WHERE id NOT IN (SELECT DISTINCT librarytaggroup_id FROM "{TAG_MEMBERSHIP_TABLE}" '
                f"WHERE librarytaggroup_id IS NOT NULL)"
            )
        else:
            writer.execute(f'DELETE FROM "{TAG_GROUP_TABLE}"')


def _snapshot_database(
    target: Path,
    keep_paper_hexes: set[str] | None = None,
    *,
    keep_question_ids: set[int] | None = None,
    extra_keep_tag_hexes: set[str] | None = None,
) -> dict | None:
    """拍一份一致的题库快照。

    不能直接拷文件：软件正在写的时候拷到半个库。也不能用 SQLite 的 backup 接口 ——
    它在**开着事务的同一个连接**上会自己把自己锁住（备份要读源库，而源库正被这个
    连接写事务锁着），表现为整个程序卡住不动。

    这里逐表读、逐表写：读到的是这个连接此刻看到的样子（没提交的改动也在），
    写出来的是一份独立、可直接打开的库。传入 keep_paper_hexes 时只留所选资料。
    """
    connection.ensure_connection()  # 连接是懒开的，不先打开就拿不到
    raw = getattr(connection, "connection", None)
    if raw is None:
        raise TransferError("题库现在没打开着，请重启一次题有据再试。")
    objects = raw.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    with contextlib_closing(sqlite3.connect(target)) as writer:
        writer.execute("PRAGMA foreign_keys=OFF")
        for kind, name, sql in objects:
            if kind != "table":
                writer.execute(sql)
                continue
            writer.execute(sql)
            cursor = raw.execute(f'SELECT * FROM "{name}"')
            columns = [description[0] for description in cursor.description]
            if not columns:
                continue
            quoted = ",".join(f'"{column}"' for column in columns)
            placeholders = ",".join("?" for _ in columns)
            rows = cursor.fetchall()
            if rows:
                writer.executemany(f'INSERT INTO "{name}" ({quoted}) VALUES ({placeholders})', rows)
        # AUTOINCREMENT 的计数器得一起搬过去，否则新行的编号可能撞上已有的。
        try:
            sequence = raw.execute("SELECT name, seq FROM sqlite_sequence").fetchall()
        except sqlite3.Error:
            sequence = []
        for name, value in sequence:
            writer.execute("INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES (?, ?)", (name, value))
        if keep_paper_hexes is not None:
            _prune_snapshot_database(
                writer, keep_paper_hexes, keep_question_ids=keep_question_ids, extra_keep_tag_hexes=extra_keep_tag_hexes
            )
        writer.commit()
        if keep_paper_hexes is not None:
            writer.execute("VACUUM")
    with contextlib_closing(sqlite3.connect(target)) as check:
        if check.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise TransferError("备份没有拍完整，已停止，没有动本机题库。")
        if keep_paper_hexes is not None:
            if check.execute("PRAGMA foreign_key_check").fetchall():
                raise TransferError("打包后的题库存在外键引用异常，已停止，没有动本机题库。")
            pub_rows = check.execute(f'SELECT id FROM "{PUBLICATION_TABLE}"').fetchall()
            paper_question_counts = {
                _uuid_hex(row[0]): int(row[1])
                for row in check.execute(
                    f'SELECT paper_id, COUNT(*) FROM "{QUESTION_TABLE}" GROUP BY paper_id'
                ).fetchall()
                if row[0]
            }
            counts = {
                "papers": int(check.execute(f'SELECT COUNT(*) FROM "{PAPER_TABLE}"').fetchone()[0]),
                "questions": int(check.execute(f'SELECT COUNT(*) FROM "{QUESTION_TABLE}"').fetchone()[0]),
                "publications": len(pub_rows),
                "tags": (
                    int(check.execute(f'SELECT COUNT(*) FROM "{TAG_TABLE}"').fetchone()[0])
                    if _table_exists(check, TAG_TABLE)
                    else 0
                ),
            }
            return {
                "counts": counts,
                "publications": {_uuid_text(row[0]) for row in pub_rows if row[0]},
                "paper_question_counts": paper_question_counts,
            }
    return None


def contextlib_closing(value):
    from contextlib import closing

    return closing(value)


def _bundle_files(root: Path, *, allowed_papers: set[str] | None = None, allowed_publications: set[str] | None = None):
    """原卷里值得带走的东西：每套卷自己的文件，和答案解析里的图片。"""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRECTORIES for part in relative.parts):
            continue
        if path.name in SKIP_NAMES or path.name.endswith(".page.jpg"):
            continue
        if allowed_papers is not None:
            parts = relative.parts
            if not parts:
                continue
            head = parts[0]
            if head in {"library", "library-solutions"}:
                if len(parts) < 2 or _uuid_text(parts[1]) not in (allowed_publications or set()):
                    continue
            elif _looks_like_uuid(head):
                if _uuid_text(head) not in allowed_papers:
                    continue
            else:
                continue
        yield path


def _paper_files(paper) -> dict:
    """这套卷在硬盘上有哪些文件，记成相对题库数据文件夹的路径。

    清单要记的是**相对路径**（换台电脑才对得上）。以前是拿库里存的绝对路径
    去反推的 —— 一旦那个绝对路径指到别处（数据文件夹挪过家、或者这份库是
    从另一台电脑恢复来的，里面存着人家的路径），反推就失败，清单里的文件
    记录整条空掉，恢复时就对不上号了。

    现在改成：**文件名取自库里，位置按本机这套卷自己的文件夹去找**。
    """
    folder = data_root() / _uuid_text(paper.pk)
    files: dict[str, str] = {}
    for label, value in (("source", paper.source_path), ("render", paper.render_path), ("zip", paper.zip_path)):
        if not value:
            continue
        name = Path(str(value)).name
        if not name:
            continue
        if (folder / name).is_file():
            files[label] = PurePosixPath(_uuid_text(paper.pk), name).as_posix()
            continue
        try:
            files[label] = Path(str(value)).resolve().relative_to(data_root().resolve()).as_posix()
        except ValueError:
            continue
    return files


def _manifest(
    papers_list: list | None = None,
    counts_override: dict | None = None,
    *,
    include_machine: bool = True,
    paper_question_counts: dict[str, int] | None = None,
    folders_list: list | None = None,
) -> dict:
    from core.models import LibraryTag, Paper, PublishedQuestion, Question

    source_papers = papers_list if papers_list is not None else Paper.objects.all().order_by("created_at")
    papers = []
    for paper in source_papers:
        files = _paper_files(paper)
        q_count = (
            paper_question_counts.get(_uuid_hex(paper.pk), 0)
            if paper_question_counts is not None
            else Question.objects.filter(paper=paper).count()
        )
        papers.append(
            {
                "id": str(paper.pk),
                "title": paper.display_name,
                "filename": paper.filename,
                "sha256": paper.sha256,
                "questions": q_count,
                "files": files,
            }
        )
    counts = (
        dict(counts_override)
        if counts_override is not None
        else {
            "papers": len(papers),
            "questions": Question.objects.count(),
            "publications": PublishedQuestion.objects.count(),
            "tags": LibraryTag.objects.count(),
        }
    )
    payload: dict = {
        "format": BUNDLE_FORMAT,
        "app_version": getattr(__import__("core.version", fromlist=["APP_VERSION"]), "APP_VERSION", ""),
        "created_at": timezone.now().isoformat(),
        "machine": (os.environ.get("COMPUTERNAME") or "这台电脑") if include_machine else "",
        "counts": counts,
        "papers": papers,
    }
    if folders_list:
        payload["scope"] = "folder"
        payload["folders"] = [{"id": str(tag.pk), "name": tag.name, "kind": tag.kind} for tag in folders_list]
    elif papers_list is not None:
        payload["scope"] = "selected"
    return payload


BACKUP_LOCK_NAME = "backup.lock"
BACKUP_LOCK_STALE_SECONDS = 2 * 60 * 60


@contextlib.contextmanager
def _backup_lock():
    """同一时刻只准一份备份在写，防止并发导出冲突或写出坏包。

    锁放在软件自己的文件夹里；超过两小时的锁是上次意外留下的，接过来继续用。
    """
    lock_path = backups_root() / BACKUP_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            handle = lock_path.open("x")
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0
            if age > BACKUP_LOCK_STALE_SECONDS:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            raise TransferError("已经有一次备份在做了，等它打完再试。") from None
        else:
            try:
                handle.write(str(os.getpid()))
            finally:
                handle.close()
            break
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def export_bundle(
    directory: Path, prefix: str = "题有据备份", *, paper_ids: list[str] | None = None, tag_ids: list[str] | None = None
) -> dict:
    """把整库、选定资料或目录文件夹打包成一个 zip 放进 `directory`。

    1.26.20：**先写临时文件，写成了再改名**。以前直接往最终文件名上写，
    中途出错（比如软件被关掉）就留下一个半个的包 —— 那不是备份，是垃圾：
    程序打不开，还白占磁盘。写坏了的临时文件当场删掉，不留残骸。
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise TransferError("选的文件夹不在了，重新选一个。")
    if paper_ids is not None and tag_ids is not None:
        raise TransferError("一次只能按卷子或按目录文件夹其中一种方式导出。")

    selected_papers: list | None = None
    selected_folders: list | None = None
    keep_question_ids: set[int] | None = None
    extra_keep_tag_hexes: set[str] | None = None
    actual_prefix = prefix

    if tag_ids is not None:
        folder_selection = _resolve_export_folders(tag_ids)
        selected_papers = folder_selection["papers"]
        selected_folders = folder_selection["tags"]
        keep_question_ids = folder_selection["question_ids"]
        extra_keep_tag_hexes = folder_selection["subtree_tag_hexes"]
        if prefix == "题有据备份":
            first_stem = _safe_bundle_stem(selected_folders[0].name)
            if len(selected_folders) == 1:
                actual_prefix = f"题有据题包-{first_stem}"
            else:
                actual_prefix = f"题有据题包-{first_stem}等{len(selected_folders)}个文件夹"
    elif paper_ids is not None:
        selected_papers = _resolve_export_papers(paper_ids)
        if selected_papers is not None and prefix == "题有据备份":
            first_stem = _safe_bundle_stem(selected_papers[0].display_name)
            if len(selected_papers) == 1:
                actual_prefix = f"题有据题包-{first_stem}"
            else:
                actual_prefix = f"题有据题包-{first_stem}等{len(selected_papers)}套"

    with _backup_lock():
        return _export_bundle(
            directory,
            actual_prefix,
            selected_papers=selected_papers,
            selected_folders=selected_folders,
            keep_question_ids=keep_question_ids,
            extra_keep_tag_hexes=extra_keep_tag_hexes,
        )


def _export_bundle(
    directory: Path,
    prefix: str,
    selected_papers: list | None = None,
    *,
    selected_folders: list | None = None,
    keep_question_ids: set[int] | None = None,
    extra_keep_tag_hexes: set[str] | None = None,
) -> dict:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = directory / f"{prefix}-{stamp}.zip"
    temp = directory / f".{prefix}-{stamp}.part"
    try:
        with tempfile.TemporaryDirectory() as work:
            snapshot = Path(work) / DATABASE_NAME
            if selected_papers is None:
                _snapshot_database(snapshot)
                manifest = _manifest()
                files_iter = _bundle_files(data_root())
            else:
                keep_paper_hexes = {_uuid_hex(paper.pk) for paper in selected_papers}
                snapshot_info = _snapshot_database(
                    snapshot,
                    keep_paper_hexes=keep_paper_hexes,
                    keep_question_ids=keep_question_ids,
                    extra_keep_tag_hexes=extra_keep_tag_hexes,
                )
                counts_override = snapshot_info["counts"] if snapshot_info else None
                allowed_publications = snapshot_info["publications"] if snapshot_info else set()
                paper_question_counts = snapshot_info.get("paper_question_counts") if snapshot_info else None
                manifest = _manifest(
                    papers_list=selected_papers,
                    counts_override=counts_override,
                    include_machine=False,
                    paper_question_counts=paper_question_counts,
                    folders_list=selected_folders,
                )
                allowed_papers = {_uuid_text(paper.pk) for paper in selected_papers}
                files_iter = _bundle_files(
                    data_root(), allowed_papers=allowed_papers, allowed_publications=allowed_publications
                )
            with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.write(snapshot, DATABASE_NAME)
                for path in files_iter:
                    archive.write(path, f"{ORIGINALS}/{path.relative_to(data_root()).as_posix()}")
        # 写完了自己先验一遍：包能打开、清单和题库都在，才算是个备份。
        with zipfile.ZipFile(temp) as check:
            names = set(check.namelist())
        if MANIFEST_NAME not in names or DATABASE_NAME not in names:
            raise TransferError("备份包没写完整，已经把半成品删掉了，请再打一次。")
    except Exception:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    os.replace(temp, target)
    paper_names = [
        str(item.get("title") or item.get("filename") or "").strip() for item in (manifest.get("papers") or [])
    ]
    folder_names = [str(item.get("name") or "").strip() for item in (manifest.get("folders") or [])]
    return {
        "file": str(target),
        "name": target.name,
        "size": target.stat().st_size,
        "counts": manifest["counts"],
        "created_at": manifest["created_at"],
        "paper_names": [name for name in paper_names if name],
        "folder_names": [name for name in folder_names if name],
    }


# ---------------------------------------------------------------- 看一眼


def _open_bundle(path: Path) -> tuple[zipfile.ZipFile, dict]:
    path = Path(path)
    if not path.is_file():
        raise TransferError("找不到这个备份文件。")
    if path.stat().st_size > MAX_BUNDLE_BYTES:
        raise TransferError("这个备份包太大了，请确认选的是题有据导出的备份包。")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise TransferError("这个文件不是题有据的备份包（打不开）。") from None
    try:
        raw = archive.read(MANIFEST_NAME)
    except KeyError:
        archive.close()
        raise TransferError("这个备份包里没有清单，可能不是题有据导出的备份包。") from None
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        archive.close()
        raise TransferError("备份包的清单读不出来，这个包可能损坏了。") from None
    if not isinstance(manifest, dict) or manifest.get("format") != BUNDLE_FORMAT:
        archive.close()
        raise TransferError("这个备份包的版本认不出来，请用题有据导出的备份包。")
    return archive, manifest


# ---------------------------------------------------------------- 合并


def _columns(connect, table: str) -> list[str]:
    try:
        return [row[1] for row in connect.execute(f'PRAGMA table_info("{table}")')]
    except sqlite3.Error:
        return []


def _rows(connect, table: str, where: str = "", params: tuple = ()) -> list[dict]:
    connect.row_factory = sqlite3.Row
    try:
        cursor = connect.execute(f'SELECT * FROM "{table}" {where}', params)
        return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error:
        return []


class _Merge:
    """把一份备份并进本机。dry_run=True 时只算，不写。"""

    def __init__(self, archive: zipfile.ZipFile, manifest: dict, *, dry_run: bool = False):
        self.archive = archive
        self.manifest = manifest
        self.dry_run = dry_run
        self.incoming: sqlite3.Connection | None = None
        # 包里有哪些条目，先看一遍：还原文件时要按名字一个个找。
        self._names = list(archive.namelist())
        self.report = {
            "新增卷子": 0,
            "新增题目": 0,
            "补上答案": 0,
            "两边都有保留本机": 0,
            "题号一样内容不同": 0,
            "两版解析都留着": 0,
            "新增标签": 0,
            "多贴的标签": 0,
            "补进来的解析版本": 0,
            "补回的文件": 0,
            "补回原卷路径": 0,
        }
        self.added = {
            "papers": [],
            "questions": [],
            "publications": [],
            "solutions": [],
            "tags": [],
            "groups": [],
            "relations": [],
            "assignments": [],
            "filled": [],
            "question_groups": [],
            "deletion_batches": [],
            "memberships": [],
            "blocks": [],
            "regions": [],
        }
        # 预览和真合并要报出同一件事，所以两边共用这些记录。
        self.notes: list[str] = []
        self.reserved_numbers: dict[str, set] = {}
        # 对方编号 → 本机编号的对照。题目、标签、题组、删除批次、入库快照都要
        # 按它改写指向 —— 对方的编号在本机不一定指向同一个东西。
        self.tag_map: dict[str, str] = {}
        self.tag_group_map: dict[str, str] = {}
        self.publication_map: dict[str, str] = {}
        self.paper_map: dict[str, str] = {}
        self.question_group_map: dict[str, str] = {}
        self.deletion_batch_map: dict[str, str] = {}
        # 删除批次里的"这些题是哪几道"要等题搬完才知道本机编号，先记着。
        self._pending_batch_questions: list[tuple[str, list]] = []

    # -- 小工具 -------------------------------------------------------

    def _incoming_db(self) -> Path:
        work = Path(tempfile.mkdtemp(prefix="qb-merge-"))
        target = work / DATABASE_NAME
        with self.archive.open(DATABASE_NAME) as reader, open(target, "wb") as writer:
            shutil.copyfileobj(reader, writer)
        return target

    def _insert(self, table: str, row: dict) -> bool:
        """插一行；已经有的（同一个编号）就跳过 —— 合并因此是幂等的。

        只吞"这个编号本机已经有了"这一种情况。别的一律报错：静默吞掉会让
        老师以为搬过去了，其实少搬了一半。
        """
        if self.dry_run:
            return str(row.get("id")) if row.get("id") is not None else None
        columns = [name for name in row if name in self._main_columns(table)]
        if not columns:
            return None
        placeholders = ",".join("?" for _ in columns)
        names = ",".join(f'"{name}"' for name in columns)
        values = [row[name] for name in columns]
        with connection.cursor() as cursor:
            try:
                cursor.execute(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', values)
            except (IntegrityError, sqlite3.IntegrityError):
                # 捕的是 Django 的 IntegrityError —— 之前这里只捕 sqlite3 的那个，
                # Django 根本不抛它，于是"编号已经有了就跳过"这条兜底从来没生效过：
                # 同一个包合并两次（或者本机已经有一份同编号的卷）就会当场失败。
                if row.get("id") is not None and self._exists_by_id(table, str(row["id"])):
                    return None
                raise
            if row.get("id") is not None:
                return str(row["id"])
            return str(cursor.lastrowid)

    def _main_columns(self, table: str) -> list[str]:
        cached = getattr(self, "_column_cache", None)
        if cached is None:
            cached = self._column_cache = {}
        if table not in cached:
            with connection.cursor() as cursor:
                # table_info 的第 0 列是列序号，第 1 列才是列名。
                cached[table] = [row[1] for row in cursor.execute(f'PRAGMA table_info("{table}")').fetchall()]
        return cached[table]

    def _local_exists(self, table: str, where: str, params: tuple) -> bool:
        with connection.cursor() as cursor:
            return cursor.execute(f'SELECT 1 FROM "{table}" WHERE {where} LIMIT 1', params).fetchone() is not None

    def _exists_by_id(self, table: str, row_id: str) -> bool:
        with connection.cursor() as cursor:
            return cursor.execute(f'SELECT 1 FROM "{table}" WHERE id=? LIMIT 1', (row_id,)).fetchone() is not None

    # -- 主流程 -------------------------------------------------------

    def run(self) -> dict:
        source = self._incoming_db()
        self.incoming = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        try:
            self._merge_tags()
            self._merge_papers()
            # 文件最后还原：这时候"对方编号 → 本机编号"的对照才全了，
            # 卷子换了新编号、入库快照换了新编号，图片才知道该放哪。
            self._restore_originals()
        finally:
            self.incoming.close()
            shutil.rmtree(source.parent, ignore_errors=True)
        self.report["本机原有"] = {
            "卷子": _count(PAPER_TABLE),
            "题目": _count(QUESTION_TABLE),
            "入库快照": _count(PUBLICATION_TABLE),
            "标签": _count(TAG_TABLE),
        }
        if self.notes:
            self.report["说明"] = list(self.notes)
        return self.report

    def _merge_tags(self) -> None:
        group_map = self.tag_group_map
        for group in _rows(self.incoming, TAG_GROUP_TABLE):
            name = str(group.get("name") or "")
            if not name:
                continue
            local = self._local_tag_group(name)
            if local is None:
                new_id = uuid.uuid4().hex
                group_map[str(group["id"])] = new_id
                row = {**group, "id": new_id}
                if self._insert(TAG_GROUP_TABLE, row):
                    self.added["groups"].append(new_id)
            else:
                group_map[str(group["id"])] = local

        tag_map = self.tag_map
        for tag in _rows(self.incoming, TAG_TABLE):
            kind = str(tag.get("kind") or "knowledge")
            name = str(tag.get("name") or "")
            if not name:
                continue
            local = self._local_tag(kind, name)
            if local is None:
                new_id = uuid.uuid4().hex
                tag_map[str(tag["id"])] = new_id
                if self._insert(TAG_TABLE, {**tag, "id": new_id}):
                    self.added["tags"].append(new_id)
                    self.report["新增标签"] += 1
            else:
                tag_map[str(tag["id"])] = local

        # 标签属于哪几个"标签组"（自定义目录）：这是多对多的中间表，编号两边不一样，
        # 不改写就会指向不存在的标签/目录。
        for row in _rows(self.incoming, TAG_MEMBERSHIP_TABLE):
            tag = tag_map.get(str(row.get("librarytag_id")))
            group = group_map.get(str(row.get("librarytaggroup_id")))
            if not tag or not group:
                continue
            if self._local_exists(TAG_MEMBERSHIP_TABLE, '"librarytag_id"=? AND "librarytaggroup_id"=?', (tag, group)):
                continue
            inserted = self._insert(
                TAG_MEMBERSHIP_TABLE,
                {key: value for key, value in row.items() if key != "id"}
                | {"librarytag_id": tag, "librarytaggroup_id": group},
            )
            if inserted:
                self.added["memberships"].append(inserted)

        for relation in _rows(self.incoming, RELATION_TABLE):
            parent = tag_map.get(str(relation.get("parent_id")))
            child = tag_map.get(str(relation.get("child_id")))
            if not parent or not child:
                continue
            if self._local_exists(
                RELATION_TABLE,
                '"parent_id"=? AND "child_id"=? AND "relation_type"=?',
                (parent, child, relation.get("relation_type") or "HIERARCHY"),
            ):
                continue
            # 标签上下级这张表的主键是**整数**（不是 UUID），不能照抄对方的编号 ——
            # 那个编号在本机可能已经被另一条关系占着。给一条新的。
            row = {key: value for key, value in relation.items() if key != "id"}
            row.update({"parent_id": parent, "child_id": child})
            inserted = self._insert(RELATION_TABLE, row)
            if inserted:
                self.added["relations"].append(inserted)

    def _local_tag(self, kind: str, name: str) -> str | None:
        with connection.cursor() as cursor:
            row = cursor.execute(
                'SELECT id FROM "core_librarytag" WHERE kind=? AND name=? LIMIT 1', (kind, name)
            ).fetchone()
        return str(row[0]) if row else None

    def _local_tag_group(self, name: str) -> str | None:
        with connection.cursor() as cursor:
            row = cursor.execute('SELECT id FROM "core_librarytaggroup" WHERE name=? LIMIT 1', (name,)).fetchone()
        return str(row[0]) if row else None

    def _merge_papers(self) -> None:
        papers = _rows(self.incoming, PAPER_TABLE)
        blocks = _rows(self.incoming, BLOCK_TABLE)
        questions = _rows(self.incoming, QUESTION_TABLE)
        publications = _rows(self.incoming, PUBLICATION_TABLE)
        solutions = _rows(self.incoming, SOLUTION_TABLE)
        regions = _rows(self.incoming, REGION_TABLE)
        assignments = _rows(self.incoming, ASSIGNMENT_TABLE)
        question_groups = _rows(self.incoming, QUESTION_GROUP_TABLE)
        deletion_batches = _rows(self.incoming, DELETION_BATCH_TABLE)

        blocks_by_paper: dict[str, list] = {}
        for block in blocks:
            blocks_by_paper.setdefault(str(block.get("paper_id")), []).append(block)
        questions_by_paper: dict[str, list] = {}
        for question in questions:
            questions_by_paper.setdefault(str(question.get("paper_id")), []).append(question)
        publications_by_question: dict[str, list] = {}
        for publication in publications:
            publications_by_question.setdefault(str(publication.get("question_id")), []).append(publication)
        solutions_by_publication: dict[str, list] = {}
        for solution in solutions:
            solutions_by_publication.setdefault(str(solution.get("publication_id")), []).append(solution)
        regions_by_question: dict[str, list] = {}
        for region in regions:
            regions_by_question.setdefault(str(region.get("question_id")), []).append(region)
        groups_by_paper: dict[str, list] = {}
        for group in question_groups:
            groups_by_paper.setdefault(str(group.get("paper_id")), []).append(group)
        batches_by_paper: dict[str, list] = {}
        for batch in deletion_batches:
            batches_by_paper.setdefault(str(batch.get("paper_id")), []).append(batch)

        for paper in papers:
            local = self._local_paper(str(paper.get("sha256") or ""))
            if local is None:
                self._add_paper(
                    paper,
                    blocks_by_paper,
                    questions_by_paper,
                    publications_by_question,
                    solutions_by_publication,
                    regions_by_question,
                    groups_by_paper,
                    batches_by_paper,
                )
            else:
                self._merge_into_paper(
                    local,
                    paper,
                    questions_by_paper.get(str(paper["id"]), []),
                    publications_by_question,
                    solutions_by_publication,
                    regions_by_question,
                    groups_by_paper.get(str(paper["id"]), []),
                    batches_by_paper.get(str(paper["id"]), []),
                )
        # 已经撤回、题目又被删掉的旧快照：没有题目可跟，那就自己搬。
        # 少了它们，"只增不减"就成了空话（本机那份历史就少了几十条）。
        for publication in publications:
            if publication.get("question_id") is not None:
                continue
            row = {**publication}
            incoming_paper = str(publication.get("paper_id") or "")
            if incoming_paper and incoming_paper in self.paper_map:
                row["paper_id"] = self.paper_map[incoming_paper]
            self._add_publication(row, solutions_by_publication)
        self._merge_assignments(assignments)

    def _local_paper(self, sha256: str) -> str | None:
        """本机有没有这份原卷。

        判重认的是**原文件内容的 sha256**，不是文件名 —— 改了名的同一份卷照样认得出。

        本机可能留着好几份一样的（老师"仍然新建"重录过一次），这时要挑得
        **确定**：先排除归档的（归档的那份老师在题库里看不见，题并进去就等于
        藏起来了），剩下里挑题最多的那份。都归档了就当本机没有 —— 另建一份
        看得见的，比把题塞进看不见的地方好。
        """
        if not sha256:
            return None
        with connection.cursor() as cursor:
            rows = cursor.execute(
                'SELECT id, archived FROM "core_paper" WHERE sha256=? ORDER BY archived ASC, created_at DESC', (sha256,)
            ).fetchall()
        visible = [str(row[0]) for row in rows if not row[1]]
        if not visible:
            return None
        if len(visible) == 1:
            return visible[0]
        with connection.cursor() as cursor:
            counts = {
                str(row[0]): int(row[1])
                for row in cursor.execute(
                    'SELECT paper_id, COUNT(*) FROM "core_question" WHERE paper_id IN (%s) GROUP BY paper_id'
                    % ",".join("?" * len(visible)),
                    visible,
                ).fetchall()
            }
        return max(visible, key=lambda paper_id: counts.get(paper_id, 0))

    def _add_paper(
        self,
        paper,
        blocks_by_paper,
        questions_by_paper,
        publications_by_question,
        solutions_by_publication,
        regions_by_question,
        groups_by_paper,
        batches_by_paper,
    ) -> None:
        incoming_id = str(paper["id"])
        row = {**paper}
        # 卷子的编号是 UUID，两台电脑各编各的 —— 但本机可能已经有这个编号了
        # （同一份卷在这儿归档过）。照抄会撞车，撞车就是一次"操作没完成"。
        local_id = incoming_id
        if self._exists_by_id(PAPER_TABLE, incoming_id):
            local_id = uuid.uuid4().hex
        row["id"] = local_id
        # 原卷文件在本机的位置：**跟着本机的新编号走**。
        # 1.26.23：以前这里写的是清单里记的那条路径（对方机器上的相对路径），
        # 清单里的编号（带横杠）和数据库里的（连写）对不上，就写成空字符串 ——
        # 原卷预览当场坏掉，审核页只剩一个白框。
        files = self._manifest_files(incoming_id)
        folder = _uuid_text(local_id)
        for label, column in (("source", "source_path"), ("render", "render_path"), ("zip", "zip_path")):
            row[column] = self._restored_path(folder, files.get(label))
        inserted = self._insert(PAPER_TABLE, row)
        if inserted:
            self.added["papers"].append(inserted)
            self.report["新增卷子"] += 1
        paper_id = inserted or incoming_id
        self.paper_map[incoming_id] = paper_id
        for block in blocks_by_paper.get(incoming_id, []):
            inserted = self._insert(BLOCK_TABLE, block)
            if inserted:
                # 撤销时要删：卷子一删，这些块还指着它，外键就断了。
                self.added["blocks"].append(inserted)
        # 题组和删除批次得先摆好：题目马上要指过去。
        self._merge_question_groups(paper_id, groups_by_paper.get(incoming_id, []))
        self._merge_deletion_batches(paper_id, batches_by_paper.get(incoming_id, []))
        id_map = self._add_questions(
            paper_id,
            questions_by_paper.get(incoming_id, []),
            publications_by_question,
            solutions_by_publication,
            regions_by_question,
        )
        self._fix_batch_question_ids(id_map)

    def _merge_question_groups(self, local_paper_id: str, incoming_groups: list) -> None:
        """题组（一、二、三 大题这些"题号作用域"）跟着卷子一起搬。

        题目的 group_id 指着题组。不搬题组，搬过来的题就指了个空 ——
        提交时外键一查，整次合并被退回。
        """
        for group in incoming_groups:
            sequence = group.get("sequence")
            title = str(group.get("title") or "")
            local = self._local_question_group(local_paper_id, sequence, title)
            if local is not None:
                self.question_group_map[str(group.get("id"))] = local
                continue
            row = {key: value for key, value in group.items() if key != "id"}
            row["paper_id"] = local_paper_id
            # 编号是本机自己编的：照抄对方的会撞上本机已有的题组。
            new_id = self._insert(QUESTION_GROUP_TABLE, row)
            if new_id:
                self.question_group_map[str(group.get("id"))] = new_id
                self.added["question_groups"].append(new_id)

    def _local_question_group(self, paper_id: str, sequence, title: str) -> str | None:
        """本机这张卷上有没有同一组。

        **有序号就只按序号认**：组名会重复（同一本书里好几个「第 70 组」），按名字
        认会把一组认成另一组，题就落进了错的组里。只有连序号都没有时（很老的库）
        才退而按名字认。
        """
        with connection.cursor() as cursor:
            if sequence is not None:
                row = cursor.execute(
                    'SELECT id FROM "core_questiongroup" WHERE paper_id=? AND sequence=? LIMIT 1', (paper_id, sequence)
                ).fetchone()
                return str(row[0]) if row else None
            if title:
                row = cursor.execute(
                    'SELECT id FROM "core_questiongroup" WHERE paper_id=? AND title=? LIMIT 1', (paper_id, title)
                ).fetchone()
        return str(row[0]) if row else None

    def _merge_deletion_batches(self, local_paper_id: str, incoming_batches: list) -> None:
        """删除批次（一次"删掉这几道题"的动作）跟着搬。

        题目的 deletion_batch_id 指着它。批次本身是 UUID，两台电脑各记各的，
        所以这里另给一个编号 —— 但"删掉的是哪几道题"要照搬，那道题在这台电脑上
        删了没有，得能还原。
        """
        for batch in incoming_batches:
            row = {key: value for key, value in batch.items() if key not in ("id", "question_ids")}
            row["id"] = uuid.uuid4().hex
            row["paper_id"] = local_paper_id
            row["question_ids"] = json.dumps([])
            new_id = self._insert(DELETION_BATCH_TABLE, row)
            if new_id:
                self.deletion_batch_map[str(batch.get("id"))] = new_id
                self.added["deletion_batches"].append(new_id)
                self._pending_batch_questions.append((new_id, list(batch.get("question_ids") or [])))

    def _fix_batch_question_ids(self, id_map: dict[str, str]) -> None:
        """批次里的题号改成搬过来之后的本机编号。

        改不出来的（那道题本机本来就有、没搬）就不写进去 —— 留着对方的编号，
        万一和本机某道题撞号，还原时会把不相干的题给还原了。
        """
        for batch_id, incoming_ids in self._pending_batch_questions:
            local_ids = [str(id_map[str(value)]) for value in incoming_ids if str(value) in id_map]
            if not local_ids:
                continue
            if self.dry_run:
                continue
            with connection.cursor() as cursor:
                cursor.execute(
                    'UPDATE "core_questiondeletionbatch" SET "question_ids"=? WHERE id=?',
                    (json.dumps(local_ids), batch_id),
                )

    def _add_questions(
        self, paper_id, questions, publications_by_question, solutions_by_publication, regions_by_question
    ) -> dict:
        """把一道卷上的若干道题整批搬进来，返回"对方编号 → 本机编号"的对照。

        **编号不能照抄**：题目的主键是整数自增，两台电脑各自从 1 开始编，
        对方的第 42 题和本机的第 42 题根本不是同一道题。照抄编号会撞车 ——
        撞上了那道题还会被当成"已经有了"而**悄悄丢掉**。

        所以：编号在本机没被占就沿用（题组之间的指向不用改），被占了就由
        数据库另编一个，然后把指向题组的那些关系改到新编号上。
        """
        id_map: dict[str, str] = {}
        for question in questions:
            source_id = str(question.get("id"))
            incoming_id = question.get("id")
            row = {key: value for key, value in question.items() if key != "id"}
            row["paper_id"] = paper_id
            # 题组指向题组表，删除批次指向批次表 —— 都要换成本机的编号，
            # 不能留对方的编号（那在本机根本不存在）。
            row["group_id"] = self.question_group_map.get(str(question.get("group_id")))
            row["deletion_batch_id"] = self.deletion_batch_map.get(str(question.get("deletion_batch_id")))
            # source_key 是**这台机器给这张卡的身份**（两台机器各自生成，本来就
            # 不会一样），搬过来要重新给一个 —— 照抄会撞上本机已有的唯一约束。
            row["source_key"] = uuid.uuid4().hex
            if incoming_id is not None and not self._exists_by_id(QUESTION_TABLE, str(incoming_id)):
                row["id"] = incoming_id
            new_id = self._insert(QUESTION_TABLE, row)
            if new_id:
                id_map[source_id] = new_id
                self.added["questions"].append(new_id)
                self.report["新增题目"] += 1
        # 入库快照、解析版本、框选识读：都跟着新的题目编号走。
        for question in questions:
            source_id = str(question.get("id"))
            target = id_map.get(source_id)
            if not target:
                continue
            for publication in (publications_by_question or {}).get(source_id, []):
                self._add_publication(
                    {**publication, "question_id": target, "paper_id": paper_id}, solutions_by_publication
                )
            for region in (regions_by_question or {}).get(source_id, []):
                # 框选识读是整型主键，交给数据库自己编号。
                inserted = self._insert(
                    REGION_TABLE, {key: value for key, value in region.items() if key != "id"} | {"question_id": target}
                )
                if inserted:
                    # 撤销时要删：题和卷子一删，这些识读记录还指着它们。
                    self.added["regions"].append(inserted)
        return id_map

    def _merge_into_paper(
        self,
        local_paper_id,
        paper,
        incoming_questions,
        publications_by_question,
        solutions_by_publication,
        regions_by_question,
        incoming_groups,
        incoming_batches,
    ) -> None:
        """同一份原卷：逐题比。本机有的不删，空着的补上。

        认题只认两样：**这套原卷**（按 sha256）+ **题号**。题号一样再看题干：
        题干也一样就是同一道题（合并内容）；题干不一样就是两道不同的题
        （两道都留着，备份里的那道放到本卷末尾的空题号上）。
        """
        # 这套卷本机已经有了：题组按"第几组"对上本机已有的，删除批次另起一批
        # （一次删除动作在本机也该算一次）。
        self._merge_question_groups(local_paper_id, incoming_groups)
        self._merge_deletion_batches(local_paper_id, incoming_batches)
        self.paper_map[str(paper.get("id"))] = local_paper_id
        self._repair_paper_paths(local_paper_id, paper)
        to_add: list[dict] = []
        for question in incoming_questions:
            number = question.get("number")
            # 这道题在本机的哪个题组里（题号只在题组里才唯一）。
            group_id = self.question_group_map.get(str(question.get("group_id")))
            stem = str(question.get("stem") or "")
            local = self._local_question(local_paper_id, number, group_id)
            if local is None:
                # 这个题号上没有题：也许同组里有题干一样的（题号变过）。
                twin = self._question_with_same_stem(local_paper_id, group_id, stem)
                if twin is not None:
                    local = twin
                else:
                    to_add.append(question)
                    continue
            else:
                mine = str(local.get("stem") or "")
                if stem and mine and not _same_stem(mine, stem):
                    # 同一个题号，题干不是一回事：先在同组里找找有没有题干一样的
                    # （上次合并给"题号一样内容不同"那道另编了题号的副本）。
                    twin = self._question_with_same_stem(local_paper_id, group_id, stem)
                    if twin is not None:
                        local = twin
                    else:
                        new_number = self._next_number(local_paper_id, group_id)
                        to_add.append({**question, "number": new_number})
                        self.report["题号一样内容不同"] += 1
                        self.notes.append(
                            f"第 {number} 题题号一样但内容不同，备份里的那道放到了第 {new_number} 题，两道都留着"
                        )
                        continue
            changed = []
            for field, _label in (("answer", "答案"), ("analysis", "解析")):
                if not str(local.get(field) or "").strip() and str(question.get(field) or "").strip():
                    changed.append((field, question[field]))
                elif str(local.get(field) or "").strip():
                    self.report["两边都有保留本机"] += 1
            if not local.get("figures") and question.get("figures"):
                changed.append(("figures", question["figures"]))
            if changed:
                if not self.dry_run:
                    with connection.cursor() as cursor:
                        for field, value in changed:
                            cursor.execute(f'UPDATE "core_question" SET "{field}"=? WHERE id=?', (value, local["id"]))
                            # 记下填进去之前是什么，撤销时才还原得回去。
                            self.added["filled"].append(
                                {
                                    "question_id": str(local["id"]),
                                    "field": field,
                                    "old": local.get(field) or "",
                                    "new": value,
                                }
                            )
                # 计数要放在 if 外面：预览时也要报出"会补上几道"，
                # 不然老师看到的是 0，真合并却是 2。
                self.report["补上答案"] += 1
            # 入库快照：同一个题、同一个版本号 = 同一个位置 → 保留本机那一份；
            # 对方多出来的版本（v2、v3…）带进来，解析版本也一起带。
            for publication in publications_by_question.get(str(question["id"]), []):
                version = publication.get("version")
                local_snapshot = self._local_publication(local["id"], version)
                if local_snapshot is not None:
                    if local_snapshot.get("content_hash") != publication.get("content_hash"):
                        self.report["两边都有保留本机"] += 1
                    incoming_solutions = solutions_by_publication.get(str(publication["id"]), [])
                    if incoming_solutions and self._local_solution_count(str(local_snapshot["id"])):
                        self.report["两版解析都留着"] += 1
                    # 对方这个入库快照在本机是另一个编号（版本号一样、内容一样，
                    # 但两边各编各的号）—— 题上贴的标签要跟着本机那个走。
                    self.publication_map[str(publication["id"])] = str(local_snapshot["id"])
                    continue
                self._add_publication(
                    {**publication, "question_id": local["id"], "paper_id": local_paper_id}, solutions_by_publication
                )
        if to_add:
            id_map = self._add_questions(
                local_paper_id, to_add, publications_by_question, solutions_by_publication, regions_by_question
            )
            self._fix_batch_question_ids(id_map)

    def _add_publication(self, publication, solutions_by_publication) -> None:
        publication_id = str(publication["id"])
        inserted = self._insert(PUBLICATION_TABLE, publication)
        if inserted:
            self.added["publications"].append(inserted)
            # 题上贴的标签指着入库快照：两边编号不一样，要记下对照。
            self.publication_map[publication_id] = inserted
        else:
            self.publication_map[publication_id] = publication_id
        for solution in (solutions_by_publication or {}).get(publication_id, []):
            solution_id = self._insert(SOLUTION_TABLE, solution)
            if solution_id:
                self.added["solutions"].append(solution_id)
                self.report["补进来的解析版本"] += 1
        # 解析里的图片跟着入库快照一起还原（见 _restore_originals）

    def _local_question(self, paper_id: str, number, group_id=None) -> dict | None:
        """本机这道题在哪儿（先按位置找）。

        **题号只在题组里才有意义**：一本书每章都从 1 开始编题号，只按题号找会把
        第一章的第 1 题和第二章的第 1 题配成一对 —— 1.26.5 真机上撞过：同一本书
        合并两次，712 道题被当成"题号一样但内容不同"又搬了一遍。

        所以认题是 **(卷, 题组, 题号)**；本机这道题没分组时，退而在同卷里按题号找
        （老库没有题组的情况）。
        """
        if group_id is not None:
            row = self._question_row("paper_id=? AND group_id=? AND number=?", (paper_id, group_id, number))
            if row is not None:
                return row
            # 本机这道题没分组（老库）：只在"没分组的那批题"里按题号找，
            # 别把别章的题配过来。
            return self._question_row("paper_id=? AND group_id IS NULL AND number=?", (paper_id, number))
        return self._question_row("paper_id=? AND number=?", (paper_id, number))

    def _question_with_same_stem(self, paper_id: str, group_id, stem: str) -> dict | None:
        """本机有没有题干一样的这道题。

        题号是显示用的，会变（上次合并遇到"题号一样内容不同"时会给备份里的那道
        另编一个题号）。**题干才是硬证据** —— 认题最终看它。
        """
        if not stem:
            return None
        where = "paper_id=?"
        params: list = [paper_id]
        if group_id is not None:
            where += " AND group_id=?"
            params.append(group_id)
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT * FROM "core_question" WHERE {where}', params)
            columns = [description[0] for description in cursor.description]
            for row in cursor.fetchall():
                candidate = dict(zip(columns, row))
                if _same_stem(candidate.get("stem") or "", stem):
                    return candidate
        return None

    def _question_row(self, where: str, params: tuple) -> dict | None:
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT * FROM "core_question" WHERE {where} LIMIT 1', params)
            columns = [description[0] for description in cursor.description]
            row = cursor.fetchone()
        return dict(zip(columns, row)) if row else None

    def _next_number(self, paper_id: str, group_id=None) -> int:
        """本卷末尾的空题号（题号在题组里才唯一，所以按题组算）。"""
        with connection.cursor() as cursor:
            if group_id is None:
                row = cursor.execute('SELECT MAX(number) FROM "core_question" WHERE paper_id=?', (paper_id,)).fetchone()
            else:
                row = cursor.execute(
                    'SELECT MAX(number) FROM "core_question" WHERE paper_id=? AND group_id=?', (paper_id, group_id)
                ).fetchone()
        highest = int(row[0] or 0) if row else 0
        reserved = self.reserved_numbers.setdefault(f"{paper_id}:{group_id}", set())
        candidate = highest + 1
        while candidate in reserved:
            candidate += 1
        reserved.add(candidate)
        return candidate

    def _local_publications(self, question_id: str) -> set[str]:
        with connection.cursor() as cursor:
            return {
                str(row[0])
                for row in cursor.execute(
                    'SELECT id FROM "core_publishedquestion" WHERE question_id=?', (question_id,)
                ).fetchall()
            }

    def _local_publication(self, question_id: str, version) -> dict | None:
        """这道题本机有没有同一个版本号的入库快照。"""
        if version is None:
            return None
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT id, content_hash FROM "core_publishedquestion" WHERE question_id=? AND version=? LIMIT 1',
                (question_id, version),
            )
            row = cursor.fetchone()
        return {"id": str(row[0]), "content_hash": row[1]} if row else None

    def _local_solution_count(self, publication_id: str) -> int:
        with connection.cursor() as cursor:
            row = cursor.execute(
                'SELECT COUNT(*) FROM "core_librarysolution" WHERE publication_id=?', (publication_id,)
            ).fetchone()
        return int(row[0]) if row else 0

    def _merge_assignments(self, assignments) -> None:
        for assignment in assignments:
            publication_id = self.publication_map.get(str(assignment.get("publication_id")))
            tag_id = self.tag_map.get(str(assignment.get("tag_id")))
            if not publication_id or not tag_id:
                continue
            if self._local_exists(ASSIGNMENT_TABLE, '"publication_id"=? AND "tag_id"=?', (publication_id, tag_id)):
                continue
            row = {key: value for key, value in assignment.items() if key != "id"}
            row["id"] = uuid.uuid4().hex  # 主键是 UUID，两边各编各的
            row["publication_id"] = publication_id
            row["tag_id"] = tag_id
            inserted = self._insert(ASSIGNMENT_TABLE, row)
            if inserted:
                # 记的是**新编号**：本机给了它一个新的 id，撤销时要按新编号删。
                self.added["assignments"].append(inserted)
                self.report["多贴的标签"] += 1

    # -- 文件 ---------------------------------------------------------

    def _manifest_paper(self, paper_id: str) -> dict:
        """清单里关于这套卷子的记录。

        清单按**带横杠**的编号记（那是硬盘上文件夹的名字），合并时手里拿到的
        是数据库里的**连写**编号。两种写法指的是同一个东西，但字符串不相等 ——
        以前就卡在这儿：清单说"没这个文件"，于是卷子的原卷路径被写成空的，
        原卷预览当场坏掉，审核页只剩一个白框。现在两边都先换成同一种写法。
        """
        wanted = _uuid_text(paper_id)
        for paper in self.manifest.get("papers") or []:
            if _uuid_text(paper.get("id")) == wanted:
                return paper
        return {}

    def _manifest_files(self, paper_id: str) -> dict:
        files = self._manifest_paper(paper_id).get("files") or {}
        return {key: value for key, value in files.items() if isinstance(value, str)}

    def _repair_paper_paths(self, local_paper_id: str, incoming_paper) -> None:
        """本机这份卷子的原卷路径是空的，就从备份里补上。

        1.26.23：早先的恢复会把原卷路径写成空字符串（清单和数据库的编号对不上），
        于是原卷预览坏掉、审核页只剩一个白框。这次恢复把文件放回来了，
        路径也得跟着指回去。
        """
        with connection.cursor() as cursor:
            row = cursor.execute(
                'SELECT source_path, render_path, zip_path FROM "core_paper" WHERE id=?', (local_paper_id,)
            ).fetchone()
        if row is None:
            return
        files = self._manifest_files(str(incoming_paper.get("id") or ""))
        folder = _uuid_text(local_paper_id)
        changes: dict[str, str] = {}
        for index, (label, column) in enumerate(
            (("source", "source_path"), ("render", "render_path"), ("zip", "zip_path"))
        ):
            if str(row[index] or "").strip():
                continue
            restored = self._restored_path(folder, files.get(label))
            if restored:
                changes[column] = restored
        if not changes:
            return
        self.report["补回原卷路径"] = self.report.get("补回原卷路径", 0) + len(changes)
        if self.dry_run:
            return
        with connection.cursor() as cursor:
            for column, value in changes.items():
                cursor.execute(f'UPDATE "core_paper" SET "{column}"=? WHERE id=?', (value, local_paper_id))

    def _restored_path(self, folder: str, relative) -> str:
        """原卷文件在本机的绝对位置；清单里没有就留空。

        清单里记的是**对方机器上**的相对路径（相对题库数据文件夹），第一段是
        卷子自己的文件夹名。本机的卷子可能换了新编号，所以第一段换成本机的，
        后面的原样保留。
        """
        if not relative or not isinstance(relative, str):
            return ""
        parts = PurePosixPath(relative).parts
        if len(parts) < 2:
            return ""
        return str(data_root().joinpath(folder, *parts[1:]))

    def _mapped_paper(self, folder: str) -> str:
        """包里那个卷子文件夹，在本机叫什么。"""
        wanted = _uuid_text(folder)
        for incoming, local in self.paper_map.items():
            if _uuid_text(incoming) == wanted:
                return _uuid_text(local)
        return wanted

    def _mapped_publication(self, folder: str) -> str | None:
        """包里那个入库快照文件夹，在本机叫什么。

        对不上就返回 None —— 那个入库快照本机没有，放下来也是没人看的孤儿。
        """
        wanted = _uuid_text(folder)
        for incoming, local in self.publication_map.items():
            if _uuid_text(incoming) == wanted:
                return _uuid_text(local)
        return None

    def _target_relative(self, relative: str) -> str | None:
        """包里的一条路径，放到硬盘上该去的那个相对路径。

        认得三种：卷子自己的文件夹、题库裁片的文件夹、答案解析的文件夹。
        顶层别的零散文件（窗口位置之类）不还原 —— 那是那台电脑的界面状态，
        搬过来只会把本机的界面弄乱。
        """
        parts = PurePosixPath(relative).parts
        if not parts:
            return None
        head, rest = parts[0], parts[1:]
        if head == "library" and rest:
            publication = self._mapped_publication(rest[0])
            if publication is None:
                return None
            return PurePosixPath("library", publication, *rest[1:]).as_posix()
        if head == "library-solutions" and rest:
            publication = self._mapped_publication(rest[0])
            if publication is None:
                return None
            return PurePosixPath("library-solutions", publication, *rest[1:]).as_posix()
        if not _looks_like_uuid(head):
            # 顶上是别的零散文件（窗口位置之类）：那是那台电脑的界面状态，
            # 搬过来只会把本机的界面弄乱，不还原。
            return None
        # 顶上就是卷子编号的，是卷子自己的文件（题目插图、原卷 PDF 都在这儿）
        return PurePosixPath(self._mapped_paper(head), *rest).as_posix()

    def _restore_originals(self) -> None:
        """把包里的原卷文件放回硬盘上该在的位置。

        1.26.23：以前是"按卷子的编号去包里找前缀"，而包里用的是带横杠的写法、
        硬盘上用的是连写的 —— 前缀对不上，**一个文件都回不来**，卷子的原卷路径
        还被写成空的，原卷预览当场坏掉。

        现在反过来：**包里怎么写，就按同一个相对路径放回去**；只有卷子或入库
        快照换了新编号时，目录名跟着换。
        """
        prefix = f"{ORIGINALS}/"
        count = 0
        for name in self._names:
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            relative = name[len(prefix) :]
            target_relative = self._target_relative(relative)
            if target_relative is None:
                continue
            count += 1
            if self.dry_run:
                continue
            target = data_root() / PurePosixPath(target_relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.archive.open(name) as reader, open(target, "wb") as writer:
                    shutil.copyfileobj(reader, writer)
            except (OSError, KeyError):
                continue
        self.report["补回的文件"] = count


def _same_stem(left: str, right: str) -> bool:
    """题干是不是同一道题：看开头三十个字。"""

    def head(value: str) -> str:
        return "".join(value.split())[:30]

    a, b = head(left), head(right)
    if not a or not b:
        return True
    return a == b or a.startswith(b) or b.startswith(a)


def _count(table: str) -> int:
    with connection.cursor() as cursor:
        row = cursor.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    return int(row[0]) if row else 0


def plan_merge(path: Path) -> dict:
    """合并前先看一眼：会发生什么。"""
    archive, manifest = _open_bundle(Path(path))
    try:
        merge = _Merge(archive, manifest, dry_run=True)
        report = merge.run()
    finally:
        archive.close()
    return {"manifest": manifest, "report": report}


def merge_bundle(path: Path) -> dict:
    """把一份备份并进本机。返回人话报告。"""
    archive, manifest = _open_bundle(Path(path))
    try:
        with transaction.atomic():
            merge = _Merge(archive, manifest, dry_run=False)
            report = merge.run()
            record = _write_record(merge)
    finally:
        archive.close()
    return {"report": report, "record": record, "manifest": manifest}


def _write_record(merge: _Merge) -> dict:
    """记下这次动了什么，好让老师能撤销。"""
    root = records_root()
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    paper_names: list[str] = []
    for item in merge.manifest.get("papers") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("title") or item.get("filename") or "").strip()
        if name and name not in paper_names:
            paper_names.append(name)
    folder_names: list[str] = []
    for item in merge.manifest.get("folders") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name and name not in folder_names:
            folder_names.append(name)
    record = {
        "id": f"{stamp}",
        "created_at": timezone.now().isoformat(),
        "paper_names": paper_names,
        "folder_names": folder_names,
        **merge.added,
    }
    target = root / f"merge-{stamp}.json"
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "papers": len(merge.added["papers"]),
        "questions": len(merge.added["questions"]),
        "paper_names": paper_names[:5],
        "folder_names": folder_names[:5],
    }


def list_records() -> list[dict]:
    root = records_root()
    if not root.is_dir():
        return []
    records = []
    for path in sorted(root.glob("merge-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        records.append(
            {
                "id": data.get("id"),
                "created_at": data.get("created_at"),
                "papers": len(data.get("papers") or []),
                "questions": len(data.get("questions") or []),
                "solutions": len(data.get("solutions") or []),
                "filled": len(data.get("filled") or []),
                "tags": len(data.get("tags") or []),
                "paper_names": [str(name) for name in (data.get("paper_names") or []) if str(name).strip()][:5],
                "folder_names": [str(name) for name in (data.get("folder_names") or []) if str(name).strip()][:5],
            }
        )
    return records


def undo_last_merge() -> dict:
    """撤销最近一次合并：精确删掉那一次加进去的东西。"""
    root = records_root()
    if not root.is_dir():
        raise TransferError("本机没有合并过备份，没有可撤销的。")
    files = sorted(root.glob("merge-*.json"), reverse=True)
    if not files:
        raise TransferError("本机没有合并过备份，没有可撤销的。")
    path = files[0]
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise TransferError("这次合并的记录读不出来，没法撤销。") from None
    removed = {"questions": 0, "papers": 0, "solutions": 0, "tags": 0, "filled": 0}
    with transaction.atomic():
        # 先把"填进已有题里的答案"还原回去 —— 不然撤销撤不干净。
        for item in record.get("filled") or []:
            field = str(item.get("field") or "")
            question_id = str(item.get("question_id") or "")
            if field not in ("answer", "analysis") or not question_id:
                continue
            with connection.cursor() as cursor:
                cursor.execute(
                    f'UPDATE "core_question" SET "{field}"=? WHERE id=?', (item.get("old") or "", question_id)
                )
            removed["filled"] += 1
        for question_id in record.get("questions") or []:
            removed["questions"] += _delete("core_question", question_id)
        for publication_id in record.get("publications") or []:
            _delete("core_publishedquestion", publication_id)
        for solution_id in record.get("solutions") or []:
            removed["solutions"] += _delete("core_librarysolution", solution_id)
        for assignment_id in record.get("assignments") or []:
            _delete("core_librarytagassignment", assignment_id)
        for membership_id in record.get("memberships") or []:
            _delete(TAG_MEMBERSHIP_TABLE, membership_id)
        for relation_id in record.get("relations") or []:
            _delete("core_librarytagrelation", relation_id)
        for tag_id in record.get("tags") or []:
            removed["tags"] += _delete("core_librarytag", tag_id)
        for group_id in record.get("groups") or []:
            _delete("core_librarytaggroup", group_id)
        for batch_id in record.get("deletion_batches") or []:
            _delete("core_questiondeletionbatch", batch_id)
        for group_id in record.get("question_groups") or []:
            _delete("core_questiongroup", group_id)
        for block_id in record.get("blocks") or []:
            _delete("core_block", block_id)
        for region_id in record.get("regions") or []:
            _delete("core_regionread", region_id)
        for paper_id in record.get("papers") or []:
            removed["papers"] += _delete("core_paper", paper_id)
            # 卷子的文件夹名是**带横杠**的编号（数据库里存的是连写的那个）——
            # 用数据库里的写法去删，删不掉真东西，原卷文件会留在盘上。
            shutil.rmtree(data_root() / _uuid_text(paper_id), ignore_errors=True)
    path.unlink(missing_ok=True)
    return removed


def _delete(table: str, row_id: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE FROM "{table}" WHERE id=?', (row_id,))
        return cursor.rowcount
