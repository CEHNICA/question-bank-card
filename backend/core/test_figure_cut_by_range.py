r"""配图有一大半落在题目范围外面，成品卷上这张图是残的，而卡是绿的。

真实案例（都是绿卡、零旗标，学生在成品卷上没法做题）：

* 八下第 1 题 id=777「如图，在菱形 ABCD 中，对角线 AC 与 BD 相交于点 O。已知
  AB=5cm，AO=4cm，求 BD 的长。」—— 老师看到的是**只有上半截的三角形**，菱形的
  B、D 两个顶点整个不见了。
* 初一 id=295「如图是正方体的展开图…」—— 只剩左上角**一个小方块**，六格展开图
  丢了一半以上。
* 八下第 1 题 id=797「求 BD 与 AD 的长」—— 只有上半截矩形，B、C、D 三个顶点被切。
* 八下第 2 题 id=778，候选图 [714,269,860,401]、范围 [98,329,914,422]：
  只有 55% 的图落在范围里。

这条以前一声不吭：`FLAG_UNFOUND_FIGURE`（原卷可能有图没有被找到）只在「识读说
缺图」或「点的标签压根不在候选池」时报；而这里标签就在候选池里，只是没被挂上，
于是既没有图、也没有提示。它和 `figures_outside_own_range` 也不是一回事——那边是
「图已经挂到卡上却落在范围外」，接在 `assign_foreign_figures` 那条路上；这边是
「图压根没挂、范围又只截到它一部分」，接在识读出旗标的那条路上。

阈值 0.81 的来历见 `figure_policy._RANGE_COVERS_FIGURE` 的注释：逐档裁图看过，
82% 往上全是完整的（多出来的是页边留白和学生写在旁边的字）。
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from . import figure_policy, pipeline
from .models import Question

# 八下第 2 题 id=778 的真实坐标：候选图 [714,269,860,401] 只露出 55%。
REGIONS = [{"page_idx": 10, "bbox": [98, 329, 914, 422]}]
CANDIDATE = {"label": "1", "seq": 116, "page_idx": 10, "bbox": [714, 269, 860, 401]}

_UNSET = object()


class CandidatesCutByOwnRangeTests(SimpleTestCase):
    def cut(self, regions=_UNSET, candidates=None, named=("1",), attached=()):
        return figure_policy.candidates_cut_by_own_range(
            list(REGIONS) if regions is _UNSET else regions,
            [CANDIDATE] if candidates is None else candidates,
            named,
            attached,
        )

    def test_a_figure_the_range_only_half_covers_is_a_hit(self):
        self.assertEqual(len(self.cut()), 1)

    def test_a_figure_fully_inside_the_range_is_not_a_hit(self):
        # 八下 id=776：候选框和范围完全重合，范围截图里完完整整有这张图。
        self.assertEqual(self.cut(regions=[{"page_idx": 10, "bbox": [700, 260, 870, 410]}]), [])

    def test_a_loose_bbox_whose_figure_is_complete_is_not_a_hit(self):
        # 初二 id=230 的形状：候选框比范围大 18%，多出来的是页边留白，图是完整的。
        region = {"page_idx": 10, "bbox": [717, 271, 857, 399]}
        box = CANDIDATE["bbox"]
        covered = (min(box[2], 857) - max(box[0], 717)) * (min(box[3], 399) - max(box[1], 271))
        self.assertGreater(covered / ((box[2] - box[0]) * (box[3] - box[1])), 0.81)
        self.assertEqual(self.cut(regions=[region]), [])

    def test_two_ranges_covering_the_figure_between_them_still_count_as_covered(self):
        # 一道题跨两段范围时，每一段都只盖住一半，但拼起来是整张图——不能报。
        box = CANDIDATE["bbox"]
        self.assertEqual(
            self.cut(
                regions=[
                    {"page_idx": 10, "bbox": [box[0], box[1], box[2], (box[1] + box[3]) // 2]},
                    {"page_idx": 10, "bbox": [box[0], (box[1] + box[3]) // 2, box[2], box[3]]},
                ]
            ),
            [],
        )

    def test_a_figure_already_cropped_onto_the_card_is_left_alone(self):
        # 单独裁出来挂在卡上的那张不受题目范围影响。
        self.assertEqual(self.cut(attached=("1",)), [])

    def test_a_candidate_nobody_named_is_left_alone(self):
        self.assertEqual(self.cut(named=()), [])
        self.assertEqual(self.cut(named=("2",)), [])

    def test_a_page_the_range_does_not_cover_is_left_to_the_other_guards(self):
        # 跨页题：候选图在第 11 页，本题范围只在第 10 页。这属于归属问题，
        # 不是「被切」，不在这条守卫里下结论。
        self.assertEqual(self.cut(regions=[{"page_idx": 9, "bbox": [98, 329, 914, 422]}]), [])

    def test_malformed_candidates_are_left_to_the_other_guards(self):
        for candidate in (
            {"label": "1"},
            {"label": "1", "bbox": [1, 2]},
            {"label": "1", "bbox": None},
            {"label": "1", "bbox": [1, 1, 1, 1]},
            "不是字典",
        ):
            self.assertEqual(self.cut(candidates=[candidate]), [], candidate)

    def test_missing_regions_do_not_flag_every_figure(self):
        # 范围整段缺失时判不了「被切」，不能把每一张候选图都报一遍。
        self.assertEqual(self.cut(regions=[]), [])
        self.assertEqual(self.cut(regions=None), [])

    def test_no_named_figures_means_no_hit(self):
        self.assertEqual(figure_policy.candidates_cut_by_own_range(REGIONS, [CANDIDATE], {}, []), [])


READING = {
    "stem": "",
    "options": {},
    "type": "free_response",
    "content_kind": "exercise",
    "figures": {"1": "stem"},
    "missing_figure": False,
    "number_seen": 2,
    "figure_descriptions": [],
    "unclear": False,
}


def snapshot(**changes):
    # 纸上印的那一行要和识读出来的题干一致，否则会先撞上「题号那一行认不出这句话」
    # 那条守卫，测的就不是这条判据了。
    stem = changes.get("stem") or "如图，在菱形 ABCD 中，对角线 AC 与 BD 相交于点 O。"
    base = {
        "id": 1,
        "number": 2,
        "group_id": None,
        "regions": list(REGIONS),
        "candidates": [dict(CANDIDATE)],
        "question_type": "free_response",
        "stem": "",
        "options": {},
        "edited": False,
        "source_kind": Question.SourceKind.EXERCISE,
        "source_anchor_seq": 128,
        "segmentation_flags": [],
        "body_mode": "text",
        "start_source": "mineru",
        "witness": "",
        "witness_blocks": [{"seq": 128, "page_idx": 10, "bbox": [98, 329, 914, 350], "text": stem, "type": "text"}],
    }
    base.update(changes)
    return base


class CutFigureFlagTests(SimpleTestCase):
    """接线：read_card 真要把这条提示挂到卡上。"""

    store = staticmethod(type("Store", (), {"load": lambda self, page: object()})())

    def run_card(self, stem="如图，在菱形 ABCD 中，对角线 AC 与 BD 相交于点 O。", **changes):
        reading = {**READING, "stem": stem}
        with (
            patch.object(pipeline.readers, "primary_engine", return_value=object()),
            patch.object(pipeline.readers, "read_question", return_value=reading),
            patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])),
            patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"),
        ):
            return pipeline.read_card(snapshot(**changes), self.store)

    def test_a_card_whose_figure_is_cut_off_is_not_green(self):
        result = self.run_card()

        self.assertIn(figure_policy.FLAG_FIGURE_CUT_BY_RANGE, result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_card_whose_range_covers_the_whole_figure_stays_green(self):
        # 范围完整罩住这张图时，本条不该报，卡是绿的：识读点名了图、候选池里也有，
        # 范围又把整张图截进去了——范围截图里完完整整有它。
        result = self.run_card(regions=[{"page_idx": 10, "bbox": [700, 260, 870, 410]}])

        self.assertNotIn(figure_policy.FLAG_FIGURE_CUT_BY_RANGE, result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)

    def test_a_card_with_no_figure_and_no_figure_wording_stays_green(self):
        # 原卷这一段压根没有配图：候选池空、识读也没点名。别因为「没挂图」就报。
        stem = "计算：$(\\pi-3)^0+\\left(\\frac{1}{2}\\right)^{-1}$。"
        reading = {**READING, "stem": stem, "figures": {}}
        with (
            patch.object(pipeline.readers, "primary_engine", return_value=object()),
            patch.object(pipeline.readers, "read_question", return_value=reading),
            patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])),
            patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"),
        ):
            result = pipeline.read_card(
                snapshot(stem=stem, candidates=[], regions=[{"page_idx": 10, "bbox": [700, 260, 870, 410]}]), self.store
            )

        self.assertEqual(result["state"], Question.State.GREEN)

    def test_a_card_that_never_named_a_figure_stays_green(self):
        reading = {**READING, "stem": "下列说法正确的是（　）", "figures": {}}
        with (
            patch.object(pipeline.readers, "primary_engine", return_value=object()),
            patch.object(pipeline.readers, "read_question", return_value=reading),
            patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])),
            patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"),
        ):
            result = pipeline.read_card(snapshot(), self.store)

        self.assertNotIn(figure_policy.FLAG_FIGURE_CUT_BY_RANGE, result["flags"])
