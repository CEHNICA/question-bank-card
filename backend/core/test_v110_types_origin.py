"""1.10：题型未定要拦住、题源拆分、中文引号、功能开关、知识点与 AI 参考答案。

全部离线：虚构题目 + 模拟模型输出，不调用任何服务。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from . import features, knowledge, library, library_ai_settings, library_jobs, pipeline, prose, qtypes, readers, segment, textnorm
from .models import Block, LibraryJob, Paper, PublishedQuestion, Question
from .tests import PAGES, fake_page_pdf, tagged, two_column_paper

USER_STEM = (
    "[2026山东枣庄滕州二中月考]已知集合 $A=\\{x \\mid -3 \\leqslant x \\leqslant 10\\}$，"
    "$B=\\{x \\mid 2m+1 \\leqslant x \\leqslant 3m-2\\}$，且 $B \\neq \\varnothing$.\n"
    "(1)若命题 $p$：\"$\\forall x \\in B, x \\in A$\"是真命题，求实数 $m$ 的取值范围；\n"
    "(2)若命题 $q$：\"$\\exists x \\in A, x \\in B$\"是真命题，求实数 $m$ 的取值范围."
)
TIDY_STEM = (
    "已知集合 $A=\\{x \\mid -3 \\leqslant x \\leqslant 10\\}$，"
    "$B=\\{x \\mid 2m+1 \\leqslant x \\leqslant 3m-2\\}$，且 $B \\neq \\varnothing$.\n"
    "(1)若命题 $p$：“$\\forall x \\in B, x \\in A$”是真命题，求实数 $m$ 的取值范围；\n"
    "(2)若命题 $q$：“$\\exists x \\in A, x \\in B$”是真命题，求实数 $m$ 的取值范围."
)


class TempDataMixin:
    def use_temp_data(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)

    def make_paper(self, **extra):
        paper = Paper.objects.create(filename="微信图片_20260927143927_512_79 等 4 张照片", kind="pdf",
                                     sha256="z" * 64, status=Paper.Status.READY, pages=PAGES[:1], **extra)
        folder = self.temp / str(paper.id)
        folder.mkdir(parents=True, exist_ok=True)
        fake_page_pdf(folder / "source.pdf")
        paper.source_path = str(folder / "source.pdf")
        paper.save()
        return paper

    def card(self, paper, number=2, **extra):
        values = {
            "question_type": "unknown", "stem": USER_STEM, "options": {},
            "regions": [{"page_idx": 0, "bbox": [50, 100, 480, 300]}], "state": Question.State.GREEN,
            "text_source": "agree",
            "figure_review": {"status": "confirmed_no_figure", "source": "human", "reason": "原卷无图"},
        }
        values.update(extra)
        return Question.objects.create(paper=paper, number=number, **values)


# ---------------------------------------------------------------- 题型


class QuestionTypeRulesTests(SimpleTestCase):
    def test_one_vocabulary_for_readers_and_section_headings(self):
        self.assertEqual(qtypes.from_words("解答题"), "free_response")
        self.assertEqual(qtypes.from_words("计算题"), "free_response")
        self.assertEqual(qtypes.from_words("证明题"), "free_response")
        self.assertEqual(qtypes.from_words("判断题"), "true_false")
        self.assertEqual(qtypes.from_words("多项选择题"), "multiple_choice")
        self.assertEqual(qtypes.from_words("不定项选择"), "multiple_choice")
        self.assertEqual(qtypes.from_words("选择题"), "single_choice")
        self.assertEqual(qtypes.from_words("综述"), "unknown")
        self.assertEqual(segment._section_type("三、判断题（共 5 小题）"), "true_false")
        self.assertEqual(segment._section_type("四、计算题"), "free_response")

    def test_reader_type_names_include_calculation_and_true_false(self):
        reading = readers.parse_reading("【题型】计算题\n【题干】\n计算 $1+1$。", 3)
        self.assertEqual(reading["type"], "free_response")
        reading = readers.parse_reading("【题型】判断题\n【题干】\n0 是自然数。（ ）", 3)
        self.assertEqual(reading["type"], "true_false")
        self.assertIn("判断题", readers.transcribe_prompt(3, False))

    def test_inference_only_where_the_text_leaves_no_doubt(self):
        self.assertEqual(qtypes.infer("unknown", "已知……\n(1)求 a；\n(2)求 b.", {}), "free_response")
        self.assertEqual(qtypes.infer("unknown", "下列正确的是（ ）", {"A": "1", "B": "2"}), "single_choice")
        self.assertEqual(qtypes.infer("unknown", "（多选）下列正确的是", {"A": "1", "B": "2"}), "multiple_choice")
        # f(1), f(2) inside a sentence are not sub-questions.
        self.assertEqual(qtypes.infer("unknown", "已知 f(1)=2，f(2)=3，则 f(3)=____", {}), "unknown")
        # Only (2) printed: not a run from (1).
        self.assertEqual(qtypes.infer("unknown", "已知……\n(2)求 b.", {}), "unknown")
        # A decided type is never changed.
        self.assertEqual(qtypes.infer("fill_blank", "已知……\n(1)求 a；\n(2)求 b.", {}), "fill_blank")

    def test_consensus_keeps_a_shared_type_and_drops_a_disputed_one(self):
        self.assertEqual(qtypes.consensus(["free_response", "unknown"]), "free_response")
        self.assertEqual(qtypes.consensus(["free_response", "free_response"]), "free_response")
        self.assertEqual(qtypes.consensus(["single_choice", "free_response"]), "unknown")
        self.assertEqual(qtypes.consensus([]), "unknown")

    def test_subquestion_count(self):
        self.assertEqual(qtypes.subquestion_count(TIDY_STEM), 2)
        self.assertEqual(qtypes.subquestion_count("（1）甲\n（2）乙\n（3）丙"), 3)
        self.assertEqual(qtypes.subquestion_count("只有一问"), 0)

    def test_reminder_moves_the_state_only_when_it_changes(self):
        flags, state = qtypes.sync([], "green", "unknown")
        self.assertEqual(flags, [qtypes.FLAG_TYPE_UNKNOWN])
        self.assertEqual(state, "yellow")
        flags, state = qtypes.sync(flags, state, "free_response")
        self.assertEqual((flags, state), ([], "green"))
        # Another flag keeps the card yellow.
        flags, state = qtypes.sync(["选项 B 没有读出来"], "yellow", "unknown")
        flags, state = qtypes.sync(flags, state, "single_choice")
        self.assertEqual((flags, state), (["选项 B 没有读出来"], "yellow"))
        # Cards still reading are left alone.
        self.assertEqual(qtypes.sync([], "reading", "unknown"), ([], "reading"))


class ReadCardTypeTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        env = mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "test", "SILICONFLOW_API_KEY": "test2",
                                             "MINERU_TOKEN": "tok"})
        env.start()
        self.addCleanup(env.stop)
        self.paper = self.make_paper()
        self.snapshot = {"id": 1, "number": 2, "group_id": None,
                         "regions": [{"page_idx": 0, "bbox": [50, 300, 480, 520]}],
                         "candidates": [], "question_type": "unknown"}

    def read(self, primary_text, checker_text=None, arbiter_text=None):
        primary = readers.parse_reading(primary_text, 2)
        with mock.patch.object(readers, "read_question", return_value=primary) as read_mock, \
                mock.patch.object(readers, "arbitrate") as arbiter:
            result = pipeline.read_card(self.snapshot, pipeline.PageStore(self.paper))
        read_mock.assert_called_once()
        arbiter.assert_not_called()
        return result

    def test_second_read_and_arbiter_no_longer_replace_primary_text(self):
        result = self.read(tagged("求实数 $m$ 的取值范围甲"), tagged("求实数 $m$ 的取值范围乙"),
                           "【题干】\n求实数 $m$ 的取值范围丙")
        self.assertEqual(result["text_source"], "single")
        self.assertIn("甲", result["stem"])
        self.assertEqual(result["question_type"], "free_response")
        self.assertEqual(result["read_b"], {"skipped": "disabled"})

    def test_the_chosen_text_and_the_reading_records_are_tidied_alike(self):
        stem = USER_STEM.split("]", 1)[1]
        result = self.read(f"【题型】解答题\n【题干】\n{USER_STEM}", f"【题型】解答题\n【题干】\n{USER_STEM}")
        self.assertEqual(result["origin"], "2026山东枣庄滕州二中月考")
        self.assertEqual(result["stem"], TIDY_STEM)
        # The review page compares readings with the stem: they must not differ only by the tidying.
        self.assertEqual(result["read_a"]["stem"], TIDY_STEM)
        self.assertEqual(result["read_b"], {"skipped": "disabled"})
        self.assertIn("“", result["read_a"]["stem"])
        self.assertTrue(stem)

    def test_switches_off_leave_the_text_as_read(self):
        features.save({"origin_split": False, "chinese_quotes": False})
        result = self.read(f"【题型】解答题\n【题干】\n{USER_STEM}", f"【题型】解答题\n【题干】\n{USER_STEM}")
        self.assertEqual(result["stem"], USER_STEM)
        self.assertEqual(result["origin"], "")

    def test_untyped_readings_with_numbered_parts_become_free_response(self):
        stem = "已知集合 $A$.\n(1)求 $A\\cap B$；\n(2)求 $m$ 的取值范围."
        result = self.read(f"【题干】\n{stem}", f"【题干】\n{stem}")
        self.assertEqual(result["question_type"], "free_response")


class PersistTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        Block.objects.bulk_create([Block(paper=self.paper, **b) for b in two_column_paper()])

    def persist(self, question, fields):
        with mock.patch.object(pipeline, "read_card", return_value=fields):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        return question

    def reading(self, **extra):
        values = {"stem": TIDY_STEM, "origin": "2026山东枣庄滕州二中月考", "options": {}, "question_type": "unknown",
                  "text_source": "agree", "figures": [], "figure_review": {}, "foreign_figures": [], "flags": [],
                  "error": "", "state": Question.State.GREEN, "read_a": {}, "read_b": {}, "read_c": {}}
        values.update(extra)
        return values

    def test_new_reading_stores_the_origin_and_flags_an_undecided_type(self):
        question = self.card(self.paper, state=Question.State.WAITING, stem="")
        question = self.persist(question, self.reading())
        self.assertEqual(question.origin, "2026山东枣庄滕州二中月考")
        self.assertEqual(question.stem, TIDY_STEM)
        self.assertEqual(question.state, Question.State.YELLOW)
        self.assertIn(qtypes.FLAG_TYPE_UNKNOWN, question.flags)

    def test_a_reread_of_an_unedited_card_takes_the_new_origin(self):
        question = self.card(self.paper, question_type="free_response", stem=TIDY_STEM, origin="旧的题源")
        question = self.persist(question, self.reading(question_type="free_response"))
        self.assertEqual(question.origin, "2026山东枣庄滕州二中月考")
        question = self.persist(question, self.reading(question_type="free_response", origin="", stem=USER_STEM))
        self.assertEqual(question.origin, "")
        self.assertEqual(question.state, Question.State.GREEN)

    def test_a_persons_type_and_origin_survive_a_new_reading_of_an_edited_card(self):
        question = self.card(self.paper, question_type="free_response", edited=True, stem=TIDY_STEM, origin="人填的")
        question = self.persist(question, self.reading(question_type="unknown"))
        self.assertEqual(question.question_type, "free_response")
        self.assertEqual(question.origin, "人填的")
        self.assertNotIn(qtypes.FLAG_TYPE_UNKNOWN, question.flags)

    def test_a_chosen_type_survives_a_reread_even_when_the_text_is_read_again(self):
        question = self.card(self.paper, question_type="free_response", type_locked=True, stem=TIDY_STEM)
        question = self.persist(question, self.reading(question_type="single_choice"))
        self.assertEqual(question.question_type, "free_response")
        self.assertEqual(question.state, Question.State.GREEN)


# ---------------------------------------------------------------- 审核与入库


class TypeGateApiTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.client = Client()
        self.paper = self.make_paper()
        self.q = self.card(self.paper)

    def post(self, path, body=None):
        return self.client.post(path, data=json.dumps(body or {}), content_type="application/json",
                                HTTP_X_QB_REQUEST="1")

    def test_undecided_type_cannot_be_approved_by_a_person_or_an_ai(self):
        for body in ({"approved": True}, {"approved": True, "by": "ai", "agent": "豆包"}):
            response = self.post(f"/api/questions/{self.q.id}/approve", body)
            self.assertEqual(response.status_code, 400)
            self.assertIn("题型还没定", response.json()["error"])
        response = self.post(f"/api/papers/{self.paper.id}/approve-green", {})
        self.assertEqual(response.json()["approved"], 0)
        detail = self.client.get(f"/api/papers/{self.paper.id}").json()
        card = next(item for item in detail["questions"] if item["id"] == self.q.id)
        self.assertTrue(card["type_blocked"])

    def test_choosing_a_type_unblocks_and_needs_a_fresh_approval(self):
        self.q.flags, self.q.state = qtypes.sync([], "green", "unknown")
        self.q.save()
        response = self.post(f"/api/questions/{self.q.id}/type", {"question_type": "free_response"})
        self.assertEqual(response.status_code, 200, response.content)
        card = response.json()["question"]
        self.assertEqual(card["question_type"], "free_response")
        self.assertFalse(card["type_blocked"])
        self.assertEqual(card["state"], "green")
        self.assertNotIn(qtypes.FLAG_TYPE_UNKNOWN, card["flags"])
        self.assertFalse(card["approved"])
        response = self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["question"]["approved"])
        self.q.refresh_from_db()
        self.assertTrue(self.q.type_locked)
        response = self.post(f"/api/questions/{self.q.id}/regions",
                             {"regions": [{"page_idx": 0, "bbox": [50, 100, 480, 320]}]})
        self.assertEqual(response.status_code, 200, response.content)
        self.q.refresh_from_db()
        self.assertTrue(self.q.type_locked)
        self.assertFalse(self.q.approved)

    def test_type_action_rejects_undecided_or_unknown_values_and_cards_still_reading(self):
        for value in ("unknown", "essay", None):
            response = self.post(f"/api/questions/{self.q.id}/type", {"question_type": value})
            self.assertEqual(response.status_code, 400)
        Question.objects.filter(pk=self.q.pk).update(state=Question.State.RED)
        response = self.post(f"/api/questions/{self.q.id}/type", {"question_type": "free_response"})
        self.assertEqual(response.status_code, 200, response.content)
        Question.objects.filter(pk=self.q.pk).update(state=Question.State.READING)
        response = self.post(f"/api/questions/{self.q.id}/type", {"question_type": "fill_blank"})
        self.assertEqual(response.status_code, 400)

    def test_publish_refuses_an_approved_card_whose_type_is_undecided(self):
        # An approval made before 1.10 (the hash matches) is no longer current.
        self.q.approved = True
        self.q.approved_at = timezone.now()
        self.q.save()
        self.q.approved_content_hash = library.approval_hash(self.q)
        self.q.save()
        self.assertFalse(library.approval_is_current(self.q))
        with self.assertRaisesRegex(ValueError, "题型还没定"):
            library.publish(self.q)
        response = self.post(f"/api/papers/{self.paper.id}/publish", {})
        self.assertEqual(response.json()["created"], 0)
        self.assertTrue(any("题型" in problem for problem in response.json()["problems"]))

    def test_saving_text_keeps_a_typed_origin_and_splits_one_from_the_stem(self):
        response = self.post(f"/api/questions/{self.q.id}/text",
                             {"stem": USER_STEM, "options": {}, "question_type": "free_response"})
        card = response.json()["question"]
        self.assertEqual(card["origin"], "2026山东枣庄滕州二中月考")
        self.assertEqual(card["stem"], TIDY_STEM)
        response = self.post(f"/api/questions/{self.q.id}/text", {
            "stem": card["stem"], "options": {}, "question_type": "free_response",
            "origin": "【2026·滕州二中·10月月考】"})
        self.assertEqual(response.json()["question"]["origin"], "2026·滕州二中·10月月考")
        response = self.post(f"/api/questions/{self.q.id}/text", {
            "stem": card["stem"], "options": {}, "question_type": "unknown"})
        card = response.json()["question"]
        self.assertEqual(card["state"], "yellow")
        self.assertIn(qtypes.FLAG_TYPE_UNKNOWN, card["flags"])
        # A different note in front of the stem is not thrown away when the card already has an origin.
        response = self.post(f"/api/questions/{self.q.id}/text", {
            "stem": "（2022·海淀期末）已知 $a>0$", "options": {}, "question_type": "free_response"})
        card = response.json()["question"]
        self.assertEqual(card["stem"], "（2022·海淀期末）已知 $a>0$")
        self.assertEqual(card["origin"], "2026·滕州二中·10月月考")


class OriginHashTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()

    def test_an_empty_origin_leaves_every_old_hash_unchanged(self):
        question = self.card(self.paper, question_type="free_response", stem=TIDY_STEM)
        content = library.final_content(question)
        self.assertEqual(content["origin"], "")
        old_style = {key: value for key, value in content.items() if key != "origin"}
        self.assertEqual(library.content_hash(content), library.content_hash(old_style))
        question.origin = "2026山东枣庄滕州二中月考"
        self.assertNotEqual(library.content_hash(library.final_content(question)), library.content_hash(content))


class TidySavedCardsTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()

    def approve(self, question):
        question.approved = True
        question.approved_at = timezone.now()
        question.save()
        question = Question.objects.select_related("paper").get(pk=question.pk)
        question.approved_content_hash = library.approval_hash(question)
        question.save()
        return question

    def test_published_card_gets_its_origin_split_in_place_and_keeps_its_approval(self):
        question = self.approve(self.card(self.paper, question_type="free_response",
                                          read_a={"stem": USER_STEM}))
        publication, _created = library.publish(question)

        counts = library.tidy_saved_cards()

        self.assertEqual(counts, {"questions": 1, "publications": 1})
        question = Question.objects.select_related("paper").get(pk=question.pk)
        self.assertEqual(question.origin, "2026山东枣庄滕州二中月考")
        self.assertEqual(question.stem, TIDY_STEM)
        self.assertEqual(question.read_a["stem"], TIDY_STEM)
        self.assertTrue(library.approval_is_current(question))
        publication.refresh_from_db()
        self.assertEqual(publication.content["origin"], "2026山东枣庄滕州二中月考")
        self.assertEqual(publication.content["stem"], TIDY_STEM)
        self.assertEqual(publication.content_hash, library.content_hash(publication.content))
        self.assertIn(library.search_key("滕州二中"), publication.search_text)
        again, created = library.publish(question)
        self.assertFalse(created)
        self.assertEqual(again.pk, publication.pk)
        self.assertEqual(library.tidy_saved_cards(), {"questions": 0, "publications": 0})

    def test_undecided_old_card_gets_the_reminder_and_loses_a_pre_110_approval(self):
        question = self.approve(self.card(self.paper, stem=TIDY_STEM))
        library.tidy_saved_cards()
        question.refresh_from_db()
        self.assertEqual(question.state, Question.State.YELLOW)
        self.assertIn(qtypes.FLAG_TYPE_UNKNOWN, question.flags)
        self.assertFalse(library.approval_is_current(question))

    def test_two_notes_in_front_keep_the_second_and_tidying_again_changes_nothing(self):
        stem = "[2023北京期中]（2022·海淀期末）已知 $a>0$，求 $a$."
        question = self.approve(self.card(self.paper, question_type="free_response", stem=stem))
        publication, _created = library.publish(question)
        self.assertEqual(library.tidy_saved_cards(), {"questions": 1, "publications": 1})
        question.refresh_from_db()
        self.assertEqual((question.origin, question.stem), ("2023北京期中", "（2022·海淀期末）已知 $a>0$，求 $a$."))
        self.assertEqual(library.tidy_saved_cards(), {"questions": 0, "publications": 0})
        publication.refresh_from_db()
        self.assertEqual(publication.content["stem"], "（2022·海淀期末）已知 $a>0$，求 $a$.")

    def test_switched_off_tidy_leaves_text_alone(self):
        features.save({"origin_split": False, "chinese_quotes": False})
        question = self.card(self.paper, question_type="free_response")
        self.assertEqual(library.tidy_saved_cards(), {"questions": 0, "publications": 0})
        question.refresh_from_db()
        self.assertEqual(question.stem, USER_STEM)


class TextRuleTests(SimpleTestCase):
    def test_origin_split_is_conservative(self):
        cases = {
            "[2026山东枣庄滕州二中月考]已知": ("已知", "2026山东枣庄滕州二中月考"),
            "（2025·北京海淀·期中）已知": ("已知", "2025·北京海淀·期中"),
            "【2024新课标Ⅰ卷】设函数": ("设函数", "2024新课标Ⅰ卷"),
            "[滕州二中月考] 已知": ("已知", "滕州二中月考"),
        }
        for text, expected in cases.items():
            self.assertEqual(textnorm.split_origin(text), expected, text)
        self.assertEqual(textnorm.split_origin("（2023·北京卷）已知"), ("已知", "2023·北京卷"))
        self.assertEqual(textnorm.split_origin("(2021浙江卷)已知"), ("已知", "2021浙江卷"))
        for text in ("（本小题满分12分）已知", "（12分）已知", "(2023)年的", "（多选）下列", "（1）若",
                     "已知(2023·北京卷)", "（2025·北京海淀·期中）", "（改编）已知", "(第15题图)已知",
                     "（$x>0$ 时 2023 年）已知", "[2026山东月考)已知", "（2022年北京冬奥会期间）某商店",
                     "(2020年第七次全国人口普查数据)下表是", "(2023年5分)已知"):
            self.assertEqual(textnorm.split_origin(text), (text, ""), text)

    def test_quotes_only_in_chinese_text_and_outside_formulas(self):
        self.assertEqual(textnorm.chinese_quotes('命题 $p$："$\\forall x$"是真命题'), '命题 $p$：“$\\forall x$”是真命题')
        self.assertEqual(textnorm.chinese_quotes('把"等差"改成"等比"'), "把“等差”改成“等比”")
        for text in ('Tom said "I like math" today.', '一个"引号', '<td rowspan="2">甲</td>',
                     '命题"p\n跨行"'):
            self.assertEqual(textnorm.chinese_quotes(text), text)
        self.assertEqual(textnorm.chinese_quotes('$f"(x)$与"导数"'), '$f"(x)$与“导数”')

    def test_a_type_note_after_the_source_note_still_names_the_type(self):
        stem, origin, labelled = prose.tidy_stem("（2023·北京卷）（多选）下列说法正确的是",
                                                 switches={"origin_split": True, "chinese_quotes": True})
        self.assertEqual((stem, origin, labelled), ("下列说法正确的是", "2023·北京卷", "multiple_choice"))
        self.assertEqual(prose.clean_origin(" 【2026 滕州二中\n月考】 "), "2026 滕州二中 月考")


# ---------------------------------------------------------------- 开关、题库筛选、知识点与 AI 答案


class FeatureSwitchTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.client = Client()

    def test_defaults_save_and_api(self):
        self.assertEqual(features.load(), features.DEFAULTS)
        self.assertTrue(features.enabled("origin_split"))
        self.assertFalse(features.enabled("knowledge_tags"))
        with self.assertRaises(features.FeatureError):
            features.save({"nope": True})
        with self.assertRaises(features.FeatureError):
            features.save({"ai_answer": "yes"})
        response = self.client.get("/api/settings/features")
        self.assertEqual({item["key"] for item in response.json()["features"]}, set(features.FEATURES))
        response = self.client.post("/api/settings/features", data=json.dumps({"features": {"knowledge_tags": True}}),
                                    content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(features.enabled("knowledge_tags"))
        self.assertTrue(knowledge.path().is_file())
        # A broken file falls back to the defaults.
        features.path().write_text("{oops", encoding="utf-8")
        self.assertEqual(features.load(), features.DEFAULTS)


class KnowledgeCatalogueTests(SimpleTestCase):
    def test_catalogue_and_matching(self):
        points = knowledge.parse_catalogue(knowledge.DEFAULT_TEXT)
        names = [item["point"] for item in points]
        self.assertIn("集合间的基本关系", names)
        self.assertIn("全称量词与存在量词", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(item["chapter"] for item in points))
        self.assertEqual(
            knowledge.match_tags("1. 集合间的基本关系；2、全称量词与存在量词；自创知识点；函数的应用(一)；指数", points),
            ["集合间的基本关系", "全称量词与存在量词", "函数的应用（一）"],
        )
        self.assertEqual(knowledge.match_tags("无", points), [])
        # Names with 、 inside, and the longer name wins over a shorter one inside it.
        self.assertEqual(knowledge.match_tags("二次函数与一元二次方程、不等式；空间直线、平面的平行", points),
                         ["二次函数与一元二次方程、不等式", "空间直线、平面的平行"])
        self.assertEqual(knowledge.match_tags("【知识点】指数函数", points), ["指数函数"])


class LibraryExtrasTests(TempDataMixin, TransactionTestCase):
    def setUp(self):
        self.use_temp_data()
        self.client = Client()
        self.paper = self.make_paper()
        # No real credentials or service are consulted by these offline jobs.
        isolated = mock.patch.dict(os.environ, {"QB_LIBRARY_AI_SETTINGS_FILE": str(self.temp / "library-ai-settings.json"),
                                                "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.temp / "library-ai.dat")})
        isolated.start()
        self.addCleanup(isolated.stop)
        ready = mock.patch.object(library_ai_settings, "ensure_ready", return_value={"ready": True, "mode": "api"})
        ready.start()
        self.addCleanup(ready.stop)
        snapshot = mock.patch.object(library_ai_settings, "execution_snapshot", return_value={"mode": "api", "revision": "offline-test"})
        snapshot.start()
        self.addCleanup(snapshot.stop)

    def published(self, number, **extra):
        values = {"question_type": "free_response", "stem": TIDY_STEM, "origin": "2026山东枣庄滕州二中月考"}
        values.update(extra)
        question = self.card(self.paper, number=number, **values)
        question.approved = True
        question.approved_at = timezone.now()
        question.save()
        question = Question.objects.select_related("paper").get(pk=question.pk)
        question.approved_content_hash = library.approval_hash(question)
        question.save()
        return question, library.publish(question)[0]

    def post(self, path, body):
        return self.client.post(path, data=json.dumps(body), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_answer_filter_facets_and_entry_fields(self):
        self.published(2)
        self.published(3, answer="C", stem="下列正确的是（ ）", question_type="single_choice",
                       options={"A": "1", "B": "2", "C": "3", "D": "4"}, origin="")
        body = self.client.get("/api/library").json()
        self.assertEqual(body["facets"]["answers"], {"yes": 1, "no": 1})
        self.assertIn("features", body)
        entry = next(item for item in body["items"] if item["number"] == 2)
        self.assertEqual(entry["origin"], "2026山东枣庄滕州二中月考")
        self.assertEqual(entry["subquestions"], 2)
        self.assertFalse(entry["has_answer"])
        self.assertEqual(entry["jobs"], [])
        self.assertEqual([item["number"] for item in self.client.get("/api/library?answer=no").json()["items"]], [2])
        self.assertEqual([item["number"] for item in self.client.get("/api/library?answer=yes").json()["items"]], [3])
        # The source note is searchable.
        self.assertEqual(self.client.get("/api/library?q=滕州二中").json()["total"], 1)

    def test_jobs_are_refused_while_switched_off(self):
        _question, publication = self.published(2)
        response = self.post("/api/library/jobs", {"kind": "answer", "ids": [str(publication.id)]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(LibraryJob.objects.count(), 0)

    def test_tags_and_ai_answer_are_made_by_the_worker_and_kept_out_of_the_snapshot(self):
        features.save({"knowledge_tags": True, "ai_answer": True})
        question, publication = self.published(2)
        old_hash = publication.content_hash
        self.assertEqual(self.post("/api/library/jobs", {"kind": "tags", "missing": True}).json()["queued"], 1)
        self.assertEqual(self.post("/api/library/jobs", {"kind": "answer", "ids": [str(publication.id)]}).json()["queued"], 1)
        # Asking twice reuses the waiting job.
        self.post("/api/library/jobs", {"kind": "answer", "ids": [str(publication.id)]})
        self.assertEqual(LibraryJob.objects.filter(status="queued").count(), 2)
        listed = self.client.get("/api/library").json()["items"][0]
        self.assertEqual(sorted(listed["jobs"]), ["answer", "tags"])

        def chat(prompt, _images, **_kwargs):
            if "知识点目录" in prompt:
                return "【知识点】集合间的基本关系；全称量词与存在量词；随便编的", "模拟豆包 Pro"
            return "【答案】(1) $3\\leqslant m\\leqslant 4$；(2) $3\\leqslant m\\leqslant \\frac{9}{2}$\n【解析】由 $B\\neq\\varnothing$ 得 $m\\geqslant 3$。", "模拟豆包 Pro"

        with mock.patch.object(library_ai_settings, "chat", chat):
            self.assertEqual(library_jobs.process_pending(), 2)
        publication.refresh_from_db()
        self.assertEqual(publication.extras["tags"], ["集合间的基本关系", "全称量词与存在量词"])
        self.assertIn("3\\leqslant m\\leqslant 4", publication.extras["ai_answer"]["answer"])
        self.assertEqual(publication.content_hash, old_hash)
        self.assertEqual(publication.version, 1)
        body = self.client.get("/api/library?tag=集合间的基本关系").json()
        self.assertEqual(body["total"], 1)
        self.assertIn({"tag": "集合间的基本关系", "count": 1}, body["facets"]["tags"])
        self.assertEqual(self.client.get("/api/library?tag=集合").json()["total"], 0)
        self.assertEqual(self.client.get("/api/library?q=全称量词").json()["total"], 1)

        # A new version (the answer was added by a person) keeps the tags; the
        # AI answer stays because the task text did not change.
        question = Question.objects.select_related("paper").get(pk=question.pk)
        question.answer = "(1) [3,4]；(2) [3,9/2]"
        question.approved_content_hash = library.approval_hash(question)
        question.save()
        newer, created = library.publish(question)
        self.assertTrue(created)
        self.assertEqual(newer.extras["tags"], ["集合间的基本关系", "全称量词与存在量词"])
        self.assertIn("ai_answer", newer.extras)
        self.assertEqual(self.client.get("/api/library?tag=全称量词与存在量词").json()["total"], 1)

    def test_a_waiting_job_keeps_its_source_version_and_new_version_needs_its_own_job(self):
        features.save({"ai_answer": True})
        question, publication = self.published(2)
        library_jobs.enqueue(publication, "answer")
        question = Question.objects.select_related("paper").get(pk=question.pk)
        question.analysis = "人补的解析"
        question.approved_content_hash = library.approval_hash(question)
        question.save()
        newer, created = library.publish(question)
        self.assertTrue(created)
        self.assertEqual(LibraryJob.objects.get().publication_id, publication.id)
        with mock.patch.object(library_ai_settings, "chat") as chat:
            library_jobs.process_pending()
            chat.assert_not_called()
        self.assertEqual(LibraryJob.objects.get().status, "failed")
        library_jobs.enqueue(newer, "answer")
        with mock.patch.object(library_ai_settings, "chat", return_value=("【答案】(1) [3,4]\n【解析】略", "模拟豆包 Pro")):
            library_jobs.process_pending()
        newer.refresh_from_db()
        publication.refresh_from_db()
        self.assertEqual(newer.extras["ai_answer"]["answer"], "(1) [3,4]")
        self.assertNotIn("ai_answer", publication.extras)

    def test_a_job_without_independent_doubao_service_fails_with_a_reason(self):
        features.save({"ai_answer": True})
        _question, publication = self.published(2)
        library_jobs.enqueue(publication, "answer")
        with mock.patch.object(library_ai_settings, "chat", side_effect=library_ai_settings.ServiceError(library_ai_settings.UNAVAILABLE)):
            library_jobs.process_pending()
        job = LibraryJob.objects.get()
        self.assertEqual(job.status, "failed")
        self.assertIn("设置", job.error)
        listed = self.client.get("/api/library").json()["items"][0]
        self.assertIn("answer", listed["job_errors"])
