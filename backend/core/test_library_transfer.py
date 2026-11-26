"""题库备份包：导出、合并、撤销。

用两个**独立**的库模拟两台电脑，验三件最要紧的事：

1. 同一份原卷认得出是同一套卷（不会变成两套）；
2. 本机空着的答案/解析会被补上，两边都有的**保留本机的**；
3. 合并是幂等的 —— 同一个包合两次，第二次什么都不会变。

还验了：撤销能把这次加进来的东西精确删掉；包里不含密钥和设置。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

from django.test import Client, TestCase, override_settings

from core import library_transfer
from core.models import (
    Block,
    LibrarySolution,
    LibraryTag,
    LibraryTagAssignment,
    LibraryTagGroup,
    LibraryTagRelation,
    Paper,
    PublishedQuestion,
    Question,
    QuestionDeletionBatch,
    QuestionGroup,
)


class TransferFixture(TestCase):
    """一套卷、几道题，外加标签和解析版本。"""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="qb-transfer-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.data = self.work / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        override = override_settings(DATA_ROOT=self.data)
        override.enable()
        self.addCleanup(override.disable)
        self.paper = self.make_paper("卷一.pdf", sha="a" * 64)
        self.questions = [
            self.make_question(self.paper, 1, "（虚构）第一题", "甲"),
            self.make_question(self.paper, 2, "（虚构）第二题", ""),
            self.make_question(self.paper, 3, "（虚构）第三题", ""),
        ]

    def make_paper(self, filename, *, sha):
        paper = Paper.objects.create(
            filename=filename,
            kind="pdf",
            sha256=sha,
            status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 1684, "height": 1190}],
        )
        folder = self.data / str(paper.pk)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "source.pdf").write_bytes(b"%PDF-1.4 fake")
        paper.source_path = str(folder / "source.pdf")
        paper.render_path = paper.source_path
        paper.save()
        Block.objects.create(paper=paper, seq=1, page_idx=0, type="image", text="", bbox=[10, 10, 200, 200])
        return paper

    def make_question(self, paper, number, stem, answer):
        return Question.objects.create(
            paper=paper,
            number=number,
            question_type="single_choice",
            stem=stem,
            options={"A": "甲"},
            answer=answer,
            analysis="",
            state=Question.State.YELLOW,
            text_source="agree",
            regions=[{"page_idx": 0, "bbox": [10, 10, 200, 200]}],
        )

    def export(self):
        target = self.work / "backup.zip"
        result = library_transfer.export_bundle(target.parent)
        return Path(result["file"])


class ExportTests(TransferFixture):
    def test_the_bundle_carries_the_library_and_the_originals(self):
        bundle = self.export()
        self.assertTrue(bundle.is_file())
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            self.assertIn(library_transfer.MANIFEST_NAME, names)
            self.assertIn(library_transfer.DATABASE_NAME, names)
            self.assertIn(f"{library_transfer.ORIGINALS}/{self.paper.pk}/source.pdf", names)
            manifest = json.loads(archive.read(library_transfer.MANIFEST_NAME).decode("utf-8"))
        self.assertEqual(manifest["counts"]["papers"], 1)
        self.assertEqual(manifest["counts"]["questions"], 3)
        # 清单里记的是相对路径，换台电脑才对得上。
        self.assertEqual(manifest["papers"][0]["files"]["source"], f"{self.paper.pk}/source.pdf")

    def test_the_bundle_does_not_carry_settings_or_secrets(self):
        bundle = self.export()
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
        for forbidden in ("features.json", "mineru_engine.json"):
            self.assertFalse(any(name.endswith(forbidden) for name in names), f"包里不该有 {forbidden}")


class MergeBehaviourTests(TransferFixture):
    """直接在一个库里造出"另一台电脑"的备份包，再合进来。"""

    def make_bundle(self, answers):
        """把当前库复制一份、改掉几道题的答案，导出成"另一台电脑"的备份包。"""
        for question in Question.objects.all():
            question.answer = answers.get(question.number, question.answer)
            question.save(update_fields=["answer"])
        bundle = self.export()
        for question in Question.objects.all():
            question.answer = {1: "甲", 2: "", 3: ""}[question.number]
            question.save(update_fields=["answer"])
        return bundle

    def test_the_same_paper_is_recognised_and_the_gaps_are_filled(self):
        bundle = self.make_bundle({1: "乙", 2: "丙", 3: "丁"})
        result = library_transfer.merge_bundle(bundle)
        report = result["report"]
        self.assertEqual(Paper.objects.count(), 1, "同一份原卷不该变成两套")
        self.assertEqual(report["新增卷子"], 0)
        answers = {q.number: q.answer for q in Question.objects.all()}
        self.assertEqual(answers[1], "甲", "两边都有的保留本机的")
        self.assertEqual(answers[2], "丙", "本机空着的被补上")
        self.assertEqual(answers[3], "丁")

    def test_merging_twice_changes_nothing(self):
        bundle = self.make_bundle({1: "乙", 2: "丙", 3: "丁"})
        library_transfer.merge_bundle(bundle)
        before = [(q.number, q.answer) for q in Question.objects.all()]
        library_transfer.merge_bundle(bundle)
        after = [(q.number, q.answer) for q in Question.objects.all()]
        self.assertEqual(before, after, "同一个包合两次，第二次什么都不会变")

    def test_a_conflicting_question_is_kept_with_a_free_number(self):
        """题号一样、题干不同 = 两道不同的题：两道都留着，不替老师删掉一个。"""
        bundle = self.make_bundle({1: "乙", 2: "丙", 3: "丁"})
        rebuilt = self._rebuild_bundle(bundle, "UPDATE core_question SET stem='（虚构）另一道题' WHERE number=1")
        plan = library_transfer.plan_merge(rebuilt)
        self.assertEqual(plan["report"]["题号一样内容不同"], 1, "预览就该看出有一道题对不上")
        merged = library_transfer.merge_bundle(rebuilt)
        self.assertEqual(merged["report"]["题号一样内容不同"], 1)
        self.assertIn("说明", merged["report"])
        stems = sorted(Question.objects.values_list("stem", flat=True))
        self.assertIn("（虚构）第一题", stems, "本机那道题不能没")
        self.assertIn("（虚构）另一道题", stems, "备份里那道也要留着")
        numbers = sorted(Question.objects.values_list("number", flat=True))
        self.assertEqual(len(numbers), len(set(numbers)), "两道题的题号不能撞车")
        self.assertEqual(Question.objects.count(), 4)

    def _rebuild_bundle(self, bundle, statement=None):
        """把包里的题库改一改，再重新打包 —— 模拟"另一台电脑上是另一道题"。"""
        with zipfile.ZipFile(bundle) as archive:
            payload = archive.read(library_transfer.DATABASE_NAME)
        probe = self.work / "probe.sqlite3"
        probe.write_bytes(payload)
        with sqlite3.connect(probe) as reader:
            for sql in statement if isinstance(statement, (list, tuple)) else [statement]:
                if sql:
                    reader.execute(sql)
            reader.commit()
        rebuilt = self.work / "rebuilt.zip"
        with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(rebuilt, "w", zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                if item.filename == library_transfer.DATABASE_NAME:
                    target.writestr(item.filename, probe.read_bytes())
                else:
                    target.writestr(item, source.read(item.filename))
        return rebuilt

    def test_a_question_whose_number_collides_is_still_brought_over(self):
        """两台电脑的题目编号各自从 1 开始编 —— 编号撞上了也不能把题丢掉。

        照抄对方的编号会撞上本机已有的那一行；以前的做法是"编号已存在就跳过"，
        那道题就**悄无声息地没了**。现在撞上就另编一个。
        """
        bundle = self.make_bundle({1: "乙", 2: "丙", 3: "丁"})
        rebuilt = self._rebuild_bundle(
            bundle,
            [
                "DELETE FROM core_question WHERE id=1",
                "UPDATE core_question SET id=1, number=99, stem='（虚构）撞号的新题' WHERE number=3",
            ],
        )
        merged = library_transfer.merge_bundle(rebuilt)
        self.assertTrue(Question.objects.filter(stem="（虚构）撞号的新题").exists(), "编号撞车的题也要搬过来")
        self.assertEqual(Question.objects.filter(stem="（虚构）第一题").count(), 1, "本机那道不能被覆盖")
        self.assertEqual(Question.objects.filter(stem="（虚构）第一题").first().answer, "甲")

    def test_a_paper_the_machine_does_not_have_comes_over_whole(self):
        other = Paper.objects.create(
            filename="卷二.pdf",
            kind="pdf",
            sha256="b" * 64,
            status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 1684, "height": 1190}],
        )
        Question.objects.create(
            paper=other,
            number=1,
            question_type="single_choice",
            stem="（虚构）新卷的题",
            options={"A": "甲"},
            answer="戊",
            analysis="",
            state=Question.State.YELLOW,
            text_source="agree",
        )
        bundle = self.export()
        # 本机已经没有这套卷了（模拟"这是另一台电脑的备份"）。
        Paper.objects.filter(sha256="b" * 64).delete()
        Question.objects.filter(stem="（虚构）新卷的题").delete()
        result = library_transfer.merge_bundle(bundle)
        self.assertEqual(result["report"]["新增卷子"], 1)
        self.assertEqual(Paper.objects.count(), 2)
        self.assertTrue(Question.objects.filter(stem="（虚构）新卷的题").exists())
        self.assertTrue(Question.objects.filter(stem="（虚构）第一题").exists(), "本机的题也还在")

    def test_the_preview_says_what_would_happen_without_touching_anything(self):
        bundle = self.make_bundle({1: "乙", 2: "丙", 3: "丁"})
        before = [(q.number, q.answer) for q in Question.objects.all()]
        plan = library_transfer.plan_merge(bundle)
        self.assertIn("report", plan)
        after = [(q.number, q.answer) for q in Question.objects.all()]
        self.assertEqual(before, after, "看一眼不能动本机")

    def test_the_preview_counts_what_the_merge_would_fill_in(self):
        """预览说的和真合并做的一样 —— 老师先看的那张卡不能骗人。"""
        bundle = self.make_bundle({1: "乙", 2: "丙", 3: "丁"})
        plan = library_transfer.plan_merge(bundle)
        merged = library_transfer.merge_bundle(bundle)
        self.assertEqual(plan["report"]["补上答案"], merged["report"]["补上答案"])
        self.assertEqual(plan["report"]["新增卷子"], merged["report"]["新增卷子"])
        self.assertEqual(plan["report"]["新增题目"], merged["report"]["新增题目"])

    def test_undo_puts_back_the_answers_the_merge_filled_in(self):
        bundle = self.make_bundle({1: "乙", 2: "丙", 3: "丁"})
        library_transfer.merge_bundle(bundle)
        self.assertEqual(Question.objects.get(number=2).answer, "丙")
        self.assertEqual(Question.objects.get(number=3).answer, "丁")
        removed = library_transfer.undo_last_merge()
        self.assertEqual(removed["filled"], 2, "补进去的两道都要还原")
        self.assertEqual(Question.objects.get(number=2).answer, "", "补进去的答案要还原回去")
        self.assertEqual(Question.objects.get(number=3).answer, "")

    def test_a_broken_bundle_is_refused_with_a_readable_reason(self):
        broken = self.work / "broken.zip"
        broken.write_bytes(b"not a zip file at all")
        with self.assertRaises(library_transfer.TransferError) as caught:
            library_transfer.plan_merge(broken)
        self.assertIn("备份包", str(caught.exception))

    def test_undo_removes_exactly_what_the_merge_added(self):
        other = Paper.objects.create(
            filename="卷二.pdf",
            kind="pdf",
            sha256="b" * 64,
            status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 1684, "height": 1190}],
        )
        Question.objects.create(
            paper=other,
            number=1,
            question_type="single_choice",
            stem="（虚构）新卷的题",
            options={"A": "甲"},
            answer="戊",
            analysis="",
            state=Question.State.YELLOW,
            text_source="agree",
        )
        bundle = self.export()
        # 本机已经没有这套卷了（模拟"这是另一台电脑的备份"）。
        Paper.objects.filter(sha256="b" * 64).delete()
        Question.objects.filter(stem="（虚构）新卷的题").delete()
        self.assertEqual(Paper.objects.count(), 1)
        library_transfer.merge_bundle(bundle)
        self.assertEqual(Paper.objects.count(), 2)
        removed = library_transfer.undo_last_merge()
        self.assertEqual(removed["papers"], 1)
        self.assertEqual(Paper.objects.count(), 1, "撤销只删这次加进来的")
        self.assertEqual(Question.objects.filter(stem="（虚构）第一题").count(), 1, "本机原来就有的题不能被撤销掉")


class ReferencedRowsTests(TransferFixture):
    """题目外面挂着的那些行：题组、删除批次、题上贴的标签。

    1.26.3 真机上撞过：这些行不跟着走，搬过来的题就指向了不存在的行 ——
    SQLite 要到提交时才查外键，那时整次合并被退回（"FOREIGN KEY constraint
    failed"），**预览却看不出任何问题**。这里用真数据把那条路堵住。
    """

    def build_references(self):
        group = QuestionGroup.objects.create(
            paper=self.paper, title="第 1 组", kind=QuestionGroup.Kind.EXAM, sequence=0
        )
        self.questions[0].group = group
        self.questions[0].save(update_fields=["group"])
        batch = QuestionDeletionBatch.objects.create(
            paper=self.paper,
            question_ids=[str(self.questions[1].pk)],
            origin=QuestionDeletionBatch.Origin.USER,
            reason="（虚构）删掉的一道",
        )
        self.questions[1].deleted_at = "2026-01-01T00:00:00Z"
        self.questions[1].deletion_batch = batch
        self.questions[1].save(update_fields=["deleted_at", "deletion_batch"])
        tag_group = LibraryTagGroup.objects.create(name="（虚构）目录", position=0)
        tag = LibraryTag.objects.create(name="（虚构）知识点", kind=LibraryTag.Kind.KNOWLEDGE)
        tag.groups.add(tag_group)
        publication = PublishedQuestion.objects.create(
            question=self.questions[0],
            paper=self.paper,
            number=1,
            version=1,
            content={"stem": "（虚构）第一题", "answer": "甲"},
            content_hash="c" * 64,
            search_text="第一题",
            source_filename="卷一.pdf",
            question_type="single_choice",
            status=PublishedQuestion.Status.PUBLISHED,
        )
        LibraryTagAssignment.objects.create(publication=publication, tag=tag, source=LibraryTagAssignment.Source.HUMAN)
        return group, batch, tag_group, tag, publication

    def forget_local_rows(self):
        """模拟另一台电脑：那些行它没有。"""
        QuestionGroup.objects.all().delete()
        QuestionDeletionBatch.objects.all().delete()
        LibraryTagAssignment.objects.all().delete()
        LibraryTagGroup.objects.all().delete()

    def test_the_merge_brings_the_rows_the_questions_point_at(self):
        self.build_references()
        bundle = self.export()
        self.forget_local_rows()

        library_transfer.merge_bundle(bundle)

        self.assertEqual(QuestionGroup.objects.count(), 1, "题组得跟着题走")
        self.assertEqual(QuestionDeletionBatch.objects.count(), 1, "删除批次得跟着题走")
        self.assertEqual(LibraryTagGroup.objects.count(), 1, "标签目录得跟着标签走")
        self.assertEqual(LibraryTagAssignment.objects.count(), 1, "题上贴的标签得跟着快照走")

    def test_the_merge_leaves_no_dangling_reference(self):
        """合并完之后，库里不能有指向空行的引用 —— 外键一断，提交就会退回。"""
        from django.db import connection

        self.build_references()
        bundle = self.export()
        self.forget_local_rows()

        library_transfer.merge_bundle(bundle)

        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_key_check")
            broken = cursor.fetchall()
        self.assertEqual(broken, [], f"合并后有指向空行的引用：{broken[:5]}")

    def test_the_undo_takes_the_brought_along_rows_with_it(self):
        self.build_references()
        bundle = self.export()
        self.forget_local_rows()

        library_transfer.merge_bundle(bundle)
        library_transfer.undo_last_merge()

        self.assertEqual(QuestionGroup.objects.count(), 0, "撤销要把题组一起带走")
        self.assertEqual(QuestionDeletionBatch.objects.count(), 0, "撤销要把删除批次一起带走")
        self.assertEqual(LibraryTagAssignment.objects.count(), 0, "撤销要把标签挂接一起带走")
        self.assertEqual(LibraryTagGroup.objects.count(), 0)

    def test_a_duplicate_paper_on_this_machine_is_picked_deterministically(self):
        """本机留着好几份一样的卷（老师重录过一次）时，要挑得确定。

        而且优先挑**没归档**的那份：归档的卷老师在题库里看不见，题并进去
        等于藏起来了。
        """
        from core.models import Paper as PaperModel

        duplicate = PaperModel.objects.create(
            filename="卷一（重录）.pdf",
            kind="pdf",
            sha256="a" * 64,
            status=PaperModel.Status.READY,
            pages=[{"page_idx": 0, "width": 1684, "height": 1190}],
        )
        Question.objects.create(
            paper=duplicate,
            number=9,
            question_type="single_choice",
            stem="（虚构）重录的那道题",
            options={"A": "甲"},
            answer="",
            analysis="",
            state=Question.State.YELLOW,
            text_source="agree",
        )
        bundle = self.export()
        # 本机删掉原来那份卷，只剩重录的这份 → 合并要并进它，而不是新建第三份。
        Question.objects.filter(paper=self.paper).delete()
        Paper.objects.filter(pk=self.paper.pk).delete()
        result = library_transfer.merge_bundle(bundle)
        self.assertEqual(result["report"]["新增卷子"], 0, "本机有的卷不该再来一套")
        self.assertEqual(Paper.objects.count(), 1)
        self.assertTrue(Question.objects.filter(stem="（虚构）重录的那道题").exists())

    def test_an_archived_duplicate_is_not_used_as_the_merge_target(self):
        """本机那份被归档了 → 另建一份看得见的，别把题塞进看不见的地方。"""
        from core.models import Paper as PaperModel

        archived = PaperModel.objects.create(
            filename="卷一（归档）.pdf",
            kind="pdf",
            sha256="a" * 64,
            status=PaperModel.Status.READY,
            archived=True,
            pages=[{"page_idx": 0, "width": 1684, "height": 1190}],
        )
        Question.objects.filter(paper=self.paper).delete()
        Paper.objects.filter(pk=self.paper.pk).delete()
        bundle = self.export()
        result = library_transfer.merge_bundle(bundle)
        self.assertEqual(result["report"]["新增卷子"], 1, "归档的那份不算数，要另建一份")
        self.assertEqual(Paper.objects.count(), 2)
        self.assertFalse(Paper.objects.get(pk=archived.pk).questions.exists(), "题不该并进归档的那份")

    def test_a_book_where_every_chapter_restarts_the_numbering(self):
        """一本书每章都从第 1 题开始编 —— 认题必须带上"题组"。

        1.26.5 真机上撞过：认题只按 (卷, 题号) 找，第一章的第 1 题就被拿去和
        第二章的第 1 题配对，712 道题被当成"题号一样内容不同"又搬了一遍。
        """
        book = Paper.objects.create(
            filename="（虚构）教材.pdf",
            kind="pdf",
            sha256="c" * 64,
            status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 1684, "height": 1190}],
        )
        for chapter in range(2):
            group = QuestionGroup.objects.create(
                paper=book, title=f"第 {chapter + 1} 章", kind=QuestionGroup.Kind.CHAPTER, sequence=chapter
            )
            for number in range(1, 4):
                Question.objects.create(
                    paper=book,
                    group=group,
                    number=number,
                    question_type="single_choice",
                    stem=f"（虚构）第 {chapter + 1} 章第 {number} 题",
                    options={"A": "甲"},
                    answer="",
                    analysis="",
                    state=Question.State.YELLOW,
                    text_source="agree",
                )
        bundle = self.export()
        Question.objects.filter(paper=book).delete()
        Paper.objects.filter(pk=book.pk).delete()

        first = library_transfer.merge_bundle(bundle)
        self.assertEqual(first["report"]["新增题目"], 6)
        self.assertEqual(first["report"]["题号一样内容不同"], 0, "各章的题号一样，但不是同一道题，不该判成冲突")
        # 再合一次同一个包：不能又搬一遍。
        second = library_transfer.merge_bundle(bundle)
        self.assertEqual(second["report"]["新增题目"], 0, "同一个包合两次，不该多出题")
        self.assertEqual(Question.objects.filter(paper__filename="（虚构）教材.pdf").count(), 6)


class SolutionVersionTests(TransferFixture):
    """两边都有解析时：两份都留着，不覆盖。"""

    def test_two_solutions_stay_two_versions(self):
        publication = PublishedQuestion.objects.create(
            question=self.questions[0],
            paper=self.paper,
            number=1,
            version=1,
            content={"stem": "（虚构）第一题", "answer": "甲"},
            content_hash="a" * 64,
            search_text="第一题",
            source_filename="卷一.pdf",
            question_type="single_choice",
            status=PublishedQuestion.Status.PUBLISHED,
        )
        LibrarySolution.objects.create(
            publication=publication, fingerprint="f" * 64, answer="甲", analysis="本机那份解析"
        )
        bundle = self.export()
        # 备份包里带上了这一版解析；本机也有一份 —— 合进来应该是两份。
        result = library_transfer.merge_bundle(bundle)
        self.assertGreaterEqual(result["report"]["两版解析都留着"], 1)
        self.assertEqual(
            LibrarySolution.objects.filter(publication=publication).count(), 1, "同一份解析不会被复制成两版"
        )


class SelectiveExportTests(TransferFixture):
    """按选定卷子/书籍或目录文件夹独立打包分享，再合并到另一台电脑。"""

    def _publish(self, question, *, version=1, answer=""):
        return PublishedQuestion.objects.create(
            question=question,
            paper=question.paper,
            number=question.number,
            version=version,
            content={"stem": question.stem, "answer": answer or question.answer},
            content_hash=uuid.uuid4().hex * 2,
            search_text=question.stem,
            source_filename=question.paper.filename,
            question_type=question.question_type,
            status=PublishedQuestion.Status.PUBLISHED,
        )

    def _inspect_snapshot(self, bundle_path: Path) -> sqlite3.Connection:
        out = self.work / f"snap-{uuid.uuid4().hex}.sqlite3"
        with zipfile.ZipFile(bundle_path) as archive:
            out.write_bytes(archive.read(library_transfer.DATABASE_NAME))
        return sqlite3.connect(out)

    def test_selective_export_only_packs_chosen_paper_and_its_files(self):
        book = self.make_paper("（虚构）思想导引.pdf", sha="b" * 64)
        book_q = self.make_question(book, 1, "（虚构）导引第一题", "答案乙")
        pub_main = self._publish(self.questions[0], answer="甲")
        pub_book = self._publish(book_q, answer="答案乙")

        (self.data / str(self.paper.pk) / "figures").mkdir(parents=True, exist_ok=True)
        (self.data / str(self.paper.pk) / "figures" / "m1.png").write_bytes(b"main-fig")
        (self.data / str(book.pk) / "figures").mkdir(parents=True, exist_ok=True)
        (self.data / str(book.pk) / "figures" / "b1.png").write_bytes(b"book-fig")
        (self.data / "library" / str(pub_main.pk)).mkdir(parents=True, exist_ok=True)
        (self.data / "library" / str(pub_main.pk) / "crop.png").write_bytes(b"main-crop")
        (self.data / "library" / str(pub_book.pk)).mkdir(parents=True, exist_ok=True)
        (self.data / "library" / str(pub_book.pk) / "crop.png").write_bytes(b"book-crop")
        (self.data / "library-solutions" / str(pub_book.pk)).mkdir(parents=True, exist_ok=True)
        (self.data / "library-solutions" / str(pub_book.pk) / "sol.png").write_bytes(b"book-sol")

        out_dir = self.work / "share_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        result = library_transfer.export_bundle(out_dir, paper_ids=[str(book.pk)])
        bundle = Path(result["file"])
        self.assertTrue(bundle.name.startswith("题有据题包-（虚构）思想导引-"))
        self.assertEqual(result["paper_names"], ["（虚构）思想导引.pdf"])

        with zipfile.ZipFile(bundle) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read(library_transfer.MANIFEST_NAME).decode("utf-8"))

        self.assertEqual(manifest["machine"], "", "分享包不该泄露电脑主机名")
        self.assertEqual(manifest["scope"], "selected")
        self.assertEqual(manifest["counts"]["papers"], 1)
        self.assertEqual(manifest["counts"]["questions"], 1)
        self.assertEqual(manifest["counts"]["publications"], 1)
        self.assertEqual(len(manifest["papers"]), 1)
        self.assertEqual(manifest["papers"][0]["title"], "（虚构）思想导引.pdf")

        self.assertIn(f"{library_transfer.ORIGINALS}/{book.pk}/source.pdf", names)
        self.assertIn(f"{library_transfer.ORIGINALS}/{book.pk}/figures/b1.png", names)
        self.assertIn(f"{library_transfer.ORIGINALS}/library/{pub_book.pk}/crop.png", names)
        self.assertIn(f"{library_transfer.ORIGINALS}/library-solutions/{pub_book.pk}/sol.png", names)
        self.assertNotIn(f"{library_transfer.ORIGINALS}/{self.paper.pk}/source.pdf", names)
        self.assertNotIn(f"{library_transfer.ORIGINALS}/{self.paper.pk}/figures/m1.png", names)
        self.assertNotIn(f"{library_transfer.ORIGINALS}/library/{pub_main.pk}/crop.png", names)

        snap = self._inspect_snapshot(bundle)
        try:
            self.assertEqual(snap.execute("SELECT COUNT(*) FROM core_paper").fetchone()[0], 1)
            self.assertEqual(snap.execute("SELECT COUNT(*) FROM core_question").fetchone()[0], 1)
            self.assertEqual(snap.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            snap.close()

    def test_selective_export_omits_recycle_bin_questions_and_unused_tags(self):
        self.questions[2].deleted_at = "2026-01-01T00:00:00Z"
        self.questions[2].save(update_fields=["deleted_at"])
        pub = self._publish(self.questions[0], answer="甲")
        parent_tag = LibraryTag.objects.create(name="（虚构）函数与导数", kind=LibraryTag.Kind.KNOWLEDGE)
        child_tag = LibraryTag.objects.create(name="（虚构）单调性", kind=LibraryTag.Kind.KNOWLEDGE)
        unused_tag = LibraryTag.objects.create(name="（虚构）无关标签", kind=LibraryTag.Kind.KNOWLEDGE)
        tag_group = LibraryTagGroup.objects.create(name="（虚构）分组", position=0)
        child_tag.groups.add(tag_group)
        LibraryTagRelation.objects.create(
            parent=parent_tag, child=child_tag, relation_type=LibraryTagRelation.Type.HIERARCHY
        )
        LibraryTagAssignment.objects.create(publication=pub, tag=child_tag, source=LibraryTagAssignment.Source.HUMAN)

        out_dir = self.work / "share_tags"
        out_dir.mkdir(parents=True, exist_ok=True)
        result = library_transfer.export_bundle(out_dir, paper_ids=[str(self.paper.pk)])
        snap = self._inspect_snapshot(Path(result["file"]))
        try:
            self.assertEqual(
                snap.execute("SELECT COUNT(*) FROM core_question").fetchone()[0], 2, "回收站废题不能进分享包"
            )
            tag_names = {row[0] for row in snap.execute("SELECT name FROM core_librarytag").fetchall()}
            self.assertEqual(tag_names, {"（虚构）函数与导数", "（虚构）单调性"})
            self.assertNotIn("（虚构）无关标签", tag_names)
            self.assertEqual(snap.execute("SELECT COUNT(*) FROM core_librarytagrelation").fetchone()[0], 1)
            self.assertEqual(snap.execute("SELECT COUNT(*) FROM core_librarytaggroup").fetchone()[0], 1)
            self.assertEqual(snap.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            snap.close()

    def test_selective_bundle_merges_cleanly_and_passes_foreign_key_check(self):
        from django.db import connection

        book = self.make_paper("（虚构）导引.pdf", sha="b" * 64)
        book_q = self.make_question(book, 1, "（虚构）导引第一题", "答案乙")
        pub = self._publish(book_q, answer="答案乙")
        LibrarySolution.objects.create(publication=pub, fingerprint="e" * 64, answer="答案乙", analysis="（虚构）详解")
        parent_tag = LibraryTag.objects.create(name="（虚构）专题一", kind=LibraryTag.Kind.KNOWLEDGE)
        child_tag = LibraryTag.objects.create(name="（虚构）小节一", kind=LibraryTag.Kind.KNOWLEDGE)
        LibraryTagRelation.objects.create(
            parent=parent_tag, child=child_tag, relation_type=LibraryTagRelation.Type.HIERARCHY
        )
        LibraryTagAssignment.objects.create(publication=pub, tag=child_tag, source=LibraryTagAssignment.Source.HUMAN)
        (self.data / "library" / str(pub.pk)).mkdir(parents=True, exist_ok=True)
        (self.data / "library" / str(pub.pk) / "figure-1.png").write_bytes(b"\x89PNG crop")

        out_dir = self.work / "share_merge"
        out_dir.mkdir(parents=True, exist_ok=True)
        bundle = Path(library_transfer.export_bundle(out_dir, paper_ids=[str(book.pk)])["file"])

        # 模拟网友本机：已有 self.paper，没有 book 及其标签。
        LibraryTagAssignment.objects.all().delete()
        LibraryTagRelation.objects.all().delete()
        LibraryTag.objects.all().delete()
        LibrarySolution.objects.all().delete()
        PublishedQuestion.objects.filter(paper=book).delete()
        Question.all_objects.filter(paper=book).delete()
        Paper.objects.filter(pk=book.pk).delete()
        shutil.rmtree(self.data / str(book.pk), ignore_errors=True)
        shutil.rmtree(self.data / "library" / str(pub.pk), ignore_errors=True)

        merged = library_transfer.merge_bundle(bundle)
        self.assertEqual(merged["report"]["新增卷子"], 1)
        self.assertEqual(merged["report"]["新增题目"], 1)
        self.assertEqual(Paper.objects.count(), 2)
        self.assertTrue(LibraryTag.objects.filter(name="（虚构）专题一").exists())
        self.assertTrue(LibraryTag.objects.filter(name="（虚构）小节一").exists())
        self.assertEqual(LibraryTagRelation.objects.count(), 1)
        restored_pub = PublishedQuestion.objects.get(paper__sha256="b" * 64)
        self.assertTrue((self.data / "library" / str(restored_pub.pk) / "figure-1.png").is_file())

        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_key_check")
            self.assertEqual(cursor.fetchall(), [])

        records = library_transfer.list_records()
        self.assertEqual(records[0]["paper_names"], ["（虚构）导引.pdf"])

        removed = library_transfer.undo_last_merge()
        self.assertEqual(removed["papers"], 1)
        self.assertEqual(Paper.objects.count(), 1)
        self.assertTrue(Paper.objects.filter(pk=self.paper.pk).exists())

    def test_folder_export_only_packs_tagged_questions_and_preserves_hierarchy(self):
        from django.db import connection

        book_a = self.make_paper("（虚构）导引上册.pdf", sha="b" * 64)
        q_a1 = self.make_question(book_a, 1, "（虚构）函数极值题", "甲")
        q_a2 = self.make_question(book_a, 2, "（虚构）立体几何题", "乙")
        book_b = self.make_paper("（虚构）导引下册.pdf", sha="c" * 64)
        q_b1 = self.make_question(book_b, 1, "（虚构）导数应用题", "丙")
        q_b2 = self.make_question(book_b, 2, "（虚构）未贴标签题", "丁")

        group_a = QuestionGroup.objects.create(
            paper=book_a, title="第一章", kind=QuestionGroup.Kind.CHAPTER, sequence=0
        )
        q_a1.group = group_a
        q_a1.save(update_fields=["group"])

        pub_a1 = self._publish(q_a1, answer="甲")
        pub_a2 = self._publish(q_a2, answer="乙")
        pub_b1 = self._publish(q_b1, answer="丙")
        self._publish(q_b2, answer="丁")

        root_folder = LibraryTag.objects.create(name="（虚构）函数与导数", kind=LibraryTag.Kind.KNOWLEDGE)
        sub_folder = LibraryTag.objects.create(name="（虚构）导数应用", kind=LibraryTag.Kind.KNOWLEDGE)
        other_folder = LibraryTag.objects.create(name="（虚构）立体几何", kind=LibraryTag.Kind.KNOWLEDGE)
        LibraryTagRelation.objects.create(
            parent=root_folder, child=sub_folder, relation_type=LibraryTagRelation.Type.HIERARCHY
        )
        LibraryTagAssignment.objects.create(publication=pub_a1, tag=root_folder)
        LibraryTagAssignment.objects.create(publication=pub_b1, tag=sub_folder)
        LibraryTagAssignment.objects.create(publication=pub_a2, tag=other_folder)

        folders = {item["name"]: item for item in library_transfer.list_exportable_folders()}
        self.assertEqual(folders["（虚构）函数与导数"]["questions"], 2)
        self.assertEqual(folders["（虚构）导数应用"]["questions"], 1)
        self.assertEqual(folders["（虚构）立体几何"]["questions"], 1)

        out_dir = self.work / "share_folder"
        out_dir.mkdir(parents=True, exist_ok=True)
        result = library_transfer.export_bundle(out_dir, tag_ids=[str(root_folder.pk)])
        bundle = Path(result["file"])
        self.assertTrue(bundle.name.startswith("题有据题包-（虚构）函数与导数-"))
        self.assertEqual(result["folder_names"], ["（虚构）函数与导数"])

        with zipfile.ZipFile(bundle) as archive:
            manifest = json.loads(archive.read(library_transfer.MANIFEST_NAME).decode("utf-8"))
        self.assertEqual(manifest["scope"], "folder")
        self.assertEqual([f["name"] for f in manifest["folders"]], ["（虚构）函数与导数"])
        self.assertEqual(manifest["counts"]["papers"], 2)
        self.assertEqual(manifest["counts"]["questions"], 2)
        self.assertTrue(all(item["questions"] == 1 for item in manifest["papers"]))

        snap = self._inspect_snapshot(bundle)
        try:
            stems = {row[0] for row in snap.execute("SELECT stem FROM core_question").fetchall()}
            self.assertEqual(stems, {"（虚构）函数极值题", "（虚构）导数应用题"})
            tag_names = {row[0] for row in snap.execute("SELECT name FROM core_librarytag").fetchall()}
            self.assertEqual(tag_names, {"（虚构）函数与导数", "（虚构）导数应用"})
            self.assertNotIn("（虚构）立体几何", tag_names)
            self.assertEqual(snap.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            snap.close()

        # 模拟网友空库加载这个文件夹题包
        LibraryTagAssignment.objects.all().delete()
        LibraryTagRelation.objects.all().delete()
        LibraryTag.objects.all().delete()
        PublishedQuestion.objects.all().delete()
        Question.all_objects.all().delete()
        QuestionGroup.objects.all().delete()
        Paper.objects.all().delete()

        merged = library_transfer.merge_bundle(bundle)
        self.assertEqual(merged["report"]["新增卷子"], 2)
        self.assertEqual(merged["report"]["新增题目"], 2)
        self.assertEqual(
            set(Question.objects.values_list("stem", flat=True)), {"（虚构）函数极值题", "（虚构）导数应用题"}
        )
        self.assertEqual(library_transfer.list_records()[0]["folder_names"], ["（虚构）函数与导数"])
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_key_check")
            self.assertEqual(cursor.fetchall(), [])

        library_transfer.undo_last_merge()
        self.assertEqual(Paper.objects.count(), 0)
        self.assertEqual(Question.objects.count(), 0)

    def test_backup_papers_and_selective_export_views(self):
        client = Client()
        pub = self._publish(self.questions[0], answer="甲")
        folder = LibraryTag.objects.create(name="（虚构）精选专题", kind=LibraryTag.Kind.CUSTOM)
        LibraryTagAssignment.objects.create(publication=pub, tag=folder)

        self.assertEqual(client.get("/api/backup/papers", REMOTE_ADDR="192.0.2.10").status_code, 403)
        listing = client.get("/api/backup/papers")
        self.assertEqual(listing.status_code, 200)
        body = listing.json()
        self.assertEqual(len(body["papers"]), 1)
        self.assertEqual(body["papers"][0]["id"], str(self.paper.pk))
        self.assertEqual(len(body["folders"]), 1)
        self.assertEqual(body["folders"][0]["id"], str(folder.pk))

        out_dir = self.work / "api_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        resp = client.post(
            "/api/backup/export",
            data=json.dumps({"directory": str(out_dir), "tag_ids": [str(folder.pk)]}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(resp.status_code, 200)
        exported = resp.json()
        self.assertEqual(exported["folder_names"], ["（虚构）精选专题"])
        self.assertEqual(exported["questions"], 1)

        with open(exported["file"], "rb") as stream:
            inspect_resp = client.post("/api/backup/inspect", {"file": stream}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(inspect_resp.status_code, 200)
        inspected = inspect_resp.json()
        self.assertEqual(inspected["folder_items"][0]["name"], "（虚构）精选专题")
        self.assertEqual(inspected["paper_items"][0]["title"], "卷一.pdf")
        self.assertEqual(inspected["paper_items"][0]["questions"], 1)

    def test_archived_book_with_live_publications_is_exportable_while_trashed_archived_paper_is_not(self):
        archived_book = self.make_paper("（虚构）已归档正式导引.pdf", sha="b" * 64)
        book_q = self.make_question(archived_book, 1, "（虚构）导引已归档题", "甲")
        self._publish(book_q, answer="甲")
        archived_book.archived = True
        archived_book.save(update_fields=["archived"])

        trashed_paper = self.make_paper("（虚构）回收站废卷.pdf", sha="c" * 64)
        trashed_q = self.make_question(trashed_paper, 1, "（虚构）已撤回题", "乙")
        PublishedQuestion.objects.create(
            question=trashed_q,
            paper=trashed_paper,
            number=trashed_q.number,
            version=1,
            content={"stem": trashed_q.stem, "answer": "乙"},
            content_hash=uuid.uuid4().hex * 2,
            search_text=trashed_q.stem,
            source_filename=trashed_paper.filename,
            question_type=trashed_q.question_type,
            status=PublishedQuestion.Status.WITHDRAWN,
        )
        trashed_paper.archived = True
        trashed_paper.save(update_fields=["archived"])

        listed_ids = {item["id"] for item in library_transfer.list_exportable_papers()}
        self.assertIn(str(archived_book.pk), listed_ids)
        self.assertNotIn(str(trashed_paper.pk), listed_ids)

        out_dir = self.work / "share_archived"
        out_dir.mkdir(parents=True, exist_ok=True)
        result = library_transfer.export_bundle(out_dir, paper_ids=[str(archived_book.pk)])
        self.assertEqual(result["paper_names"], ["（虚构）已归档正式导引.pdf"])
        self.assertEqual(result["counts"]["publications"], 1)

    def test_selective_export_prunes_superseded_publications(self):
        old_pub = PublishedQuestion.objects.create(
            question=self.questions[0],
            paper=self.paper,
            number=self.questions[0].number,
            version=1,
            content={"stem": self.questions[0].stem, "answer": "旧答案"},
            content_hash=uuid.uuid4().hex * 2,
            search_text=self.questions[0].stem,
            source_filename=self.paper.filename,
            question_type=self.questions[0].question_type,
            status=PublishedQuestion.Status.SUPERSEDED,
        )
        live_pub = self._publish(self.questions[0], version=2, answer="新答案")

        out_dir = self.work / "share_superseded"
        out_dir.mkdir(parents=True, exist_ok=True)
        bundle = Path(library_transfer.export_bundle(out_dir, paper_ids=[str(self.paper.pk)])["file"])
        snap = self._inspect_snapshot(bundle)
        try:
            pub_ids = {row[0] for row in snap.execute("SELECT id FROM core_publishedquestion").fetchall()}
            self.assertEqual(pub_ids, {live_pub.pk.hex})
            self.assertNotIn(old_pub.pk.hex, pub_ids)
            self.assertEqual(snap.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            snap.close()


class CommunityPackTests(TransferFixture):
    """发到题有据社区的题包：默认不带原卷、预览不含答案、照样能合并。"""

    _publish = SelectiveExportTests._publish
    _inspect_snapshot = SelectiveExportTests._inspect_snapshot

    def _community_publish(self):
        pub = self._publish(self.questions[0], answer="（虚构）答案甲")
        pub.content = {
            "stem": "（虚构）已知 $f(x)=x^2$，求 $f(2)$。",
            "options": {"A": "$2$", "B": "$4$"},
            "answer": "（虚构）答案甲",
            "analysis": "（虚构）详细解析",
        }
        pub.save(update_fields=["content"])
        figures = self.data / str(self.paper.pk) / "figures"
        figures.mkdir(parents=True, exist_ok=True)
        (figures / "f1.png").write_bytes(b"fig")
        (self.data / str(self.paper.pk) / "source.docx").write_bytes(b"docx")
        (self.data / "library" / str(pub.pk)).mkdir(parents=True, exist_ok=True)
        (self.data / "library" / str(pub.pk) / "crop.png").write_bytes(b"\x89PNG crop")
        tag = LibraryTag.objects.create(name="（虚构）函数求值", kind=LibraryTag.Kind.KNOWLEDGE)
        LibraryTagAssignment.objects.create(publication=pub, tag=tag, source=LibraryTagAssignment.Source.HUMAN)
        return pub

    def _export(self, **kwargs):
        out_dir = self.work / f"community-{uuid.uuid4().hex[:6]}"
        out_dir.mkdir(parents=True, exist_ok=True)
        return library_transfer.export_community_pack(out_dir, paper_ids=[str(self.paper.pk)], **kwargs)

    def test_default_pack_has_no_originals_or_local_paths(self):
        pub = self._community_publish()
        result = self._export()
        bundle = Path(result["file"])
        self.assertTrue(bundle.name.startswith("题有据社区题包-"))
        self.assertFalse(result["community"]["include_originals"])
        with zipfile.ZipFile(bundle) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read(library_transfer.MANIFEST_NAME).decode("utf-8"))
        prefix = library_transfer.ORIGINALS
        self.assertNotIn(f"{prefix}/{self.paper.pk}/source.pdf", names)
        self.assertNotIn(f"{prefix}/{self.paper.pk}/source.docx", names)
        self.assertIn(f"{prefix}/{self.paper.pk}/figures/f1.png", names)
        self.assertIn(f"{prefix}/library/{pub.pk}/crop.png", names)
        self.assertEqual(manifest["papers"][0]["files"], {})
        self.assertEqual(manifest["community"], {"format": 1, "include_originals": False})
        snap = self._inspect_snapshot(bundle)
        try:
            paths = snap.execute("SELECT source_path, render_path, zip_path FROM core_paper").fetchall()
        finally:
            snap.close()
        self.assertEqual(paths, [("", "", "")], "本机路径里有用户名，不能发到网上")

    def test_preview_shows_stem_and_options_but_never_answers(self):
        self._community_publish()
        result = self._export()
        with zipfile.ZipFile(result["file"]) as archive:
            names = set(archive.namelist())
            preview = json.loads(archive.read(library_transfer.COMMUNITY_PREVIEW_NAME).decode("utf-8"))
        self.assertEqual(preview["counts"]["questions"], 1)
        self.assertEqual(preview["question_types"], {"single_choice": 1})
        self.assertEqual([tag["name"] for tag in preview["tags"]], ["（虚构）函数求值"])
        sample = preview["samples"][0]
        self.assertIn("$f(x)=x^2$", sample["stem"])
        self.assertEqual(sample["options"], [{"key": "A", "text": "$2$"}, {"key": "B", "text": "$4$"}])
        text = json.dumps(preview, ensure_ascii=False)
        self.assertNotIn("答案甲", text)
        self.assertNotIn("详细解析", text)
        self.assertNotIn("_publication", text)
        self.assertEqual(preview["cover"], "community-cover.png")
        self.assertIn("community-cover.png", names)

    def test_pack_with_originals_keeps_source_and_merges_cleanly(self):
        from django.db import connection

        self._community_publish()
        result = self._export(include_originals=True)
        bundle = Path(result["file"])
        with zipfile.ZipFile(bundle) as archive:
            self.assertIn(f"{library_transfer.ORIGINALS}/{self.paper.pk}/source.pdf", archive.namelist())

        LibraryTagAssignment.objects.all().delete()
        LibraryTag.objects.all().delete()
        PublishedQuestion.objects.all().delete()
        Question.all_objects.all().delete()
        Block.objects.all().delete()
        Paper.objects.all().delete()
        shutil.rmtree(self.data / str(self.paper.pk), ignore_errors=True)

        merged = library_transfer.merge_bundle(bundle)
        self.assertEqual(merged["report"]["新增卷子"], 1)
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_key_check")
            self.assertEqual(cursor.fetchall(), [])

    def test_stripped_pack_merges_without_originals(self):
        self._community_publish()
        bundle = Path(self._export()["file"])
        LibraryTagAssignment.objects.all().delete()
        LibraryTag.objects.all().delete()
        PublishedQuestion.objects.all().delete()
        Question.all_objects.all().delete()
        Block.objects.all().delete()
        Paper.objects.all().delete()
        shutil.rmtree(self.data / str(self.paper.pk), ignore_errors=True)

        merged = library_transfer.merge_bundle(bundle)
        self.assertEqual(merged["report"]["新增卷子"], 1)
        self.assertEqual(PublishedQuestion.objects.count(), 1)
        paper = Paper.objects.get()
        self.assertTrue((self.data / str(paper.pk) / "figures" / "f1.png").is_file())

    def test_whole_library_is_refused(self):
        with self.assertRaises(library_transfer.TransferError):
            library_transfer.export_community_pack(self.work)

    def test_view_requires_a_selection_and_returns_counts(self):
        self._community_publish()
        client = Client()
        headers = {"HTTP_X_QB_REQUEST": "1"}
        refused = client.post(
            "/api/backup/community-export",
            data=json.dumps({"directory": str(self.work)}),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(refused.status_code, 400)
        ok = client.post(
            "/api/backup/community-export",
            data=json.dumps({"directory": str(self.work), "paper_ids": [str(self.paper.pk)]}),
            content_type="application/json",
            **headers,
        )
        self.assertEqual(ok.status_code, 200, ok.content.decode("utf-8"))
        body = ok.json()
        self.assertEqual(body["questions"], 1)
        self.assertFalse(body["include_originals"])
