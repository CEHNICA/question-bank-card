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
        return {"page_idx": page_idx, "bbox": bbox}

    def strip_width(self, figures):
        return imaging.figure_strip(lambda index: self.page(), figures).width

    def tile_width(self, strip):
        """从 strip 的总宽反推单幅宽度（左右各一个间隙）。"""
        return strip.width - 2 * imaging.STRIP_GAP

    def test_every_tile_comes_out_at_the_same_height(self):
        strip = imaging.figure_strip(
            lambda index: self.page(), [self.figure([0, 0, 100, 100]), self.figure([500, 0, 700, 20])]
        )
        self.assertEqual(strip.height, imaging.STRIP_TILE_HEIGHT)

    def test_two_equal_candidates_take_two_tiles_plus_three_gaps(self):
        one = imaging.figure_strip(lambda index: self.page(), [self.figure([200, 200, 300, 300])])
        two = imaging.figure_strip(
            lambda index: self.page(), [self.figure([200, 200, 300, 300]), self.figure([600, 200, 700, 300])]
        )
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
        self.assertLessEqual(self.tile_width(tiny) / imaging.STRIP_TILE_HEIGHT, imaging.STRIP_MAX_ZOOM + 0.05)

    def test_a_box_that_cannot_be_cropped_gives_no_picture_at_all(self):
        # 发一张缺图的裁图，等于让模型对着不存在的东西回答「不是印刷插图」。
        self.assertIsNone(imaging.figure_strip(lambda index: self.page(), [self.figure([0, 0, 0, 0])]))
        self.assertIsNone(imaging.figure_strip(lambda index: self.page(), [self.figure([500, 400, 200, 600])]))
        self.assertIsNone(imaging.figure_strip(lambda index: self.page(), []))

    def test_a_box_written_wrongly_also_gives_no_picture_at_all(self):
        # 「裁不出图就返回 None」这条承诺以前只对坐标反了成立。少一维、坐标是
        # 字符串或 None、NaN、无穷、超出页面：五种各自抛一种异常，其中三种
        # 调用方的 ``except ValueError`` 接不住，一道题的框坏掉就整份卷子停住。
        for bbox in (
            [0, 0, 100],
            [0, 0, 100, 100, 100],
            [],
            None,
            "0,0,100,100",
            [0, 0, 100, None],
            [0, 0, 100, "80"],
            [0, 0, 100, True],
            [0, 0, 100, float("nan")],
            [0, 0, 100, float("inf")],
            [-5, 0, 100, 100],
            [0, 0, 1001, 100],
        ):
            with self.subTest(bbox=bbox):
                self.assertIsNone(imaging.figure_strip(lambda index: self.page(), [self.figure(bbox)]))

    def test_one_bad_box_poisons_the_whole_strip(self):
        good = self.figure([0, 0, 100, 100])
        self.assertIsNone(imaging.figure_strip(lambda index: self.page(), [good, self.figure([0, 0, 0, 0]), good]))

    def test_a_page_that_cannot_be_loaded_gives_no_picture_at_all(self):
        # 页码越界、缓存损坏、取页拿到不是图像的东西：同样是不给图。
        for loader in (
            lambda index: None,
            lambda index: (_ for _ in ()).throw(IndexError("没有这一页")),
            lambda index: (_ for _ in ()).throw(ValueError("缓存坏了")),
            lambda index: (_ for _ in ()).throw(TypeError("不是图像")),
        ):
            with self.subTest(loader=loader):
                self.assertIsNone(imaging.figure_strip(loader, [self.figure([0, 0, 100, 100])]))

    def test_a_box_outside_the_page_is_still_clamped_not_rejected(self):
        # ``figure_strip`` 自己加边距时就会算出负坐标和超过页宽的框；
        # ``to_pixels`` 收超出页面的框是它的本分，不能一并拒掉。
        self.assertEqual(imaging.to_pixels([-2, -2, 102, 22], (1000, 1000)), (0, 0, 102, 22))

    def test_a_box_written_wrongly_raises_the_one_error_callers_catch(self):
        # 调用方写的是 ``except ValueError``，所以格式不对也得是 ValueError，
        # 不能是解包错、类型错、溢出那几种接不住的。
        for bbox in (
            [0, 0, 100],
            [],
            None,
            "0,0,100,100",
            [0, 0, 100, None],
            [0, 0, 100, "80"],
            [0, 0, 100, float("nan")],
            [0, 0, 100, float("inf")],
        ):
            with self.subTest(bbox=bbox):
                with self.assertRaises(ValueError):
                    imaging.to_pixels(bbox, (1000, 1000))

    def test_a_box_smaller_than_a_pixel_still_says_so(self):
        with self.assertRaisesMessage(ValueError, "范围小于一个像素"):
            imaging.to_pixels([100, 100, 100, 200], (1000, 1000))


class GeometricOwnershipGateTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(
            filename="fixture.pdf",
            kind="pdf",
            sha256="e" * 64,
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
            status="ready",
            processing_plan={"revision": 1},
        )
        self.group = QuestionGroup.objects.create(paper=self.paper, title="卷一", kind="exam", sequence=0)

    def question(self, **extra):
        values = {
            "paper": self.paper,
            "group": self.group,
            "number": 13,
            "stem": CUED,
            "question_type": "free_response",
            "state": "yellow",
            "regions": [REGION],
            "figure_candidates": [candidate(1)],
            "figures": [owned(BOX)],
            "figure_review": {"status": "ok", "source": "automatic"},
            "flags": [figure_policy.FLAG_ROW_FIGURE],
        }
        values.update(extra)
        return Question.objects.create(**values)

    def run_gate(self, answer, question=None, confirm=frozenset()):
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value=answer),
            mock.patch.object(pipeline.readers, "confirm_all_handwritten", return_value=confirm) as asked,
        ):
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
        # 条图上一次，单独那一张再一次。
        self.assertEqual(asked.call_count, 2)
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
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value=set()),
            mock.patch.object(pipeline.readers, "confirm_all_handwritten", return_value=None),
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(question.figures, [owned(BOX)])
        self.assertEqual(question.flags, [figure_policy.FLAG_ROW_FIGURE])

    def test_only_the_confirmed_one_goes_when_a_card_carries_two(self):
        question = self.question(
            figure_candidates=[candidate(1), candidate(2, [600, 200, 900, 420])],
            figures=[owned(BOX), dict(owned([600, 200, 900, 420]))],
        )
        changed, asked = self.run_gate({2}, question, confirm={1})
        self.assertEqual(changed, 1)
        self.assertEqual(asked.call_args.args[2], [1])
        self.assertEqual(question.figures, [dict(owned([600, 200, 900, 420]))])

    def test_a_second_call_that_names_nothing_removes_nothing(self):
        question = self.question(
            figure_candidates=[candidate(1), candidate(2, [600, 200, 900, 420])],
            figures=[owned(BOX), dict(owned([600, 200, 900, 420]))],
        )
        changed, asked = self.run_gate({2}, question, confirm=set())
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_args.args[2], [1])
        self.assertEqual(len(question.figures), 2)

    def test_an_unreadable_answer_leaves_the_card_exactly_as_it_was(self):
        # 答不出来不是「否」。把它当成否会一路走到撤图，第二问再问也是白问。
        question = self.question()
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value=None),
            mock.patch.object(pipeline.readers, "confirm_all_handwritten") as second,
        ):
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
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(
                pipeline.readers, "verify_figure_ownership", side_effect=readers.ReaderError("断了")
            ) as asked,
            mock.patch.object(pipeline.readers, "confirm_all_handwritten") as second,
        ):
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
            figures=[{"page_idx": 0, "bbox": [0, 0, 0, 0], "slot": "stem", "source": "row"}],
        )
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "verify_figure_ownership") as asked,
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 0)
        self.assertEqual(len(question.figures), 1)

    def test_a_strip_that_cannot_be_built_never_reaches_the_reader(self):
        # bbox 畸形已经被上一道过滤挡掉了；figure_strip 自己也说裁不出来（空页、
        # 零宽图，或者以后把拒绝条件收严）时，同样一张都不该发出去。少了图模型只
        # 会自信地答 none，那是在替裁图背锅。
        question = self.question()
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.imaging, "figure_strip", return_value=None),
            mock.patch.object(pipeline.readers, "verify_figure_ownership") as asked,
            mock.patch.object(pipeline.readers, "confirm_all_handwritten") as second,
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 0)
        self.assertEqual(second.call_count, 0)
        self.assertEqual(question.figures, [owned(BOX)])

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
        shared = self.question(
            number=14, figures=[dict(owned(BOX), source="shared")], flags=[figure_policy.FLAG_SHARED_FIGURE]
        )
        foreign = self.question(
            number=15, figures=[dict(owned(BOX), source="other")], flags=[figure_policy.FLAG_FOREIGN_FIGURE]
        )
        changed, asked = self.run_gate(set(), confirm={1})
        self.assertEqual((changed, asked.call_count), (2, 4))
        for item in (shared, foreign):
            item.refresh_from_db()
            self.assertEqual(item.figures, [])
            self.assertIn(figure_policy.FLAG_NO_FIGURE, item.flags)

    def test_a_card_with_no_candidate_of_its_own_is_still_looked_at(self):
        # 凤城高一第 14 题：三张图都是别的题的识读认过来的（source=other），本卡
        # 一个候选框都没有——条图那一套要给模型编号、再和候选配对，根本搭不起来，
        # 以前在这里直接 continue，整张卡一次都没看过。里面混了一张学生的铅笔
        # 草稿，闸门连问都没问就把它留在了题上。
        question = self.question(
            figure_candidates=[],
            flags=[figure_policy.FLAG_FOREIGN_FIGURE],
            figures=[
                dict(owned([300, 200, 500, 400]), source="other"),
                dict(owned([826, 387, 865, 449]), source="other"),
                dict(owned([600, 450, 800, 600]), source="other"),
            ],
        )
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        alone = mock.Mock(side_effect=[set(), {1}, set()])
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "verify_figure_ownership") as strip,
            mock.patch.object(pipeline.readers, "confirm_all_handwritten", alone),
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        # 没有候选框就不该走条图那一套：编号和配对都无从谈起。
        self.assertEqual(strip.call_count, 0)
        # 三张各单独裁一次问一遍，只有中间那张判手写。
        self.assertEqual(alone.call_count, 3)
        self.assertEqual(changed, 1)
        self.assertEqual([figure["bbox"] for figure in question.figures], [[300, 200, 500, 400], [600, 450, 800, 600]])
        self.assertNotIn(figure_policy.FLAG_FOREIGN_FIGURE, question.flags)

    def test_a_foreign_gift_that_is_printed_stays(self):
        question = self.question(
            figure_candidates=[],
            flags=[figure_policy.FLAG_FOREIGN_FIGURE],
            figures=[dict(owned([826, 387, 865, 449]), source="other")],
        )
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "confirm_all_handwritten", return_value=set()),
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(len(question.figures), 1)
        self.assertIn(figure_policy.FLAG_FOREIGN_FIGURE, question.flags)

    def test_a_foreign_gift_that_cannot_be_cropped_is_left_alone(self):
        question = self.question(
            figure_candidates=[],
            flags=[figure_policy.FLAG_FOREIGN_FIGURE],
            figures=[dict(owned([826, 387, 865, 449]), source="other")],
        )
        store = mock.Mock()
        store.load.side_effect = [OSError("缓存坏了")]
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "confirm_all_handwritten") as asked,
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 0)
        self.assertEqual(len(question.figures), 1)

    def test_a_row_figure_survives_when_each_one_alone_is_readable(self):
        # 四道选择题的图横排在一起：并排放大本来就难分清，在同一张糊图上问两遍
        # 会得到同一个错答案，两遍都指向手写，四张图就这么全没了。单独裁一张、
        # 放到最大再看，答案才作数。
        question = self.question(
            figure_candidates=[
                candidate(1, [0, 0, 200, 200]),
                candidate(2, [250, 0, 450, 200]),
                candidate(3, [500, 0, 700, 200]),
                candidate(4, [750, 0, 950, 200]),
            ],
            figures=[
                owned([0, 0, 200, 200]),
                dict(owned([250, 0, 450, 200])),
                dict(owned([500, 0, 700, 200])),
                dict(owned([750, 0, 950, 200])),
            ],
        )
        # 条图上说四张全是手写；每张单独放大看，答案都是「不是」。
        alone = mock.Mock(side_effect=[{1, 2, 3, 4}, set(), set(), set(), set()])
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value=set()),
            mock.patch.object(pipeline.readers, "confirm_all_handwritten", alone),
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(len(question.figures), 4)
        # 单独裁的那四张：前一张是条图上的第二问，后三张各自一次。
        self.assertEqual([call.args[2] for call in alone.call_args_list], [[1, 2, 3, 4], [1], [1], [1], [1]])

    def test_only_the_figure_that_still_looks_handwritten_alone_goes(self):
        question = self.question(
            figure_candidates=[candidate(1, [0, 0, 200, 200]), candidate(2, [250, 0, 450, 200])],
            figures=[owned([0, 0, 200, 200]), dict(owned([250, 0, 450, 200]))],
        )
        alone = mock.Mock(side_effect=[{1, 2}, {1}, set()])
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value=set()),
            mock.patch.object(pipeline.readers, "confirm_all_handwritten", alone),
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 1)
        self.assertEqual(question.figures, [dict(owned([250, 0, 450, 200]))])

    def test_a_figure_that_cannot_be_cropped_alone_is_not_removed(self):
        question = self.question(
            figure_candidates=[candidate(1), {"label": "2", "page_idx": 0, "bbox": [400, 400, 400, 400]}],
            figures=[owned(BOX), dict(owned([400, 400, 400, 400]))],
        )
        alone = mock.Mock(side_effect=[{1, 2}, {1}])
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value=set()),
            mock.patch.object(pipeline.readers, "confirm_all_handwritten", alone),
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 1)
        self.assertEqual(question.figures, [dict(owned([400, 400, 400, 400]))])

    def test_the_stem_travels_with_the_question(self):
        self.question()
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value={1}) as asked,
        ):
            pipeline.verify_geometric_figures(self.paper, revision=1)
        self.assertEqual(asked.call_count, 1)
        self.assertIn(CUED, asked.call_args.args[2])

    def test_no_reading_service_means_no_questions_are_asked(self):
        question = self.question()
        with (
            mock.patch.object(pipeline.readers, "primary_engine", return_value=None),
            mock.patch.object(pipeline.readers, "verify_figure_ownership") as asked,
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        question.refresh_from_db()
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 0)


class AutomaticFigureGateTests(TestCase):
    """识读自己认的配图也得过一次手写核验。

    几何闸门原来只看 other/row/shared，auto 里的图一次都没被看过。真卷实测五份、
    93 张配图：挂在 auto 上的手写有六张（胜利初一第 12、21、25 题，胜利初二第 8、
    24 题，凤城高一第 5、15 题）。其中胜利初一第 21 题是本地兜底干的——题干写了
    「如图」、识读说找不到图，剩下那一个候选框就被直接算成题图，整条 726×95 的铅笔
    演算贴到了一个三角形题上，中间没有一次模型判断。
    """

    def setUp(self):
        self.paper = Paper.objects.create(
            filename="fixture.pdf",
            kind="pdf",
            sha256="f" * 64,
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
            status="ready",
            processing_plan={"revision": 1},
        )
        self.group = QuestionGroup.objects.create(paper=self.paper, title="卷一", kind="exam", sequence=0)

    def question(self, **extra):
        values = {
            "paper": self.paper,
            "group": self.group,
            "number": 21,
            "stem": CUED,
            "question_type": "free_response",
            "state": "yellow",
            "regions": [REGION],
            "figure_candidates": [candidate(1)],
            "figures": [{"page_idx": 0, "bbox": list(BOX), "slot": "stem", "source": "auto"}],
            "figure_review": {"status": "ok", "source": "automatic"},
            "flags": [],
        }
        values.update(extra)
        return Question.objects.create(**values)

    def run_gate(self, answer, confirm, question=None):
        store = mock.Mock()
        store.load.return_value = Image.new("RGB", (1000, 1000), "white")
        with (
            mock.patch.object(pipeline, "PageStore", return_value=store),
            mock.patch.object(pipeline.readers, "primary_engine", return_value=ENGINE),
            mock.patch.object(pipeline.readers, "verify_figure_ownership", return_value=answer),
            mock.patch.object(pipeline.readers, "confirm_all_handwritten", return_value=confirm) as asked,
        ):
            changed = pipeline.verify_geometric_figures(self.paper, revision=1)
        if question is not None:
            question.refresh_from_db()
        return changed, asked

    def test_the_readers_own_picture_is_put_to_the_reader_too(self):
        # 识读直接认的图没有「请核对」的旗标，以前这道闸门根本走不到它。
        question = self.question()
        changed, asked = self.run_gate({1}, set(), question)
        self.assertEqual(changed, 0)
        self.assertEqual(asked.call_count, 0)
        self.assertEqual(len(question.figures), 1)

    def test_two_calls_agreeing_point_at_a_readers_own_scribble(self):
        # 胜利初一第 21 题那条铅笔演算：两问都指向整幅手写。这条路线的准确率
        # 实测 14 次命中 11 真 3 误，撑不起撤图这种破坏性动作，所以只标不动。
        question = self.question()
        changed, asked = self.run_gate(set(), {1}, question)
        self.assertEqual(changed, 1)
        self.assertEqual(asked.call_count, 2)
        self.assertEqual(len(question.figures), 1)
        self.assertIn(figure_policy.FLAG_DOUBTFUL_FIGURE, question.flags)
        self.assertEqual(question.state, Question.State.YELLOW)

    def test_a_printed_picture_the_reader_named_is_never_removed(self):
        # 撤错了得靠旗标捞回来这条规矩，是 auto 只标不撤的全部理由：一旦这张图
        # 其实是印好的，题上不能少东西，只能多一句提醒。
        question = self.question()
        self.run_gate(set(), {1}, question)
        self.assertNotIn(figure_policy.FLAG_WITHDRAWN_FIGURE, question.flags)
        self.assertNotIn(figure_policy.FLAG_NO_FIGURE, question.flags)
        self.assertEqual(len(question.figures), 1)

    def test_the_suspicion_is_not_stamped_twice_on_one_card(self):
        question = self.question()
        self.run_gate(set(), {1}, question)
        first = list(question.flags)
        self.run_gate(set(), {1}, question)
        self.assertEqual(question.flags, first)
        self.assertEqual(list(question.flags).count(figure_policy.FLAG_DOUBTFUL_FIGURE), 1)

    def test_an_option_picture_is_looked_at_but_never_taken_off(self):
        # 凤城高一第 10 题那两张手写草稿是识读自己认成选项 B、D 的。撤错了整道选择题
        # 就做不出来，所以选项图只说一声、不动——但要说，否则这两张就一直混在题里。
        question = self.question(
            question_type="single_choice",
            stem="下列函数中，图象经过点 A 的是（ ）",
            options={"A": "y=x", "B": "y=x^2"},
            figures=[{"page_idx": 0, "bbox": list(BOX), "slot": "B", "source": "auto"}],
        )
        changed, asked = self.run_gate(set(), {1}, question)
        self.assertEqual(changed, 1)
        self.assertGreaterEqual(asked.call_count, 1)
        self.assertEqual(len(question.figures), 1)
        self.assertIn(figure_policy.FLAG_DOUBTFUL_FIGURE, question.flags)
        self.assertNotIn(figure_policy.FLAG_WITHDRAWN_FIGURE, question.flags)

    def test_switching_to_auto_withdrawal_still_spares_the_option_slot(self):
        # 开关翻到自动撤那一侧，题干图会撤，选项图照样只标——选择题做不出来是硬伤。
        question = self.question(
            question_type="single_choice",
            stem="下列函数中，图象经过点 A 的是（ ）",
            options={"A": "y=x", "B": "y=x^2"},
            figures=[{"page_idx": 0, "bbox": list(BOX), "slot": "B", "source": "auto"}],
        )
        with mock.patch.object(pipeline, "AUTO_FIGURES_ARE_WITHDRAWN", True):
            changed, _asked = self.run_gate(set(), {1}, question)
        self.assertEqual(changed, 1)
        self.assertEqual(len(question.figures), 1)
        self.assertIn(figure_policy.FLAG_DOUBTFUL_FIGURE, question.flags)
        self.assertNotIn(figure_policy.FLAG_WITHDRAWN_FIGURE, question.flags)
