from django.test import SimpleTestCase

from . import segment


PAGES = [
    {"page_idx": index, "width": 1000, "height": 1400}
    for index in range(8)
]


def block(seq, page, y, text, kind="text", x0=80, x1=920, height=45):
    return {
        "seq": seq,
        "type": kind,
        "page_idx": page,
        "bbox": [x0, y, x1, y + height],
        "text": text,
    }


def contains(regions, page, y):
    return any(
        item["page_idx"] == page and item["bbox"][1] <= y <= item["bbox"][3]
        for item in regions
    )


class BookTypedMarkerTests(SimpleTestCase):
    def test_examples_stop_before_analysis_and_solution(self):
        blocks = [
            block(0, 0, 80, "3. 单调性"),
            block(1, 0, 140, "教材讲解正文。"),
            block(2, 0, 240, "例3 求函数的最大值。"),
            block(3, 0, 320, "分析：先研究函数的单调性。"),
            block(4, 0, 400, "解：计算可得最大值为 1。"),
            block(5, 0, 600, "例4 比较下列各数大小。"),
            block(6, 0, 680, "解：结论略。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual([(item["source_kind"], item["number"]) for item in result["questions"]], [
            ("example", 3), ("example", 4),
        ])
        first = result["questions"][0]
        self.assertFalse(contains(first["regions"], 0, 320))
        self.assertFalse(contains(first["regions"], 0, 400))
        self.assertFalse(contains(first["regions"], 0, 600))
        self.assertFalse(contains(result["questions"][1]["regions"], 0, 680))
        self.assertEqual(first["source_anchor_seq"], 2)
        self.assertEqual(first["start"]["source_kind"], "example")
        self.assertFalse(first["segmentation"]["range_limited"])
        self.assertTrue(first["segmentation"]["solution_trimmed"])
        self.assertEqual(first["segmentation"]["solution_boundary_seq"], 3)

    def test_solution_boundary_keeps_stem_figure_but_excludes_overlapping_answer_figure(self):
        blocks = [
            block(0, 0, 100, "例1 如图，求函数的最大值。"),
            # Its centre is above the solution boundary, like a side-by-side
            # input diagram whose bottom extends into the printed answer.
            block(1, 0, 180, "", kind="image", x0=650, x1=900, height=210),
            block(2, 0, 300, "解：由图可得。"),
            # More than 20% overlaps the stem crop, but its centre lies inside
            # the answer; the generic overlap rule must not pull it backwards.
            block(3, 0, 250, "", kind="image", x0=350, x1=600, height=160),
            block(4, 0, 960, "", kind="image", x0=350, x1=650, height=28),
            block(5, 0, 600, "例2 求函数的最小值。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        first = result["questions"][0]
        self.assertEqual([item["seq"] for item in first["figure_candidates"]], [1])
        self.assertTrue(segment.overlaps_regions(0, blocks[3]["bbox"], first["regions"]))
        self.assertFalse(segment.center_in_regions(0, blocks[3]["bbox"], first["regions"]))

    def test_numbered_input_visual_beside_solution_is_recovered_without_answer_text(self):
        blocks = [
            block(0, 0, 80, "例4 如图 4.2-7，估计城市人口的倍增期。"),
            block(1, 0, 160, "（1）根据图象回答问题；（2）计算二十年后的人口。"),
            block(2, 0, 240, "分析：先从图象中选取适当的点。"),
            block(3, 0, 330, "解：由图可知倍增期约为二十年。"),
            # The printed input chart is laid out beside the worked solution,
            # but the stem explicitly refers to its numbered source figure.
            block(4, 0, 300, "", kind="chart", x0=600, x1=900, height=210),
            block(5, 0, 620, "例5 求函数的定义域。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        first = result["questions"][0]
        self.assertFalse(segment.center_in_regions(0, blocks[2]["bbox"], first["regions"]))
        self.assertFalse(segment.center_in_regions(0, blocks[3]["bbox"], first["regions"]))
        self.assertEqual(first["regions"][1], {
            "page_idx": 0, "bbox": blocks[4]["bbox"],
        })
        self.assertEqual(first["figure_candidates"], [{
            "seq": 4, "page_idx": 0, "bbox": blocks[4]["bbox"],
            "recovered_input": True,
        }])
        self.assertEqual(first["segmentation"]["recovered_input_figure_seqs"], [4])

    def test_solution_visual_is_not_recovered_without_numbered_stem_reference(self):
        blocks = [
            block(0, 0, 80, "例1 求函数的最大值。"),
            block(1, 0, 220, "分析：先研究函数的单调性。"),
            block(2, 0, 300, "解：作出下面的辅助图象。"),
            block(3, 0, 360, "", kind="chart", x0=500, x1=850, height=220),
            block(4, 0, 650, "例2 求函数的最小值。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        first = result["questions"][0]
        self.assertEqual(first["figure_candidates"], [])
        self.assertNotIn("recovered_input_figure_seqs", first["segmentation"])
        self.assertEqual(len(first["regions"]), 1)

    def test_multiple_visuals_beside_solution_are_left_for_manual_review(self):
        blocks = [
            block(0, 0, 80, "例1 根据图 2.1-3 回答问题。"),
            block(1, 0, 220, "分析：先观察图象。"),
            block(2, 0, 300, "解：作出辅助线。"),
            block(3, 0, 360, "", kind="image", x0=120, x1=380, height=180),
            block(4, 0, 360, "", kind="chart", x0=600, x1=880, height=180),
            block(5, 0, 650, "例2 求函数的最小值。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        first = result["questions"][0]
        self.assertEqual(first["figure_candidates"], [])
        self.assertNotIn("recovered_input_figure_seqs", first["segmentation"])
        self.assertEqual(len(first["regions"]), 1)

    def test_example_references_are_not_new_cards(self):
        blocks = [
            block(0, 0, 80, "例1 已知集合 A，求 A 的子集。"),
            block(1, 0, 160, "解：答案略。"),
            block(2, 0, 260, "例 1 中命题（1）给出了一个充分条件。"),
            block(3, 0, 340, "例7的结果还可以写成另一种形式。"),
            block(4, 0, 420, "例8的证明用到了换元方法。"),
            block(5, 0, 560, "例2 求证下列恒等式。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual([item["number"] for item in result["questions"]], [1, 2])
        self.assertFalse(contains(result["questions"][0]["regions"], 0, 160))
        self.assertEqual(result["questions"][1]["source_anchor_seq"], 5)

    def test_practice_area_keeps_short_questions_and_stops_at_example(self):
        blocks = [
            block(0, 0, 70, "练习"),
            block(1, 0, 130, "1. 求值"),
            block(2, 0, 200, "计算过程所需的印刷条件。"),
            block(3, 0, 300, "2. 化简："),
            block(4, 0, 390, "例7 表中给出了六次测试成绩，请分析。"),
            block(5, 0, 470, "分析：先画出折线图。"),
            block(6, 0, 550, "解：由图可知。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual([(item["source_kind"], item["number"]) for item in result["questions"]], [
            ("exercise", 1), ("exercise", 2), ("example", 7),
        ])
        second = result["questions"][1]
        self.assertFalse(contains(second["regions"], 0, 390))
        self.assertFalse(contains(result["questions"][2]["regions"], 0, 470))

    def test_long_declarative_numbered_problem_does_not_need_a_question_mark(self):
        blocks = [
            block(0, 0, 70, "练习", kind="header"),
            block(
                1, 0, 130,
                r"6. 某种细菌每十分钟分裂一次，那么经过一小时，"
                r"一个这种细菌可以分裂成 \_\_\_\_ 个。",
            ),
            block(2, 0, 210, "7. 求函数值。"),
            block(3, 0, 900, "人民教育出版社", kind="header"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(
            [(item["number"], item["source_anchor_seq"]) for item in result["questions"]],
            [(6, 1), (7, 2)],
        )

    def test_exercise_ranges_are_not_shortened_by_solution_labels(self):
        blocks = [
            block(0, 0, 70, "练习"),
            block(1, 0, 130, "1. 求函数的最大值。"),
            block(2, 0, 220, "分析：先研究函数的单调性。"),
            block(3, 0, 300, "解：最大值为 1。"),
            block(4, 0, 430, "2. 求函数的最小值。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual([item["source_kind"] for item in result["questions"]], [
            "exercise", "exercise",
        ])
        first = result["questions"][0]
        self.assertTrue(contains(first["regions"], 0, 220))
        self.assertTrue(contains(first["regions"], 0, 300))
        self.assertFalse(contains(first["regions"], 0, 430))
        self.assertFalse(first["segmentation"]["solution_trimmed"])

    def test_example_without_solution_marker_stops_at_next_source_anchor(self):
        blocks = [
            block(0, 0, 80, "例1 求函数的最大值。"),
            block(1, 0, 180, "补充一个必要条件。"),
            block(2, 0, 360, "例2 求函数的最小值。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        first = result["questions"][0]
        self.assertTrue(contains(first["regions"], 0, 180))
        self.assertFalse(contains(first["regions"], 0, 360))
        self.assertFalse(first["segmentation"]["solution_trimmed"])

    def test_question_word_proof_is_not_mistaken_for_solution_heading(self):
        blocks = [
            block(0, 0, 80, "例1 已知函数满足下列条件。"),
            block(1, 0, 170, "证明下列恒等式成立。"),
            block(2, 0, 280, "解"),
            block(3, 0, 380, "例2 求值。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        first = result["questions"][0]
        self.assertTrue(contains(first["regions"], 0, 170))
        self.assertFalse(contains(first["regions"], 0, 280))

    def test_solution_merged_into_example_text_block_still_ends_the_stem(self):
        blocks = [
            block(
                0, 0, 80,
                "例1 求函数的最大值。\n分析：先研究单调性。\n解：最大值为 1。",
                height=180,
            ),
            block(1, 0, 360, "例2 求函数的最小值。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        first = result["questions"][0]
        self.assertLess(first["regions"][0]["bbox"][3], 200)
        self.assertFalse(contains(first["regions"], 0, 360))
        self.assertTrue(first["segmentation"]["solution_trimmed"])
        self.assertEqual(first["segmentation"]["solution_boundary_seq"], 0)

    def test_inline_solution_and_numbered_solution_method_end_examples(self):
        blocks = [
            block(
                0, 0, 80,
                "例2 已知 $x=1$，求 $x+1$ 的值. 解：代入可得 2。",
                height=100,
            ),
            block(1, 0, 260, "例3 已知三角形 ABC，求角 A。"),
            block(2, 0, 340, "解法 1：先使用余弦定理。"),
            block(3, 0, 500, "例4 求证恒等式。"),
            block(4, 0, 580, "证法1：先通分。"),
            block(5, 0, 720, "例5 求值。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        first, second, third, _fourth = result["questions"]
        self.assertTrue(first["segmentation"]["solution_trimmed"])
        self.assertEqual(first["segmentation"]["solution_boundary_seq"], 0)
        self.assertLess(first["regions"][0]["bbox"][3], 160)
        self.assertTrue(second["segmentation"]["solution_trimmed"])
        self.assertEqual(second["segmentation"]["solution_boundary_seq"], 2)
        self.assertFalse(contains(second["regions"], 0, 340))
        self.assertTrue(third["segmentation"]["solution_trimmed"])
        self.assertEqual(third["segmentation"]["solution_boundary_seq"], 4)

    def test_short_numbered_subsections_are_boundaries_not_cards(self):
        blocks = [
            block(0, 0, 80, "1. 周期性"),
            block(1, 0, 160, "周期函数的教材讲解。"),
            block(2, 0, 280, "2. 奇偶性"),
            block(3, 0, 360, "偶函数的教材讲解。"),
            block(4, 0, 480, "3. 单调性"),
            block(5, 0, 560, "单调性的教材讲解。"),
            block(6, 0, 680, "4. 值域"),
            block(7, 0, 760, "值域的教材讲解。"),
            block(8, 0, 860, "例6 求函数的定义域、周期及单调区间。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual([(item["source_kind"], item["number"]) for item in result["questions"]], [
            ("example", 6),
        ])
        self.assertGreaterEqual(len(result["layout"].boundaries), 4)

    def test_heading_only_scope_has_columns_and_builds_no_cards(self):
        blocks = [
            block(0, 0, 80, "一、知识讲解"),
            block(1, 0, 180, "这里是教材正文，不是一道题。"),
        ]

        layout, starts = segment.analyse_book(PAGES[:1], blocks)

        self.assertEqual(starts, [])
        self.assertTrue(layout.headings)
        self.assertTrue(all("col" in heading for heading in layout.headings))
        self.assertEqual(segment.build_book_questions(layout, starts, blocks), [])

    def test_english_example_markers_are_supported(self):
        blocks = [
            block(0, 0, 80, "Example 2 Find the domain."),
            block(1, 0, 180, "Solution text."),
            block(2, 0, 300, "Ex. 3 Prove the identity."),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual([item["number"] for item in result["questions"]], [2, 3])

    def test_runaway_range_is_bounded_and_explicitly_flagged(self):
        blocks = [block(0, 0, 80, "例1 这是一个边界缺失的例题。")]
        for page in range(6):
            blocks.append(block(page + 1, page, 300, f"第 {page + 1} 页正文"))

        result = segment.segment_book(PAGES[:6], blocks)
        question = result["questions"][0]

        self.assertEqual(len({item["page_idx"] for item in question["regions"]}), segment.BOOK_MAX_CARD_PAGES)
        self.assertTrue(question["segmentation"]["range_limited"])
        self.assertTrue(any("安全上限" in item for item in question["segmentation_flags"]))

    def test_choice_bundle_parenthesized_items_are_independent_cards(self):
        blocks = [
            block(0, 0, 60, "复习巩固"),
            block(1, 0, 110, "1. 选择题"),
            block(2, 0, 160, "(1) 函数图象关于哪条轴对称（    ）？"),
            block(3, 0, 210, "(A) x轴 (B) y轴 (C) 原点 (D) 直线y=x"),
            block(4, 0, 280, "(2) 如图，正确的图象是（    ）？"),
            block(5, 0, 350, "（3）下列函数有零点的是（    ）？"),
            # A bare figure caption must not become another question 1.
            block(6, 0, 420, "(1)"),
            block(7, 0, 500, "2. 用“<”“>”“=”填空："),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(
            [(item["number"], item["source_anchor_seq"]) for item in result["questions"]],
            [(1, 2), (2, 4), (3, 5), (2, 7)],
        )
        self.assertFalse(contains(result["questions"][0]["regions"], 0, 280))
        self.assertTrue(contains(result["questions"][1]["regions"], 0, 280))

    def test_choice_bundle_figure_captions_assign_each_following_visual(self):
        blocks = [
            block(0, 0, 700, "1. 选择题"),
            block(1, 0, 760, "(1) 普通文字选项（    ）？"),
            block(2, 0, 820, "(2) 如图(1)，选择正确结论（    ）？"),
            block(3, 1, 70, "(3) 如图(2)，选择正确结论（    ）？"),
            block(4, 1, 125, "", kind="image", x0=240, x1=430, height=130),
            block(5, 1, 265, "(1)", x0=300, x1=340, height=20),
            block(6, 1, 125, "", kind="image", x0=520, x1=740, height=130),
            block(7, 1, 265, "(2)", x0=600, x1=640, height=20),
            block(8, 1, 340, "2. 求值。"),
        ]

        result = segment.segment_book(PAGES[:2], blocks)
        by_anchor = {item["source_anchor_seq"]: item for item in result["questions"]}

        self.assertEqual([item["seq"] for item in by_anchor[2]["figure_candidates"]], [4])
        self.assertEqual([item["seq"] for item in by_anchor[3]["figure_candidates"]], [6])
        self.assertTrue(any(region["bbox"] == blocks[4]["bbox"] for region in by_anchor[2]["regions"]))
        self.assertFalse(any(
            segment.center_in_regions(1, blocks[4]["bbox"], [region])
            for region in by_anchor[3]["regions"]
        ))

    def test_numbered_question_caption_moves_right_side_visual_to_that_question(self):
        blocks = [
            block(0, 0, 100, "练习"),
            block(1, 0, 170, "8. 已知函数，求它的零点。", x0=100, x1=650, height=90),
            block(2, 0, 245, "9. 如图，判断下列说法。", x0=100, x1=700, height=100),
            block(3, 0, 155, "", kind="image", x0=720, x1=900, height=180),
            block(4, 0, 345, "(第9题)", x0=770, x1=850, height=20),
            block(5, 0, 430, "10. 求值。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)
        by_number = {item["number"]: item for item in result["questions"]}

        self.assertEqual(by_number[8]["figure_candidates"], [])
        self.assertLessEqual(by_number[8]["regions"][0]["bbox"][2], 718)
        self.assertEqual([item["seq"] for item in by_number[9]["figure_candidates"]], [3])
        self.assertTrue(any(region["bbox"] == blocks[3]["bbox"] for region in by_number[9]["regions"]))

    def test_explicit_figure_cue_reclaims_tall_right_side_image_from_next_question(self):
        blocks = [
            block(0, 0, 60, "练习"),
            block(1, 0, 110, "7. 指数函数的图象如图所示，求参数范围。", x0=100, x1=690),
            block(2, 0, 165, "8. 某种物质每年衰减 2%，求十年后的剩余量。", x0=100, x1=690),
            # The centre is below question 8's start, while more than 20% of
            # the crop overlaps question 7 and only question 7 says 如图.
            block(3, 0, 115, "", kind="image", x0=720, x1=890, height=140),
            block(4, 0, 300, "9. 求函数的零点。", x0=100, x1=690),
        ]

        result = segment.segment_book(PAGES[:1], blocks)
        by_number = {item["number"]: item for item in result["questions"]}

        self.assertEqual([item["seq"] for item in by_number[7]["figure_candidates"]], [3])
        self.assertEqual(by_number[8]["figure_candidates"], [])
        self.assertTrue(any(region["bbox"] == blocks[3]["bbox"] for region in by_number[7]["regions"]))

    def test_numbered_procedure_and_report_fields_do_not_become_cards(self):
        blocks = [
            block(0, 0, 60, "给定精确度，用二分法求零点的一般步骤如下："),
            block(1, 0, 120, "1. 确定零点的初始区间。"),
            block(2, 0, 180, "2. 求区间中点 c。"),
            block(3, 0, 240, "3. 计算 f(c)，并确定新区间。"),
            block(4, 0, 300, "4. 判断是否达到精确度。"),
            block(5, 0, 380, "一、数学建模活动的一个实例"),
            block(6, 0, 440, "1. 观察实际情景，发现和提出问题"),
            block(7, 0, 500, "2. 收集数据"),
            block(8, 0, 560, "3. 分析数据"),
            block(9, 0, 620, "4. 参考文献：列出使用的资料。"),
            block(10, 0, 700, "练习"),
            block(11, 0, 760, "1. 求函数的零点。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(
            [(item["number"], item["source_anchor_seq"]) for item in result["questions"]],
            [(1, 11)],
        )

    def test_modelling_context_heading_recovers_the_real_following_problem(self):
        blocks = [
            block(0, 0, 80, "一、数学建模活动的一个实例"),
            block(1, 0, 140, "1. 观察实际情景，发现和提出问题"),
            block(
                2, 0, 210,
                "某种绿茶在 60 ℃ 时口感最佳，那么在室温下大约要放置多长时间？",
                height=80,
            ),
            block(3, 0, 330, "显然，可以先记录温度随时间变化的数据。"),
            block(4, 0, 430, "2. 收集数据"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(
            [(item["number"], item["source_anchor_seq"]) for item in result["questions"]],
            [(1, 2)],
        )
        question = result["questions"][0]
        self.assertTrue(contains(question["regions"], 0, 210))
        self.assertFalse(contains(question["regions"], 0, 330))
        self.assertFalse(contains(question["regions"], 0, 430))

    def test_decimal_section_reference_inside_solution_is_not_a_new_question(self):
        blocks = [
            block(0, 0, 80, "探究"),
            block(1, 0, 140, "在 4.2.1 的问题 1 中，求游客人数翻倍，就是计算对数的值。"),
            block(2, 0, 210, "由换底公式可得结果，约为 7 年。"),
            block(3, 0, 330, "练习"),
            block(4, 0, 390, "1. 求函数的定义域。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(
            [(item["number"], item["source_anchor_seq"]) for item in result["questions"]],
            [(1, 4)],
        )

    def test_referenced_chapter_problem_is_kept_but_its_explanation_is_not(self):
        blocks = [
            block(0, 0, 70, "4.3 对数"),
            block(
                1, 0, 180,
                "在 4.2.1 的问题 1 中，我们能求出游客人次。反之，达到 2 倍该如何解决？",
                height=90,
            ),
            block(2, 0, 290, "4.3.1 对数的概念"),
            block(3, 0, 360, "显然，可以利用换底公式计算并得到结论。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(
            [(item["number"], item["source_anchor_seq"]) for item in result["questions"]],
            [(1, 1)],
        )
        question = result["questions"][0]
        self.assertTrue(contains(question["regions"], 0, 180))
        self.assertFalse(contains(question["regions"], 0, 360))

    def test_think_panel_referenced_problem_is_kept_as_a_card(self):
        blocks = [
            block(0, 0, 80, "4.4.1 对数函数的概念"),
            block(1, 0, 150, "思考"),
            block(
                2, 0, 200,
                "在 4.2.1 的问题 2 中，已知碳 14 含量，如何得知死亡时间呢？",
                height=80,
            ),
            block(3, 0, 310, "根据指数与对数的关系，可以建立函数。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(
            [(item["number"], item["source_anchor_seq"]) for item in result["questions"]],
            [(2, 2)],
        )
        self.assertFalse(contains(result["questions"][0]["regions"], 0, 310))

    def test_missing_number_before_table_is_recovered_between_one_and_three(self):
        blocks = [
            block(0, 0, 60, "习题4.5"),
            block(1, 0, 110, "复习巩固"),
            block(2, 0, 160, "1. 下列函数图象中不能用二分法求零点的是____。"),
            block(3, 0, 220, "", kind="image", x0=150, x1=850, height=100),
            block(4, 0, 350, "", kind="table", x0=250, x1=800, height=50),
            block(5, 0, 420, "函数 y=f(x) 在哪几个区间内一定有零点？为什么？"),
            block(6, 0, 500, "3. 已知函数 f(x)，证明方程至少有两个实数解。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(
            [(item["number"], item["source_anchor_seq"]) for item in result["questions"]],
            [(1, 2), (2, 4), (3, 6)],
        )
        first, recovered, _third = result["questions"]
        self.assertNotIn(4, [item["seq"] for item in first["figure_candidates"]])
        self.assertEqual([item["seq"] for item in recovered["figure_candidates"]], [4])
        self.assertTrue(contains(recovered["regions"], 0, 420))

    def test_information_technology_instructions_stop_before_actual_questions(self):
        blocks = [
            block(0, 0, 60, "信息技术应用"),
            block(1, 0, 120, "1. 用信息技术绘制函数图象。"),
            block(2, 0, 180, "2. 拖动点 A，观察图象的变化。"),
            block(3, 0, 240, "3. 根据图象可以发现下列性质。"),
            block(4, 0, 320, "1. 当自变量取同一个数时，函数值有什么关系？"),
            block(5, 0, 400, "2. 类似地研究幂函数，你能得到什么结论？"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(
            [(item["number"], item["source_anchor_seq"]) for item in result["questions"]],
            [(1, 4), (2, 5)],
        )

    def test_known_textbook_boundaries_stop_cross_chapter_leakage(self):
        blocks = [
            block(0, 0, 60, "练习"),
            block(1, 0, 120, "4. 填表："),
            block(2, 0, 180, "", kind="table", x0=120, x1=860, height=180),
            block(3, 0, 380, "下面在探究1的基础上继续探究。"),
            block(4, 0, 430, "探究2"),
            block(5, 0, 500, "这里是探究正文。"),
            block(6, 1, 80, "练习"),
            block(7, 1, 140, "5. 求集合的并集。"),
            block(8, 1, 230, "补集"),
            block(9, 1, 300, "这里是下一节教材正文。"),
            block(10, 2, 80, "练习"),
            block(11, 2, 140, "27. 请完成统计表并回答后面的问题。"),
            block(12, 2, 220, "", kind="table", height=300),
            block(13, 3, 80, "部分中英文词汇索引"),
            block(14, 3, 180, "", kind="table", height=600),
        ]

        result = segment.segment_book(PAGES[:4], blocks)
        by_anchor = {item["source_anchor_seq"]: item for item in result["questions"]}

        self.assertFalse(contains(by_anchor[1]["regions"], 0, 430))
        self.assertFalse(contains(by_anchor[7]["regions"], 1, 300))
        self.assertFalse(contains(by_anchor[11]["regions"], 3, 180))
        self.assertFalse(by_anchor[11]["segmentation_flags"])

    def test_mixed_layout_left_exercise_excludes_right_prose_and_next_full_width_section(self):
        blocks = [
            block(0, 0, 80, "练习"),
            block(1, 0, 300, "4. 画出函数图象。", x0=105, x1=370, height=32),
            block(2, 0, 342, "(1) 写出定义域。", x0=130, x1=430, height=24),
            block(3, 0, 376, "(2) 证明你的结论。", x0=130, x1=560, height=24),
            block(
                4, 0, 270,
                "通过观察图象并进行逻辑推理，可以研究函数具有的各种性质，"
                "这是一段放在右栏的较长教材说明文字，不属于左边的练习题。",
                x0=675, x1=900, height=130,
            ),
            block(
                5, 0, 440,
                "再来观察下一节的图象，这一整段是后续教材正文，不属于第4题。",
                x0=105, x1=900, height=70,
            ),
            block(6, 0, 560, "思考"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)
        question = result["questions"][0]

        self.assertLess(question["regions"][0]["bbox"][2], 675)
        self.assertLess(question["regions"][0]["bbox"][3], 440)
        self.assertEqual(len(question["regions"]), 1)

    def test_question_number_merged_into_previous_text_block_is_recovered(self):
        blocks = [
            block(0, 0, 60, "练习"),
            block(1, 0, 120, "3. 根据图象回答问题。"),
            block(
                2, 0, 210,
                "函数是否为从集合 A 到集合 B 的函数？4. 构建一个问题情境，"
                "使变量关系能用 y=√x 描述。",
                height=90,
            ),
            block(3, 0, 380, "下一节教材正文。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(
            [(item["number"], item["source_anchor_seq"]) for item in result["questions"]],
            [(3, 1), (4, 2)],
        )
        self.assertLess(result["questions"][0]["regions"][0]["bbox"][3], 300)
        self.assertLessEqual(result["questions"][1]["regions"][0]["bbox"][3], 300)

    def test_book_candidate_must_have_its_centre_inside_the_card(self):
        blocks = [
            block(0, 0, 60, "练习"),
            block(1, 0, 120, "1. 求函数值。"),
            block(2, 0, 300, "2. 如图，求面积。"),
            # It overlaps question 1 by more than 20%, but its centre belongs
            # to question 2 and therefore must not be offered to both cards.
            block(3, 0, 240, "", kind="image", x0=200, x1=800, height=180),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(result["questions"][0]["figure_candidates"], [])
        self.assertEqual(
            [item["seq"] for item in result["questions"][1]["figure_candidates"]],
            [3],
        )

    def test_book_scopes_separate_examples_and_exercises_with_repeated_numbers(self):
        blocks = [
            block(0, 0, 80, "例1 求值。"),
            block(1, 0, 180, "例2 求证。"),
            block(2, 0, 300, "练习"),
            block(3, 0, 380, "1. 求值。"),
            block(4, 0, 480, "2. 求证。"),
            block(5, 0, 600, "例1 计算。"),
        ]

        scopes = segment.book_numbering_scopes(PAGES[:1], blocks)

        self.assertEqual([scope["source_kind"] for scope in scopes], ["example", "exercise", "example"])
        self.assertEqual([scope["first_number"] for scope in scopes], [1, 1, 1])
        self.assertEqual([scope["seq_start"] for scope in scopes], [None, 2, 5])

    def test_repeated_number_inside_one_practice_does_not_reuse_its_header(self):
        blocks = [
            block(0, 0, 60, "练习"),
            block(1, 0, 120, "1. 求值。"),
            block(2, 0, 180, "2. 求证。"),
            block(3, 0, 260, "习题5.5"),
            block(4, 0, 320, "1. 已知条件，求结果。"),
            block(5, 0, 380, "2. 已知另一个条件，求结果。"),
            block(6, 0, 440, "2. 已知补充条件，求结果。"),
        ]

        scopes = segment.book_numbering_scopes(PAGES[:1], blocks)

        self.assertEqual([scope["seq_start"] for scope in scopes], [None, 3, 6])
        self.assertFalse(any(
            scope["seq_start"] is not None and scope["seq_end"] is not None
            and scope["seq_start"] > scope["seq_end"]
            for scope in scopes
        ))

    def test_exam_segmenter_behavior_is_unchanged(self):
        blocks = [
            block(0, 0, 80, "1. 已知 x=1，求 x+1。"),
            block(1, 0, 300, "2. 已知 y=2，求 y+1。"),
        ]

        result = segment.segment(PAGES[:1], blocks)

        self.assertEqual([item["number"] for item in result["questions"]], [1, 2])
        self.assertEqual([item["source_kind"] for item in result["questions"]], ["question", "question"])
