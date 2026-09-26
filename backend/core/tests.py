"""离线测试：虚构原卷 + 模拟模型输出，不产生任何付费调用。"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, override_settings
from PIL import Image, ImageDraw

from . import library, pipeline, readers, segment
from .models import Block, Paper, PublishedQuestion, Question
from .textnorm import canon, clean_stem, same_reading


def block(seq, page, bbox, text, kind="text"):
    return {"seq": seq, "type": kind, "page_idx": page, "bbox": bbox, "text": text}


PAGES = [{"page_idx": 0, "width": 842, "height": 595}, {"page_idx": 1, "width": 842, "height": 595}]


def two_column_paper():
    """两栏试卷：第 3 题从左栏底部跨到右栏顶部，第 4 题开头被 MinerU 误读成"4"以外的样子。"""
    return [
        block(0, 0, [60, 30, 460, 60], "高一数学月考", "text"),
        block(1, 0, [60, 70, 460, 90], "注意事项：1. 答卷前，考生务必填写姓名。"),
        block(2, 0, [60, 100, 470, 120], "一、选择题：本题共 3 小题"),
        block(3, 0, [60, 130, 470, 200], "1. 已知集合 A={1,2}，则（ ）A. 1 B. 2"),
        block(4, 0, [60, 330, 470, 400], "2. 如图，在三角形 ABC 中，求角 A"),
        block(5, 0, [300, 410, 460, 500], "", "image"),
        block(6, 0, [60, 800, 470, 900], "3. （10分）已知函数 f(x)=x^2，"),
        block(7, 0, [540, 40, 940, 80], "(1) 求 f(2)；(2) 求 f(x) 的最小值。"),
        block(8, 0, [540, 90, 700, 180], "", "image"),
        block(9, 0, [540, 200, 950, 230], "二、解答题：本题共 2 小题"),
        block(10, 0, [540, 250, 950, 300], "5. (12分) 已知数列 {a_n}"),
        block(11, 0, [540, 600, 950, 640], "6. (12分) 如图，四棱锥"),
        block(12, 0, [480, 960, 520, 980], "第1页", "page_number"),
    ]


class SegmentTests(TestCase):
    def test_cross_column_question_keeps_its_continuation(self):
        result = segment.segment(PAGES[:1], two_column_paper())
        numbers = [q["number"] for q in result["questions"]]
        self.assertEqual(numbers, [1, 2, 3, 5, 6])  # 说明里的"1. 答卷前"不算题
        q3 = next(q for q in result["questions"] if q["number"] == 3)
        self.assertEqual(len(q3["regions"]), 2)
        left, right = q3["regions"]
        self.assertLess(left["bbox"][0], 500)
        self.assertGreaterEqual(right["bbox"][0], 500)
        # 右栏续题止于"二、解答题"标题，不含标题。
        self.assertLessEqual(right["bbox"][3], 200)
        self.assertEqual([f["seq"] for f in q3["figure_candidates"]], [8])
        self.assertEqual(q3["question_type"], "single_choice")
        q5 = next(q for q in result["questions"] if q["number"] == 5)
        self.assertEqual(q5["question_type"], "free_response")
        self.assertEqual([(n, a.number) for n, a in result["missing"]], [(4, 3)])

    def test_suffix_repair(self):
        blocks = [block(i, 0, [60, 50 + 60 * i, 470, 80 + 60 * i], f"{n}. 题目{n}")
                  for i, n in enumerate([21, 22, 3, 24])]
        layout, starts = segment.analyse(PAGES[:1], blocks)
        self.assertEqual([s.number for s in starts], [21, 22, 23, 24])
        self.assertEqual(starts[2].source, "repaired")

    def test_number_in_middle_of_handwriting_box(self):
        blocks = [
            block(0, 0, [60, 50, 470, 80], "1. 已知 x=1"),
            block(1, 0, [20, 200, 470, 300], "-7x+1=9 2. 直线 3x+4y-2=0 的一个方向向量为"),
            block(2, 0, [60, 400, 470, 430], "3. 已知 y=2"),
        ]
        layout, starts = segment.analyse(PAGES[:1], blocks)
        self.assertEqual([s.number for s in starts], [1, 2, 3])
        self.assertGreater(starts[1].y, 200)


    def test_single_column_pages_with_alternating_margins(self):
        """A3 对折扫描成单页：单页题号靠右（装订边），双页题号靠左——都是单栏，不能当成两栏。"""
        pages = [{"page_idx": 0, "width": 595, "height": 842}, {"page_idx": 1, "width": 595, "height": 842}]
        blocks, seq = [], 0
        for page, x0, numbers in ((0, 166, range(1, 6)), (1, 33, range(6, 11))):
            for i, n in enumerate(numbers):
                blocks.append(block(seq, page, [x0, 100 + i * 150, 980 if page == 0 else 900, 140 + i * 150],
                                    f"{n}. 下列说法正确的是（ ）A. 1 B. 2 C. 3 D. 4"))
                seq += 1
        result = segment.segment(pages, blocks)
        self.assertEqual(result["layout"].splits, {0: [], 1: []})
        self.assertEqual([q["number"] for q in result["questions"]], list(range(1, 11)))
        for q in result["questions"]:
            self.assertTrue(q["regions"], q["number"])
            left, _, right, _ = q["regions"][0]["bbox"]
            self.assertLess(left, 170)
            self.assertGreater(right, 890)

    def test_right_column_with_single_question_is_still_a_column(self):
        blocks = [
            block(0, 0, [50, 80, 480, 240], "23. （10分）小明对笔记本电脑的研究"),
            block(1, 0, [50, 270, 150, 410], "", "image"),
            block(2, 0, [50, 495, 480, 540], "24. （12分）如图，直线 y=kx+2"),
            block(3, 0, [55, 550, 450, 610], "（1）求双曲线的解析式；"),
            block(4, 0, [505, 78, 947, 228], "25. （12分）如图，在平面直角坐标系中"),
            block(5, 0, [523, 253, 627, 413], "", "image"),
        ]
        result = segment.segment(PAGES[:1], blocks)
        self.assertEqual(len(result["layout"].splits[0]), 1)
        q25 = next(q for q in result["questions"] if q["number"] == 25)
        self.assertGreater(q25["regions"][0]["bbox"][0], 480)
        q24 = next(q for q in result["questions"] if q["number"] == 24)
        self.assertLess(q24["regions"][0]["bbox"][2], 520)

    def test_figure_straddling_next_question_is_candidate_for_both(self):
        blocks = [
            block(0, 0, [60, 100, 470, 130], "1. 如图是正方体的展开图，相对面上的字是（ ）"),
            block(1, 0, [380, 150, 470, 260], "", "image"),
            block(2, 0, [60, 180, 360, 210], "2. 四位同学画数轴如图所示（ ）"),
            block(3, 0, [60, 400, 470, 430], "3. 已知"),
        ]
        result = segment.segment(PAGES[:1], blocks)
        q1, q2 = result["questions"][:2]
        self.assertEqual([f["seq"] for f in q1["figure_candidates"]], [1])
        self.assertEqual([f["seq"] for f in q2["figure_candidates"]], [1])


class TextTests(TestCase):
    def test_canon_ignores_notation(self):
        self.assertEqual(canon(r"$\dfrac{1}{2}$ ，且 $AB//CD$"), canon(r"\frac12, 且 AB \parallel CD"))
        self.assertEqual(canon(r"$\overrightarrow{a}\cdot\vec{b}$"), canon(r"\vec a \cdot \vec b"))
        self.assertEqual(canon("则 x=（　）"), canon("则 $x=$ ( )"))
        self.assertNotEqual(canon(r"$\frac{1}{2}$"), canon(r"$\frac{1}{3}$"))
        self.assertEqual(canon("（15分）已知"), canon("已知"))

    def test_same_reading_checks_options(self):
        a = {"stem": "已知", "options": {"A": "1", "B": "2"}}
        self.assertTrue(same_reading(a, {"stem": "已知", "options": {"A": "$1$", "B": "2"}}))
        self.assertFalse(same_reading(a, {"stem": "已知", "options": {"A": "1", "B": "3"}}))

    def test_clean_stem(self):
        self.assertEqual(clean_stem("17. （15分）已知向量", 17), "已知向量")


class ParallelogramSymbolTests(TestCase):
    def test_square_before_vertex_letters_becomes_parallelogram(self):
        from .textnorm import fix_symbols

        self.assertEqual(fix_symbols(r"如图，在 $\square ABCD$ 中，$E$ 为中点"), "如图，在 ▱ABCD 中，$E$ 为中点")
        self.assertEqual(fix_symbols("在□ABCD中"), "在▱ABCD中")
        self.assertEqual(fix_symbols(r"$\square ABCD \cong \Box EFGH$"), r"$\text{▱}ABCD \cong \text{▱}EFGH$")
        self.assertEqual(fix_symbols(r"在 \square ABCD 中"), "在 ▱ABCD 中")
        self.assertEqual(fix_symbols(r"在 $\text{□}ABCD$ 中"), "在 ▱ABCD 中")
        self.assertEqual(fix_symbols(r"在 $\parallelogram ABCD$ 中"), "在 ▱ABCD 中")
        self.assertEqual(fix_symbols("□$ABCD$"), "▱$ABCD$")
        # 填空用的方框不动
        self.assertEqual(fix_symbols(r"在□内填数：$3\square 5=15$"), r"在□内填数：$3\square 5=15$")

    def test_reading_is_normalized_and_old_cards_are_fixed(self):
        reading = readers.parse_reading("【题干】如图，在$\\square ABCD$中，求证：$AC=BD$\n【题型】解答题", 23)
        self.assertEqual(reading["stem"], "如图，在▱ABCD中，求证：$AC=BD$")
        from importlib import import_module

        from django.apps import apps

        paper = Paper.objects.create(filename="卷.pdf", kind="pdf", sha256="p" * 64)
        question = Question.objects.create(paper=paper, number=23, stem="在 $\\square ABCD$ 中", options={"A": "□ABCD 是菱形"})
        import_module("core.migrations.0003_fix_parallelogram_symbol").forwards(apps, None)
        question.refresh_from_db()
        self.assertEqual((question.stem, question.options), ("在 ▱ABCD 中", {"A": "▱ABCD 是菱形"}))

    def test_followup_migration_fixes_structured_reads_but_preserves_raw(self):
        from importlib import import_module

        from django.apps import apps

        paper = Paper.objects.create(filename="旧识读.pdf", kind="pdf", sha256="r" * 64)
        question = Question.objects.create(
            paper=paper,
            number=5,
            stem="在 ▱ABCD 中",
            read_a={"stem": "在□ABCD中", "options": {"A": r"$\square EFGH$"}, "raw": "模型原文：□ABCD"},
            approved=True,
            approved_content_hash="a" * 64,
        )
        bad_final = Question.objects.create(
            paper=paper,
            number=6,
            stem=r"在 $\square WXYZ$ 中",
            approved=True,
            approved_content_hash="b" * 64,
        )
        migrate = import_module("core.migrations.0005_normalize_parallelogram_readings").forwards
        migrate(apps, None)
        migrate(apps, None)  # 可重复执行不应产生第二次变化
        question.refresh_from_db()
        bad_final.refresh_from_db()
        self.assertEqual(question.read_a["stem"], "在▱ABCD中")
        self.assertEqual(question.read_a["options"]["A"], "▱EFGH")
        self.assertEqual(question.read_a["raw"], "模型原文：□ABCD")
        self.assertTrue(question.approved)  # 只清理审计记录，不改变最终题面
        self.assertEqual(bad_final.stem, "在 ▱WXYZ 中")
        self.assertFalse(bad_final.approved)
        self.assertEqual(bad_final.approved_content_hash, "")


class ParseTests(TestCase):
    def test_parse_reading(self):
        raw = """<think>略</think>```
【题号】9
【题型】单选题
【题干】
下列图象中，是函数图象的是（ ）
【A】A. 图
【B】
【C】$y=\\frac{1}{x}$
【D】无
【配图】1=A, 2=B, 3=无关，4＝C 缺图
【其他题号】10
```"""
        reading = readers.parse_reading(raw, 9)
        self.assertEqual(reading["stem"], "下列图象中，是函数图象的是（ ）")
        self.assertEqual(reading["options"], {"A": "图", "C": "$y=\\frac{1}{x}$"})
        self.assertEqual(reading["type"], "single_choice")
        self.assertEqual(reading["figures"], {"1": "A", "2": "B", "3": "none", "4": "C"})
        self.assertTrue(reading["missing_figure"])
        self.assertEqual(reading["others"], [10])
        self.assertEqual(reading["number_seen"], 9)

    def test_handwritten_choice_in_brackets_is_dropped(self):
        self.assertEqual(readers.drop_filled_choice("直线方程为(A)"), "直线方程为(　)")
        self.assertEqual(readers.drop_filled_choice("则 x=（ B ）"), "则 x=（　）")
        self.assertEqual(readers.drop_filled_choice("正确的是（ACD）。"), "正确的是（　）。")
        self.assertEqual(readers.drop_filled_choice("求 $P(A)$"), "求 $P(A)$")
        self.assertEqual(readers.drop_filled_choice("事件 P(A)"), "事件 P(A)")

    def test_options_repeated_in_stem_are_removed(self):
        reading = readers.parse_reading("**【题干】**：已知函数\nA. 1\nB. 2\n【A】1\n【B】2", 3)
        self.assertEqual(reading["stem"], "已知函数")
        self.assertEqual(reading["options"], {"A": "1", "B": "2"})

    def test_parse_requires_stem(self):
        with self.assertRaises(ValueError):
            readers.parse_reading("没有格式", 1)

    def test_band(self):
        self.assertEqual(readers.parse_band("【刻度】07"), 7)
        self.assertIsNone(readers.parse_band("【刻度】无"))

    def test_generated_figure_descriptions_are_not_options(self):
        reading = readers.parse_reading(
            "【题型】单选题\n【题干】选出正确的数轴（ ）[图：数轴上有 [?]、1、2]\n"
            "【A】[图：数轴上有 1、2、3]\n"
            "【B】【图片: 函数图象经过 [?]】\n"
            "【C】（图示：一个三角形）\n"
            "【D】图\n【配图】1=A, 2=B, 3=C",
            4,
        )
        self.assertEqual(reading["options"], {"D": "图"})
        self.assertEqual(reading["stem"], "选出正确的数轴（ ）")
        self.assertEqual(reading["figure_descriptions"], ["A", "B", "C", "stem"])
        self.assertFalse(reading["unclear"])

    def test_figure_description_filter_is_conservative(self):
        for value in (
            "[图：数轴]", "【图片: 函数图象】", "（示意图：三角形）", "[表格：星期与产量]",
            "（图形：呈阶梯状排列的六个正方形）",
            "数轴上标有点，标号依次为 1, 2, 3, 4, 5",
            "数轴上标有点，标号依次为 $-2$、$-1$、$0$、$1$、$2$",
        ):
            self.assertTrue(readers.is_figure_description(value), value)
        for value in (
            "如图，点 A 在数轴上", "图1中的阴影部分", "[图1]", "$[a,b]$", "图",
            "数轴上点 A 表示 1", "向右", "甲",
        ):
            self.assertFalse(readers.is_figure_description(value), value)

    def test_all_reader_prompts_forbid_turning_figures_into_text(self):
        prompts = [
            readers.transcribe_prompt(4, with_figures=True),
            readers.transcribe_prompt(4, with_figures=False),
            readers.arbiter_prompt(4, {"stem": "题干"}, {"stem": "题干"}),
        ]
        for prompt in prompts:
            self.assertIn("不得改写成", prompt)
            self.assertIn("某个选项只有图时", prompt)
            self.assertIn("表格", prompt)
            self.assertIn("Markdown", prompt)

    def test_markdown_table_can_be_removed_without_touching_surrounding_text(self):
        source = "已知数据如下：\n\n| 星期 | 一 | 二 |\n|---|---|---|\n| 增减 | +5 | -2 |\n\n求总数。"
        cleaned, removed = readers.strip_markdown_tables(source)
        self.assertTrue(removed)
        self.assertEqual(cleaned, "已知数据如下：\n\n求总数。")
        untouched, removed = readers.strip_markdown_tables("若 $|x|=2$，求 $x$。")
        self.assertFalse(removed)
        self.assertEqual(untouched, "若 $|x|=2$，求 $x$。")

    def test_migration_cleans_drafts_but_preserves_human_edits_and_published_snapshot(self):
        from importlib import import_module

        from django.apps import apps

        paper = Paper.objects.create(filename="图片选项.pdf", kind="pdf", sha256="f" * 64)
        table_text = "根据下表回答：\n\n| 星期 | 一 |\n|---|---|\n| 增减 | +5 |\n\n求总数。"
        question = Question.objects.create(
            paper=paper,
            number=4,
            question_type="single_choice",
            stem=table_text,
            options={
                "A": "数轴上标有点，标号依次为 1, 2, 3, 4, 5",
                "B": "向右",
                "C": "数轴上标有点，标号依次为 -2, -1, 0, 1, 2",
                "D": "数轴上标有点，标号依次为 $-2$、$-1$、$0$、$1$、$2$",
            },
            figures=[
                {"slot": "A", "page_idx": 0, "bbox": [1, 2, 3, 4], "source": "auto"},
                {"slot": "B", "page_idx": 0, "bbox": [2, 3, 4, 5], "source": "auto"},
                {"slot": "stem", "page_idx": 0, "bbox": [5, 6, 7, 8], "source": "auto"},
            ],
            read_a={"options": {}, "figures": {"1": "A", "2": "B", "3": "stem"},
                    "raw": "【A】\\n【B】\\n【配图】1=A,2=B,3=题干"},
            state="green",
            approved=True,
            approved_content_hash="a" * 64,
        )
        human = Question.objects.create(
            paper=paper,
            number=5,
            options={"A": "[图：这是人工保留的说明]"},
            figures=[{"slot": "A", "page_idx": 0, "bbox": [1, 2, 3, 4], "source": "auto"}],
            read_a={"options": {}, "figures": {"1": "A"}, "raw": "原始记录"},
            edited=True,
            text_source="human",
            state="green",
            approved=True,
            approved_content_hash="b" * 64,
        )
        table_only = Question.objects.create(
            paper=paper,
            number=6,
            stem=table_text,
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [5, 6, 7, 8], "source": "auto"}],
            read_a={"options": {}, "figures": {"1": "stem"}, "raw": "原始表格"},
            state="green",
            approved=True,
            approved_content_hash="c" * 64,
        )
        publication = PublishedQuestion.objects.create(
            question=table_only,
            paper=paper,
            source_filename=paper.filename,
            number=6,
            question_type="free_response",
            version=1,
            content={"stem": table_text},
            content_hash="d" * 64,
            search_text=table_text,
        )
        migrate = import_module("core.migrations.0006_remove_ai_figure_descriptions").forwards
        migrate(apps, None)
        migrate(apps, None)
        question.refresh_from_db()
        human.refresh_from_db()
        table_only.refresh_from_db()
        publication.refresh_from_db()
        self.assertEqual(question.options, {"B": "向右"})
        self.assertEqual(question.stem, "根据下表回答：\n\n求总数。")
        self.assertEqual(question.read_a["raw"], "【A】\\n【B】\\n【配图】1=A,2=B,3=题干")
        self.assertFalse(question.approved)
        self.assertEqual(question.approved_content_hash, "")
        self.assertEqual(question.state, "yellow")
        self.assertTrue(any("重复转写" in flag for flag in question.flags))
        self.assertTrue(any("表格内容" in flag for flag in question.flags))
        self.assertIn(pipeline.FLAG_UNFOUND_FIGURE, question.flags)
        self.assertEqual(human.options, {"A": "[图：这是人工保留的说明]"})
        self.assertTrue(human.approved)
        self.assertEqual(table_only.stem, "根据下表回答：\n\n求总数。")
        self.assertFalse(table_only.approved)
        self.assertTrue(any("表格内容" in flag for flag in table_only.flags))
        self.assertFalse(any("AI 生成的图片说明" in flag for flag in table_only.flags))
        self.assertEqual(publication.content["stem"], table_text)


def fake_page_pdf(path: Path, pages: int = 1) -> None:
    images = []
    for index in range(pages):
        image = Image.new("RGB", (1684, 1190), "white")
        draw = ImageDraw.Draw(image)
        for row in range(20):
            draw.rectangle((120, 80 + row * 52, 900, 96 + row * 52), fill="black")
            draw.rectangle((1080, 80 + row * 52, 1600, 96 + row * 52), fill="black")
        images.append(image)
    images[0].save(path, "PDF", save_all=True, append_images=images[1:])


class ScriptedChat:
    """按题号返回预设的模型输出；记录调用次数。"""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, engine, prompt, images, max_tokens=3000):
        import re
        number = int(re.search(r"第 (\d+) 题", prompt).group(1))
        kind = "locate" if "横带" in prompt else "arbiter" if "读法甲" in prompt else \
            "a" if "蓝色框" in prompt else "b"
        self.calls.append((kind, number, engine.provider))
        value = self.answers.get((kind, number), self.answers.get(("*", number), ""))
        if isinstance(value, Exception):
            raise value
        return value


def tagged(stem, options=None, figures="无", others="无", number=None):
    lines = ([f"【题号】{number}"] if number else []) + ["【题型】单选题" if options else "【题型】解答题", "【题干】", stem]
    for key, value in (options or {}).items():
        lines.append(f"【{key}】{value}")
    lines += [f"【配图】{figures}", f"【其他题号】{others}"]
    return "\n".join(lines)


class PipelineTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.settings_override = override_settings(DATA_ROOT=self.temp)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        env = mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "test", "SILICONFLOW_API_KEY": "test2",
                                             "MINERU_TOKEN": "tok"})
        env.start()
        self.addCleanup(env.stop)
        self.paper = Paper.objects.create(filename="卷.pdf", kind="pdf", sha256="x" * 64)
        folder = self.temp / str(self.paper.id)
        folder.mkdir(parents=True)
        fake_page_pdf(folder / "source.pdf")
        self.paper.source_path = str(folder / "source.pdf")
        self.paper.pages = PAGES[:1]
        self.paper.status = Paper.Status.SEGMENTING
        self.paper.save()
        Block.objects.bulk_create([Block(paper=self.paper, **b) for b in two_column_paper()])

    def run_paper(self, answers):
        chat = ScriptedChat(answers)
        with mock.patch.object(readers, "chat", chat):
            pipeline.process_paper(self.paper)
        self.paper.refresh_from_db()
        return chat

    def test_full_pipeline_states(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("a", 1): tagged("已知集合 $A=\\{1,2\\}$，则（ ）", {"A": "1", "B": "2"}),
            ("b", 1): tagged("已知集合 $A = \\{1, 2\\}$ ，则（　）", {"A": "$1$", "B": "2"}),
            ("a", 2): tagged("如图，在三角形 $ABC$ 中，求角 $A$", {"A": "30°", "B": "60°"}, figures="1=题干", number=2),
            ("b", 2): tagged("如图，在三角形 ABC 中，求角 B", {"A": "30°", "B": "60°"}),
            ("arbiter", 2): tagged("如图，在三角形 $ABC$ 中，求角 $A$", {"A": "$30^\\circ$", "B": "60°"}),
            ("a", 3): tagged("已知函数 $f(x)=x^2$，\n(1) 求 $f(2)$；\n(2) 求最小值。", figures="1=题干", number=4),
            ("b", 3): tagged("已知函数 $f(x)=x^3$，\n(1) 求 $f(2)$；\n(2) 求最小值。"),
            ("arbiter", 3): tagged("已知函数 $f(x)=x^4$，\n(1) 求 $f(2)$；\n(2) 求最小值。"),
            ("a", 5): tagged("已知数列 $\\{a_n\\}$", others="6"),
            ("b", 5): readers.ReaderError("硅基流动接口返回 HTTP 500"),
            ("a", 6): readers.ReaderError("MiniMax 接口返回 HTTP 500"),
            ("b", 6): readers.ReaderError("硅基流动接口返回 HTTP 500"),
        }
        chat = self.run_paper(answers)
        self.assertEqual(self.paper.status, Paper.Status.READY, self.paper.error)
        cards = {q.number: q for q in self.paper.questions.all()}
        self.assertEqual(sorted(cards), [1, 2, 3, 5, 6])
        self.assertEqual((cards[1].state, cards[1].text_source), ("green", "agree"))
        self.assertEqual((cards[2].state, cards[2].text_source), ("green", "majority"), cards[2].flags)
        self.assertEqual([f["slot"] for f in cards[2].figures], ["stem"])
        self.assertEqual((cards[3].state, cards[3].text_source), ("yellow", "arbiter"))
        self.assertIn("x^4", cards[3].stem)
        self.assertTrue(any("AI 看到的题号是 4" in f for f in cards[3].flags))
        self.assertEqual(cards[5].state, "yellow")
        self.assertTrue(any("只有一次" in f for f in cards[5].flags))
        self.assertTrue(any("第 6 题" in f for f in cards[5].flags))
        self.assertEqual(cards[6].state, "red")
        self.assertTrue(any("没有找到第 4 题" in note or "AI 没有找到第 4 题" in note for note in self.paper.notes))
        # 第二位读者用的是另一家
        self.assertIn(("b", 1, "siliconflow"), chat.calls)
        self.assertIn(("a", 1, "minimax"), chat.calls)

    def test_figure_printed_for_another_question_is_handed_over(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("a", 1): tagged("已知集合", {"A": "1", "B": "2"}),
            ("b", 1): tagged("已知集合", {"A": "1", "B": "2"}),
            # 第 2 题范围里的图其实印着"第 3 题图"
            ("a", 2): tagged("下列图形中是柱体的是（ ）", figures="1=第3题", others="3"),
            ("b", 2): tagged("下列图形中是柱体的是（ ）", others="3"),
            ("*", 3): tagged("如图，已知函数 $f(x)=x^2$"),
            ("a", 5): tagged("已知数列"), ("b", 5): tagged("已知数列"),
            ("a", 6): tagged("如图，四棱锥", figures="无"), ("b", 6): tagged("如图，四棱锥"),
        }
        self.run_paper(answers)
        cards = {q.number: q for q in self.paper.questions.all()}
        # 第 2 题：选择题，但选项全是图？这里没有选项文字也没有选项图 → 提示；"第 3 题"已由图解释，不提示范围
        self.assertFalse(any("露出了" in f for f in cards[2].flags), cards[2].flags)
        self.assertEqual(cards[2].figures, [])
        # 第 3 题得到了这张图，"如图"提示被清掉
        self.assertTrue(any(f["source"] == "other" for f in cards[3].figures), cards[3].figures)
        self.assertFalse(any(pipeline.figure_flag(f) for f in cards[3].flags), cards[3].flags)
        # 第 6 题说"如图"却没有图 → 提示
        self.assertIn(pipeline.FLAG_NO_FIGURE, cards[6].flags)
        self.assertEqual(cards[6].state, "yellow")

    def test_option_figures_do_not_trigger_missing_options(self):
        answers = {("locate", 4): "【刻度】无", ("*", 1): tagged("x"), ("*", 3): tagged("x"), ("*", 5): tagged("x"),
                   ("*", 6): tagged("x"),
                   ("a", 2): "【题型】单选题\n【题干】\n下列图形，不是柱体的是（ ）\n【A】\n【B】\n【配图】1=A\n【其他题号】无",
                   ("b", 2): "【题型】单选题\n【题干】\n下列图形，不是柱体的是（ ）\n【其他题号】无"}
        self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual([f["slot"] for f in q2.figures], ["A"])
        self.assertEqual(q2.state, "green", q2.flags)

    def test_plain_image_description_from_second_reader_is_not_saved(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("*", 1): tagged("x"), ("*", 3): tagged("x"),
            ("*", 5): tagged("x"), ("*", 6): tagged("x"),
            ("a", 2): (
                "【题型】单选题\n【题干】四位同学画数轴如图所示（ ）\n"
                "【A】\n【B】\n【C】\n【D】\n【配图】1=A\n【其他题号】无"
            ),
            ("b", 2): tagged(
                "四位同学画数轴如图所示（ ）",
                {
                    "A": "数轴上标有点，标号依次为 1, 2, 3, 4, 5",
                    "B": "数轴上标有点，标号依次为 -1, -2, 0, 1, 2",
                    "C": "数轴上标有点，标号依次为 -2, -1, 0, 1, 2",
                    "D": "数轴上标有点，标号依次为 -2, -1, [?], 1, 2",
                },
            ),
        }
        chat = self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual(q2.options, {})
        self.assertEqual([figure["slot"] for figure in q2.figures], ["A"])
        self.assertEqual(q2.state, "yellow", q2.flags)
        self.assertIn(pipeline.FLAG_UNFOUND_FIGURE, q2.flags)
        self.assertFalse(any("看不清的字" in flag for flag in q2.flags))
        self.assertEqual(set(q2.read_b["options"]), {"A", "B", "C", "D"})
        self.assertFalse(any(kind == "arbiter" and number == 2 for kind, number, _ in chat.calls))

    def test_real_printed_option_text_next_to_a_figure_is_preserved(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("*", 1): tagged("x"), ("*", 3): tagged("x"),
            ("*", 5): tagged("x"), ("*", 6): tagged("x"),
            ("a", 2): (
                "【题型】单选题\n【题干】选择箭头方向（ ）\n"
                "【A】\n【配图】1=A\n【其他题号】无"
            ),
            ("b", 2): tagged("选择箭头方向（ ）", {"A": "向右"}),
            ("arbiter", 2): tagged("选择箭头方向（ ）", {"A": "向右"}),
        }
        chat = self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual(q2.options, {"A": "向右"})
        self.assertEqual([figure["slot"] for figure in q2.figures], ["A"])
        self.assertEqual(q2.text_source, "majority")
        self.assertEqual(q2.state, "green", q2.flags)
        self.assertTrue(any(kind == "arbiter" and number == 2 for kind, number, _ in chat.calls))

    def test_plain_image_description_from_primary_reader_is_not_saved(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("*", 1): tagged("x"), ("*", 3): tagged("x"),
            ("*", 5): tagged("x"), ("*", 6): tagged("x"),
            ("a", 2): tagged(
                "选择正确的数轴（ ）",
                {"A": "数轴上标有点，标号依次为 1, 2, 3, 4, 5"},
                figures="1=A",
            ),
            ("b", 2): tagged("选择正确的数轴（ ）"),
        }
        chat = self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual(q2.options, {})
        self.assertEqual([figure["slot"] for figure in q2.figures], ["A"])
        self.assertEqual(q2.state, "green", q2.flags)
        self.assertFalse(any(kind == "arbiter" and number == 2 for kind, number, _ in chat.calls))

    def test_arbiter_figure_description_without_a_bound_figure_gets_warning(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("*", 1): tagged("x"), ("*", 3): tagged("x"),
            ("*", 5): tagged("x"), ("*", 6): tagged("x"),
            ("a", 2): tagged("选择正确答案（ ）", {"A": "甲"}),
            ("b", 2): tagged("选择正确答案（ ）", {"A": "乙"}),
            ("arbiter", 2): tagged("选择正确答案（ ）", {"A": "[图：一条没有框出的数轴]"}),
        }
        self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual(q2.options, {})
        self.assertIn(pipeline.FLAG_UNFOUND_FIGURE, q2.flags)
        self.assertEqual(q2.state, "yellow")

    def test_markdown_table_is_not_saved_when_the_table_is_a_stem_figure(self):
        table_stem = "根据下表回答：\n\n| 星期 | 一 | 二 |\n|---|---|---|\n| 增减 | +5 | -2 |\n\n求总数。"
        answers = {
            ("locate", 4): "【刻度】无",
            ("*", 1): tagged("x"), ("*", 3): tagged("x"),
            ("*", 5): tagged("x"), ("*", 6): tagged("x"),
            ("a", 2): tagged(table_stem, {"A": "1", "B": "2"}, figures="1=题干"),
            ("b", 2): tagged(table_stem, {"A": "1", "B": "2"}),
        }
        chat = self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual(q2.stem, "根据下表回答：\n\n求总数。")
        self.assertEqual([figure["slot"] for figure in q2.figures], ["stem"])
        self.assertEqual(q2.state, "green", q2.flags)
        self.assertIn("| 星期 |", q2.read_a["stem"])
        self.assertFalse(any(kind == "arbiter" and number == 2 for kind, number, _ in chat.calls))

    def test_resegment_keeps_unchanged_cards_and_rereads_changed(self):
        answers = {("locate", 4): "【刻度】无", **{("*", n): tagged(f"第{n}题", {"A": "1", "B": "2"}) for n in (1, 2, 3)},
                   **{("*", n): tagged(f"第{n}题") for n in (5, 6)}}
        self.run_paper(answers)
        q1 = self.paper.questions.get(number=1)
        q1.approved = True
        q1.approved_content_hash = library.approval_hash(q1)
        q1.save(update_fields=["approved", "approved_content_hash"])
        # 模拟旧版切错：第 2 题范围是空的（红卡），第 5 题被人工调整过
        q2 = self.paper.questions.get(number=2)
        Question.objects.filter(pk=q2.pk).update(regions=[], regions_auto=[], state="red", error="x")
        q5 = self.paper.questions.get(number=5)
        manual = [{"page_idx": 0, "bbox": [520, 240, 960, 420]}]
        Question.objects.filter(pk=q5.pk).update(regions=manual)
        self.paper.status = Paper.Status.SEGMENTING
        self.paper.save()
        chat = self.run_paper(answers)
        cards = {q.number: q for q in self.paper.questions.all()}
        self.assertTrue(library.approval_is_current(cards[1]))  # 内容和来源没变：保留通过
        self.assertTrue(cards[2].regions)                   # 重新切出范围
        self.assertEqual(cards[2].state, "green")           # 并重新识读
        self.assertEqual(cards[5].regions, manual)          # 人工范围不动
        read_numbers = {n for kind, n, _ in chat.calls if kind in ("a", "b")}
        self.assertEqual(read_numbers, {2})
        self.assertTrue(any("重新切题" in note for note in self.paper.notes))

    def test_missing_number_is_located_and_split(self):
        answers = {("locate", 4): "【刻度】05", ("*", 1): tagged("x"), ("*", 2): tagged("x"), ("*", 3): tagged("x"),
                   ("*", 4): tagged("x"), ("*", 5): tagged("x"), ("*", 6): tagged("x")}
        self.run_paper(answers)
        numbers = list(self.paper.questions.values_list("number", flat=True))
        self.assertEqual(numbers, [1, 2, 3, 4, 5, 6])
        q4 = self.paper.questions.get(number=4)
        self.assertEqual(q4.start_source, "located")
        q3 = self.paper.questions.get(number=3)
        self.assertLessEqual(q3.regions[-1]["bbox"][3], q4.regions[0]["bbox"][1] + segment.START_PAD + 1)


class ApiTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        self.client = Client()
        self.paper = Paper.objects.create(filename="卷.pdf", kind="pdf", sha256="y" * 64, status=Paper.Status.READY,
                                          pages=PAGES[:1])
        folder = self.temp / str(self.paper.id)
        folder.mkdir(parents=True)
        fake_page_pdf(folder / "source.pdf")
        self.paper.source_path = str(folder / "source.pdf")
        self.paper.save()
        self.q = Question.objects.create(
            paper=self.paper, number=1, question_type="single_choice", stem="已知 $x=1$", options={"A": "1", "B": "2"},
            regions=[{"page_idx": 0, "bbox": [50, 100, 480, 300]}], state=Question.State.GREEN,
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [300, 150, 450, 250], "source": "auto"}],
        )
        self.q2 = Question.objects.create(paper=self.paper, number=2, stem="求证", regions=[{"page_idx": 0, "bbox": [50, 300, 480, 400]}],
                                          state=Question.State.YELLOW, flags=["两次识读不一致，已由第三次识读裁决，请看标黄的地方"])

    def post(self, path, body=None, header=True):
        headers = {"HTTP_X_QB_REQUEST": "1"} if header else {}
        return self.client.post(path, data=json.dumps(body or {}), content_type="application/json", **headers)

    def patch(self, path, body=None, header=True):
        headers = {"HTTP_X_QB_REQUEST": "1"} if header else {}
        return self.client.patch(path, data=json.dumps(body or {}), content_type="application/json", **headers)

    def delete(self, path, header=True):
        headers = {"HTTP_X_QB_REQUEST": "1"} if header else {}
        return self.client.delete(path, **headers)

    def test_requires_page_header(self):
        response = self.post(f"/api/questions/{self.q.id}/approve", {"approved": True}, header=False)
        self.assertEqual(response.status_code, 403)

    def test_approve_green_then_publish_and_version(self):
        data = self.post(f"/api/papers/{self.paper.id}/approve-green").json()
        self.assertEqual(data["approved"], 1)
        self.q.refresh_from_db()
        self.assertTrue(self.q.approved_content_hash)
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/publish").json()["created"], 1)
        again = self.post(f"/api/papers/{self.paper.id}/publish").json()
        self.assertEqual((again["created"], again["unchanged"]), (0, 1))
        publication = PublishedQuestion.objects.get()
        self.assertEqual(publication.content["figures"][0]["url"], f"/api/library/{publication.id}/figures/figure-1.png")
        self.assertEqual(publication.content["figures"][0]["source"], "auto")
        # 测试题没有 regions_auto，对系统来说是人工框定的来源范围。
        self.assertEqual(publication.content["sources"][0]["source"], "manual")
        self.assertEqual(publication.content["review"]["state"], "green")
        self.assertTrue(publication.content["review"]["approved_content_hash"])
        self.assertTrue((self.temp / "library" / str(publication.id) / "figure-1.png").is_file())
        # 改字后再入库 → 第 2 版，旧版标记为已替代
        self.post(f"/api/questions/{self.q.id}/text", {"stem": "已知 $x=2$", "options": {"A": "1", "B": "2"}})
        self.q.refresh_from_db()
        self.assertFalse(self.q.approved)
        self.assertEqual(self.q.approved_content_hash, "")
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/publish").json()["created"], 0)
        self.assertEqual(PublishedQuestion.objects.count(), 1)
        self.assertEqual(self.post(f"/api/questions/{self.q.id}/approve", {"approved": True}).status_code, 200)
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/publish").json()["created"], 1)
        self.assertEqual(list(PublishedQuestion.objects.order_by("version").values_list("status", flat=True)),
                         ["superseded", "published"])
        library = self.client.get("/api/library").json()
        self.assertEqual(library["total"], 1)
        self.assertEqual(library["items"][0]["content"]["stem"], "已知 $x=2$")
        self.assertEqual(self.client.get(f"/api/library?q=x=2").json()["total"], 1)
        malformed = self.client.get("/api/library?document=--------------------------------")
        self.assertEqual(malformed.status_code, 200)
        self.assertEqual(malformed.json()["total"], 0)

    def test_rename_updates_every_publication_source_without_revalidating_stale_content(self):
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        self.post(f"/api/papers/{self.paper.id}/publish")

        for stem in ("已知 $x=2$", "已知 $x=3$"):
            current = PublishedQuestion.objects.filter(
                question=self.q, status=PublishedQuestion.Status.PUBLISHED,
            ).first()
            if stem.endswith("3$"):
                library.withdraw(current)
            self.post(f"/api/questions/{self.q.id}/text", {
                "stem": stem, "options": {"A": "1", "B": "2"},
            })
            self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
            self.post(f"/api/papers/{self.paper.id}/publish")

        before = {
            item.pk: (item.version, item.status, item.published_at, item.withdrawn_at, item.content_hash)
            for item in PublishedQuestion.objects.filter(question=self.q)
        }
        self.assertEqual(
            [item.status for item in PublishedQuestion.objects.filter(question=self.q).order_by("version")],
            [PublishedQuestion.Status.SUPERSEDED, PublishedQuestion.Status.WITHDRAWN,
             PublishedQuestion.Status.PUBLISHED],
        )
        self.q.refresh_from_db()
        self.assertTrue(library.approval_is_current(self.q))

        response = self.patch(f"/api/papers/{self.paper.id}", {"name": "秋季月考任务"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["changed"])
        self.assertEqual(response.json()["paper"]["name"], "秋季月考任务")
        self.assertEqual(response.json()["paper"]["filename"], "卷.pdf")
        self.paper.refresh_from_db()
        self.q.refresh_from_db()
        self.assertEqual(self.paper.filename, "卷.pdf")
        self.assertEqual(self.paper.task_name, "秋季月考任务")
        self.assertTrue(library.approval_is_current(self.q))
        self.assertTrue(library.publication_state(self.q)["up_to_date"])

        publications = list(PublishedQuestion.objects.filter(question=self.q).order_by("version"))
        self.assertEqual(len(publications), 3)
        for publication in publications:
            old = before[publication.pk]
            self.assertEqual(
                (publication.version, publication.status, publication.published_at, publication.withdrawn_at),
                old[:4],
            )
            self.assertNotEqual(publication.content_hash, old[4])
            self.assertEqual(publication.source_filename, "秋季月考任务")
            self.assertEqual(publication.content["source_filename"], "秋季月考任务")
            self.assertEqual(publication.content_hash, library.content_hash(publication.content))
            self.assertEqual(publication.content["review"]["approved_content_hash"], publication.content_hash)
            self.assertIn(library.search_key("秋季月考任务"), publication.search_text)
            self.assertNotIn(library.search_key("卷.pdf"), publication.search_text)

        listing = self.client.get("/api/library?q=秋季月考任务").json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["source_filename"], "秋季月考任务")
        self.assertEqual(listing["facets"]["sources"], [{
            "document_id": str(self.paper.id), "filename": "秋季月考任务", "count": 1,
        }])
        self.assertEqual(self.client.get("/api/library?q=卷.pdf").json()["total"], 0)

    def test_rename_keeps_an_already_stale_approval_stale(self):
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        self.q.refresh_from_db()
        approved_hash = self.q.approved_content_hash
        Question.objects.filter(pk=self.q.pk).update(stem="审批后被后台改过")
        self.q.refresh_from_db()
        self.assertFalse(library.approval_is_current(self.q))

        response = self.patch(f"/api/papers/{self.paper.id}", {"name": "改名后仍需复核"})
        self.assertEqual(response.status_code, 200, response.content)
        self.q.refresh_from_db()
        self.assertTrue(self.q.approved)
        self.assertEqual(self.q.approved_content_hash, approved_hash)
        self.assertFalse(library.approval_is_current(self.q))

    def test_rename_validates_name_and_keeps_original_filename(self):
        endpoint = f"/api/papers/{self.paper.id}"
        for invalid in (None, "", "   ", "甲\n乙", "甲\x00乙", "甲" * 256):
            with self.subTest(invalid=repr(invalid)):
                response = self.patch(endpoint, {"name": invalid})
                self.assertEqual(response.status_code, 400)
                self.paper.refresh_from_db()
                self.assertEqual(self.paper.task_name, "")
                self.assertEqual(self.paper.filename, "卷.pdf")

        same = self.patch(endpoint, {"name": "卷.pdf"})
        self.assertEqual(same.status_code, 200)
        self.assertFalse(same.json()["changed"])
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.task_name, "")
        self.assertEqual(self.paper.filename, "卷.pdf")

    def test_paper_rename_and_delete_require_local_page_header(self):
        failed = Paper.objects.create(
            filename="失败.pdf", kind="pdf", sha256="h" * 64, status=Paper.Status.FAILED,
        )
        rename = self.patch(f"/api/papers/{self.paper.id}", {"name": "不应生效"}, header=False)
        delete = self.delete(f"/api/papers/{failed.id}", header=False)
        self.assertEqual(rename.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.task_name, "")
        self.assertTrue(Paper.objects.filter(pk=failed.pk).exists())

    def test_delete_failed_task_removes_related_drafts_and_controlled_folder(self):
        failed = Paper.objects.create(
            filename="失败.pdf", kind="pdf", sha256="d" * 64, status=Paper.Status.FAILED,
        )
        folder = self.temp / str(failed.id)
        folder.mkdir()
        source = folder / "source.pdf"
        source.write_bytes(b"private draft")
        failed.source_path = str(source)
        failed.save(update_fields=["source_path"])
        draft = Question.objects.create(paper=failed, number=1, stem="未完成草稿")
        block = Block.objects.create(
            paper=failed, seq=0, type="text", page_idx=0, bbox=[0, 0, 10, 10], text="草稿",
        )

        response = self.delete(f"/api/papers/{failed.id}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["deleted"], str(failed.id))
        self.assertFalse(Paper.objects.filter(pk=failed.pk).exists())
        self.assertFalse(Question.objects.filter(pk=draft.pk).exists())
        self.assertFalse(Block.objects.filter(pk=block.pk).exists())
        self.assertFalse(folder.exists())

    def test_delete_keeps_task_when_its_files_cannot_be_staged(self):
        failed = Paper.objects.create(
            filename="占用中.pdf", kind="pdf", sha256="b" * 64, status=Paper.Status.FAILED,
        )
        folder = self.temp / str(failed.id)
        folder.mkdir()
        (folder / "source.pdf").write_bytes(b"busy")
        with mock.patch("pathlib.Path.replace", side_effect=OSError("busy")):
            response = self.delete(f"/api/papers/{failed.id}")
        self.assertEqual(response.status_code, 400)
        self.assertIn("被占用", response.json()["error"])
        self.assertTrue(Paper.objects.filter(pk=failed.pk).exists())
        self.assertTrue(folder.exists())

    def test_delete_rejects_nonfailed_and_any_task_with_publication_history(self):
        ready = self.delete(f"/api/papers/{self.paper.id}")
        self.assertEqual(ready.status_code, 400)
        self.assertTrue(Paper.objects.filter(pk=self.paper.pk).exists())

        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        self.post(f"/api/papers/{self.paper.id}/publish")
        Paper.objects.filter(pk=self.paper.pk).update(status=Paper.Status.FAILED)
        blocked = self.delete(f"/api/papers/{self.paper.id}")
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("正式题库", blocked.json()["error"])
        self.assertTrue(Paper.objects.filter(pk=self.paper.pk).exists())
        self.assertTrue(PublishedQuestion.objects.filter(paper_id=self.paper.pk).exists())

    def test_text_edit_clears_text_flags_but_requires_separate_approval(self):
        data = self.post(f"/api/questions/{self.q2.id}/text", {"stem": "求证：AB=CD", "question_type": "free_response"}).json()
        self.assertFalse(data["question"]["approved"])
        self.assertFalse(data["question"]["approval_valid"])
        self.assertEqual(data["question"]["state"], "green")
        self.assertEqual(data["question"]["text_source"], "human")
        approved = self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True}).json()["question"]
        self.assertTrue(approved["approved"])
        self.assertTrue(approved["approval_valid"])

    def test_manual_text_edit_normalizes_parallelogram_notation(self):
        data = self.post(f"/api/questions/{self.q.id}/text", {
            "stem": r"如图，在 $\square ABCD$ 中",
            "options": {"A": "□EFGH", "B": r"$3\square 5=15$"},
            "answer": r"\Box WXYZ",
            "analysis": r"由 $\text{□}ABCD$ 可知",
            "question_type": "single_choice",
        }).json()["question"]
        self.assertEqual(data["stem"], "如图，在 ▱ABCD 中")
        self.assertEqual(data["options"], {"A": "▱EFGH", "B": r"$3\square 5=15$"})
        self.assertEqual(data["answer"], "▱WXYZ")
        self.assertEqual(data["analysis"], "由 ▱ABCD 可知")
        self.assertFalse(data["approved"])

    def test_regions_change_requests_reread(self):
        data = self.post(f"/api/questions/{self.q.id}/regions",
                         {"regions": [{"page_idx": 0, "bbox": [40, 90, 490, 320]}]}).json()
        q = data["question"]
        self.assertEqual(q["state"], "waiting")
        self.assertTrue(q["regions_changed"])
        self.assertEqual(q["figures"], [])
        self.assertTrue(Question.objects.get(pk=self.q.id).reread_requested)
        bad = self.post(f"/api/questions/{self.q.id}/regions", {"regions": [{"page_idx": 3, "bbox": [0, 0, 10, 10]}]})
        self.assertEqual(bad.status_code, 400)

    def test_figures_manual(self):
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        data = self.post(f"/api/questions/{self.q.id}/figures",
                         {"figures": [{"slot": "A", "page_idx": 0, "bbox": [60, 200, 120, 260]}]}).json()
        self.assertEqual(data["question"]["figures"][0]["source"], "manual")
        self.assertFalse(data["question"]["approved"])
        self.assertFalse(Question.objects.get(pk=self.q.id).approved_content_hash)
        image = self.client.get(data["question"]["figures"][0]["url"])
        self.assertEqual(image.status_code, 200)

    def test_cannot_approve_empty(self):
        Question.objects.filter(pk=self.q2.id).update(stem="")
        self.assertEqual(self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True}).status_code, 400)

    def test_cannot_approve_unreviewable_states(self):
        for state in (Question.State.RED, Question.State.WAITING, Question.State.READING):
            Question.objects.filter(pk=self.q.id).update(state=state, approved=False, approved_content_hash="")
            response = self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
            self.assertEqual(response.status_code, 400, state)
        Question.objects.filter(pk=self.q.id).update(state=Question.State.YELLOW, flags=["请人工核对"])
        self.assertEqual(self.post(f"/api/questions/{self.q.id}/approve", {"approved": True}).status_code, 200)

    def test_publish_rejects_stale_approval_hash(self):
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        Question.objects.filter(pk=self.q.id).update(stem="审批后被其他代码改过")
        result = self.post(f"/api/papers/{self.paper.id}/publish").json()
        self.assertEqual(result["created"], 0)
        self.assertTrue(any("重新终审" in problem for problem in result["problems"]))
        self.assertFalse(PublishedQuestion.objects.exists())

    def test_publish_rejects_red_even_with_matching_hash(self):
        self.q.state = Question.State.RED
        self.q.approved = True
        self.q.approved_content_hash = library.approval_hash(self.q)
        self.q.save(update_fields=["state", "approved", "approved_content_hash"])
        result = self.post(f"/api/papers/{self.paper.id}/publish").json()
        self.assertEqual(result["created"], 0)
        self.assertTrue(any("当前状态不能入库" in problem for problem in result["problems"]))
        self.assertFalse(PublishedQuestion.objects.exists())

    def test_source_range_change_creates_new_version_after_reapproval(self):
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        self.post(f"/api/papers/{self.paper.id}/publish")
        self.post(f"/api/questions/{self.q.id}/regions",
                  {"regions": [{"page_idx": 0, "bbox": [40, 90, 490, 320]}]})
        # 人工确认文字无误后，题卡先进入可审核态，再单独通过。
        self.post(f"/api/questions/{self.q.id}/text",
                  {"stem": self.q.stem, "options": self.q.options, "question_type": self.q.question_type})
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        result = self.post(f"/api/papers/{self.paper.id}/publish").json()
        self.assertEqual(result["created"], 1)
        versions = list(PublishedQuestion.objects.order_by("version"))
        self.assertEqual([item.version for item in versions], [1, 2])
        self.assertNotEqual(versions[0].content_hash, versions[1].content_hash)
        self.assertEqual(versions[1].content["sources"][0]["source"], "manual")

    def test_resegment_endpoint(self):
        Block.objects.create(paper=self.paper, seq=0, type="text", page_idx=0, bbox=[50, 100, 480, 130], text="1. 已知")
        data = self.post(f"/api/papers/{self.paper.id}/resegment").json()
        self.assertEqual(data["paper"]["status"], "segmenting")
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/resegment").status_code, 400)

    def test_card_without_regions_turns_red_with_hint(self):
        store = pipeline.PageStore(self.paper)
        with mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "k"}):
            result = pipeline.read_card({"id": 1, "number": 9, "regions": [], "candidates": [],
                                         "question_type": "single_choice"}, store)
        self.assertEqual(result["state"], "red")
        self.assertIn("调整范围", result["error"])

    def test_page_store_bounds_decoded_page_memory(self):
        store = pipeline.PageStore(self.paper)
        store.MAX_MEMORY_PAGES = 2
        with mock.patch("core.pipeline.imaging.render_source_page",
                        side_effect=lambda *_args: Image.new("RGB", (12, 12), "white")):
            for page_idx in range(3):
                store.load(page_idx)
        self.assertEqual(list(store.memory), [1, 2])

    def test_add_and_delete_question(self):
        data = self.post(f"/api/papers/{self.paper.id}/questions",
                         {"number": 3, "regions": [{"page_idx": 0, "bbox": [520, 100, 950, 300]}]})
        self.assertEqual(data.status_code, 201)
        new_id = data.json()["question"]["id"]
        self.assertTrue(Question.objects.get(pk=new_id).reread_requested)
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/questions",
                                   {"number": 3, "regions": [{"page_idx": 0, "bbox": [520, 100, 950, 300]}]}).status_code, 400)
        response = self.client.delete(f"/api/questions/{new_id}", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200)

    def test_page_preview_and_detail(self):
        self.assertEqual(self.client.get(f"/api/papers/{self.paper.id}/pages/0/preview").status_code, 200)
        self.assertEqual(self.client.get(f"/api/documents/{self.paper.id}/pages/0/preview").status_code, 200)
        detail = self.client.get(f"/api/papers/{self.paper.id}").json()
        self.assertEqual(detail["paper"]["counts"]["yellow"], 1)
        self.assertEqual(len(detail["questions"]), 2)

    def test_upload_needs_credentials(self):
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "", "MINIMAX_API_KEY": ""}):
            response = self.client.post("/api/papers", {"file": io.BytesIO(b"%PDF-1.4")}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 400)

    def test_upload_creates_queued_paper(self):
        source = self.temp / "valid-upload.pdf"
        fake_page_pdf(source)
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"}):
            upload = io.BytesIO(source.read_bytes())
            upload.name = "新卷.pdf"
            response = self.client.post("/api/papers", {"file": upload}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get(filename="新卷.pdf")
        self.assertEqual(paper.status, Paper.Status.QUEUED)
        self.assertEqual(len(paper.pages), 1)
        self.assertTrue(Path(paper.source_path).is_file())

    def test_upload_pdf_preflight_accepts_exactly_200_pages(self):
        pages = [{"page_idx": index, "width": 842, "height": 595} for index in range(200)]
        upload = io.BytesIO(b"mock PDF at the supported page limit")
        upload.name = "200页.pdf"
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"}), \
                mock.patch("core.views.imaging.page_sizes", return_value=pages):
            response = self.client.post("/api/papers", {"file": upload}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get(filename="200页.pdf")
        self.assertEqual(len(paper.pages), 200)
        self.assertTrue((self.temp / str(paper.id)).is_dir())

    def test_upload_pdf_preflight_rejects_201_pages_without_orphan_task_or_folder(self):
        before_ids = set(Paper.objects.values_list("id", flat=True))
        before_folders = {path.name for path in self.temp.iterdir() if path.is_dir()}
        pages = [{"page_idx": index, "width": 842, "height": 595} for index in range(201)]
        upload = io.BytesIO(b"mock PDF over the supported page limit")
        upload.name = "201页.pdf"
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"}), \
                mock.patch("core.views.imaging.page_sizes", return_value=pages):
            response = self.client.post("/api/papers", {"file": upload}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 400)
        self.assertIn("200 页", response.json()["error"])
        self.assertEqual(set(Paper.objects.values_list("id", flat=True)), before_ids)
        self.assertEqual({path.name for path in self.temp.iterdir() if path.is_dir()}, before_folders)

    def test_rejected_upload_cleanup_failure_leaves_only_reconcilable_staging(self):
        pages = [{"page_idx": index, "width": 842, "height": 595} for index in range(201)]
        upload = io.BytesIO(b"mock private PDF")
        upload.name = "稍后清理.pdf"
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"}), \
                mock.patch("core.views.imaging.page_sizes", return_value=pages), \
                mock.patch("core.views.shutil.rmtree", side_effect=OSError("busy")):
            response = self.client.post("/api/papers", {"file": upload}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Paper.objects.filter(filename="稍后清理.pdf").exists())
        leftovers = [path.name for path in self.temp.iterdir() if path.is_dir() and path.name.startswith(".uploading-")]
        self.assertEqual(len(leftovers), 1)
