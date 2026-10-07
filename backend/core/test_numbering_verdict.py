"""Deciding whether printed numbers open questions or only title sections.

A 讲义 and a 试卷 look identical to the cutter: a printed number at the start of
a line.  Cutting a 讲义's section titles as questions hands the user a pile of
unreadable "cards" with nothing anywhere saying the file contained no questions
at all.  These fixtures pin the rule that tells the two apart, and — just as
important — pin that a real exam is never mistaken for a handout.
"""
from __future__ import annotations

from django.test import SimpleTestCase, TestCase
from PIL import Image

from . import pipeline, segment
from .models import Block, Paper, Question


def block(seq: int, text: str, *, page_idx: int = 0, top: float | None = None) -> dict:
    top = float(seq * 100) if top is None else top
    return {"seq": seq, "type": "text", "page_idx": page_idx, "bbox": [80.0, top, 920.0, top + 40.0],
            "text": text}


def starts_for(blocks: list[dict], numbers: list[int]) -> list[segment.Start]:
    return [segment.Start(number=number, page=0, x=80.0, y=float(block["seq"]) * 100.0,
                          seq=block["seq"], at_start=True, score=4.0)
            for number, block in zip(numbers, blocks)]


HANDOUT = [
    block(0, "1、棱柱的结构特征"),
    block(1, "1.1 棱柱的定义：有两个面互相平行，其余各面都是四边形的几何体叫做棱柱"),
    block(2, "2、棱锥的结构特征"),
    block(3, "2.1 正棱锥的定义：底面是正多边形，顶点在底面正多边形中心的棱锥"),
    block(4, "3、棱台的结构特征"),
    block(5, "3.1 棱台的定义：用一个平行于底面的平面去截棱锥，截面和底面之间的部分"),
]
# The 1.1 / 2.1 decimal sub-headings are not starts: the chain runs on the
# top-level numbers, so the numbered lines are the three section titles.
HANDOUT_STARTS = [HANDOUT[0], HANDOUT[2], HANDOUT[4]]

# The wording that defeats a keyword rule: “面积计算” contains 计算, exactly like
# “计算下列各式”, yet the line is a topic title and carries no sentence mark.
NOTES_WITH_VERB = [
    block(0, "1、多面体和旋转体定义"),
    block(1, "2、常见平面图形面积计算"),
    block(2, "3、多面体其他性质"),
    block(3, "4、表面积与体积计算"),
    block(4, "5、空间点、直线、平面"),
]

EXAM = [
    block(0, "1. 下列各组数中，是勾股数的是（ ）"),
    block(1, "2. 如图，所有三角形都是直角三角形，所有四边形都是正方形，已知 $S_1=4$"),
    block(2, "3. 在 $\\sqrt{3}$，$-\\frac{3}{4}$，$\\pi$ 中，无理数的个数是（ ）个"),
    block(3, "4. 已知 $\\triangle ABC$ 的三边长分别为 a，b，c，选择下列条件中的一个"),
    block(4, "5. 下列说法：①有理数和数轴上的点一一对应，其中正确的是（ ）"),
]


class NumberingVerdictTests(SimpleTestCase):
    def test_a_handout_of_numbered_topics_is_recognised(self):
        verdict = segment.classify_numbering(HANDOUT, starts_for(HANDOUT_STARTS, [1, 2, 3]))
        self.assertEqual(verdict.verdict, "topics")
        self.assertEqual(verdict.numbered, 3)
        self.assertEqual(verdict.topic_like, 3)
        self.assertEqual(verdict.stem_like, 0)

    def test_the_message_names_what_was_actually_found(self):
        verdict = segment.classify_numbering(HANDOUT, starts_for(HANDOUT_STARTS, [1, 2, 3]))
        message = verdict.message
        self.assertIn("棱柱的结构特征", message)
        self.assertIn("不是试卷", message)
        self.assertIn("书籍", message)
        self.assertIn("3", message)

    def test_a_topic_title_holding_a_verb_is_still_a_topic(self):
        verdict = segment.classify_numbering(NOTES_WITH_VERB, starts_for(NOTES_WITH_VERB, [1, 2, 3, 4, 5]))
        self.assertEqual(verdict.verdict, "topics")
        self.assertIn("常见平面图形面积计算", verdict.samples)

    def test_a_real_exam_is_never_mistaken_for_a_handout(self):
        verdict = segment.classify_numbering(EXAM, starts_for(EXAM, [1, 2, 3, 4, 5]))
        self.assertEqual(verdict.verdict, "exam")
        self.assertEqual(verdict.stem_like, 5)

    def test_one_question_is_enough_to_keep_cutting_the_whole_paper(self):
        # A handout that happens to contain a single exercise must not be
        # refused: the cost of a false refusal (throwing away real questions)
        # is far higher than the cost of a missed handout.
        mixed = HANDOUT + [block(6, "4. 求下列各式的值：（ ）")]
        verdict = segment.classify_numbering(mixed, starts_for(mixed + [block(6, "4. 求下列各式的值：（ ）")], [1, 2, 3, 4]))
        self.assertEqual(verdict.verdict, "exam")

    def test_too_few_numbered_lines_is_not_enough_evidence(self):
        verdict = segment.classify_numbering(HANDOUT[:2], starts_for(HANDOUT[:2], [1, 2]))
        self.assertEqual(verdict.verdict, "exam")

    def test_a_title_with_a_comma_counts_as_a_sentence_not_a_topic(self):
        # “计算下列各式, 并化简” is a question even though 计算 is a topic word.
        blocks = [block(index, text) for index, text in enumerate([
            "1. 棱柱的结构特征",
            "2. 计算下列各式，并化简：（ ）",
            "3. 棱锥的结构特征",
            "4. 求证：（ ）",
        ])]
        verdict = segment.classify_numbering(blocks, starts_for(blocks, [1, 2, 3, 4]))
        self.assertEqual(verdict.verdict, "exam")

    def test_the_rule_reads_the_start_line_not_the_whole_block(self):
        # MinerU often packs a question and the next section title into one box.
        # Only the start's own line may decide.
        packed = [
            block(0, "1. 如图，已知 AB=AC，\n2、棱台的结构特征\n3、圆台的结构特征"),
            block(1, "2、圆锥的结构特征"),
            block(2, "3、圆柱的结构特征"),
        ]
        starts = [
            segment.Start(number=1, page=0, x=80.0, y=0.0, seq=0, at_start=True, score=4.0),
            segment.Start(number=2, page=0, x=80.0, y=100.0, seq=1, at_start=True, score=4.0),
            segment.Start(number=3, page=0, x=80.0, y=200.0, seq=2, at_start=True, score=4.0),
        ]
        verdict = segment.classify_numbering(packed, starts)
        self.assertEqual(verdict.verdict, "topics")
        self.assertNotIn("如图", verdict.samples)

    def test_a_terse_fill_in_exam_is_never_refused(self):
        # “化简 √8”“计算下列各式的值” have no comma, no blank and no question
        # mark.  A rule that only looks for sentence punctuation throws this
        # real exam away; the imperative verb at the start of the line is what
        # saves it.  The cost of getting this wrong is the whole paper.
        terse = [block(index, text) for index, text in enumerate([
            "1. 化简 $\\sqrt{8}$",
            "2. 计算下列各式的值",
            "3. 解方程：$x^2=4$",
            "4. 写出下列各数的相反数",
            "5. 证明：三角形的外角大于不相邻的内角",
        ])]
        verdict = segment.classify_numbering(terse, starts_for(terse, [1, 2, 3, 4, 5]))
        self.assertEqual(verdict.verdict, "exam")
        self.assertEqual(verdict.stem_like, 5)

    def test_a_paper_of_bare_topic_names_is_read_as_topics_on_purpose(self):
        # “1. 平行四边形的性质”“3. 圆锥的结构特征” — nothing here separates this
        # from a handout, and refusing it is the safe direction.  The costs are
        # not symmetric: a missed handout leaves the user looking at section
        # cards they can recognise as not questions, while a refused exam loses
        # every question in it.  So this paper is deliberately refused.
        terse = [block(index, text) for index, text in enumerate([
            "1. 平行四边形的性质",
            "2. 三角形的内角和",
            "3. 圆锥的结构特征",
            "4. 圆柱的结构特征",
        ])]
        verdict = segment.classify_numbering(terse, starts_for(terse, [1, 2, 3, 4]))
        self.assertEqual(verdict.verdict, "topics")

    def test_a_handful_of_topic_like_titles_does_not_condemn_a_real_exam(self):
        # Two headings in a paper of twenty-five real questions must not tip it.
        blocks = EXAM + [
            block(index + 5, text) for index, text in enumerate([
                "6. 多边形的性质",
                "7. 三角形的分类",
            ])
        ]
        verdict = segment.classify_numbering(blocks, starts_for(blocks, list(range(1, 8))))
        self.assertEqual(verdict.verdict, "exam")
        self.assertEqual(verdict.stem_like, 5)

    def test_inline_tags_do_not_hide_the_punctuation(self):
        blocks = [block(index, text) for index, text in enumerate([
            "<sub>1.</sub> <sub>棱柱的结构特征</sub>",
            "<sub>2.</sub> <sub>棱锥的结构特征</sub>",
            "<sub>3.</sub> <sub>下列各式中正确的是（ ）</sub>",
        ])]
        verdict = segment.classify_numbering(blocks, starts_for(blocks, [1, 2, 3]))
        self.assertEqual(verdict.verdict, "topics")
        self.assertEqual(verdict.stem_like, 1)


class PipelineWiringTests(TestCase):
    """The verdict is only useful if the real cut path honours it.

    A verdict nothing consults is a comment.  These go through
    ``_collect_segmentation_items`` — the function the worker actually calls.
    """

    def _paper(self, blocks: list[dict]) -> Paper:
        paper = Paper.objects.create(filename="讲义.pdf", kind="pdf", sha256="a" * 64,
                                     pages=[{"page_idx": 0, "mode": "mineru"}])
        for item in blocks:
            Block.objects.create(
                paper=paper, seq=item["seq"], type=item["type"], page_idx=item["page_idx"],
                bbox=item["bbox"], text=item["text"],
            )
        return paper

    class _BlankPages:
        """A blank sheet: a cut that consults the page finds no ink and keeps itself."""

        def load(self, page_idx: int):
            return Image.new("RGB", (1240, 878), "white")

    def test_a_handout_stops_the_cut_instead_of_emitting_section_cards(self):
        paper = self._paper(HANDOUT)
        groups = pipeline._ensure_question_groups(paper)
        with self.assertRaises(RuntimeError) as caught:
            pipeline._collect_segmentation_items(
                paper, list(groups), locate_gaps=False, page_store=self._BlankPages())
        self.assertIn("不是试卷", str(caught.exception))
        self.assertEqual(Question.objects.filter(paper=paper).count(), 0)

    def test_a_real_exam_still_produces_every_question(self):
        paper = self._paper(EXAM)
        groups = pipeline._ensure_question_groups(paper)
        _blocks, questions, _notes, _diagnostics = pipeline._collect_segmentation_items(
            paper, list(groups), locate_gaps=False, page_store=self._BlankPages())
        self.assertEqual([item["number"] for item in questions], [1, 2, 3, 4, 5])
        self.assertTrue(all(item["regions"] for item in questions))

    def test_fewer_questions_than_pages_is_reported_not_swallowed(self):
        # One page, one question: legal, but the user should hear about it.  This
        # is the shape a 讲义 or an answer sheet takes when it is routed to the
        # exam cutter, and it is the only signal left for material shapes the
        # numbering verdict cannot name.
        paper = self._paper([block(index, text) for index, text in enumerate([
            "1. 如图，AB=AC（ ）",
            "2. 下列说法正确的是（ ）",
        ])])
        paper.pages = [{"page_idx": index, "mode": "mineru"} for index in range(3)]
        paper.save(update_fields=["pages"])
        groups = pipeline._ensure_question_groups(paper)
        for group in groups:
            group.page_start, group.page_end = 1, 3
            group.metadata = {"pages": [0, 1, 2]}
            group.save(update_fields=["page_start", "page_end", "metadata"])
        _blocks, questions, notes, _diagnostics = pipeline._collect_segmentation_items(
            paper, list(groups), locate_gaps=False, page_store=self._BlankPages())
        self.assertEqual(len(questions), 2)
        self.assertTrue(any("题数少于页数" in note for note in notes), notes)

    def test_a_dense_exam_raises_no_such_note(self):
        paper = self._paper(EXAM)
        groups = pipeline._ensure_question_groups(paper)
        _blocks, _questions, notes, _diagnostics = pipeline._collect_segmentation_items(
            paper, list(groups), locate_gaps=False, page_store=self._BlankPages())
        self.assertFalse([note for note in notes if "题数少于页数" in note], notes)
