import copy
from types import SimpleNamespace

from django.test import TestCase

from . import figure_policy, pipeline
from .models import Paper, PublishedQuestion, Question


def question_stub(**overrides):
    values = {
        "figure_review": {},
        "stem": "",
        "options": {},
        "figures": [],
        "figure_candidates": [],
        "read_a": {},
        "read_b": {},
        "read_c": {},
        "question_type": "free_response",
        "state": "green",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FigureCuePolicyV3Tests(TestCase):
    def test_new_chinese_and_english_cues_are_detected(self):
        positive = [
            "如图是正方体的展开图，回答下列问题。",
            "如图为一个三棱柱。",
            "如图显示各组的统计结果。",
            "如图给出函数图像。",
            "图中阴影部分的面积是多少？",
            "下列图象中，有可能表示指数函数的是（ ）。",
            "能反映药物含量随时间变化的图象是（ ）。",
            "利用所给图形，证明下列等式。",
            "设 $0<x<2\\pi$，填表：",
            "例7的数据见表3.1-4。",
            "函数有如下对应值表：请判断零点所在区间。",
            "下列函数图象与 x 轴均有交点，选择正确图号。",
            "表5.7-2是某港口的时刻与水深关系预报。",
            "下列供求曲线中，哪条是供应曲线？",
            "图 (1)(2)(3) 分别为函数在三个范围内的图象。",
            "上述两表反映了肉鸡数量和人口数。",
            "观察下图并回答问题。",
            "由图可知，点 A 位于第二象限。",
            "As shown, find the value of x.",
            "In the figure, AB is parallel to CD.",
            "The diagram below shows a triangular prism.",
        ]
        for text in positive:
            with self.subTest(text=text):
                self.assertTrue(figure_policy.cue_matches(text))

    def test_library_words_and_non_visual_english_are_not_false_cues(self):
        negative = [
            "在图书馆阅读数学书。",
            "比如图书馆距学校两千米。",
            "请根据图书资料回答。",
            "请认真阅读意见表达部分。",
            "查看图书馆藏目录。",
            "As shown in the equation below, solve for x.",
            "According to graph theory, a tree has no cycles.",
            "The expression in the figure of speech is metaphorical.",
            "Refer to the figure of merit when comparing the devices.",
            "As shown in the figure of speech, the phrase is metaphorical.",
            "As shown in the figure of merit, higher is better.",
            "函数值表示不超过 x 的最大整数，并画出函数的图象。",
        ]
        for text in negative:
            with self.subTest(text=text):
                self.assertEqual(figure_policy.cue_matches(text), [])

    def test_student_drawing_request_is_not_a_supplied_figure_cue(self):
        requests = [
            "画出函数 $y=3^x$ 的图象，并说明其性质。",
            "画出下列函数的图象，并通过图象判断函数的单调性。",
            "作出温度随时间变化的一个可能的示意图。",
            "在同一直角坐标系中画出函数 $y=3^x$ 和 "
            "$y=\\left(\\dfrac{1}{3}\\right)^x$ 的图象。",
            "画出函数 $f(x)$，$g(x)$ 的图象，并比较它们的最小值。",
            "Sketch the graph of y=x^2.",
        ]
        for text in requests:
            with self.subTest(text=text):
                self.assertEqual(figure_policy.cue_matches(text), [])
                self.assertTrue(figure_policy.asks_student_to_draw(text))
                review = figure_policy.automatic_review(
                    stem=text, options={}, candidate_labels=set(), assignments={}, figures=[],
                    reader_missing=True,
                )
                self.assertEqual(review["status"], figure_policy.OK)
                self.assertIn("reader_missing_resolved", review["signals"])
                self.assertIn("student_drawing_request", review["signals"])

        supplied = figure_policy.automatic_review(
            stem="根据下图画出函数的大致图象。", options={}, candidate_labels=set(),
            assignments={}, figures=[], reader_missing=True,
        )
        self.assertEqual(supplied["status"], figure_policy.BLOCKED_MISSING)
        self.assertTrue(supplied["cue_matches"])

    def test_bound_figure_resolves_coarse_reader_missing_boolean(self):
        review = figure_policy.automatic_review(
            stem="函数图象如图所示，选择正确解析式。",
            options={"A": "x", "B": "2x", "C": "3x", "D": "4x"},
            candidate_labels=set(), assignments={},
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [100, 200, 300, 400]}],
            reader_missing=True,
        )
        self.assertEqual(review["status"], figure_policy.OK)
        self.assertIn("reader_missing_resolved", review["signals"])

    def test_bound_data_table_phrase_is_contextual_not_a_missing_image_claim(self):
        stem = "汽车每小时耗油量与速度有下列数据：求最合适的函数模型。"
        without_crop = figure_policy.automatic_review(
            stem=stem, options={}, candidate_labels=set(), assignments={}, figures=[],
        )
        with_crop = figure_policy.automatic_review(
            stem=stem, options={}, candidate_labels={"1"}, assignments={"1": "stem"},
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [100, 200, 800, 300]}],
        )
        self.assertEqual(without_crop["status"], figure_policy.OK)
        self.assertEqual(with_crop["status"], figure_policy.OK)
        self.assertEqual(with_crop["cue_matches"], ["下列数据:"])

    def test_badge_geometry_is_narrow_and_never_overrides_assignment(self):
        badge = {"label": "5", "seq": 9, "page_idx": 0, "bbox": [92, 790, 129, 815]}
        same_size_elsewhere = {
            "label": "1", "seq": 10, "page_idx": 0, "bbox": [300, 790, 337, 815],
        }
        self.assertTrue(figure_policy.is_likely_textbook_section_badge(badge))
        self.assertFalse(figure_policy.is_likely_textbook_section_badge(same_size_elsewhere))
        self.assertEqual(
            figure_policy._automatic_decoration_labels(
                candidates=[badge], assignments={"5": "A"}, kind="single_choice", options={},
            ),
            set(),
        )
        self.assertEqual(
            figure_policy._automatic_decoration_labels(
                candidates=[badge], assignments={}, kind="single_choice", options={},
            ),
            set(),
        )
        self.assertEqual(
            figure_policy._automatic_decoration_labels(
                candidates=[badge], assignments={"1": "A"}, kind="single_choice", options={},
            ),
            {"5"},
        )

    def test_new_automatic_decisions_carry_version_and_input_hash(self):
        first = figure_policy.automatic_review(
            stem="如图是正方体的展开图。",
            options={"A": "核", "B": "素"},
            candidate_labels={"1"},
            assignments={"1": "stem"},
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [1, 2, 3, 4], "source": "auto"}],
        )
        same = figure_policy.automatic_review(
            stem="如图是正方体的展开图。",
            options={"A": "核", "B": "素"},
            candidate_labels={"1"},
            assignments={"1": "stem"},
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [1, 2, 3, 4], "source": "auto"}],
        )
        changed = figure_policy.automatic_review(
            stem="如图是正方体的展开图，已修改。",
            options={"A": "核", "B": "素"},
            candidate_labels={"1"},
            assignments={"1": "stem"},
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [1, 2, 3, 4], "source": "auto"}],
        )

        self.assertEqual(first["status"], figure_policy.OK)
        self.assertEqual(first["source"], "automatic")
        self.assertEqual(first["policy_version"], figure_policy.FIGURE_REVIEW_POLICY_VERSION)
        self.assertEqual(len(first["input_hash"]), 64)
        self.assertEqual(first["input_hash"], same["input_hash"])
        self.assertNotEqual(first["input_hash"], changed["input_hash"])


class StoredFigureReviewUpgradeTests(TestCase):
    def test_saved_free_response_normalisation_does_not_recreate_missing_choice_flags(self):
        question = question_stub(
            figure_review={"status": figure_policy.BLOCKED_MISSING, "source": "automatic"},
            stem=(
                "下列哪一组中的函数相同？\n"
                "(1) 比较第一组函数；\n(2) 比较第二组函数；\n(3) 比较第三组函数。"
            ),
            question_type="free_response",
            read_a={
                "stem": "原始主读", "type": "single_choice", "options": {},
                "figures": {}, "missing_figure": False, "figure_descriptions": [],
            },
            read_b={
                "stem": "原始复读", "type": "free_response", "options": {},
                "figures": {}, "missing_figure": False, "figure_descriptions": [],
            },
        )

        upgraded = figure_policy.stored_or_derived_review(question)

        self.assertEqual(upgraded["status"], figure_policy.OK)
        self.assertEqual(question.read_a["type"], "single_choice")
        self.assertEqual(question.read_b["type"], "free_response")

    def test_unassigned_section_badge_is_locally_excluded_without_hiding_real_option_images(self):
        badge = {"label": "2", "seq": 2220, "page_idx": 102, "bbox": [92, 460, 127, 483]}
        real = {"label": "1", "seq": 2218, "page_idx": 102, "bbox": [321, 313, 694, 409]}
        question = question_stub(
            figure_review={"status": figure_policy.CONFLICT, "source": "automatic"},
            stem="根据计费方法回答问题。",
            figure_candidates=[real, badge],
            figures=[{"slot": "stem", "page_idx": 102, "bbox": real["bbox"], "source": "auto"}],
            read_a={
                "stem": "根据计费方法回答问题。", "type": "free_response",
                "figures": {"1": "stem"}, "missing_figure": False,
                "figure_descriptions": [],
            },
        )

        upgraded = figure_policy.stored_or_derived_review(question)

        # The real bound crop survives; only the known left-margin ornament is
        # removed from the unresolved-candidate count.
        self.assertEqual(upgraded["status"], figure_policy.CONFLICT)
        self.assertNotIn("candidate_unclassified", upgraded["signals"])
        self.assertIn("candidate_decoration", upgraded["signals"])
        self.assertEqual(upgraded["excluded_count"], 1)
        self.assertIn("bound_figure_without_text_cue", upgraded["signals"])

    def test_only_section_badge_becomes_auto_excluded(self):
        question = question_stub(
            figure_review={"status": figure_policy.CONFLICT, "source": "automatic"},
            stem="求函数的定义域。",
            figure_candidates=[{
                "label": "1", "seq": 2657, "page_idx": 124,
                "bbox": [92, 835, 127, 862],
            }],
            read_a={
                "stem": "求函数的定义域。", "type": "free_response",
                "figures": {}, "missing_figure": False, "figure_descriptions": [],
            },
        )

        upgraded = figure_policy.stored_or_derived_review(question)

        self.assertEqual(upgraded["status"], figure_policy.AUTO_EXCLUDED)
        self.assertIn("candidate_decoration", upgraded["signals"])
        self.assertEqual(upgraded["excluded_count"], 1)

    def test_recovered_input_overrides_a_misassigned_section_badge(self):
        badge = {
            "label": "1", "seq": 5383, "page_idx": 262,
            "bbox": [95, 65, 131, 90],
        }
        recovered = {
            "label": "2", "seq": 5375, "page_idx": 261,
            "bbox": [717, 760, 882, 885], "recovered_input": True,
        }

        resolved = pipeline._resolve_automatic_figure_assignments(
            stem="如图，正方形 ABCD 的边长为 1。",
            options={},
            kind="free_response",
            candidates=[badge, recovered],
            assignments={"1": "stem", "2": "none"},
        )

        self.assertEqual(resolved, {"1": "decoration", "2": "stem"})

    def test_batch_refresh_restores_recovered_input_and_keeps_raw_reads(self):
        paper = Paper.objects.create(
            filename="recovered.pdf", kind="pdf", sha256="c" * 64,
            source_path="data/recovered/source.pdf",
        )
        badge = {
            "label": "1", "seq": 5383, "page_idx": 262,
            "bbox": [95, 65, 131, 90],
        }
        recovered = {
            "label": "2", "seq": 5375, "page_idx": 261,
            "bbox": [717, 760, 882, 885], "recovered_input": True,
        }
        raw_read = {
            "stem": "如图，正方形 ABCD 的边长为 1。", "type": "free_response",
            "figures": {"1": "stem", "2": "none"}, "missing_figure": False,
            "figure_descriptions": [], "raw": "immutable reader evidence",
        }
        question = Question.objects.create(
            paper=paper, number=23, stem=raw_read["stem"], question_type="free_response",
            figure_candidates=[badge, recovered],
            figures=[{
                "slot": "stem", "page_idx": 262, "bbox": badge["bbox"],
                "source": "auto",
            }],
            read_a=copy.deepcopy(raw_read),
            figure_review={"status": figure_policy.OK, "source": "automatic"},
            flags=[], state=Question.State.GREEN,
        )

        stats = figure_policy.persist_automatic_review_upgrades([question])

        question.refresh_from_db()
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(question.figures, [{
            "slot": "stem", "page_idx": 261, "bbox": recovered["bbox"],
            "source": "auto",
        }])
        self.assertEqual(question.figure_review["status"], figure_policy.OK)
        self.assertEqual(question.state, Question.State.GREEN)
        self.assertEqual(question.read_a, raw_read)

    def test_badge_sized_option_crop_is_never_removed(self):
        option = {
            "slot": "A", "page_idx": 3, "bbox": [95, 65, 131, 90],
            "source": "auto",
        }
        self.assertEqual(figure_policy.without_automatic_textbook_badges([option]), [option])

    def test_batch_refresh_removes_automatically_bound_section_badge(self):
        paper = Paper.objects.create(
            filename="badge.pdf", kind="pdf", sha256="b" * 64,
            source_path="data/badge/source.pdf",
        )
        badge = {
            "label": "1", "seq": 2320, "page_idx": 107,
            "bbox": [110, 579, 144, 602],
        }
        question = Question.objects.create(
            paper=paper, number=10, stem="求函数的最大值。",
            question_type="free_response", figure_candidates=[badge],
            figures=[{
                "slot": "stem", "page_idx": 107, "bbox": badge["bbox"],
                "source": "auto",
            }],
            read_a={
                "stem": "求函数的最大值。", "type": "free_response",
                "figures": {"1": "stem"}, "missing_figure": False,
                "figure_descriptions": [],
            },
            figure_review={"status": figure_policy.CONFLICT, "source": "automatic"},
            flags=[figure_policy.FLAG_UNCUED_FIGURE], state=Question.State.YELLOW,
        )

        stats = figure_policy.persist_automatic_review_upgrades([question])

        question.refresh_from_db()
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(question.figures, [])
        self.assertEqual(question.figure_review["status"], figure_policy.AUTO_EXCLUDED)
        self.assertEqual(question.flags, [])
        self.assertEqual(question.state, Question.State.GREEN)

    def test_old_automatic_conflict_is_recomputed_from_saved_text_and_figure(self):
        old = {
            "status": figure_policy.CONFLICT,
            "source": "automatic",
            "policy_version": 1,
            "reason": "题目文字没有发现图像提示词",
            "signals": ["bound_figure_without_text_cue"],
            "cue_matches": [],
            "excluded_count": 0,
        }
        question = question_stub(
            figure_review=old,
            stem="如图是正方体的展开图，与“数”字相对面的字是（ ）。",
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [10, 20, 80, 90], "source": "auto"}],
        )

        upgraded = figure_policy.stored_or_derived_review(question)

        self.assertEqual(upgraded["status"], figure_policy.OK)
        self.assertEqual(upgraded["source"], "automatic")
        self.assertEqual(upgraded["policy_version"], figure_policy.FIGURE_REVIEW_POLICY_VERSION)
        self.assertEqual(upgraded["cue_matches"], ["如图是"])

    def test_source_less_legacy_review_is_also_recomputed(self):
        question = question_stub(
            figure_review={"status": figure_policy.CONFLICT, "reason": "legacy"},
            stem="In the figure, which angle is equal to A?",
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [1, 2, 3, 4], "source": "auto"}],
        )

        upgraded = figure_policy.stored_or_derived_review(question)

        self.assertEqual(upgraded["status"], figure_policy.OK)
        self.assertEqual(upgraded["source"], "automatic")
        self.assertEqual(upgraded["policy_version"], figure_policy.FIGURE_REVIEW_POLICY_VERSION)

    def test_human_decision_is_never_overwritten(self):
        human = {
            "status": figure_policy.CONFIRMED_NO_FIGURE,
            "source": "human",
            "reason": "已人工核对原卷",
            "signals": ["human_confirmed_no_figure"],
            "confirmed_at": "2026-09-27T00:00:00+08:00",
        }
        question = question_stub(
            figure_review=copy.deepcopy(human),
            stem="如图所示，求阴影面积。",
        )

        self.assertEqual(figure_policy.stored_or_derived_review(question), human)

    def test_saved_reads_and_candidates_are_used_without_external_recognition(self):
        question = question_stub(
            figure_review={"status": figure_policy.OK, "source": "automatic", "policy_version": 1},
            stem="计算 1+1 的值。",
            figure_candidates=[{"label": "crop-1", "page_idx": 0, "bbox": [1, 2, 3, 4]}],
            read_a={
                "stem": "计算 1+1 的值。",
                "type": "free_response",
                "figures": {},
                "missing_figure": False,
                "figure_descriptions": [],
            },
        )

        upgraded = figure_policy.stored_or_derived_review(question)

        self.assertEqual(upgraded["status"], figure_policy.CONFLICT)
        self.assertEqual(upgraded["unclassified_count"], 1)
        self.assertIn("candidate_unclassified", upgraded["signals"])

    def test_read_time_recalculation_does_not_write_source_or_published_snapshot(self):
        paper = Paper.objects.create(
            filename="policy-v1.pdf",
            kind="pdf",
            sha256="1" * 64,
            source_path="data/policy-v1/source.pdf",
        )
        old_review = {
            "status": figure_policy.CONFLICT,
            "source": "automatic",
            "policy_version": 1,
            "reason": "legacy conflict",
        }
        question = Question.objects.create(
            paper=paper,
            number=1,
            stem="如图为三棱柱，求体积。",
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [1, 2, 3, 4], "source": "auto"}],
            state=Question.State.YELLOW,
            flags=[figure_policy.FLAG_UNCUED_FIGURE],
            figure_review=old_review,
        )
        snapshot = {"review": {"figure_review": copy.deepcopy(old_review)}, "stem": question.stem}
        publication = PublishedQuestion.objects.create(
            question=question,
            paper=paper,
            source_filename=paper.filename,
            number=1,
            question_type="free_response",
            version=1,
            content=copy.deepcopy(snapshot),
            content_hash="2" * 64,
        )

        with self.assertNumQueries(0):
            upgraded = figure_policy.stored_or_derived_review(question)

        self.assertEqual(upgraded["status"], figure_policy.OK)
        self.assertEqual(question.figure_review["policy_version"], figure_policy.FIGURE_REVIEW_POLICY_VERSION)
        self.assertEqual(question.flags, [])
        self.assertEqual(question.state, Question.State.GREEN)

        question.refresh_from_db()
        publication.refresh_from_db()

        self.assertEqual(question.figure_review, old_review)
        self.assertEqual(question.flags, [figure_policy.FLAG_UNCUED_FIGURE])
        self.assertEqual(question.state, Question.State.YELLOW)
        self.assertEqual(publication.content, snapshot)

    def test_explicit_batch_refresh_persists_only_automatic_reviews_and_keeps_other_flags(self):
        paper = Paper.objects.create(
            filename="refresh.pdf", kind="pdf", sha256="3" * 64,
            source_path="data/refresh/source.pdf",
        )
        automatic = Question.objects.create(
            paper=paper, number=1, stem="求函数的定义域。", question_type="free_response",
            figure_candidates=[{
                "label": "1", "seq": 1, "page_idx": 0, "bbox": [92, 100, 127, 125],
            }],
            read_a={
                "stem": "求函数的定义域。", "type": "free_response",
                "figures": {}, "missing_figure": False, "figure_descriptions": [],
            },
            figure_review={
                "status": figure_policy.CONFLICT, "source": "automatic",
                "policy_version": 2,
            },
            flags=[figure_policy.FLAG_UNFOUND_FIGURE, "两次识读不一致"],
            state=Question.State.YELLOW,
        )
        human_review = {
            "status": figure_policy.CONFIRMED_NO_FIGURE, "source": "human",
            "reason": "已人工确认本题确实无图", "signals": ["human_confirmed_no_figure"],
        }
        human = Question.objects.create(
            paper=paper, number=2, stem="如图所示。", question_type="free_response",
            figure_review=copy.deepcopy(human_review), flags=[figure_policy.FLAG_NO_FIGURE],
            state=Question.State.YELLOW,
        )

        stats = figure_policy.persist_automatic_review_upgrades(
            Question.objects.filter(paper=paper).order_by("id")
        )

        automatic.refresh_from_db()
        human.refresh_from_db()
        self.assertEqual(stats, {
            "seen": 2, "updated": 1, "unchanged": 0, "human_skipped": 1,
            "state_skipped": 0,
        })
        self.assertEqual(automatic.figure_review["status"], figure_policy.AUTO_EXCLUDED)
        self.assertEqual(
            automatic.figure_review["policy_version"],
            figure_policy.FIGURE_REVIEW_POLICY_VERSION,
        )
        self.assertEqual(automatic.flags, ["两次识读不一致"])
        self.assertEqual(automatic.state, Question.State.YELLOW)
        self.assertEqual(human.figure_review, human_review)
        self.assertEqual(human.flags, [figure_policy.FLAG_NO_FIGURE])
