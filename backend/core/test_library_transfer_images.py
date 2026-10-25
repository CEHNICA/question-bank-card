"""备份恢复之后，图片必须还在。

1.26.23 真机上撞过：恢复完之后原卷预览只剩一个白框、题库里的小图全没了。
根因是两条，加在一起刚好把图弄丢：

* **清单里的编号和数据库里的不是一回事** —— 清单按带横杠的写法记（那是硬盘上
  文件夹的名字），数据库里存的是连写的。清单说"没这个文件"，卷子的原卷路径
  被写成空字符串，原卷预览当场坏掉。
* **还原文件时是"按卷子的编号去包里找前缀"** —— 包里用带横杠、硬盘上用连写，
  前缀对不上，**一个文件都回不来**。

这里用真数据把这两条堵住：文件真的回到硬盘上、路径真的指得到。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path

from django.db import connection
from django.test import TestCase, override_settings

from core import library_transfer
from core.models import Paper, PublishedQuestion, Question


class ImageRestoreFixture(TestCase):
    """一套卷、几道题、一个入库快照，外加原卷文件和题库里的小图。"""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="qb-images-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)
        self.data = self.work / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        override = override_settings(DATA_ROOT=self.data)
        override.enable()
        self.addCleanup(override.disable)

        self.paper = Paper.objects.create(
            filename="卷一.pdf",
            kind="pdf",
            sha256="a" * 64,
            status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 1684, "height": 1190}],
        )
        self.folder = self.data / str(self.paper.pk)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "source.pdf").write_bytes(b"%PDF-1.4 fake")
        (self.folder / "figures").mkdir(exist_ok=True)
        (self.folder / "figures" / "q1_abc.png").write_bytes(b"\x89PNG question figure")
        self.paper.source_path = str(self.folder / "source.pdf")
        self.paper.render_path = self.paper.source_path
        self.paper.save()

        self.question = Question.objects.create(
            paper=self.paper,
            number=1,
            question_type="single_choice",
            stem="（虚构）第一题",
            options={"A": "甲"},
            answer="甲",
            analysis="",
            state=Question.State.YELLOW,
            text_source="agree",
            regions=[{"page_idx": 0, "bbox": [10, 10, 200, 200]}],
        )
        self.publication = PublishedQuestion.objects.create(
            question=self.question,
            paper=self.paper,
            number=1,
            version=1,
            content={"stem": "（虚构）第一题", "answer": "甲", "figures": [{"file": "figure-1.png"}]},
            content_hash="c" * 64,
            search_text="第一题",
            source_filename="卷一.pdf",
            question_type="single_choice",
            status=PublishedQuestion.Status.PUBLISHED,
        )
        self.library_folder = self.data / "library" / str(self.publication.pk)
        self.library_folder.mkdir(parents=True, exist_ok=True)
        (self.library_folder / "figure-1.png").write_bytes(b"\x89PNG library crop")
        (self.library_folder / "question-1.png").write_bytes(b"\x89PNG library question")

    def export(self) -> Path:
        target = self.work / "backup.zip"
        result = library_transfer.export_bundle(target.parent)
        return Path(result["file"])

    def make_other_paper(self) -> Paper:
        other = Paper.objects.create(
            filename="卷二.pdf",
            kind="pdf",
            sha256="b" * 64,
            status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 1684, "height": 1190}],
        )
        folder = self.data / str(other.pk)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "source.pdf").write_bytes(b"%PDF-1.4 other fake")
        other.source_path = str(folder / "source.pdf")
        other.render_path = other.source_path
        other.save()
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
        return other

    def forget_other_paper(self) -> None:
        """模拟另一台电脑：本机没有这套卷。"""
        Paper.objects.filter(sha256="b" * 64).delete()
        Question.objects.filter(stem="（虚构）新卷的题").delete()


class BundleContentTests(ImageRestoreFixture):
    """包里该有什么。"""

    def test_the_bundle_carries_the_library_crops(self):
        """题库里的小图以前没进包 —— 恢复之后就再也回不来了。"""
        bundle = self.export()
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
        self.assertIn(
            f"{library_transfer.ORIGINALS}/library/{self.publication.pk}/figure-1.png", names, "题库里的小图必须进包"
        )
        self.assertIn(f"{library_transfer.ORIGINALS}/{self.paper.pk}/source.pdf", names, "原卷文件必须进包")

    def test_the_bundle_carries_the_question_figures(self):
        bundle = self.export()
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
        self.assertIn(f"{library_transfer.ORIGINALS}/{self.paper.pk}/figures/q1_abc.png", names, "题目插图必须进包")

    def test_the_manifest_records_the_path_the_folder_actually_has(self):
        """清单里的编号是带横杠的（硬盘上文件夹的名字），文件路径也得对得上。"""
        bundle = self.export()
        with zipfile.ZipFile(bundle) as archive:
            manifest = json.loads(archive.read(library_transfer.MANIFEST_NAME).decode("utf-8"))
        files = manifest["papers"][0]["files"]
        self.assertEqual(files["source"], f"{self.paper.pk}/source.pdf")


class ManifestTests(ImageRestoreFixture):
    """清单：包里那份卷子的文件在哪儿。"""

    def test_the_manifest_records_the_path_the_folder_actually_has(self):
        """清单里的编号是带横杠的（硬盘上文件夹的名字），文件路径也得对得上。"""
        bundle = self.export()
        with zipfile.ZipFile(bundle) as archive:
            manifest = json.loads(archive.read(library_transfer.MANIFEST_NAME).decode("utf-8"))
        files = manifest["papers"][0]["files"]
        self.assertEqual(files["source"], f"{self.paper.pk}/source.pdf")

    def test_a_path_pointing_at_another_machine_still_records_the_file(self):
        """库里的绝对路径指到别处（数据文件夹挪过家、或者这份库是从另一台电脑
        恢复来的，里面存着人家的路径）—— 清单里也得记着这个文件，
        不然恢复时就对不上号，那套卷的原卷路径就再也指不回去了。"""
        Paper.objects.filter(pk=self.paper.pk).update(source_path=r"D:\另一台电脑\data\某个卷\source.pdf")
        bundle = self.export()
        with zipfile.ZipFile(bundle) as archive:
            manifest = json.loads(archive.read(library_transfer.MANIFEST_NAME).decode("utf-8"))
        self.assertEqual(
            manifest["papers"][0]["files"]["source"],
            f"{self.paper.pk}/source.pdf",
            "文件名取自库里、位置按本机卷子文件夹去找",
        )


class RestoreOriginalsTests(ImageRestoreFixture):
    """原卷文件要跟着合并一起回来。"""

    def test_the_original_file_comes_back_with_the_merge(self):
        """本机磁盘上的原卷没了（另一台电脑恢复过、或者文件被清理了）——
        合并之后要把它放回来。"""
        bundle = self.export()
        shutil.rmtree(self.folder, ignore_errors=True)

        library_transfer.merge_bundle(bundle)

        paper = Paper.objects.get(sha256="a" * 64)
        self.assertTrue(paper.source_path, "原卷路径不能是空的")
        self.assertTrue(Path(paper.source_path).is_file(), "原卷文件得真的回到硬盘上")
        self.assertEqual(Path(paper.source_path).parent.name, str(paper.pk), "文件要放回这套卷自己的文件夹")

    def test_the_question_figures_come_back_too(self):
        bundle = self.export()
        shutil.rmtree(self.folder, ignore_errors=True)

        library_transfer.merge_bundle(bundle)

        figure = self.folder / "figures" / "q1_abc.png"
        self.assertTrue(figure.is_file(), "题目插图也得回来")

    def test_an_empty_original_path_is_pointed_at_the_restored_file(self):
        """以前恢复一次会把原卷路径写成空的（编号对不上）。这次要把它指回去。"""
        bundle = self.export()
        paper = Paper.objects.get(pk=self.paper.pk)
        paper.source_path = ""
        paper.render_path = ""
        paper.save(update_fields=["source_path", "render_path"])
        shutil.rmtree(self.folder, ignore_errors=True)

        result = library_transfer.merge_bundle(bundle)

        paper.refresh_from_db()
        self.assertTrue(paper.source_path, "空的原卷路径要补上")
        self.assertTrue(Path(paper.source_path).is_file(), "补上的路径得真的指着一个文件")
        self.assertEqual(result["report"]["补回原卷路径"], 2, "原卷和渲染两处都要补")

    def test_a_paper_that_is_not_on_this_machine_arrives_with_its_file(self):
        other = self.make_other_paper()
        bundle = self.export()
        self.forget_other_paper()
        shutil.rmtree(self.data / str(other.pk), ignore_errors=True)

        library_transfer.merge_bundle(bundle)

        paper = Paper.objects.get(sha256="b" * 64)
        self.assertTrue(paper.source_path, "新来的卷子也得有原卷路径")
        self.assertTrue(Path(paper.source_path).is_file(), "新来的卷子也得有原卷文件")

    def test_a_paper_that_gets_a_new_number_still_gets_its_file(self):
        """本机已经有一个**同编号、但内容是另一份**的卷 —— 备份里这份进来时
        本机得给它另编一个号。原卷文件要跟着新编号走，不能留在旧编号那儿
        （留在那儿就等于没人看得到）。"""
        bundle = self.export()
        Paper.objects.filter(pk=self.paper.pk).delete()
        Question.objects.filter(paper_id=self.paper.pk).delete()
        shutil.rmtree(self.folder, ignore_errors=True)
        clash = Paper.objects.create(
            id=self.paper.pk,
            filename="另一份.pdf",
            kind="pdf",
            sha256="d" * 64,
            status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 1684, "height": 1190}],
        )
        self.assertEqual(clash.pk, self.paper.pk, "本机确实占着这个编号")

        library_transfer.merge_bundle(bundle)

        incoming = Paper.objects.get(sha256="a" * 64)
        self.assertNotEqual(incoming.pk, self.paper.pk, "本机该给它另编一个号")
        self.assertTrue(incoming.source_path, "新来的卷子得有原卷路径")
        self.assertTrue(Path(incoming.source_path).is_file(), "原卷文件要跟着新编号走")
        self.assertEqual(Path(incoming.source_path).parent.name, str(incoming.pk), "文件要落在新编号那个文件夹里")

    def test_the_preview_counts_the_files_the_merge_would_restore(self):
        """预览说的和真合做的一样 —— 老师先看的那张卡不能骗人。"""
        bundle = self.export()
        shutil.rmtree(self.folder, ignore_errors=True)

        plan = library_transfer.plan_merge(bundle)
        merged = library_transfer.merge_bundle(bundle)

        self.assertGreater(plan["report"]["补回的文件"], 0, "预览得说出要补回几个文件")
        self.assertEqual(plan["report"]["补回的文件"], merged["report"]["补回的文件"])
        self.assertEqual(plan["report"]["补回原卷路径"], merged["report"]["补回原卷路径"])


class RestoreLibraryCropsTests(ImageRestoreFixture):
    """题库里的小图（入库快照的裁片）要跟着入库快照回来。"""

    def test_the_crops_come_back_with_the_publication(self):
        bundle = self.export()
        PublishedQuestion.objects.filter(pk=self.publication.pk).delete()
        shutil.rmtree(self.library_folder, ignore_errors=True)

        library_transfer.merge_bundle(bundle)

        restored = PublishedQuestion.objects.get(question=self.question)
        self.assertTrue(
            (self.data / "library" / str(restored.pk) / "figure-1.png").is_file(), "题库的小图得跟着入库快照回来"
        )
        self.assertTrue(
            (self.data / "library" / str(restored.pk) / "question-1.png").is_file(), "题库里的整题截图也得回来"
        )

    def test_the_crops_land_under_the_number_this_machine_uses(self):
        """两台电脑各编各的号：备份里的入库快照到了本机是另一个编号，
        小图要放到本机那个编号下面 —— 放在备份里的编号那儿，等于没人看。"""
        bundle = self.export()
        local_id = uuid.uuid4()
        with connection.cursor() as cursor:
            cursor.execute(
                'UPDATE "core_publishedquestion" SET id=? WHERE id=?', (local_id.hex, self.publication.pk.hex)
            )
        local_folder = self.data / "library" / str(local_id)
        local_folder.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(self.library_folder, ignore_errors=True)

        library_transfer.merge_bundle(bundle)

        self.assertTrue((local_folder / "figure-1.png").is_file(), "小图要落到本机那个入库快照的编号下面")
        self.assertFalse(
            (self.data / "library" / str(self.publication.pk)).exists(), "备份里的编号那个文件夹不该留在本机"
        )

    def test_undo_takes_the_restored_files_away(self):
        """撤销要把这次放回硬盘上的原卷文件带走 —— 原来用的编号写法不对，
        文件会留在盘上变成孤儿。"""
        other = self.make_other_paper()
        bundle = self.export()
        self.forget_other_paper()
        shutil.rmtree(self.data / str(other.pk), ignore_errors=True)

        library_transfer.merge_bundle(bundle)
        incoming = Paper.objects.get(sha256="b" * 64)
        self.assertTrue(Path(incoming.source_path).is_file())

        library_transfer.undo_last_merge()

        self.assertFalse((self.data / str(incoming.pk)).exists(), "撤销要把这套卷的文件夹整个删掉，不能留在盘上")
