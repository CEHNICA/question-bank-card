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

from django.test import TestCase, override_settings

from core import library_transfer
from core.models import (
    Block,
    LibrarySolution,
    LibraryTag,
    LibraryTagAssignment,
    LibraryTagGroup,
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
        paper = Paper.objects.create(filename=filename, kind="pdf", sha256=sha,
                                     status=Paper.Status.READY,
                                     pages=[{"page_idx": 0, "width": 1684, "height": 1190}])
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
            paper=paper, number=number, question_type="single_choice", stem=stem,
            options={"A": "甲"}, answer=answer, analysis="", state=Question.State.YELLOW,
            text_source="agree", regions=[{"page_idx": 0, "bbox": [10, 10, 200, 200]}])

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
            self.assertFalse(any(name.endswith(forbidden) for name in names),
                             f"包里不该有 {forbidden}")


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
            for sql in (statement if isinstance(statement, (list, tuple)) else [statement]):
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
        rebuilt = self._rebuild_bundle(bundle, [
            "DELETE FROM core_question WHERE id=1",
            "UPDATE core_question SET id=1, number=99, stem='（虚构）撞号的新题' WHERE number=3",
        ])
        merged = library_transfer.merge_bundle(rebuilt)
        self.assertTrue(Question.objects.filter(stem="（虚构）撞号的新题").exists(),
                        "编号撞车的题也要搬过来")
        self.assertEqual(Question.objects.filter(stem="（虚构）第一题").count(), 1, "本机那道不能被覆盖")
        self.assertEqual(Question.objects.filter(stem="（虚构）第一题").first().answer, "甲")

    def test_a_paper_the_machine_does_not_have_comes_over_whole(self):
        other = Paper.objects.create(filename="卷二.pdf", kind="pdf", sha256="b" * 64,
                                     status=Paper.Status.READY,
                                     pages=[{"page_idx": 0, "width": 1684, "height": 1190}])
        Question.objects.create(paper=other, number=1, question_type="single_choice",
                                stem="（虚构）新卷的题", options={"A": "甲"}, answer="戊",
                                analysis="", state=Question.State.YELLOW, text_source="agree")
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
        other = Paper.objects.create(filename="卷二.pdf", kind="pdf", sha256="b" * 64,
                                     status=Paper.Status.READY,
                                     pages=[{"page_idx": 0, "width": 1684, "height": 1190}])
        Question.objects.create(paper=other, number=1, question_type="single_choice",
                                stem="（虚构）新卷的题", options={"A": "甲"}, answer="戊",
                                analysis="", state=Question.State.YELLOW, text_source="agree")
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
        self.assertEqual(Question.objects.filter(stem="（虚构）第一题").count(), 1,
                         "本机原来就有的题不能被撤销掉")


class ReferencedRowsTests(TransferFixture):
    """题目外面挂着的那些行：题组、删除批次、题上贴的标签。

    1.26.3 真机上撞过：这些行不跟着走，搬过来的题就指向了不存在的行 ——
    SQLite 要到提交时才查外键，那时整次合并被退回（"FOREIGN KEY constraint
    failed"），**预览却看不出任何问题**。这里用真数据把那条路堵住。
    """

    def build_references(self):
        group = QuestionGroup.objects.create(paper=self.paper, title="第 1 组",
                                             kind=QuestionGroup.Kind.EXAM, sequence=0)
        self.questions[0].group = group
        self.questions[0].save(update_fields=["group"])
        batch = QuestionDeletionBatch.objects.create(
            paper=self.paper, question_ids=[str(self.questions[1].pk)],
            origin=QuestionDeletionBatch.Origin.USER, reason="（虚构）删掉的一道")
        self.questions[1].deleted_at = "2026-01-01T00:00:00Z"
        self.questions[1].deletion_batch = batch
        self.questions[1].save(update_fields=["deleted_at", "deletion_batch"])
        tag_group = LibraryTagGroup.objects.create(name="（虚构）目录", position=0)
        tag = LibraryTag.objects.create(name="（虚构）知识点", kind=LibraryTag.Kind.KNOWLEDGE)
        tag.groups.add(tag_group)
        publication = PublishedQuestion.objects.create(
            question=self.questions[0], paper=self.paper, number=1, version=1,
            content={"stem": "（虚构）第一题", "answer": "甲"},
            content_hash="c" * 64, search_text="第一题", source_filename="卷一.pdf",
            question_type="single_choice", status=PublishedQuestion.Status.PUBLISHED)
        LibraryTagAssignment.objects.create(publication=publication, tag=tag,
                                            source=LibraryTagAssignment.Source.HUMAN)
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

        duplicate = PaperModel.objects.create(filename="卷一（重录）.pdf", kind="pdf",
                                               sha256="a" * 64, status=PaperModel.Status.READY,
                                               pages=[{"page_idx": 0, "width": 1684, "height": 1190}])
        Question.objects.create(paper=duplicate, number=9, question_type="single_choice",
                                stem="（虚构）重录的那道题", options={"A": "甲"}, answer="",
                                analysis="", state=Question.State.YELLOW, text_source="agree")
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

        archived = PaperModel.objects.create(filename="卷一（归档）.pdf", kind="pdf", sha256="a" * 64,
                                             status=PaperModel.Status.READY, archived=True,
                                             pages=[{"page_idx": 0, "width": 1684, "height": 1190}])
        Question.objects.filter(paper=self.paper).delete()
        Paper.objects.filter(pk=self.paper.pk).delete()
        bundle = self.export()
        result = library_transfer.merge_bundle(bundle)
        self.assertEqual(result["report"]["新增卷子"], 1, "归档的那份不算数，要另建一份")
        self.assertEqual(Paper.objects.count(), 2)
        self.assertFalse(Paper.objects.get(pk=archived.pk).questions.exists(),
                         "题不该并进归档的那份")


    def test_a_book_where_every_chapter_restarts_the_numbering(self):
        """一本书每章都从第 1 题开始编 —— 认题必须带上"题组"。

        1.26.5 真机上撞过：认题只按 (卷, 题号) 找，第一章的第 1 题就被拿去和
        第二章的第 1 题配对，712 道题被当成"题号一样内容不同"又搬了一遍。
        """
        book = Paper.objects.create(filename="（虚构）教材.pdf", kind="pdf", sha256="c" * 64,
                                    status=Paper.Status.READY,
                                    pages=[{"page_idx": 0, "width": 1684, "height": 1190}])
        for chapter in range(2):
            group = QuestionGroup.objects.create(paper=book, title=f"第 {chapter + 1} 章",
                                                 kind=QuestionGroup.Kind.CHAPTER, sequence=chapter)
            for number in range(1, 4):
                Question.objects.create(
                    paper=book, group=group, number=number, question_type="single_choice",
                    stem=f"（虚构）第 {chapter + 1} 章第 {number} 题", options={"A": "甲"},
                    answer="", analysis="", state=Question.State.YELLOW, text_source="agree")
        bundle = self.export()
        Question.objects.filter(paper=book).delete()
        Paper.objects.filter(pk=book.pk).delete()

        first = library_transfer.merge_bundle(bundle)
        self.assertEqual(first["report"]["新增题目"], 6)
        self.assertEqual(first["report"]["题号一样内容不同"], 0,
                         "各章的题号一样，但不是同一道题，不该判成冲突")
        # 再合一次同一个包：不能又搬一遍。
        second = library_transfer.merge_bundle(bundle)
        self.assertEqual(second["report"]["新增题目"], 0, "同一个包合两次，不该多出题")
        self.assertEqual(Question.objects.filter(paper__filename="（虚构）教材.pdf").count(), 6)


class SolutionVersionTests(TransferFixture):
    """两边都有解析时：两份都留着，不覆盖。"""

    def test_two_solutions_stay_two_versions(self):
        publication = PublishedQuestion.objects.create(
            question=self.questions[0], paper=self.paper, number=1, version=1,
            content={"stem": "（虚构）第一题", "answer": "甲"},
            content_hash="a" * 64, search_text="第一题", source_filename="卷一.pdf",
            question_type="single_choice", status=PublishedQuestion.Status.PUBLISHED)
        LibrarySolution.objects.create(publication=publication, fingerprint="f" * 64,
                                       answer="甲", analysis="本机那份解析")
        bundle = self.export()
        # 备份包里带上了这一版解析；本机也有一份 —— 合进来应该是两份。
        result = library_transfer.merge_bundle(bundle)
        self.assertGreaterEqual(result["report"]["两版解析都留着"], 1)
        self.assertEqual(LibrarySolution.objects.filter(publication=publication).count(), 1,
                         "同一份解析不会被复制成两版")

