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

# 跟着题走的表：题在哪张卷下，它们就跟着走。
QUESTION_CHILDREN = (PUBLICATION_TABLE, SOLUTION_TABLE, REGION_TABLE)
# 只在本机有意义、不该跟着走的：AI 任务队列、AI 标签候选、操作日志、回收站。
LOCAL_ONLY_TABLES = {
    "core_libraryjob", "core_librarytagsuggestion", "core_librarytagoperation",
    "core_questionlayoutoperation", "core_questiondeletionbatch", "core_importchunk",
    "core_librarytagcatalogue", "core_questiongroup",
}
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
                      "tags": [], "groups": [], "relations": [], "assignments": [], "filled": []}

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
            return True
        columns = [name for name in row if name in self._main_columns(table)]
        if not columns:
            return False
        placeholders = ",".join("?" for _ in columns)
        names = ",".join(f'"{name}"' for name in columns)
        values = [row[name] for name in columns]
        with connection.cursor() as cursor:
            try:
                cursor.execute(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})', values)
                return True
            except sqlite3.IntegrityError:
                if row.get("id") and self._exists_by_id(table, str(row["id"])):
                    return False
                raise

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
        return self.report

    def _merge_tags(self) -> None:
        groups = _rows(self.incoming, TAG_GROUP_TABLE)
        group_map: dict[str, str] = {}
        for group in groups:
            name = str(group.get("name") or "")
            if not name:
                continue
            local = self._local_tag_group(name)
            if local is None:
                new_id = str(uuid.uuid4())
                group_map[str(group["id"])] = new_id
                row = {**group, "id": new_id}
                if self._insert(TAG_GROUP_TABLE, row):
                    self.added["groups"].append(new_id)
                    self.report["新增标签"] += 0
            else:
                group_map[str(group["id"])] = local

        tag_map: dict[str, str] = {}
        for tag in _rows(self.incoming, TAG_TABLE):
            kind = str(tag.get("kind") or "knowledge")
            name = str(tag.get("name") or "")
            if not name:
                continue
            local = self._local_tag(kind, name)
            if local is None:
                new_id = str(uuid.uuid4())
                tag_map[str(tag["id"])] = new_id
                if self._insert(TAG_TABLE, {**tag, "id": new_id}):
                    self.added["tags"].append(new_id)
                    self.report["新增标签"] += 1
            else:
                tag_map[str(tag["id"])] = local

        for relation in _rows(self.incoming, RELATION_TABLE):
            parent = tag_map.get(str(relation.get("parent_id")))
            child = tag_map.get(str(relation.get("child_id")))
            if not parent or not child:
                continue
            row = {**relation, "parent_id": parent, "child_id": child}
            if self._local_exists(RELATION_TABLE, '"parent_id"=? AND "child_id"=? AND "relation_type"=?',
                                  (parent, child, relation.get("relation_type") or "HIERARCHY")):
                continue
            if self._insert(RELATION_TABLE, row):
                self.added["relations"].append(str(relation.get("id")))

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

        for paper in papers:
            local = self._local_paper(str(paper.get("sha256") or ""))
            if local is None:
                self._add_paper(paper, blocks_by_paper, questions_by_paper, publications_by_question,
                                solutions_by_publication, regions_by_question)
            else:
                self._merge_into_paper(local, paper, questions_by_paper.get(str(paper["id"]), []),
                                       publications_by_question, solutions_by_publication, regions_by_question)
        self._merge_assignments(assignments)

    def _local_paper(self, sha256: str) -> str | None:
        if not sha256:
            return None
        with connection.cursor() as cursor:
            row = cursor.execute('SELECT id FROM "core_paper" WHERE sha256=? LIMIT 1', (sha256,)).fetchone()
        return str(row[0]) if row else None

    def _add_paper(self, paper, blocks_by_paper, questions_by_paper, publications_by_question,
                   solutions_by_publication, regions_by_question) -> None:
        paper_id = str(paper["id"])
        row = {**paper}
        files = self._manifest_files(paper_id)
        for label, column in (("source", "source_path"), ("render", "render_path"), ("zip", "zip_path")):
            relative = files.get(label)
            row[column] = str(data_root() / relative) if relative else ""
        if self._insert(PAPER_TABLE, row):
            self.added["papers"].append(paper_id)
            self.report["新增卷子"] += 1
        for block in blocks_by_paper.get(paper_id, []):
            self._insert(BLOCK_TABLE, block)
        for question in questions_by_paper.get(paper_id, []):
            self._add_question(question, publications_by_question, solutions_by_publication, regions_by_question)
        self._copy_paper_files(paper_id)

    def _add_question(self, question, publications_by_question, solutions_by_publication, regions_by_question):
        """整道题搬进来：题、入库快照、解析版本、框选识读。"""
        question_id = str(question["id"])
        if self._insert(QUESTION_TABLE, question):
            self.added["questions"].append(question_id)
            self.report["新增题目"] += 1
        for publication in (publications_by_question or {}).get(question_id, []):
            self._add_publication(publication, solutions_by_publication)
        for region in (regions_by_question or {}).get(question_id, []):
            self._insert(REGION_TABLE, region)

    def _merge_into_paper(self, local_paper_id, paper, incoming_questions,
                          publications_by_question, solutions_by_publication, regions_by_question) -> None:
        """同一份原卷：逐题比。本机有的不删，空着的补上。"""
        for question in incoming_questions:
            number = question.get("number")
            local = self._local_question(local_paper_id, number)
            if local is None:
                self._add_question({**question, "paper_id": local_paper_id},
                                   publications_by_question, solutions_by_publication, regions_by_question)
                continue
            stem = str(question.get("stem") or "")
            mine = str(local.get("stem") or "")
            if stem and mine and not _same_stem(mine, stem):
                self.report["题号一样内容不同"] += 1
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
            # 两边都有解析：两份都留着（题有据的解析本来就是一串版本），不覆盖。
            local_publications = self._local_publications(local["id"])
            for publication in publications_by_question.get(str(question["id"]), []):
                if str(publication["id"]) in local_publications:
                    incoming_solutions = solutions_by_publication.get(str(publication["id"]), [])
                    if incoming_solutions and self._local_solution_count(str(publication["id"])):
                        self.report["两版解析都留着"] += 1
                    continue
                self._add_publication(publication, solutions_by_publication)

    def _add_publication(self, publication, solutions_by_publication) -> None:
        publication_id = str(publication["id"])
        if self._insert(PUBLICATION_TABLE, publication):
            self.added["publications"].append(publication_id)
        for solution in (solutions_by_publication or {}).get(publication_id, []):
            if self._insert(SOLUTION_TABLE, solution):
                self.added["solutions"].append(str(solution["id"]))
                self.report["补进来的解析版本"] += 1
        self._copy_solution_files(publication_id)

    def _local_question(self, paper_id: str, number) -> dict | None:
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM "core_question" WHERE paper_id=? AND number=? LIMIT 1',
                           (paper_id, number))
            columns = [description[0] for description in cursor.description]
            row = cursor.fetchone()
        return dict(zip(columns, row)) if row else None

    def _local_publications(self, question_id: str) -> set[str]:
        with connection.cursor() as cursor:
            return {str(row[0]) for row in cursor.execute(
                'SELECT id FROM "core_publishedquestion" WHERE question_id=?', (question_id,)).fetchall()}

    def _local_solution_count(self, publication_id: str) -> int:
        with connection.cursor() as cursor:
            row = cursor.execute('SELECT COUNT(*) FROM "core_librarysolution" WHERE publication_id=?',
                                 (publication_id,)).fetchone()
        return int(row[0]) if row else 0

    def _merge_assignments(self, assignments) -> None:
        for assignment in assignments:
            publication_id = str(assignment.get("publication_id"))
            tag_id = str(assignment.get("tag_id"))
            if not publication_id or not tag_id:
                continue
            if self._local_exists(ASSIGNMENT_TABLE, '"publication_id"=? AND "tag_id"=?',
                                  (publication_id, tag_id)):
                continue
            if self._insert(ASSIGNMENT_TABLE, assignment):
                self.added["assignments"].append(str(assignment.get("id")))
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
        for relation_id in record.get("relations") or []:
            _delete("core_librarytagrelation", relation_id)
        for tag_id in record.get("tags") or []:
            removed["tags"] += _delete("core_librarytag", tag_id)
        for group_id in record.get("groups") or []:
            _delete("core_librarytaggroup", group_id)
        for paper_id in record.get("papers") or []:
            removed["papers"] += _delete("core_paper", paper_id)
            shutil.rmtree(data_root() / paper_id, ignore_errors=True)
    path.unlink(missing_ok=True)
    return removed


def _delete(table: str, row_id: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(f'DELETE FROM "{table}" WHERE id=?', (row_id,))
        return cursor.rowcount
