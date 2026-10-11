# -*- coding: utf-8 -*-
r"""`manage.py reconcile_flags` 这条独立入口的守门测试。

「重新切题」跑完末尾会顺带补四条兜底提示（几何挂图落在范围外、卡号那一行认不出、
题型标成填空题却没有空格、小问排成两栏只读到左栏）。这些判据只跟卡上已有的范围、
题型、旗标有关，跟重切没有半点关系，却只能等一次全量重切才补得上——一次几分钟到十几分钟，
还要重新读题。

真库实测（2026-11-04，959 张）：三条一起跑 0.7 秒、零模型调用，84 张挂上新提示、
绿卡 720 → 667，题干一个字没改、已通过的一张没掉。

这里守三件事：

1. ``--list`` 只是预览，**一个字都不许写进库**；
2. 清完再跑一次必须是 0，否则「命中 22 张」是在报一件没发生的事；
3. ``--only`` 真的只跑指定那几条，未知名字要报错。

第五条（caption）额外要一页真有文字层的原书 PDF：它得对着原书页面上那一行
「图 4-1」核「编号被切在框外了没有」，光看卡上的框是判不出来的。
"""
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase

from . import figure_policy, pipeline, qtypes
from .models import Block, Paper, Question, QuestionGroup

PAGE_WIDTH, PAGE_HEIGHT = 533.0, 754.0
# 「图 N-M」那行在纸面上的位置，跟真教材一样：印在图的下面，框底再往下差几 pt。
CAPTION_BASELINE_Y = 660.0


def normalized_box(x0, y0, x1, y1):
    return [x0 * 1000 / PAGE_WIDTH, y0 * 1000 / PAGE_HEIGHT,
            x1 * 1000 / PAGE_WIDTH, y1 * 1000 / PAGE_HEIGHT]


def caption_line_pdf(path: Path) -> None:
    """一页真的印着「图 N-M」编号的原书页。

    第 0 页印「图 4-1」，第 1 页左右各印一行「图 4-2」「图 4-3」——
    后者用来验「一题点名两张图，两个编号都被切在框外」。
    第 2 页印一句含编号的正文和一个离得很远的编号，是两个「不许报」的反例。
    """
    import pymupdf as fitz

    with fitz.open() as pdf:
        for index in range(3):
            page = pdf.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            if index == 2:
                page.insert_text((60, CAPTION_BASELINE_Y), "例 1  确定图 9-9 中路灯灯泡所在的位置。",
                                 fontname="china-s", fontsize=11)
                page.insert_text((200, 660), "图 9-7", fontname="china-s", fontsize=11)
                continue
            page.insert_text((200, CAPTION_BASELINE_Y), "图 4-1" if not index else "图 4-2",
                             fontname="china-s", fontsize=11)
            if index:
                page.insert_text((350, CAPTION_BASELINE_Y), "图 4-3",
                                 fontname="china-s", fontsize=11)
        pdf.save(path)


class ReconcileFlagsCommandTests(TestCase):
    """五条卡各踩一条判据（另有两张是正常题），加命令外壳。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        source = Path(self.directory.name) / "教材.pdf"
        caption_line_pdf(source)
        self.paper = Paper.objects.create(
            filename="月考.pdf", kind="pdf", material_type=Paper.MaterialType.EXAM,
            sha256="a" * 64, source_path=str(source),
            pages=[{"page_idx": index, "width": PAGE_WIDTH, "height": PAGE_HEIGHT}
                   for index in range(3)],
            status=Paper.Status.READY)
        self.group = QuestionGroup.objects.create(
            paper=self.paper, title="第一章", sequence=1, page_start=1, page_end=2,
            metadata={"pages": [0, 1]})
        # 纸上印的是这一行；认出它的卡会在「卡号那一行认不出」那条上中招。
        Block.objects.create(paper=self.paper, seq=10, type="text", page_idx=1,
                             bbox=[80, 500, 920, 545],
                             text="1. 已知矩形面积为 9，求它的对角线长。")
        self.counter = 0

        self.geometric = self.card(
            stem="如图，△ABC 的三个顶点分别为 A（1，2），求 $y=\\dfrac{k}{x}$ 的图象。",
            regions=[{"page_idx": 1, "bbox": [83, 505, 899, 535]}],
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [747, 628, 853, 756],
                      "source": "other"}])
        self.anchor = self.card(
            stem="某果园有苹果树 $x$ 棵，梨树 $y$ 棵，两种树共 120 棵。",
            regions=[{"page_idx": 1, "bbox": [40, 330, 490, 420]}],
            source_anchor_seq=10)
        # 题型标成填空题，成品卷却只给解答题留书写空间——学生在卷上没地方写过程。
        self.no_slot = self.card(
            stem="一个矩形的两条对角线的夹角为 $60^\\circ$，对角线长为 $15$，求矩形较短边的长。",
            regions=[{"page_idx": 1, "bbox": [40, 90, 490, 320]}], question_type="fill_blank")
        self.real_blank = self.card(
            stem="已知 $x+1=2$，则 $x=$ ______。",
            regions=[{"page_idx": 1, "bbox": [40, 430, 490, 480]}], question_type="fill_blank")
        # 教材把一道题的小问排成左右两栏，切题只罩到左栏，右栏那几条在成品卷上没了。
        self.two_column = self.card(
            stem="解下列方程：\n（1）$5(x^2-x)=3(x^2+x)$；\n（3）$(x-2)(x-3)=12$；\n（5）$2y^2+4y=y+2$。",
            regions=[{"page_idx": 1, "bbox": [40, 500, 300, 820]}])
        # 同样从（1）跳到（3），但范围是满宽的：#1152 是被切成了两张卡，
        # （2）在隔壁卡里，内容并没有丢。判据不许把它也挂上提示。
        self.wide_gap = self.card(
            stem="解下列方程：\n（1）$x(x-14)=0$；\n（3）$x^2=x+56$。",
            regions=[{"page_idx": 1, "bbox": [40, 850, 900, 960]}])

        # 教材在图的正下方印一行「图 4-1」，切图只圈了图形本身，编号落在框外 9pt。
        self.cut_caption = self.card(
            stem="例 1 确定图 4-1 中路灯灯泡所在的位置。",
            regions=[{"page_idx": 0, "bbox": [40, 90, 490, 320]}],
            figures=[{"slot": "stem", "page_idx": 0, "source": "auto",
                      "bbox": normalized_box(180, 400, 360, 640)}])
        # 同一张图，框往下拉了 35pt 把编号一起圈进来：这种情况不许报。
        self.kept_caption = self.card(
            stem="例 2 确定图 4-1 中路灯灯泡所在的位置。",
            regions=[{"page_idx": 0, "bbox": [40, 330, 490, 560]}],
            figures=[{"slot": "stem", "page_idx": 0, "source": "auto",
                      "bbox": normalized_box(180, 400, 360, 675)}])
        # 题干点名的是这一页上没有的编号（真库里 #1022 就是这样）：
        # 原书上根本没有这一行，不是被切掉的。
        self.absent_caption = self.card(
            stem="如图 9-16，D，E 分别是 △ABC 的边 AB，AC 上的点。",
            regions=[{"page_idx": 1, "bbox": [40, 90, 490, 320]}],
            figures=[{"slot": "stem", "page_idx": 1, "source": "auto",
                      "bbox": normalized_box(180, 400, 360, 640)}])
        # 一题点名两张图、两个编号都被切在框外（真库里的 #1301）：
        # 印出来是两张没有编号的图，学生分不清哪张是哪张。
        self.two_captions = self.card(
            stem="（1）描点：如图 4-2 所示。\n（2）连线：如图 4-3 所示。",
            regions=[{"page_idx": 1, "bbox": [40, 90, 490, 320]}],
            figures=[{"slot": "stem", "page_idx": 1, "source": "auto",
                      "bbox": normalized_box(175, 400, 265, 640)},
                     {"slot": "stem", "page_idx": 1, "source": "auto",
                      "bbox": normalized_box(325, 400, 415, 640)}])
        # 编号出现在一句正文里（「例 1 确定图 9-9 中…」），而且那一句就印在框底
        # 下方 11pt——位置完全对得上，但那不是图的编号，是题干自己在指路。
        # 认成编号就会把一张好好的图也挂上提示。
        self.sentence_caption = self.card(
            stem="例 2 确定图 9-9 中路灯灯泡所在的位置。",
            regions=[{"page_idx": 2, "bbox": [40, 90, 490, 320]}],
            figures=[{"slot": "stem", "page_idx": 2, "source": "auto",
                      "bbox": normalized_box(180, 400, 360, 640)}])
        # 原书上确实印着「图 9-7」，但在框底下方 169pt——那已经是别处的图了。
        # 真库里 #1187 的「图 2-10」就在 203pt 之外。认成同一张图的编号就是冤枉人。
        self.far_caption = self.card(
            stem="如图 9-7 所示，求这个三角形的面积。",
            regions=[{"page_idx": 2, "bbox": [40, 330, 490, 560]}],
            figures=[{"slot": "stem", "page_idx": 2, "source": "auto",
                      "bbox": normalized_box(180, 400, 360, 480)}])
        # 右边那张图下面印的是「图 4-3」，左边这张底下什么都没有。
        # 只看「框底下方几 pt」不看左右，就把右边图的编号算到左边图头上了。
        self.wrong_side = self.card(
            stem="如图 4-3 所示，点 P 是中点。",
            regions=[{"page_idx": 1, "bbox": [40, 620, 490, 900]}],
            figures=[{"slot": "stem", "page_idx": 1, "source": "auto",
                      "bbox": normalized_box(175, 400, 265, 640)}])

    def card(self, **values):
        self.counter += 1
        base = dict(
            paper=self.paper, group=self.group, number=self.counter,
            processing_mode="auto", body_mode="text",
            state=Question.State.GREEN, flags=[], figures=[], figure_review={})
        base.update(values)
        return Question.objects.create(**base)

    def run_command(self, *args):
        out = StringIO()
        call_command("reconcile_flags", *args, stdout=out)
        return out.getvalue()

    def stored(self):
        """从库里重新读。手里那个 Question 是建卡时的旧对象，不会自己变。"""
        return {q.pk: (q.state, list(q.flags or []))
                for q in Question.objects.order_by("pk")}

    # -- 五条判据的接线 ---------------------------------------------------

    def test_each_of_the_five_reaches_its_own_card(self):
        self.assertEqual(pipeline.reconcile_geometric_figure_flags(self.paper), 1)
        self.assertEqual(pipeline.reconcile_anchor_reading_flags(self.paper), 1)
        self.assertEqual(pipeline.reconcile_fill_blank_slot_flags(self.paper), 1)
        self.assertEqual(pipeline.reconcile_subitem_column_flags(self.paper), 1)
        self.assertEqual(pipeline.reconcile_figure_caption_flags(self.paper), 2)

        rows = self.stored()
        self.assertIn(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE, rows[self.geometric.pk][1])
        self.assertTrue(any(str(f).startswith(pipeline.ANCHOR_MISMATCH_FLAG_PREFIX)
                            for f in rows[self.anchor.pk][1]),
                        rows[self.anchor.pk][1])
        self.assertIn(qtypes.FLAG_FILL_NO_SLOT, rows[self.no_slot.pk][1])
        flag = next(f for f in rows[self.two_column.pk][1]
                    if str(f).startswith(pipeline.SUBITEM_GAP_PREFIX))
        self.assertIn("（2）、（4）没有读出来", flag)
        self.assertIn("左右两栏", flag)

    # -- 第五条：图号被切在配图框外 ----------------------------------------

    def caption_flags(self, card):
        return [f for f in Question.objects.get(pk=card.pk).flags
                if str(f).startswith(figure_policy.FIGURE_CAPTION_PREFIX)]

    def test_a_caption_below_the_frame_is_named_in_the_flag(self):
        self.assertEqual(pipeline.reconcile_figure_caption_flags(self.paper), 2)

        flags = self.caption_flags(self.cut_caption)
        self.assertEqual(len(flags), 1)
        self.assertIn("图 4-1", flags[0])
        self.assertIn("往下拉", flags[0])

    def test_a_caption_inside_the_frame_is_left_alone(self):
        """框本来就圈住了编号，纸面上看得见，不该报。"""
        self.run_command()

        self.assertEqual(self.caption_flags(self.kept_caption), [])
        self.assertEqual(self.stored()[self.kept_caption.pk], (Question.State.GREEN, []))

    def test_a_number_the_page_never_printed_is_left_alone(self):
        """原书上根本没有那一行编号，就不是被切掉的。真库里 #1022 就是这样。"""
        self.run_command()

        self.assertEqual(self.caption_flags(self.absent_caption), [])
        self.assertEqual(self.stored()[self.absent_caption.pk], (Question.State.GREEN, []))

    def test_a_stem_without_any_figure_number_is_left_alone(self):
        """纯「如图」没有编号可丢。试卷上全是这种。"""
        self.assertEqual(
            pipeline._figure_number_keys("如图，△ABC 的三个顶点分别为 A（1，2）。"), set())
        self.assertEqual(pipeline._figure_number_keys("如图所示，判断正误。"), set())

    def test_a_number_written_inside_a_sentence_is_not_a_caption(self):
        """「例 1 确定图 9-9 中…」那一行位置完全对得上，但它不是图的编号。"""
        self.run_command()

        self.assertEqual(self.caption_flags(self.sentence_caption), [])
        self.assertEqual(self.stored()[self.sentence_caption.pk], (Question.State.GREEN, []))

    def test_a_number_far_below_the_frame_is_someone_elses_caption(self):
        """原书上印着「图 9-7」，但在框底下方 169pt——那是别处的图。"""
        self.run_command()

        self.assertEqual(self.caption_flags(self.far_caption), [])
        self.assertEqual(self.stored()[self.far_caption.pk], (Question.State.GREEN, []))

    def test_a_caption_below_a_different_figure_on_the_same_row_is_not_ours(self):
        """教材上两张图并排，各自在下面印自己的编号。只看上下位置不看左右就串了。"""
        self.run_command()

        self.assertEqual(self.caption_flags(self.wrong_side), [])
        self.assertEqual(self.stored()[self.wrong_side.pk], (Question.State.GREEN, []))

    def test_only_a_card_naming_several_lost_figures_goes_yellow(self):
        """一题点名两张图、学生分不清时才值得回头看；单张图只是少个标签。"""
        self.run_command()

        self.assertEqual(self.stored()[self.two_captions.pk][0], Question.State.YELLOW)
        self.assertEqual(self.stored()[self.cut_caption.pk][0], Question.State.GREEN)

    def test_every_lost_number_of_one_card_is_listed(self):
        """题干先点名「图 4-1」而那一张没丢，不代表后面几号也没丢。"""
        flags = self.caption_flags(self.two_captions)
        self.run_command()
        flags = self.caption_flags(self.two_captions)

        self.assertEqual(len(flags), 1)
        self.assertIn("图 4-2、图 4-3", flags[0])

    def test_the_caption_hint_is_never_written_twice(self):
        """它的文案里带着缺哪几个编号，每次算出来可能不一样，只能靠前缀认。"""
        self.run_command()
        self.run_command()

        self.assertEqual(len(self.caption_flags(self.cut_caption)), 1)
        self.assertEqual(len(self.caption_flags(self.two_captions)), 1)

    def test_a_source_file_that_is_gone_is_skipped_not_raised(self):
        self.paper.source_path = str(Path(self.directory.name) / "不在了.pdf")
        self.paper.save()

        self.assertEqual(pipeline.reconcile_figure_caption_flags(self.paper), 0)

    def test_a_full_width_gap_is_left_alone(self):
        """小问跳号但范围是满宽的：#1152 那种被切成两张卡的情况，内容并没有丢。"""
        self.assertEqual(pipeline.reconcile_subitem_column_flags(self.paper), 1)

        self.assertEqual(self.stored()[self.wide_gap.pk], (Question.State.GREEN, []))

    def test_only_a_number_at_the_start_of_a_line_counts_as_a_subitem(self):
        """行中间的「（2）」不算小问编号——$y_1$、$y_2$ 这类下标满篇都是。"""
        self.assertEqual(
            pipeline._missing_subitem_numbers(
                "已知点（$-6$，$y_1$）在（1）号题里；（2）不算。\n（1）$x$；\n（3）$y$。"),
            [2])
        self.assertEqual(
            pipeline._missing_subitem_numbers("（1）$x$；\n（2）$y$；\n（3）$z$。"), [])
        # 只有一个子项时不判——没有「跳号」可言。
        self.assertEqual(pipeline._missing_subitem_numbers("（1）$x$；"), [])

    def test_a_card_that_breaks_none_of_them_is_left_green(self):
        self.run_command()

        self.assertEqual(self.stored()[self.real_blank.pk],
                         (Question.State.GREEN, []))

    # -- 1) --list 不许写库 ----------------------------------------------

    def test_list_only_writes_nothing_at_all(self):
        before = self.stored()

        self.run_command("--list")

        self.assertEqual(self.stored(), before)

    def test_list_only_counts_the_same_cards_a_real_run_would(self):
        preview = self.run_command("--list")

        self.assertIn("几何挂图落在本题范围外：命中 1 张", preview)
        self.assertIn("卡号那一行认不出：命中 1 张", preview)
        self.assertIn("题型标成填空题却没有空格：命中 1 张", preview)
        self.assertIn("小问排成两栏、只读到了左栏：命中 1 张", preview)
        self.assertIn("题干点名的图号被切在配图框外：命中 2 张", preview)

    def test_list_only_says_the_library_was_left_alone(self):
        self.assertIn("库没有改动", self.run_command("--list"))

    # -- 2) 跑第二遍必须是 0 ----------------------------------------------

    def test_a_second_run_reports_nothing_left_to_do(self):
        self.run_command()

        second = self.run_command("--list")

        self.assertIn("几何挂图落在本题范围外：命中 0 张", second)
        self.assertIn("卡号那一行认不出：命中 0 张", second)
        self.assertIn("题型标成填空题却没有空格：命中 0 张", second)
        self.assertIn("小问排成两栏、只读到了左栏：命中 0 张", second)
        self.assertIn("题干点名的图号被切在配图框外：命中 0 张", second)

    def test_a_flag_is_never_written_twice(self):
        self.run_command()
        self.run_command()

        card = Question.objects.get(pk=self.geometric.pk)
        self.assertEqual(card.flags.count(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE), 1)

    def test_the_two_column_hint_is_never_written_twice(self):
        """它的文案里带着缺哪几个小问，每次算出来都不一样，只能靠前缀认。"""
        self.run_command()
        self.run_command()

        card = Question.objects.get(pk=self.two_column.pk)
        self.assertEqual(
            len([f for f in card.flags if str(f).startswith(pipeline.SUBITEM_GAP_PREFIX)]), 1)

    # -- 3) --only / --paper / 报错 ---------------------------------------

    def test_only_skips_the_check_it_was_not_asked_for(self):
        self.run_command("--only", "fill_blank")

        rows = self.stored()
        self.assertIn(qtypes.FLAG_FILL_NO_SLOT, rows[self.no_slot.pk][1])
        self.assertNotIn(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE,
                         rows[self.geometric.pk][1])
        self.assertEqual(rows[self.geometric.pk][0], Question.State.GREEN)

    def test_a_name_that_does_not_exist_is_refused(self):
        with self.assertRaisesMessage(CommandError, "不知道这几条检查：nope"):
            self.run_command("--only", "nope")

    def test_a_file_name_that_does_not_exist_is_refused(self):
        with self.assertRaisesMessage(CommandError, "找不到这些资料"):
            self.run_command("--paper", "没有这份卷子.pdf")

    def test_a_real_run_reports_how_many_cards_were_recoloured(self):
        self.assertIn("绿卡 13 → 8", self.run_command())