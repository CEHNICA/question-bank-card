"""Tables that are only words and numbers are question text, not pictures."""

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import library, tables
from .figure_policy import automatic_review
from .mineru import load_blocks
from .models import Block, Paper, Question
from .pipeline import _table_check_flag, _without_inferred_figure_text, paper_dir
from .readers import FIGURE_RULES, TRANSCRIBE_RULES, parse_reading
from .textnorm import witness_agrees, witness_key

CONTINGENCY = "| | 优级品 | 非优级品 |\n|---|---|---|\n| 甲车间 | | |\n| 乙车间 | | |"
MINERU_DATA = ("<table><tr><td></td><td>优级品</td><td>合格品</td></tr>"
               "<tr><td>甲车间</td><td>26</td><td>24</td></tr><tr><td>乙车间</td><td>70</td><td>28</td></tr></table>")
MINERU_CONTINGENCY = ("<table><tr><td></td><td>优级品</td><td>非优级品</td></tr>"
                      "<tr><td>甲车间</td><td></td><td></td></tr><tr><td>乙车间</td><td></td><td></td></tr></table>")


class TableTextTests(SimpleTestCase):
    def test_pipe_table_is_found_with_its_cells(self):
        text = f"（1）填写如下列联表：\n{CONTINGENCY}\n能否认为有差异？"
        [table] = tables.markdown_tables(text)
        self.assertTrue(table["header"])
        self.assertEqual(table["rows"], [["", "优级品", "非优级品"], ["甲车间", "", ""], ["乙车间", "", ""]])
        self.assertEqual(text[table["start"]:table["end"]], CONTINGENCY)
        self.assertEqual(tables.without_tables(text), "（1）填写如下列联表：\n\n能否认为有差异？")

    def test_absolute_value_is_not_a_table_and_stays_in_a_cell(self):
        self.assertEqual(tables.markdown_tables("若 |x|=2，且\n|y|=3"), [])
        [table] = tables.markdown_tables("| $|x|$ | a\\|b |\n| 1 | 2 |")
        self.assertEqual(table["rows"][0], ["$|x|$", "a|b"])
        self.assertFalse(table["header"])

    def test_mineru_html_becomes_markdown_or_clean_html(self):
        self.assertEqual(tables.to_text(MINERU_CONTINGENCY),
                         "|  | 优级品 | 非优级品 |\n|---|---|---|\n| 甲车间 |  |  |\n| 乙车间 |  |  |")
        entity = tables.to_text("<table><tr><td> $\\triangle ABC$ </td><td>A&#x27;(4,2)</td></tr>"
                                "<tr><td>x|y</td><td>1</td></tr></table>")
        self.assertIn("| $\\triangle ABC$ | A'(4,2) |", entity)
        self.assertIn("| x\\|y | 1 |", entity)
        merged = tables.to_text('<table><tr><td rowspan="2">x</td><td>1</td></tr><tr><td>2</td></tr></table>')
        self.assertEqual(merged, '<table><tr><td rowspan="2">x</td><td>1</td></tr><tr><td>2</td></tr></table>')
        self.assertEqual([t["rows"] for t in tables.all_tables(merged)], [[["x", "1"], ["2"]]])

    def test_table_goes_after_the_line_printed_before_it(self):
        stem = "某工厂检验，数据如下：\n（1）填写如下列联表：\n能否认为有差异？"
        placed = tables.insert_table(stem, CONTINGENCY, ["（1）填写如下列联表："], key=witness_key)
        self.assertEqual(placed, f"某工厂检验，数据如下：\n（1）填写如下列联表：\n{CONTINGENCY}\n能否认为有差异？")
        # The number printed before the question text does not stop the match.
        placed = tables.insert_table(stem, "| a | b |\n| 1 | 2 |", ["17. 某工厂检验，数据如下："], key=witness_key)
        self.assertTrue(placed.startswith("某工厂检验，数据如下：\n| a | b |"))
        self.assertEqual(tables.insert_table("已知", "| a | b |\n| 1 | 2 |", ["无关的话"], key=witness_key),
                         "已知\n| a | b |\n| 1 | 2 |")

    def test_prose_check_leaves_the_table_out(self):
        reading = {"stem": f"某工厂检验，数据如下：\n{CONTINGENCY}\n能否认为两车间有差异？", "options": {}}
        self.assertTrue(witness_agrees(reading, "某工厂检验，数据如下：能否认为两车间有差异？"))

    def test_written_table_is_checked_against_mineru(self):
        stem = f"填写如下列联表：\n{CONTINGENCY}"
        self.assertEqual(_table_check_flag(stem, [MINERU_CONTINGENCY], {"1": "table"}), "")
        wrong = stem.replace("非优级品", "合格品")
        self.assertIn("MinerU", _table_check_flag(wrong, [MINERU_CONTINGENCY], {}))
        self.assertIn("只有一次识读", _table_check_flag(stem, [], {}))
        self.assertIn("没有写出表格", _table_check_flag("填写如下列联表：", [], {"1": "table"}))
        self.assertEqual(_table_check_flag("求值。", [], {"1": "stem"}), "")


class ReadingTableTests(SimpleTestCase):
    def test_prompt_asks_for_text_tables(self):
        self.assertIn("按 Markdown 表格逐格照抄", TRANSCRIBE_RULES)
        self.assertIn("切成两截的同一张表，接起来写成一张表", TRANSCRIBE_RULES)
        self.assertIn("编号=表格", FIGURE_RULES)

    def test_table_role_is_parsed_and_resolves_the_candidate(self):
        raw = f"【题号】17\n【题型】解答题\n【题干】\n填写如下列联表：\n{CONTINGENCY}\n【配图】1=表格, 2=题干\n【其他题号】无"
        reading = parse_reading(raw, 17)
        self.assertEqual(reading["figures"], {"1": "table", "2": "stem"})
        self.assertIn("| 甲车间 | | |", reading["stem"])
        review = automatic_review(stem=reading["stem"], options={}, candidate_labels={"1"},
                                  assignments={"1": "table"}, figures=[])
        self.assertNotIn("candidate_unclassified", review["signals"])
        self.assertIn("candidate_text_table", review["signals"])
        self.assertEqual(review["status"], "ok")

    def test_written_table_survives_unless_it_copies_a_bound_crop(self):
        reading = {"stem": f"如图，填写下表：\n{CONTINGENCY}", "options": {}}
        kept = _without_inferred_figure_text(reading, {"figures": {"1": "stem", "2": "table"}})
        self.assertIn(CONTINGENCY, kept["stem"])
        copied = _without_inferred_figure_text(reading, {"figures": {"1": "stem"}})
        self.assertNotIn("甲车间", copied["stem"])


class TableBlockTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        override = override_settings(DATA_ROOT=self.root)
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="gaokao.pdf", kind="pdf", sha256="d" * 64, source_path=str(self.root / "s.pdf"),
            pages=[{"page_idx": 2, "width": 1000, "height": 1000}, {"page_idx": 3, "width": 1000, "height": 1000}],
            status=Paper.Status.READY,
        )

    def archive(self, entries: list[dict]) -> Path:
        path = paper_dir(self.paper) / "mineru_result.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("x/x_content_list.json", json.dumps(entries))
        return path

    def test_new_parse_keeps_the_table_html(self):
        path = self.archive([
            {"type": "text", "text": "数据如下：", "bbox": [80, 580, 900, 600], "page_idx": 0},
            {"type": "table", "table_body": MINERU_DATA, "bbox": [87, 615, 907, 769], "page_idx": 0},
        ])
        blocks = load_blocks(path, 1)
        self.assertNotIn("html", blocks[0])
        self.assertEqual(blocks[1]["html"], MINERU_DATA)
        self.assertEqual(blocks[1]["text"], "")

    def make_card(self):
        entries = [
            {"type": "text", "text": "17. 某工厂检验，数据如下：", "bbox": [80, 580, 900, 600], "page_idx": 2},
            {"type": "table", "table_body": MINERU_DATA, "bbox": [87, 615, 907, 769], "page_idx": 2},
            {"type": "text", "text": "（1）填写如下列联表：", "bbox": [80, 790, 900, 810], "page_idx": 2},
            # MinerU joins a table a page break cut: every row is in the first piece.
            {"type": "table", "table_body": MINERU_CONTINGENCY, "bbox": [87, 824, 907, 900], "page_idx": 2},
            {"type": "table", "bbox": [87, 51, 907, 91], "page_idx": 3},
            {"type": "text", "text": "能否认为有差异？", "bbox": [80, 100, 900, 120], "page_idx": 3},
        ]
        self.archive(entries)
        # An older paper: the blocks were saved before the HTML was kept.
        for seq, entry in enumerate(entries):
            Block.objects.create(paper=self.paper, seq=seq, type=entry["type"], page_idx=entry["page_idx"],
                                 bbox=entry["bbox"], text=entry.get("text", ""))
        candidates = [{"page_idx": 2, "bbox": [87, 615, 907, 769]}, {"page_idx": 2, "bbox": [87, 824, 907, 900]},
                      {"page_idx": 3, "bbox": [87, 51, 907, 91]}]
        return Question.objects.create(
            paper=self.paper, number=17, question_type="free_response",
            regions=[{"page_idx": 2, "bbox": [50, 560, 950, 995]}, {"page_idx": 3, "bbox": [50, 5, 950, 130]}],
            regions_auto=[{"page_idx": 2, "bbox": [50, 560, 950, 995]}, {"page_idx": 3, "bbox": [50, 5, 950, 130]}],
            figure_candidates=candidates,
            figures=[{"slot": "stem", "source": "auto", **candidate} for candidate in candidates],
            stem="某工厂检验，数据如下：\n（1）填写如下列联表：\n能否认为有差异？",
            state=Question.State.GREEN, approved=True, approved_at=timezone.now(),
        )

    def post(self, question, body):
        return self.client.post(f"/api/questions/{question.pk}/figure-table", data=json.dumps(body),
                                 content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_old_paper_learns_its_tables_once(self):
        self.make_card()
        found = tables.table_blocks(self.paper)
        self.assertEqual([bool(item["html"]) for item in found], [True, True, False])
        # The cut-off piece has no HTML; the archive is not opened again for it.
        (paper_dir(self.paper) / "mineru_result.zip").unlink()
        self.assertEqual([bool(item["html"]) for item in tables.table_blocks(self.paper)], [True, True, False])

    def test_table_figures_turn_into_text_tables_in_place(self):
        question = self.make_card()
        detail = self.client.get(f"/api/papers/{self.paper.id}").json()["questions"][0]
        self.assertEqual([figure.get("table") for figure in detail["figures"]], [True, True, True])

        # The piece cut off by the page break leads to the whole joined table.
        response = self.post(question, {"figure": 2})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["question"]
        self.assertEqual(len(data["figures"]), 1)
        self.assertIn("（1）填写如下列联表：\n|  | 优级品 | 非优级品 |", data["stem"])
        self.assertIn("| 乙车间 |  |  |\n能否认为有差异？", data["stem"])
        self.assertFalse(data["approved"])

        response = self.post(question, {"figure": 0})
        data = response.json()["question"]
        self.assertEqual(data["figures"], [])
        self.assertTrue(data["stem"].startswith("某工厂检验，数据如下：\n|  | 优级品 | 合格品 |"))
        self.assertFalse(data["figure_blocked"])
        question.refresh_from_db()
        self.assertTrue(question.edited)
        self.assertEqual(sorted(question.figure_review["ignored_candidates"]),
                         ["2:87,615,907,769", "2:87,824,907,900", "3:87,51,907,91"])
        self.assertEqual(len(tables.all_tables(question.stem)), 2)
        self.assertEqual(library.final_content(question)["figures"], [])

    def test_a_crop_that_is_not_a_table_is_refused(self):
        question = self.make_card()
        question.figures = [{"slot": "stem", "source": "auto", "page_idx": 3, "bbox": [500, 500, 700, 700]}]
        question.save()
        self.assertEqual(self.post(question, {"figure": 0}).status_code, 400)
        self.assertEqual(self.post(question, {"figure": 5}).status_code, 400)
