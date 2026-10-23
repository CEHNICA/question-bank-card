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

import json
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
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
SKIP_DIRECTORIES = {"chunks", "pages", "library"}
SKIP_NAMES = {"features.json", "mineru_engine.json", "knowledge-points.txt",
              "library-drafts.json", "worker.lock", "library-drafts.lock"}


class TransferError(ValueError):
    """A problem the teacher can act on; the message says what to do next."""


def data_root() -> Path:
    return Path(settings.DATA_ROOT)


def database_path() -> Path:
    return Path(settings.DATABASES["default"]["NAME"])


def records_root() -> Path:
    return data_root() / "imports"


# ---------------------------------------------------------------- 导出


def _snapshot_database(target: Path) -> None:
    """拍一份一致的题库快照。

    不能直接拷文件：软件正在写的时候拷到半个库。也不能用 SQLite 的 backup 接口 ——
    它在**开着事务的同一个连接**上会自己把自己锁住（备份要读源库，而源库正被这个
    连接写事务锁着），表现为整个程序卡住不动。

    这里逐表读、逐表写：读到的是这个连接此刻看到的样子（没提交的改动也在），
    写出来的是一份独立、可直接打开的库。
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
        writer.commit()
    with contextlib_closing(sqlite3.connect(target)) as check:
        if check.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise TransferError("备份没有拍完整，已停止，没有动本机题库。")


def contextlib_closing(value):
    from contextlib import closing
    return closing(value)


def _bundle_files(root: Path):
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
        yield path


def _manifest() -> dict:
    from core.models import LibraryTag, Paper, PublishedQuestion, Question

    papers = []
    for paper in Paper.objects.all().order_by("created_at"):
        files = {}
        for label, value in (("source", paper.source_path), ("render", paper.render_path),
                             ("zip", paper.zip_path)):
            if value and Path(value).is_file():
                try:
                    files[label] = Path(value).resolve().relative_to(data_root().resolve()).as_posix()
                except ValueError:
                    continue
        papers.append({
            "id": str(paper.pk), "filename": paper.filename, "sha256": paper.sha256,
            "questions": Question.objects.filter(paper=paper).count(),
            "files": files,
        })
    return {
        "format": BUNDLE_FORMAT,
        "app_version": getattr(__import__("core.version", fromlist=["APP_VERSION"]), "APP_VERSION", ""),
        "created_at": timezone.now().isoformat(),
        "machine": os.environ.get("COMPUTERNAME") or "这台电脑",
        "counts": {
            "papers": len(papers),
            "questions": Question.objects.count(),
            "publications": PublishedQuestion.objects.count(),
            "tags": LibraryTag.objects.count(),
        },
        "papers": papers,
    }


def export_bundle(directory: Path) -> dict:
    """把整库打包成一个 zip 放进 `directory`。"""
    directory = Path(directory)
    if not directory.is_dir():
        raise TransferError("选的文件夹不在了，重新选一个。")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = directory / f"题有据备份-{stamp}.zip"
    with tempfile.TemporaryDirectory() as work:
        snapshot = Path(work) / DATABASE_NAME
        _snapshot_database(snapshot)
        manifest = _manifest()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.write(snapshot, DATABASE_NAME)
            for path in _bundle_files(data_root()):
                archive.write(path, f"{ORIGINALS}/{path.relative_to(data_root()).as_posix()}")
    return {"file": str(target), "name": target.name, "size": target.stat().st_size,
            "counts": manifest["counts"], "created_at": manifest["created_at"]}


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
        self.report = {
            "新增卷子": 0, "新增题目": 0, "补上答案": 0, "两边都有保留本机": 0,
            "题号一样内容不同": 0, "两版解析都留着": 0, "新增标签": 0, "多贴的标签": 0,
            "补进来的解析版本": 0,
        }
        self.added = {"papers": [], "questions": [], "publications": [], "solutions": [],
                      "tags": [], "groups": [], "relations": [], "assignments": [], "filled": [],
                      "question_groups": [], "deletion_batches": [], "memberships": [], "blocks": [],
                      "regions": []}
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
            except sqlite3.IntegrityError:
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
        finally:
            self.incoming.close()
            shutil.rmtree(source.parent, ignore_errors=True)
        self.report["本机原有"] = {
            "卷子": _count(PAPER_TABLE), "题目": _count(QUESTION_TABLE),
            "入库快照": _count(PUBLICATION_TABLE), "标签": _count(TAG_TABLE),
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
            if self._local_exists(TAG_MEMBERSHIP_TABLE, '"librarytag_id"=? AND "librarytaggroup_id"=?',
                                  (tag, group)):
                continue
            inserted = self._insert(TAG_MEMBERSHIP_TABLE,
                                    {key: value for key, value in row.items() if key != "id"} |
                                    {"librarytag_id": tag, "librarytaggroup_id": group})
            if inserted:
                self.added["memberships"].append(inserted)

        for relation in _rows(self.incoming, RELATION_TABLE):
            parent = tag_map.get(str(relation.get("parent_id")))
            child = tag_map.get(str(relation.get("child_id")))
            if not parent or not child:
                continue
            if self._local_exists(RELATION_TABLE, '"parent_id"=? AND "child_id"=? AND "relation_type"=?',
                                  (parent, child, relation.get("relation_type") or "HIERARCHY")):
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
            row = cursor.execute('SELECT id FROM "core_librarytag" WHERE kind=? AND name=? LIMIT 1',
                                 (kind, name)).fetchone()
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
                self._add_paper(paper, blocks_by_paper, questions_by_paper, publications_by_question,
                                solutions_by_publication, regions_by_question,
                                groups_by_paper, batches_by_paper)
            else:
                self._merge_into_paper(local, paper, questions_by_paper.get(str(paper["id"]), []),
                                       publications_by_question, solutions_by_publication, regions_by_question,
                                       groups_by_paper.get(str(paper["id"]), []),
                                       batches_by_paper.get(str(paper["id"]), []))
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
        if not sha256:
            return None
        with connection.cursor() as cursor:
            row = cursor.execute('SELECT id FROM "core_paper" WHERE sha256=? LIMIT 1', (sha256,)).fetchone()
        return str(row[0]) if row else None

    def _add_paper(self, paper, blocks_by_paper, questions_by_paper, publications_by_question,
                   solutions_by_publication, regions_by_question, groups_by_paper, batches_by_paper) -> None:
        paper_id = str(paper["id"])
        row = {**paper}
        files = self._manifest_files(paper_id)
        for label, column in (("source", "source_path"), ("render", "render_path"), ("zip", "zip_path")):
            relative = files.get(label)
            row[column] = str(data_root() / relative) if relative else ""
        inserted = self._insert(PAPER_TABLE, row)
        if inserted:
            self.added["papers"].append(inserted)
            self.report["新增卷子"] += 1
        self.paper_map[paper_id] = inserted or paper_id
        for block in blocks_by_paper.get(paper_id, []):
            inserted = self._insert(BLOCK_TABLE, block)
            if inserted:
                # 撤销时要删：卷子一删，这些块还指着它，外键就断了。
                self.added["blocks"].append(inserted)
        # 题组和删除批次得先摆好：题目马上要指过去。
        self._merge_question_groups(paper_id, groups_by_paper.get(paper_id, []))
        self._merge_deletion_batches(paper_id, batches_by_paper.get(paper_id, []))
        id_map = self._add_questions(paper_id, questions_by_paper.get(paper_id, []),
                                     publications_by_question, solutions_by_publication, regions_by_question)
        self._fix_batch_question_ids(id_map)
        self._copy_paper_files(paper_id)

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
                    'SELECT id FROM "core_questiongroup" WHERE paper_id=? AND sequence=? LIMIT 1',
                    (paper_id, sequence)).fetchone()
                return str(row[0]) if row else None
            if title:
                row = cursor.execute(
                    'SELECT id FROM "core_questiongroup" WHERE paper_id=? AND title=? LIMIT 1',
                    (paper_id, title)).fetchone()
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
                cursor.execute('UPDATE "core_questiondeletionbatch" SET "question_ids"=? WHERE id=?',
                               (json.dumps(local_ids), batch_id))

    def _add_questions(self, paper_id, questions, publications_by_question,
                       solutions_by_publication, regions_by_question) -> dict:
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
                self._add_publication({**publication, "question_id": target, "paper_id": paper_id},
                                      solutions_by_publication)
            for region in (regions_by_question or {}).get(source_id, []):
                # 框选识读是整型主键，交给数据库自己编号。
                inserted = self._insert(REGION_TABLE, {key: value for key, value in region.items() if key != "id"} |
                                       {"question_id": target})
                if inserted:
                    # 撤销时要删：题和卷子一删，这些识读记录还指着它们。
                    self.added["regions"].append(inserted)
        return id_map

    def _merge_into_paper(self, local_paper_id, paper, incoming_questions,
                          publications_by_question, solutions_by_publication, regions_by_question,
                          incoming_groups, incoming_batches) -> None:
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
        to_add: list[dict] = []
        for question in incoming_questions:
            number = question.get("number")
            local = self._local_question(local_paper_id, number)
            if local is None:
                to_add.append(question)
                continue
            stem = str(question.get("stem") or "")
            mine = str(local.get("stem") or "")
            if stem and mine and not _same_stem(mine, stem):
                new_number = self._next_number(local_paper_id)
                to_add.append({**question, "number": new_number})
                self.report["题号一样内容不同"] += 1
                self.notes.append(f"第 {number} 题题号一样但内容不同，备份里的那道放到了第 {new_number} 题，两道都留着")
                continue
            changed = []
            for field, label in (("answer", "答案"), ("analysis", "解析")):
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
                            self.added["filled"].append({"question_id": str(local["id"]), "field": field,
                                                        "old": local.get(field) or "", "new": value})
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
                self._add_publication({**publication, "question_id": local["id"], "paper_id": local_paper_id},
                                      solutions_by_publication)
        if to_add:
            id_map = self._add_questions(local_paper_id, to_add, publications_by_question,
                                          solutions_by_publication, regions_by_question)
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
        self._copy_solution_files(publication_id)

    def _local_question(self, paper_id: str, number) -> dict | None:
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM "core_question" WHERE paper_id=? AND number=? LIMIT 1',
                           (paper_id, number))
            columns = [description[0] for description in cursor.description]
            row = cursor.fetchone()
        return dict(zip(columns, row)) if row else None

    def _next_number(self, paper_id: str) -> int:
        """本卷末尾的空题号。预览时也要算对，所以先把用掉的号记下来。"""
        with connection.cursor() as cursor:
            row = cursor.execute('SELECT MAX(number) FROM "core_question" WHERE paper_id=?',
                                 (paper_id,)).fetchone()
        highest = int(row[0] or 0) if row else 0
        reserved = self.reserved_numbers.setdefault(paper_id, set())
        candidate = highest + 1
        while candidate in reserved:
            candidate += 1
        reserved.add(candidate)
        return candidate

    def _local_publications(self, question_id: str) -> set[str]:
        with connection.cursor() as cursor:
            return {str(row[0]) for row in cursor.execute(
                'SELECT id FROM "core_publishedquestion" WHERE question_id=?', (question_id,)).fetchall()}

    def _local_publication(self, question_id: str, version) -> dict | None:
        """这道题本机有没有同一个版本号的入库快照。"""
        if version is None:
            return None
        with connection.cursor() as cursor:
            cursor.execute('SELECT id, content_hash FROM "core_publishedquestion" WHERE question_id=? AND version=? LIMIT 1',
                           (question_id, version))
            row = cursor.fetchone()
        return {"id": str(row[0]), "content_hash": row[1]} if row else None

    def _local_solution_count(self, publication_id: str) -> int:
        with connection.cursor() as cursor:
            row = cursor.execute('SELECT COUNT(*) FROM "core_librarysolution" WHERE publication_id=?',
                                 (publication_id,)).fetchone()
        return int(row[0]) if row else 0

    def _merge_assignments(self, assignments) -> None:
        for assignment in assignments:
            publication_id = self.publication_map.get(str(assignment.get("publication_id")))
            tag_id = self.tag_map.get(str(assignment.get("tag_id")))
            if not publication_id or not tag_id:
                continue
            if self._local_exists(ASSIGNMENT_TABLE, '"publication_id"=? AND "tag_id"=?',
                                  (publication_id, tag_id)):
                continue
            row = {key: value for key, value in assignment.items() if key != "id"}
            row["id"] = uuid.uuid4().hex       # 主键是 UUID，两边各编各的
            row["publication_id"] = publication_id
            row["tag_id"] = tag_id
            inserted = self._insert(ASSIGNMENT_TABLE, row)
            if inserted:
                # 记的是**新编号**：本机给了它一个新的 id，撤销时要按新编号删。
                self.added["assignments"].append(inserted)
                self.report["多贴的标签"] += 1

    # -- 文件 ---------------------------------------------------------

    def _manifest_files(self, paper_id: str) -> dict:
        for paper in self.manifest.get("papers") or []:
            if str(paper.get("id")) == paper_id:
                files = paper.get("files") or {}
                return {key: value for key, value in files.items() if isinstance(value, str)}
        return {}

    def _copy_paper_files(self, paper_id: str) -> None:
        if self.dry_run:
            return
        folder = data_root() / paper_id
        prefix = f"{ORIGINALS}/{paper_id}/"
        for name in self.archive.namelist():
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            relative = name[len(prefix):]
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with self.archive.open(name) as reader, open(target, "wb") as writer:
                shutil.copyfileobj(reader, writer)

    def _copy_solution_files(self, publication_id: str) -> None:
        if self.dry_run:
            return
        folder = data_root() / "library-solutions" / publication_id
        prefix = f"{ORIGINALS}/library-solutions/{publication_id}/"
        for name in self.archive.namelist():
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            target = folder / name[len(prefix):]
            target.parent.mkdir(parents=True, exist_ok=True)
            with self.archive.open(name) as reader, open(target, "wb") as writer:
                shutil.copyfileobj(reader, writer)


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
    record = {"id": f"{stamp}", "created_at": timezone.now().isoformat(), **merge.added}
    target = root / f"merge-{stamp}.json"
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": record["id"], "created_at": record["created_at"],
            "papers": len(merge.added["papers"]), "questions": len(merge.added["questions"])}


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
        records.append({"id": data.get("id"), "created_at": data.get("created_at"),
                        "papers": len(data.get("papers") or []),
                        "questions": len(data.get("questions") or []),
                        "solutions": len(data.get("solutions") or []),
                        "filled": len(data.get("filled") or []),
                        "tags": len(data.get("tags") or [])})
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
                cursor.execute(f'UPDATE "core_question" SET "{field}"=? WHERE id=?',
                               (item.get("old") or "", question_id))
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
            shutil.rmtree(data_root() / paper_id, ignore_errors=True)
    path.unlink(missing_ok=True)
    return removed


def _delete(table: str, row_id: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE FROM "{table}" WHERE id=?', (row_id,))
        return cursor.rowcount
