"""题干里留着分值「计算（7分）：」，会照原样印进组卷卷面。

誊录规则本来就写着「分值不要写进题干」，但清理只去题首那一个；识读把整句抄
进来时题首没有分值，就漏过去了。真卷上量过：11 份卷子 241 张卡，只有汶源第 16
题这一处（`read_a.stem` 里就带着它，不是后处理弄出来的）。

括号里包着「N分」在数学题干里不会是内容，整段去掉即可；小问的 (1)(2)、空括号
（ ）、区间 (a,b) 都不含「分」字，不受影响。

    .\\backend\\.venv\\Scripts\\python.exe backend\\manage.py test core.test_score_mark
"""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from core import pipeline, readers, textnorm
from core.models import Question

# 真卷上那一行的原文。
REAL = "计算（7分）：\n\n$\\tan^2 60° - 2\\sin 30° - \\sqrt{2}\\cos 45°$"


class ScoreMarkTests(SimpleTestCase):
    def test_the_real_paper_line_loses_its_score(self):
        self.assertEqual(textnorm.clean_stem(REAL, 16), "计算：\n\n$\\tan^2 60° - 2\\sin 30° - \\sqrt{2}\\cos 45°$")

    def test_a_leading_score_is_still_removed(self):
        self.assertEqual(textnorm.clean_stem("（15分）已知向量", 16), "已知向量")

    def test_subquestion_markers_survive(self):
        stem = "已知向量 $\\vec{a}$，求：(1) $\\vec{a}+\\vec{b}$；(2) $\\vec{a}-\\vec{b}$。"
        self.assertEqual(textnorm.clean_stem(stem, 16), stem)

    def test_an_empty_answer_bracket_survives(self):
        stem = "下列说法正确的是（    ），请选一个。"
        self.assertEqual(textnorm.clean_stem(stem, 16), stem)

    def test_parentheses_without_a_score_survive(self):
        for stem in ("在区间 (a, b] 上求最值", "已知 $\\angle ABC=30°$（$b>0$）", "取值范围是 (1, 5)"):
            self.assertEqual(textnorm.clean_stem(stem, 16), stem)

    def test_a_stem_without_a_score_is_untouched(self):
        stem = "如图，在 $\\triangle ABC$ 中，$\\angle B=30°$，求 $AB$。"
        self.assertEqual(textnorm.clean_stem(stem, 16), stem)


class ScoreMarkReadingTests(SimpleTestCase):
    """清理由 ``parse_reading`` 走，题卡上拿到的必须已经没有分值。"""

    RAW = (
        "【内容类型】练习题\n"
        "【题号】16\n"
        "【题型】解答题\n"
        f"【题干】\n{REAL}\n"
        "【配图】无\n"
    )

    def test_parse_drops_the_score(self):
        reading = readers.parse_reading(self.RAW, 16)
        self.assertNotIn("7分", reading["stem"])
        self.assertTrue(reading["stem"].startswith("计算："))

    def snapshot(self):
        return {
            "id": 1, "number": 16, "group_id": None,
            "regions": [{"page_idx": 4, "bbox": [40.0, 600.0, 480.0, 700.0]}],
            "candidates": [], "question_type": "free_response", "section": "三、解答题",
            "stem": "", "options": {}, "edited": False,
            "source_kind": Question.SourceKind.UNKNOWN, "source_anchor_seq": 12,
            "segmentation_flags": [], "body_mode": "text", "start_source": "mineru",
            "witness_blocks": [],
        }

    def test_the_card_itself_is_clean(self):
        with mock.patch.object(readers, "primary_engine", return_value=object()), \
                mock.patch.object(readers, "read_question",
                                  return_value=readers.parse_reading(self.RAW, 16)), \
                mock.patch.object(pipeline.imaging, "stack_regions",
                                  return_value=(SimpleNamespace(height=700, width=900), [])), \
                mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"):
            result = pipeline.read_card(self.snapshot(),
                                        SimpleNamespace(load=lambda page: object()))
        self.assertNotIn("7分", result["stem"])
        self.assertTrue(result["stem"].startswith("计算："))