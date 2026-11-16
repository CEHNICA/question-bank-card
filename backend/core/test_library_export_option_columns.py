"""屏幕预览排成几栏，导出的 Word 就得排成几栏。

「自动排版」原来前后端各算各的：预览按实际宽度量出这道题该排几栏，后端按
自己的字宽估算另算一遍，谁也不提示谁。于是老师在屏幕上看到四个选项并排，
下载到的文件里却是竖着四个 —— 老师核对的是屏幕，拿到手的是文件。

预览量好的栏数现在随导出请求一起过去，Word 照屏幕排；没有实测值时（老前端、
PDF 路径）后端仍按原样估算，并且必须把降级说出来。
"""

from django.test import SimpleTestCase

from . import library_export
from .library_drafts import DraftError, _print_options

QUESTION = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"

OPTIONS = {"A": "1", "B": "2", "C": "3", "D": "4"}


def item(question_id=QUESTION, options=None):
    return {
        "id": question_id,
        "content": {"options": dict(options or OPTIONS)},
        "fields": {},
        "images": [],
    }


def options_for(**extra):
    base = {"option_layout": "auto", "font_size": 12, "answer_space": 0}
    base.update(extra)
    return base


class MeasuredOptionColumnsTests(SimpleTestCase):
    def test_measured_columns_win_over_the_local_estimate(self):
        # 没有实测值时，四个短选项自己会排成四栏。
        self.assertEqual(library_export._option_columns(item(), options_for()), 4)
        # 预览量出两栏，就照两栏排 —— 文件跟屏幕一样。
        self.assertEqual(
            library_export._option_columns(item(), options_for(option_columns={QUESTION: 2})), 2
        )

    def test_measured_columns_may_ask_for_one_or_five(self):
        self.assertEqual(
            library_export._option_columns(item(), options_for(option_columns={QUESTION: 1})), 1
        )
        five = {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"}
        self.assertEqual(
            library_export._option_columns(item(options=five), options_for(option_columns={QUESTION: 5})),
            5,
        )

    def test_out_of_range_measurements_fall_back_to_the_estimate(self):
        for value in (0, 6, "2", True, None):
            self.assertEqual(
                library_export._option_columns(item(), options_for(option_columns={QUESTION: value})), 4
            )

    def test_a_measurement_for_another_question_is_not_used(self):
        self.assertEqual(
            library_export._option_columns(item(), options_for(option_columns={OTHER: 2})), 4
        )

    def test_vertical_layout_still_wins_over_the_measurement(self):
        self.assertEqual(
            library_export._option_columns(
                item(), options_for(option_layout="vertical", option_columns={QUESTION: 4})
            ),
            1,
        )


class PrintOptionsColumnTests(SimpleTestCase):
    def test_measured_columns_survive_normalization(self):
        result = _print_options({"option_columns": {QUESTION: 3}}, ids=[QUESTION])
        self.assertEqual(result["option_columns"], {QUESTION: 3})

    def test_broken_column_counts_are_refused(self):
        for value in (0, 6, "3", 2.5, True, None):
            with self.assertRaises(DraftError):
                _print_options({"option_columns": {QUESTION: value}}, ids=[QUESTION])

    def test_columns_for_questions_outside_the_paper_are_refused(self):
        with self.assertRaises(DraftError):
            _print_options({"option_columns": {OTHER: 2}}, ids=[QUESTION])

    def test_a_malformed_question_id_is_refused(self):
        with self.assertRaises(DraftError):
            _print_options({"option_columns": {"not-a-uuid": 2}}, ids=[QUESTION])

    def test_no_measured_columns_is_still_valid(self):
        result = _print_options({}, ids=[QUESTION])
        self.assertEqual(result["option_columns"], {})
