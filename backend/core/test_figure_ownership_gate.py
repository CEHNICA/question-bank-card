"""A geometrically attached figure gets one look from the reader before a person does.

三种几何配图（同行分配、别题转赠、共用图注）全都跑在识读之后，那条路上没有
任何模型看过图，所以手写草稿、试卷标题也会被贴上去。这里钉住那条核验路：
认了的撤旗标，明确没有的撤图，说不清的一律不动。
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase
from PIL import Image

from . import figure_policy, imaging, pipeline, readers
from .models import Paper, Question, QuestionGroup


REGION = {"page_idx": 0, "bbox": [0, 0, 1000, 1000]}
BOX = [100, 200, 450, 420]
CUED = "如图，正六边形的顶点为 ABCDEF，求 ∠1+∠2"
ENGINE = readers.Engine(provider="minimax", model="MiniMax-M3")


def candidate(label, box=None):
    return {"label": str(label), "page_idx": 0, "bbox": list(box or BOX)}


def owned(picture):
    return {"page_idx": 0, "bbox": list(picture), "slot": "stem", "source": "row"}


class OwnershipAnswerTests(SimpleTestCase):
    def ask(self, reply, count=2):
        with mock.patch.object(readers, "chat", return_value=reply):
            return readers.verify_figure_ownership(ENGINE, "url", CUED, count)

    def test_a_numbered_answer_is_read_as_acceptance(self):
        self.assertEqual(self.ask("1"), {1})
        self.assertEqual(self.ask("1, 2"), {1, 2})
        self.assertEqual(self.ask("编号 2 才是它的"), {2})

    def test_saying_none_is_read_as_refusal(self):
        self.assertEqual(self.ask("none"), set())
        self.assertEqual(self.ask("None."), set())
        self.assertEqual(self.ask("没有一幅是"), set())

    def test_an_answer_we_cannot_read_is_no_answer_at_all(self):
        # 「看不清」不能当成 none：撤掉一张对的图，代价比留一张错的图大得多。
        self.assertIsNone(self.ask(""))
        self.assertIsNone(self.ask("看不清"))
        self.assertIsNone(self.ask("图有点模糊，无法判断"))

    def test_thinking_is_stripped_before_the_answer_is_read(self):
        self.assertEqual(self.ask("<think>让我看看</think>\n2"), {2})

    def test_nothing_to_ask_about_is_no_answer(self):
        self.assertIsNone(readers.verify_figure_ownership(ENGINE, "url", CUED, 0))


class ConfirmHandwrittenTests(SimpleTestCase):
    def ask(self, reply, indices):
        with mock.patch.object(readers, "chat", return_value=reply):
            return readers.confirm_all_handwritten(ENGINE, "url", indices)

    def test_only_the_hard_standard_counts_as_handwriting(self):
        self.assertEqual(self.ask("1", [1]), {1})
        self.assertEqual(self.ask("2", [1, 2]), {2})

    def test_a_picture_with_any_printed_mark_is_not_all_handwriting(self):
        self.assertEqual(self.ask("none", [1]), set())
        self.assertEqual(self.ask("都没有", [1, 2]), set())

    def test_a_number_we_never_asked_about_is_not_an_answer(self):
        self.assertEqual(self.ask("3", [1, 2]), None)
        self.assertIsNone(self.ask("看不清", [1]))
        self.assertEqual(self.ask("1", []), set())


class FigureStripTests(SimpleTestCase):
    """每幅候选图必须自己被裁清：按题卡范围整段裁时，范围接近整页的那张只剩几十像素。"""

    def page(self, size=(1000, 1000)):
        return Image.new("RGB", size, "white")

    def figure(self, bbox, page_idx=0):
        return {"page_idx": page_idx, "bbox": list(bbox)}

    def strip_width(self, figures):
        return imaging.figure_strip(lambda index: self.page(), figures).width

    def tile_width(self, strip):
        """从 strip 的总宽反推单幅宽度（左右各一个间隙）。"""
        return strip.width - 2 * imaging.STRIP_GAP

    def test_every_tile_comes_out_at_the_same_height(self):
        strip = imaging.figure_strip(
            lambda index: self.page(),
            [self.figure([0, 0, 100, 100]), self.figure([500, 0, 700, 20])])
        self.assertEqual(strip.height, imaging.STRIP_TILE_HEIGHT)

    def test_two_equal_candidates_take_two_tiles_plus_three_gaps(self):
        one = imaging.figure_strip(lambda index: self.page(), [self.figure([200, 200, 300, 300])])
        two = imaging.figure_strip(lambda index: self.page(),
                                   [self.figure([200, 200, 300, 300]), self.figure([600, 200, 700, 300])])
        self.assertEqual(two.width, one.width + self.tile_width(one) + imaging.STRIP_GAP)

    def test_a_short_candidate_is_zoomed_so_it_stays_readable(self):
        # 高度只有 20 的候选必须被放大到统一高度，否则模型看不清。
        page = self.page()
        strip = imaging.figure_strip(lambda index: page, [self.figure([0, 0, 100, 20])])
        self.assertEqual(strip.height, imaging.STRIP_TILE_HEIGHT)
        original = page.crop(imaging.to_pixels([-2, -2, 102, 22], page.size))
        self.assertEqual(round(self.tile_width(strip)), round(original.width * imaging.STRIP_MAX_ZOOM))

    def test_zooming_stops_at_a_ceiling_so_a_smudge_cannot_fill_the_page(self):
        tiny = imaging.figure_strip(lambda index: self.page(), [self.figure([0, 0, 10, 4])])
        self.assertLessEqual(self.tile_width(tiny) / imaging.STRIP_TILE_HEIGHT,
                             imaging.STRIP_MAX_ZOOM + .05)

    def test_a_box_that_cannot_be_cropped_gives_no_picture_at_all(self):
        # 发一张缺图的裁图，等于让模型对着不存在的东西回答「不是印刷插图」。
        self.assertIsNone(imaging.figure_strip(lambda index: self.page(),
                                                [self.figure([0, 0, 0, 0])]))
        self.assertIsNone(imaging.figure_strip(lambda index: self.page(), [self.figure([500, 400, 200, 600])]))
        self.assertIsNone(imaging.figure_strip(lambda index: self.page(), []))


class GeometricOwnershipGateTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(filename="fixture.pdf", kind="pdf", sha256="e" * 64,
                                          pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
                                          status="ready", processing_plan={"revision": 1})
        self.group = QuestionGroup.objects.create(paper=self.paper, title="卷一", kind="exam", sequence=0)

    def question(self, **extra):
        values = dict(paper=self.paper, group=self.group, number=13, stem=CUED,
                      question_type="free_response", state="yellow", regions=[REGION],
                      figure_candidates=[candidate(1)], figures=[owned(BOX)],
                      figure_review={"status": "ok", "source": "automatic"},
                      flags=[figure_policy.FLAG_ROW_FIGURE])
        values.update(extra)
        return Question.objects.create(**values)

    def run_gate(self, answer, question=None, confirm=set()):
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with mock.patch.object(pipeline, "PageStore", return_value=store), \
             mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE), \
             mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value=answer), \
             mock.patch.object(pipeline.readers, "confirm_all_handwritten",
                               return_value=confirm) as asked:
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        if question is not None:
            question.refresh_from_db()
        return changed, asked

    def test_a_printed_picture_keeps_the_confirmation_flag(self):
        # 「是印的」只说明它不是手写，不说明它属于这道题；归属仍要人确认。
        question = self.question()
        changed, asked = self.run_gate({1}, question)
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 0)
        self.assertEqual(question.figures, [owned(BOX)])
        self.assertEqual(question.flags, [figure_policy.FLAG_ROW_FIGURE])

    def test_one_call_is_not_enough_to_remove_a_picture(self):
        # 一次判定会翻转，所以判否之后还要过第二问「一笔印刷的内容都没有」。
        question = self.question()
        changed, asked = self.run_gate(set(), question, confirm=set())
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(question.figures, [owned(BOX)])

    def test_two_calls_agreeing_take_the_picture_off(self):
        # 整幅手写草稿被同行分配贴上来，两问都指向「没有一个印刷的笔画」。
        # 撤错了用户手工再框一次，所以这里按撤图处理。
        question = self.question()
        changed, asked = self.run_gate(set(), question, confirm={1})
        self.assertEqual(changed, 1)
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(question.figures, [])
        self.assertEqual(question.state, Question.State.YELLOW)
        self.assertIn(figure_policy.FLAG_NO_FIGURE, question.flags)
        self.assertNotIn(figure_policy.FLAG_ROW_FIGURE, question.flags)

    def test_a_printed_figure_written_all_over_stays(self):
        # 陈毅初三第 22 题：印刷的平行四边形被学生写满答案。第一问否掉了它，
        # 第二问「一笔印刷的内容都没有」答否——那句话是对的，别冤枉它。
        question = self.question()
        changed, asked = self.run_gate(set(), question, confirm=set())
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(len(question.figures), 1)

    def test_an_unreadable_second_call_keeps_the_card_exactly_as_it_was(self):
        question = self.question()
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with mock.patch.object(pipeline, "PageStore", return_value=store), \
             mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE), \
             mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value=set()), \
             mock.patch.object(pipeline.readers, "confirm_all_handwritten", return_value=None):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(question.figures, [owned(BOX)])
        self.assertEqual(question.flags, [figure_policy.FLAG_ROW_FIGURE])

    def test_only_the_confirmed_one_goes_when_a_card_carries_two(self):
        question = self.question(
            figure_candidates=[candidate(1), candidate(2, [600, 200, 900, 420])],
            figures=[owned(BOX), dict(owned([600, 200, 900, 420]))])
        changed, asked = self.run_gate({2}, question, confirm={1})
        self.assertEqual(changed, 1)
        self.assertEqual(asked.call_args.args[2], [1])
        self.assertEqual(question.figures, [dict(owned([600, 200, 900, 420]))])

    def test_a_second_call_that_names_nothing_removes_nothing(self):
        question = self.question(
            figure_candidates=[candidate(1), candidate(2, [600, 200, 900, 420])],
            figures=[owned(BOX), dict(owned([600, 200, 900, 420]))])
        changed, asked = self.run_gate({2}, question, confirm=set())
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_args.args[2], [1])
        self.assertEqual(len(question.figures), 2)

    def test_an_unreadable_answer_leaves_the_card_exactly_as_it_was(self):
        # 答不出来不是「否」。把它当成否会一路走到撤图，第二问再问也是白问。
        question = self.question()
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with mock.patch.object(pipeline, "PageStore", return_value=store), \
             mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE), \
             mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value=None), \
             mock.patch.object(pipeline.readers, "confirm_all_handwritten") as second:
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(second.call_count, 0)
        self.assertEqual(question.figures, [owned(BOX)])
        self.assertEqual(question.flags, [figure_policy.FLAG_ROW_FIGURE])

    def test_a_request_failure_leaves_the_card_exactly_as_it_was(self):
        question = self.question()
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with mock.patch.object(pipeline, "PageStore", return_value=store), \
             mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE), \
             mock.patch.object(pipeline.readers, "verify_figure_ownership",
                               side_effect=readers.ReaderError("断了")) as asked, \
             mock.patch.object(pipeline.readers, "confirm_all_handwritten") as second:
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 1)
        self.assertEqual(second.call_count, 0)
        self.assertEqual(question.figures, [owned(BOX)])

    def test_a_card_whose_candidate_cannot_be_cropped_is_never_asked(self):
        # 问一张缺图的裁图，模型会自信地答 none —— 那是在替裁图背锅。
        question = self.question(
            figure_candidates=[{"label": "1", "page_idx": 0, "bbox": [0, 0, 0, 0]}],
            figures=[{"page_idx": 0, "bbox": [0, 0, 0, 0], "slot": "stem", "source": "row"}])
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with mock.patch.object(pipeline, "PageStore", return_value=store), \
             mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE), \
             mock.patch.object(pipeline.readers, "verify_figure_ownership") as asked:
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 0)
        self.assertEqual(len(question.figures), 1)

    def test_a_picture_the_reader_already_confirmed_is_not_asked_again(self):
        # 没有旗标 = 这张图本来就是读题模型自己认下的，再问一次只是花钱。
        question = self.question(flags=[])
        changed, asked = self.run_gate({1}, question)
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 0)

    def test_a_person_who_already_decided_is_left_alone(self):
        question = self.question(edited=True)
        changed, asked = self.run_gate(set(), question)
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 0)
        self.assertEqual(question.figures, [owned(BOX)])

    def test_a_shared_caption_and_a_foreign_gift_are_both_covered(self):
        shared = self.question(number=14, figures=[dict(owned(BOX), source="shared")],
                               flags=[figure_policy.FLAG_SHARED_FIGURE])
        foreign = self.question(number=15, figures=[dict(owned(BOX), source="other")],
                                flags=[figure_policy.FLAG_FOREIGN_FIGURE])
        changed, asked = self.run_gate(set(), confirm={1})
        self.assertEqual((changed, asked.call_count), (2, 2))
        for item in (shared, foreign):
            item.refresh_from_db()
            self.assertEqual(item.figures, [])
            self.assertIn(figure_policy.FLAG_NO_FIGURE, item.flags)

    def test_the_stem_travels_with_the_question(self):
        self.question()
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with mock.patch.object(pipeline, "PageStore", return_value=store), \
             mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE), \
             mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value={1}) as asked:
            pipeline.verify_geometric_figures(self.paper, revision=1)
        self.assertEqual(asked.call_count, 1)
        self.assertIn(CUED, asked.call_args.args[2])

    def test_no_reading_service_means_no_questions_are_asked(self):
        question = self.question()
        with mock.patch.object(pipeline.readers, "primary_engine", return_value=None), \
             mock.patch.object(pipeline.readers, "verify_figure_ownership") as asked:
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 0)