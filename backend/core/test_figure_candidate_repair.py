"""找回丢失候选图与识读前防复发：以第 46 节例 1（菱形 ABCD）及存量 47 张误报卡为原型。"""

from copy import deepcopy
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from . import figure_policy, pipeline, test_manual_intake_review as manual_review
from .figure_candidate_repair import repair_lost_figure_candidates
from .management.commands.run_worker import clean_saved_example_labels
from .models import Block, Paper, Question

STEM = "如图，已知菱形 $ABCD$ 的边长为 $2$，$\\angle BAD=120^\\circ$，则 $\\overrightarrow{AE}\\cdot\\overrightarrow{AF}$ 的最小值为________。"
FIGURE_BBOX = [697.0, 816.0, 875.0, 902.0]
# 与真库 #1426 一致：题干一块、右侧配图一块，两块都在本题范围里。
REGIONS = [{"page_idx": 170, "bbox": [100, 813, 685, 909]}, {"page_idx": 170, "bbox": [693, 812, 879, 906]}]


def make_paper(name: str = "book.pdf", material_type: str = Paper.MaterialType.BOOK) -> Paper:
    return Paper.objects.create(
        filename=name,
        kind="pdf",
        sha256=(name * 64)[:64],
        source_path=f"data/{name}",
        material_type=material_type,
        status=Paper.Status.READY,
        processing_plan={"revision": 1},
    )


def make_figure_block(paper: Paper, seq: int = 2417, bbox: list[float] | None = None) -> Block:
    return Block.objects.create(paper=paper, seq=seq, type="image", page_idx=170, bbox=bbox or FIGURE_BBOX, text="")


def make_lost_question(paper: Paper, **overrides) -> Question:
    """读者当时看到了图（figures={"1": "stem"}），但候选列表之后被清空，于是被判成漏图。"""
    values = {
        "paper": paper,
        "number": 1,
        "stem": STEM,
        "question_type": "fill_blank",
        "regions": REGIONS,
        "regions_auto": REGIONS,
        "figure_candidates": [],
        "figures": [],
        "read_a": {
            "stem": STEM,
            "type": "fill_blank",
            "figures": {"1": "stem"},
            "missing_figure": False,
            "figure_descriptions": [],
        },
        "figure_review": {"status": figure_policy.BLOCKED_MISSING, "source": "automatic"},
        "flags": [figure_policy.FLAG_NO_FIGURE],
        "state": Question.State.YELLOW,
    }
    values.update(overrides)
    return Question.objects.create(**values)


class RepairLostFigureCandidatesTests(TestCase):
    def test_apply_recovers_candidate_binds_figure_and_clears_false_missing(self):
        paper = make_paper()
        make_figure_block(paper)
        question = make_lost_question(paper)

        report = repair_lost_figure_candidates([paper], apply=True)

        question.refresh_from_db()
        self.assertEqual(report.recovered, 1)
        self.assertEqual(report.cleared_to_green, 1)
        self.assertEqual([item["seq"] for item in question.figure_candidates], [2417])
        self.assertEqual([figure["slot"] for figure in question.figures], ["stem"])
        self.assertEqual(question.figure_review["status"], figure_policy.OK)
        self.assertEqual(question.state, Question.State.GREEN)
        self.assertNotIn(figure_policy.FLAG_NO_FIGURE, question.flags)
        # 读者原始记录是审计证据，不能被改写。
        self.assertEqual(question.read_a["figures"], {"1": "stem"})

    def test_dangling_question_number_key_is_recovered_and_turns_green(self):
        # 无候选框识读时模型把第 5 题的图记成 {"5": "stem"}，但范围里其实只有 1 张候选图 "1"。
        paper = make_paper()
        make_figure_block(paper, seq=644)
        question = make_lost_question(
            paper,
            number=5,
            question_type="free_response",
            read_a={
                "stem": STEM,
                "type": "free_response",
                "figures": {"5": "stem"},
                "missing_figure": False,
                "figure_descriptions": [],
            },
        )

        report = repair_lost_figure_candidates([paper], apply=True)

        question.refresh_from_db()
        self.assertEqual(report.recovered, 1)
        self.assertEqual(report.cleared_to_green, 1)
        self.assertEqual([figure["slot"] for figure in question.figures], ["stem"])
        self.assertEqual(question.figure_review["status"], figure_policy.OK)
        self.assertEqual(question.state, Question.State.GREEN)
        self.assertEqual(question.read_a["figures"], {"5": "stem"})

    def test_complete_four_option_choice_with_dangling_key_turns_green(self):
        paper = make_paper()
        make_figure_block(paper, seq=645)
        for kind in ("single_choice", "multiple_choice"):
            with self.subTest(kind=kind):
                question = make_lost_question(
                    paper,
                    number=5,
                    question_type=kind,
                    options={"A": "$1$", "B": "$2$", "C": "$3$", "D": "$4$"},
                    read_a={
                        "stem": "如图，下列值正确的是（ ）。",
                        "options": {"A": "$1$", "B": "$2$", "C": "$3$", "D": "$4$"},
                        "type": kind,
                        "figures": {"5": "stem"},
                        "missing_figure": False,
                        "figure_descriptions": [],
                    },
                    stem="如图，下列值正确的是（ ）。",
                )
                report = repair_lost_figure_candidates([paper], apply=True)
                question.refresh_from_db()
                self.assertEqual(report.recovered, 1)
                self.assertEqual(report.cleared_to_green, 1)
                self.assertEqual([figure["slot"] for figure in question.figures], ["stem"])
                self.assertEqual(question.state, Question.State.GREEN)
                question.delete()

    def test_choice_missing_option_d_stays_yellow_and_rolls_back(self):
        # 类似真库 #1223：缺 D 选项时，范围里这张唯一图块不能盲绑给题干。
        paper = make_paper()
        make_figure_block(paper, seq=915)
        question = make_lost_question(
            paper,
            number=2,
            question_type="single_choice",
            options={"A": "1", "B": "2", "C": "3"},
            read_a={
                "stem": "如图，求值（ ）。",
                "options": {"A": "1", "B": "2", "C": "3"},
                "type": "single_choice",
                "figures": {"2": "stem"},
                "missing_figure": False,
                "figure_descriptions": [],
            },
            stem="如图，求值（ ）。",
        )

        report = repair_lost_figure_candidates([paper], apply=True)

        question.refresh_from_db()
        self.assertEqual(report.recovered, 0)
        self.assertEqual(report.skipped_unresolved, 1)
        self.assertEqual(question.figure_candidates, [])
        self.assertEqual(question.state, Question.State.YELLOW)

    def test_multiple_non_badge_candidates_are_not_guessed_and_roll_back(self):
        # 类似真库 #1540（两张折纸图）或 #1200（五张图片选择题）：有多张普通候选图时不能盲猜归属。
        paper = make_paper()
        make_figure_block(paper, seq=3341, bbox=[304.0, 820.0, 455.0, 890.0])
        make_figure_block(paper, seq=3342, bbox=[465.0, 820.0, 680.0, 895.0])
        question = make_lost_question(
            paper,
            number=3,
            question_type="single_choice",
            options={"A": "1", "B": "2", "C": "3", "D": "4"},
            read_a={
                "stem": "如图，折起后正确的是（ ）。",
                "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
                "type": "single_choice",
                "figures": {"3": "stem"},
                "missing_figure": False,
                "figure_descriptions": [],
            },
            stem="如图，折起后正确的是（ ）。",
        )

        report = repair_lost_figure_candidates([paper], apply=True)

        question.refresh_from_db()
        self.assertEqual(report.recovered, 0)
        self.assertEqual(report.skipped_unresolved, 1)
        self.assertEqual(question.figure_candidates, [])
        self.assertEqual(question.state, Question.State.YELLOW)

    def test_preview_rolls_back_and_leaves_database_untouched(self):
        paper = make_paper()
        make_figure_block(paper)
        question = make_lost_question(paper)

        report = repair_lost_figure_candidates([paper], apply=False)

        question.refresh_from_db()
        self.assertEqual(report.recovered, 1)
        self.assertEqual(report.items[0].state_after, Question.State.GREEN)
        self.assertEqual(question.figure_candidates, [])
        self.assertEqual(question.figures, [])
        self.assertEqual(question.state, Question.State.YELLOW)

    def test_human_decision_is_never_overridden(self):
        paper = make_paper()
        make_figure_block(paper)
        question = make_lost_question(
            paper,
            figure_review={"status": figure_policy.CONFIRMED_NO_FIGURE, "source": "human"},
            flags=[],
            state=Question.State.GREEN,
        )

        report = repair_lost_figure_candidates([paper], apply=True)

        question.refresh_from_db()
        self.assertEqual(report.recovered, 0)
        self.assertEqual(report.skipped_human, 1)
        self.assertEqual(question.figure_candidates, [])

    def test_question_with_existing_candidates_is_skipped(self):
        paper = make_paper()
        make_figure_block(paper)
        existing = [{"label": "1", "seq": 2417, "page_idx": 170, "bbox": FIGURE_BBOX}]
        make_lost_question(paper, figure_candidates=existing)

        report = repair_lost_figure_candidates([paper], apply=True)

        self.assertEqual(report.recovered, 0)
        self.assertEqual(report.skipped_has_figure, 1)

    def test_figure_owned_by_another_question_is_not_stolen(self):
        paper = make_paper()
        make_figure_block(paper)
        owner = {"label": "1", "seq": 2417, "page_idx": 170, "bbox": FIGURE_BBOX}
        make_lost_question(paper, number=2, figure_candidates=[owner], state=Question.State.GREEN, flags=[])
        lost = make_lost_question(paper, number=1)

        report = repair_lost_figure_candidates([paper], apply=True)

        lost.refresh_from_db()
        self.assertEqual(report.recovered, 0)
        self.assertEqual(lost.figure_candidates, [])

    def test_book_requires_figure_centre_inside_regions(self):
        # 图块只有边缘擦到本题范围（中心在范围外）：教材切题时刻意排除，修复也不能拿回来。
        paper = make_paper()
        make_figure_block(paper, bbox=[860.0, 700.0, 960.0, 820.0])
        make_lost_question(paper)

        report = repair_lost_figure_candidates([paper], apply=True)

        self.assertEqual(report.recovered, 0)
        self.assertEqual(report.skipped_nothing_found, 1)

    def test_green_question_is_not_touched(self):
        # 绿卡本来没问题：重算候选只会把装饰性图块变成「未分类候选」，那是新增误报。
        paper = make_paper()
        make_figure_block(paper)
        question = make_lost_question(
            paper,
            stem="求函数的最大值。",
            figure_review={"status": figure_policy.OK, "source": "automatic"},
            flags=[],
            state=Question.State.GREEN,
        )

        report = repair_lost_figure_candidates([paper], apply=True)

        question.refresh_from_db()
        self.assertEqual(report.recovered, 0)
        self.assertEqual(report.skipped_not_missing, 1)
        self.assertEqual(question.figure_candidates, [])
        self.assertEqual(question.state, Question.State.GREEN)

    def test_unresolved_question_is_rolled_back_untouched(self):
        # 读者明确说这张图「不属于本题」：自动策略不会推翻，修复必须整张回滚，不能留下半成品。
        paper = make_paper()
        make_figure_block(paper)
        question = make_lost_question(paper, read_a={"stem": STEM, "type": "fill_blank", "figures": {"1": "none"}})

        report = repair_lost_figure_candidates([paper], apply=True)

        question.refresh_from_db()
        self.assertEqual(report.recovered, 0)
        self.assertEqual(report.skipped_unresolved, 1)
        self.assertEqual(question.figure_candidates, [])
        self.assertEqual(question.figure_review["status"], figure_policy.BLOCKED_MISSING)
        self.assertEqual(question.state, Question.State.YELLOW)

    def test_management_command_defaults_to_preview(self):
        paper = make_paper()
        make_figure_block(paper)
        question = make_lost_question(paper)
        out = StringIO()

        call_command("repair_figure_candidates", stdout=out)

        question.refresh_from_db()
        self.assertEqual(question.figure_candidates, [])
        self.assertIn("预演", out.getvalue())

    def test_read_questions_backfills_missing_candidates_before_read_card(self):
        # 防复发：手工/原图题若 figure_candidates 为空，识读前先按当前范围回填候选图，
        # 让截图真正画出蓝色候选框，同时不抢占同卷他题图块，也不改写人工配图决定。
        paper = make_paper(name="reread-backfill.pdf")
        make_figure_block(paper, seq=2417)
        claimed_bbox = [697.0, 200.0, 875.0, 290.0]
        make_figure_block(paper, seq=2418, bbox=claimed_bbox)
        human_bbox = [697.0, 400.0, 875.0, 490.0]
        make_figure_block(paper, seq=2419, bbox=human_bbox)

        # 同卷第 9 题已占着 seq=2418，待读卡范围哪怕擦到也不能抢。
        Question.objects.create(
            paper=paper,
            number=9,
            stem="他题",
            regions=[{"page_idx": 170, "bbox": [100, 190, 890, 300]}],
            figure_candidates=[{"label": "1", "seq": 2418, "page_idx": 170, "bbox": claimed_bbox}],
            state=Question.State.GREEN,
        )
        target = Question.objects.create(
            paper=paper,
            number=1,
            stem="",
            body_mode="source_image",
            processing_mode="manual",
            reread_requested=True,
            ocr_pending=True,
            regions=[{"page_idx": 170, "bbox": [100, 190, 890, 300]}, *REGIONS],
            figure_candidates=[],
            state=Question.State.YELLOW,
        )
        human_card = Question.objects.create(
            paper=paper,
            number=2,
            stem="",
            body_mode="source_image",
            processing_mode="manual",
            reread_requested=True,
            ocr_pending=True,
            regions=[{"page_idx": 170, "bbox": [100, 390, 890, 500]}],
            figure_candidates=[],
            figures=[{"slot": "stem", "page_idx": 170, "bbox": human_bbox, "source": "manual"}],
            figure_review={"status": figure_policy.OK, "source": "human"},
            state=Question.State.YELLOW,
        )

        seen_snapshots: dict[int, dict] = {}

        def fake_read_card(snapshot: dict, _store) -> dict:
            seen_snapshots[snapshot["id"]] = deepcopy(snapshot)
            return {"state": Question.State.RED, "error": "模拟中断", "flags": []}

        with (
            mock.patch.object(pipeline, "ThreadPoolExecutor", manual_review.ImmediateExecutor),
            mock.patch.object(pipeline, "close_old_connections"),
            mock.patch.object(pipeline, "_reader_parallelism", return_value=1),
            mock.patch.object(pipeline, "PageStore"),
            mock.patch.object(pipeline, "read_card", side_effect=fake_read_card),
            mock.patch.object(pipeline, "assign_foreign_figures"),
            mock.patch.object(pipeline, "distribute_figure_rows"),
            mock.patch.object(pipeline, "audit_figure_claims"),
            mock.patch.object(pipeline, "verify_geometric_figures", return_value=False),
            mock.patch.object(pipeline, "reconcile_geometric_figure_flags"),
        ):
            pipeline.read_questions(paper, [target, human_card], revision=1)

        target.refresh_from_db()
        human_card.refresh_from_db()
        self.assertEqual([c["seq"] for c in seen_snapshots[target.pk]["candidates"]], [2417])
        self.assertEqual([c["seq"] for c in target.figure_candidates], [2417])
        self.assertEqual(seen_snapshots[human_card.pk]["candidates"], [])
        self.assertEqual(human_card.figure_candidates, [])

    def test_worker_startup_cleanup_repairs_and_is_idempotent(self):
        # 模拟带历史「AI 看到的题号」旧旗标且丢失候选的误报卡，验证启动自愈后一次清净且可幂等重复执行。
        paper = make_paper(name="startup-heal.pdf")
        make_figure_block(paper, seq=2417)
        question = make_lost_question(
            paper,
            number=5,
            flags=[
                figure_policy.FLAG_NO_FIGURE,
                "AI 看到的题号是 2，请确认",
                "这张卡的范围里还印着第 2 题的题号，卡上只有这一道；请对照原书确认那几道题是不是漏进来了",
            ],
            read_a={
                "stem": STEM,
                "type": "fill_blank",
                "figures": {"5": "stem"},
                "missing_figure": False,
                "figure_descriptions": [],
            },
        )
        out_first = StringIO()
        clean_saved_example_labels(out=out_first)

        question.refresh_from_db()
        self.assertEqual([c["seq"] for c in question.figure_candidates], [2417])
        self.assertEqual([f["slot"] for f in question.figures], ["stem"])
        self.assertEqual(question.figure_review["status"], figure_policy.OK)
        self.assertEqual(question.flags, [])
        self.assertEqual(question.state, Question.State.GREEN)
        self.assertIn("已自动找回 1 张", out_first.getvalue())

        # 第二次运行必须完全幂等、不再改动或再次计数。
        out_second = StringIO()
        clean_saved_example_labels(out=out_second)
        self.assertEqual(out_second.getvalue(), "")

    def test_approved_and_ocr_pending_questions_are_skipped(self):
        paper = make_paper(name="guard-pending-approved.pdf")
        make_figure_block(paper, seq=2417)
        make_figure_block(paper, seq=2418, bbox=[697.0, 200.0, 875.0, 290.0])
        approved_q = make_lost_question(paper, number=1)
        approved_q.approved = True
        approved_q.save(update_fields=["approved"])
        pending_q = Question.objects.create(
            paper=paper,
            number=2,
            stem=STEM,
            question_type="fill_blank",
            ocr_pending=True,
            regions=[{"page_idx": 170, "bbox": [100, 190, 890, 300]}],
            figure_candidates=[],
            figures=[],
            figure_review={"status": figure_policy.BLOCKED_MISSING, "source": "automatic"},
            flags=[figure_policy.FLAG_NO_FIGURE],
            state=Question.State.YELLOW,
        )

        report = repair_lost_figure_candidates([paper], apply=True)
        self.assertEqual(report.recovered, 0)
        approved_q.refresh_from_db()
        pending_q.refresh_from_db()
        self.assertEqual(approved_q.figure_candidates, [])
        self.assertEqual(pending_q.figure_candidates, [])

    def test_apply_commits_per_card_so_unresolved_rollback_preserves_siblings(self):
        paper = make_paper(name="per-card-commit.pdf")
        make_figure_block(paper, seq=2417)
        # 第二题范围里放两张非徽章候选图且无模型绑定，试算后无法自动转 ok 会被回滚。
        make_figure_block(paper, seq=2418, bbox=[697.0, 200.0, 875.0, 290.0])
        make_figure_block(paper, seq=2419, bbox=[697.0, 300.0, 875.0, 390.0])

        good = make_lost_question(paper, number=1)
        unresolved = Question.objects.create(
            paper=paper,
            number=2,
            stem="如图，求角的大小。",
            question_type="free_response",
            regions=[{"page_idx": 170, "bbox": [100, 190, 890, 410]}],
            figure_candidates=[],
            figures=[],
            figure_review={"status": figure_policy.BLOCKED_MISSING, "source": "automatic"},
            flags=[figure_policy.FLAG_NO_FIGURE],
            state=Question.State.YELLOW,
        )

        report = repair_lost_figure_candidates([paper], apply=True)
        self.assertEqual(report.recovered, 1)
        self.assertEqual(report.skipped_unresolved, 1)
        good.refresh_from_db()
        unresolved.refresh_from_db()
        self.assertEqual([c["seq"] for c in good.figure_candidates], [2417])
        self.assertEqual(good.state, Question.State.GREEN)
        self.assertEqual(unresolved.figure_candidates, [])
        self.assertEqual(unresolved.state, Question.State.YELLOW)
