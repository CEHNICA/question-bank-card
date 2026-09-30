"""Five-option choice questions and printed type notes (“（多项选择题）”)."""

import tempfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import library
from .figure_policy import missing_choice_figure_slots
from .models import Paper, PublishedQuestion, Question, QuestionGroup
from .readers import TRANSCRIBE_RULES, parse_reading, split_inline_options
from .textnorm import strip_type_label, witness_agrees, witness_key

RAW = """【内容类型】练习题
【题号】4
【题型】不确定
【题干】(多项选择题)函数$y=1+\\cos x$，$x\\in\\left(\\frac{\\pi}{3},2\\pi\\right)$的图象与直线$y=t$($t$为常数)的交点可能有（ ）.
【A】0个
【B】1个
【C】2个
【D】3个
【E】4个
【其他题号】无"""


class ParseTests(SimpleTestCase):
    def test_option_e_and_the_printed_type(self):
        reading = parse_reading(RAW, 4)
        self.assertEqual(reading["options"], {"A": "0个", "B": "1个", "C": "2个", "D": "3个", "E": "4个"})
        self.assertEqual(reading["type"], "multiple_choice")
        self.assertTrue(reading["stem"].startswith("函数"))

    def test_type_notes(self):
        for text, kind in {
            "(多项选择题)函数": "multiple_choice", "（多选）已知": "multiple_choice",
            "【多选题】下列": "multiple_choice", "（不定项选择题）下列": "multiple_choice",
            "（单选题）已知": "single_choice", "(单项选择题) 已知": "single_choice",
        }.items():
            with self.subTest(text=text):
                self.assertEqual(strip_type_label(text)[1], kind)
        self.assertEqual(strip_type_label("(1)求函数"), ("(1)求函数", None))
        self.assertEqual(strip_type_label("（多选）"), ("（多选）", None))

    def test_inline_options_keep_e(self):
        stem, options = split_inline_options("交点可能有（ ） A. 0个 B. 1个 C. 2个 D. 3个 E. 4个")
        self.assertEqual(stem, "交点可能有（ ）")
        self.assertEqual(options["E"], "4个")
        self.assertEqual(split_inline_options("有（ ） A. 1 B. 2 C. 3 D. 4")[1], {"A": "1", "B": "2", "C": "3", "D": "4"})

    def test_prompt_mentions_e_and_type_notes(self):
        self.assertIn("原卷印了 E 选项就再写【E】", TRANSCRIBE_RULES)
        self.assertIn("题型标注不要写进题干", TRANSCRIBE_RULES)

    def test_witness_ignores_the_type_note_and_reads_e(self):
        reading = parse_reading(RAW, 4)
        witness = ("4．(多项选择题)函数$y=1+\\cos x$，$x\\in\\left(\\frac{\\pi}{3},2\\pi\\right)$的图象与直线$y=t$($t$为常数)"
                   "的交点可能有（ ）. A.0个 B.1个 C.2个 D.3个 E.4个")
        self.assertEqual(witness_key("(多选)已知"), witness_key("已知"))
        self.assertTrue(witness_agrees(reading, witness))

    def test_picture_options_still_need_a_to_d_only(self):
        figures = [{"slot": key} for key in "ABCD"]
        self.assertEqual(set(missing_choice_figure_slots(kind="single_choice", options={}, figures=figures, readings=[])), set())


class TidySavedCardsTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        override = override_settings(DATA_ROOT=Path(temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="必修一.pdf", kind="pdf", sha256="f" * 64,
            source_path=str(Path(temp.name) / "source.pdf"),
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}], status=Paper.Status.READY,
        )
        self.group = QuestionGroup.objects.create(
            paper=self.paper, title="第74组", sequence=0, page_start=1, page_end=1, metadata={"pages": [0]},
        )

    def card(self, **fields):
        base = dict(paper=self.paper, group=self.group, number=4,
                    regions=[{"page_idx": 0, "bbox": [20, 100, 900, 260]}], state=Question.State.YELLOW)
        base.update(fields)
        return Question.objects.create(**base)

    def test_old_card_is_split_and_typed(self):
        question = self.card(
            stem="(多项选择题)函数y=1+cos x的图象与直线y=t的交点可能有（ ）.",
            options={"A": "0个", "B": "1个", "C": "2个", "D": "3个【E】4个"},
            question_type="unknown",
            read_a={"stem": "(多项选择题)函数…", "options": {"D": "3个【E】4个"}, "raw": "【D】3个【E】4个"},
        )
        self.assertEqual(library.tidy_saved_cards(), {"questions": 1, "publications": 0})
        question.refresh_from_db()
        self.assertEqual(question.stem, "函数y=1+cos x的图象与直线y=t的交点可能有（ ）.")
        self.assertEqual(question.options, {"A": "0个", "B": "1个", "C": "2个", "D": "3个", "E": "4个"})
        self.assertEqual(question.question_type, "multiple_choice")
        self.assertEqual(question.read_a["options"], {"D": "3个", "E": "4个"})
        self.assertEqual(question.read_a["raw"], "【D】3个【E】4个")
        self.assertEqual(library.tidy_saved_cards(), {"questions": 0, "publications": 0})

    def test_a_person_s_free_response_type_is_kept(self):
        question = self.card(stem="（多选）下列说法正确的有哪些？说明理由。", question_type="free_response")
        library.tidy_saved_cards()
        question.refresh_from_db()
        self.assertEqual(question.question_type, "free_response")
        self.assertEqual(question.stem, "下列说法正确的有哪些？说明理由。")

    def test_four_option_content_and_approval_are_untouched(self):
        question = self.card(stem="已知集合A", options={"A": "1", "B": "2", "C": "3", "D": "4"},
                             question_type="single_choice", state=Question.State.GREEN,
                             approved=True, approved_at=timezone.now())
        question = Question.objects.select_related("paper", "group").get(pk=question.pk)
        self.assertEqual(list(library.final_content(question)["options"]), ["A", "B", "C", "D"])
        question.approved_content_hash = library.approval_hash(question)
        question.save(update_fields=["approved_content_hash"])
        before = question.approved_content_hash
        self.assertEqual(library.tidy_saved_cards(), {"questions": 0, "publications": 0})
        question.refresh_from_db()
        self.assertEqual(question.approved_content_hash, before)

    def test_approved_and_published_card_keeps_one_version(self):
        question = self.card(stem="（多选题）下列函数中是偶函数的有（ ）",
                             options={"A": "y=x^2", "B": "y=|x|", "C": "y=x", "D": "y=1【E】y=x^3"},
                             question_type="multiple_choice", state=Question.State.GREEN,
                             approved=True, approved_at=timezone.now())
        question = Question.objects.select_related("paper", "group").get(pk=question.pk)
        question.approved_content_hash = library.approval_hash(question)
        question.save(update_fields=["approved_content_hash"])
        publication, created = library.publish(question)
        self.assertTrue(created)

        self.assertEqual(library.tidy_saved_cards(), {"questions": 1, "publications": 1})
        question = Question.objects.select_related("paper", "group").get(pk=question.pk)
        self.assertTrue(library.approval_is_current(question))
        publication.refresh_from_db()
        self.assertEqual(publication.content["options"]["E"], "y=x^3")
        self.assertEqual(publication.content["stem"], "下列函数中是偶函数的有（ ）")
        again, created = library.publish(question)
        self.assertFalse(created)
        self.assertEqual(PublishedQuestion.objects.count(), 1)
