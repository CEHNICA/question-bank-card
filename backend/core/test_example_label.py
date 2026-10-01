"""A textbook example's label (“例1”) names the card; it is never task text."""

import tempfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import library
from .management.commands.run_worker import clean_saved_example_labels
from .models import Paper, PublishedQuestion, Question, QuestionGroup
from .readers import TRANSCRIBE_RULES, parse_reading
from .textnorm import clean_stem, strip_example_label, witness_agrees, witness_key


class StripExampleLabelTests(SimpleTestCase):
    def test_labels_are_removed(self):
        cases = {
            "例1用列举法表示下列集合：": "用列举法表示下列集合：",
            "例 2．已知集合A": "已知集合A",
            "例题3 求值": "求值",
            "【例4】计算": "计算",
            "**例5** 证明：对任意": "证明：对任意",
            "例12 已知": "已知",
            "例一 已知": "已知",
            "例1，已知": "已知",
        }
        for printed, expected in cases.items():
            with self.subTest(printed=printed):
                self.assertEqual(strip_example_label(printed), expected)
                self.assertEqual(clean_stem(printed, 1), expected)

    def test_content_that_only_looks_like_a_label_stays(self):
        for text in ("例2中的函数f(x)，求", "例1与例2的结论", "例如，1是自然数", "例1", "已知例1"):
            with self.subTest(text=text):
                self.assertEqual(strip_example_label(text), text)

    def test_reader_output_loses_the_label(self):
        raw = "【题号】1\n【题型】解答题\n【题干】例1用列举法表示下列集合：\n(1)小于10的所有自然数组成的集合；"
        self.assertTrue(parse_reading(raw, 1)["stem"].startswith("用列举法表示下列集合"))

    def test_prompt_tells_the_reader(self):
        self.assertIn("“例1”“例题2”这类例题标号也不要写进题干", TRANSCRIBE_RULES)

    def test_mineru_text_with_the_label_still_supports_the_reading(self):
        witness = "例1用列举法表示下列集合：(1)小于10的所有自然数组成的集合；"
        reading = {"stem": "用列举法表示下列集合：\n(1)小于10的所有自然数组成的集合；", "options": {}}
        self.assertEqual(witness_key("例1用列举法表示"), witness_key("用列举法表示"))
        self.assertTrue(witness_agrees(reading, witness))


class SavedExampleLabelCleanupTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        override = override_settings(DATA_ROOT=Path(temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="必修一.pdf", kind="pdf", sha256="e" * 64,
            source_path=str(Path(temp.name) / "source.pdf"),
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
            status=Paper.Status.READY, material_type=Paper.MaterialType.BOOK,
        )
        self.group = QuestionGroup.objects.create(
            paper=self.paper, title="1.1 集合", sequence=0, page_start=1, page_end=1,
            metadata={"pages": [0]},
        )

    def card(self, stem, **extra):
        # 1.10: an undecided type blocks approval; these cards are about labels.
        extra.setdefault("question_type", "free_response")
        return Question.objects.create(
            paper=self.paper, group=self.group, number=extra.pop("number", 1), stem=stem,
            regions=[{"page_idx": 0, "bbox": [20, 100, 900, 260]}],
            read_a={"stem": stem, "raw": f"【题干】{stem}"}, read_b={"stem": stem},
            state=Question.State.GREEN, source_kind=Question.SourceKind.EXAMPLE, **extra,
        )

    def approve(self, question):
        question.approved = True
        question.approved_at = timezone.now()
        question.save(update_fields=["approved", "approved_at"])
        question = Question.objects.select_related("paper", "group").get(pk=question.pk)
        question.approved_content_hash = library.approval_hash(question)
        question.save(update_fields=["approved_content_hash"])
        return question

    def test_approved_and_published_card_loses_only_the_label(self):
        question = self.approve(self.card("例1用列举法表示下列集合：\n(1)小于10的所有自然数组成的集合；"))
        publication, created = library.publish(question)
        self.assertTrue(created)

        counts = library.strip_saved_example_labels()

        self.assertEqual(counts, {"questions": 1, "publications": 1})
        question = Question.objects.select_related("paper", "group").get(pk=question.pk)
        self.assertEqual(question.stem, "用列举法表示下列集合：\n(1)小于10的所有自然数组成的集合；")
        self.assertEqual(question.read_a["stem"], question.stem)
        self.assertEqual(question.read_a["raw"], "【题干】例1用列举法表示下列集合：\n(1)小于10的所有自然数组成的集合；")
        self.assertEqual(question.read_b["stem"], question.stem)
        self.assertTrue(library.approval_is_current(question))

        publication.refresh_from_db()
        self.assertEqual(publication.content["stem"], question.stem)
        self.assertEqual(publication.content_hash, library.content_hash(publication.content))
        self.assertEqual(publication.content["review"]["approved_content_hash"], publication.content_hash)
        self.assertNotIn("例1", publication.search_text)
        # Publishing again finds nothing new: the draft and the snapshot agree.
        again, created = library.publish(question)
        self.assertFalse(created)
        self.assertEqual(again.pk, publication.pk)
        self.assertEqual(PublishedQuestion.objects.count(), 1)

    def test_stale_approval_stays_stale_and_other_cards_are_untouched(self):
        stale = self.card("例2 已知集合A={1,2}", number=2, approved=True,
                          approved_at=timezone.now(), approved_content_hash="0" * 64)
        reference = self.card("例2中的集合A有几个元素？", number=3)
        plain = self.card("已知集合B={3}", number=4)

        counts = library.strip_saved_example_labels()

        self.assertEqual(counts, {"questions": 1, "publications": 0})
        stale = Question.objects.select_related("paper", "group").get(pk=stale.pk)
        self.assertEqual(stale.stem, "已知集合A={1,2}")
        self.assertEqual(stale.approved_content_hash, "0" * 64)
        self.assertFalse(library.approval_is_current(stale))
        self.assertEqual(Question.objects.get(pk=reference.pk).stem, "例2中的集合A有几个元素？")
        self.assertEqual(Question.objects.get(pk=plain.pk).stem, "已知集合B={3}")

    def test_worker_start_runs_it_once_and_again_changes_nothing(self):
        self.card("例1 用列举法表示")
        clean_saved_example_labels()
        self.assertEqual(Question.objects.get().stem, "用列举法表示")
        self.assertEqual(library.strip_saved_example_labels(), {"questions": 0, "publications": 0})
