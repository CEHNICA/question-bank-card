"""Offline option-image ownership checks using explicit external text labels."""

from copy import deepcopy

from django.test import SimpleTestCase

from .figure_assignment import option_assignments, option_evidence


def picture(label, bbox, page=0, **extra):
    return {"label": label, "page_idx": page, "bbox": bbox, **extra}


def marker(text, bbox, page=0, **extra):
    return {"type": "text", "text": text, "page_idx": page, "bbox": bbox, **extra}


class OptionAssignmentTests(SimpleTestCase):
    def resolve(self, candidates, blocks, assignments=None, **extra):
        return option_evidence(stem="下列选项正确的是（ ）", options={}, kind="single_choice",
                               candidates=candidates, assignments=assignments or {}, blocks=blocks, **extra)

    def test_two_by_two_follows_labels_not_candidate_numbers(self):
        candidates = [picture("4", [130, 100, 260, 210]), picture("1", [530, 100, 660, 210]),
                      picture("3", [130, 300, 260, 410]), picture("2", [530, 300, 660, 410])]
        blocks = [marker("A.", [100, 100, 120, 120]), marker("B.", [500, 100, 520, 120]),
                  marker("C.", [100, 300, 120, 320]), marker("D.", [500, 300, 520, 320])]
        self.assertEqual(self.resolve(candidates, blocks)["assignments"],
                         {"4": "A", "1": "B", "3": "C", "2": "D"})

    def test_vertical_options_include_e(self):
        candidates = [picture(str(20 - i), [140, 100 + 160 * i, 300, 200 + 160 * i])
                      for i in range(5)]
        blocks = [marker(f"（{slot}）", [100, 100 + 160 * i, 130, 125 + 160 * i])
                  for i, slot in enumerate("ABCDE")]
        self.assertEqual(self.resolve(candidates, blocks)["assignments"],
                         {str(20 - i): slot for i, slot in enumerate("ABCDE")})

    def test_text_plus_image_option_keeps_external_anchor(self):
        candidates = [picture("7", [100, 145, 280, 270])]
        block = marker("A. 向右平移", [100, 100, 280, 130], seq=12)
        result = option_evidence(stem="请选择正确的选项", options={"A": "向右平移"},
                                 kind="single_choice", candidates=candidates, assignments={"7": "stem"},
                                 blocks=[block])
        self.assertEqual(result["assignments"], {"7": "A"})
        self.assertEqual(result["evidence"]["7"]["marker_seq"], 12)

    def test_labels_below_graphs_and_full_width_forms(self):
        candidates = [picture("5", [100, 100, 240, 200]), picture("9", [400, 100, 540, 200])]
        blocks = [marker("Ａ", [160, 210, 180, 230]), marker("Ｂ", [460, 210, 480, 230])]
        self.assertEqual(self.resolve(candidates, blocks)["assignments"], {"5": "A", "9": "B"})

    def test_label_centered_at_left_of_graph_matches_real_page_geometry(self):
        # On the scanned exam, C. is centered beside a lower-row graph, rather
        # than beside its top edge. The marker and crop come from that page.
        candidates = [picture("4", [554, 475, 652, 594])]
        blocks = [marker("C.", [532, 526, 547, 546])]
        self.assertEqual(self.resolve(candidates, blocks)["assignments"], {"4": "C"})

    def test_existing_conflicting_option_is_retained_and_diagnosed(self):
        result = self.resolve([picture("1", [130, 100, 260, 210])],
                              [marker("B.", [100, 100, 120, 120])], {"1": "A"})
        self.assertEqual(result["assignments"], {"1": "A"})
        self.assertFalse(result["evidence"]["1"]["applied"])
        self.assertEqual(result["conflicts"][0]["reason"], "printed_option_marker_disagrees")

    def test_exclusions_foreign_and_unknown_roles_are_never_replaced(self):
        for role in ("none", "decoration", "q3", "table", "manual", "unknown"):
            with self.subTest(role=role):
                result = self.resolve([picture("1", [130, 100, 260, 210])],
                                      [marker("A.", [100, 100, 120, 120])], {"1": role})
                self.assertEqual(result["assignments"], {"1": role})

    def test_manual_candidate_and_handwritten_label_are_skipped(self):
        candidates = [picture("1", [130, 100, 260, 210], source="manual")]
        self.assertEqual(self.resolve(candidates, [marker("A.", [100, 100, 120, 120])])["assignments"], {})
        for metadata in ({"handwritten": True}, {"is_handwritten": True}, {"source": "manual"}):
            with self.subTest(metadata=metadata):
                self.assertEqual(self.resolve([picture("1", [130, 100, 260, 210])],
                                              [marker("A.", [100, 100, 120, 120], **metadata)])["assignments"], {})

    def test_internal_point_labels_do_not_bind_an_option(self):
        candidates = [picture("1", [100, 100, 300, 300])]
        blocks = [marker("A.", [110, 110, 130, 130]), marker("B.", [210, 110, 230, 130])]
        self.assertEqual(self.resolve(candidates, blocks)["assignments"], {})

    def test_cross_page_coordinates_are_not_matched(self):
        candidates = [picture("1", [130, 100, 260, 210], page=1)]
        self.assertEqual(self.resolve(candidates, [marker("A.", [100, 100, 120, 120], page=0)])["assignments"], {})
        self.assertEqual(self.resolve(candidates, [marker("A.", [100, 100, 120, 120], page=1)])["assignments"], {"1": "A"})

    def test_merged_option_block_and_bare_lone_letter_are_not_localised(self):
        candidates = [picture("1", [130, 100, 260, 210])]
        for text in ("A. 向左 B. 向右", "A. 向左\nB. 向右", "A", "A点的位置"):
            with self.subTest(text=text):
                self.assertEqual(self.resolve(candidates, [marker(text, [100, 100, 120, 120])])["assignments"], {})

    def test_duplicate_option_markers_are_not_guessed(self):
        candidates = [picture("1", [130, 100, 260, 210]), picture("2", [130, 300, 260, 410])]
        blocks = [marker("A.", [100, 100, 120, 120]), marker("A.", [100, 300, 120, 320])]
        result = self.resolve(candidates, blocks)
        self.assertEqual(result["assignments"], {})
        self.assertEqual(result["conflicts"][0]["reason"], "duplicate_option_marker")

    def test_ambiguous_nearby_images_and_two_labels_for_one_image_stay_pending(self):
        candidates = [picture("1", [130, 100, 260, 210]), picture("2", [132, 101, 262, 211])]
        result = self.resolve(candidates, [marker("A.", [100, 100, 120, 120])])
        self.assertEqual(result["assignments"], {})
        self.assertEqual(result["conflicts"][0]["reason"], "ambiguous_marker_geometry")
        result = self.resolve([picture("1", [130, 100, 260, 210])],
                              [marker("A.", [100, 100, 120, 120]), marker("B.", [100, 130, 120, 150])])
        self.assertEqual(result["assignments"], {})
        self.assertEqual(result["conflicts"][0]["reason"], "multiple_option_markers")

    def test_without_blocks_nonchoice_or_distant_geometry_does_not_change_roles(self):
        candidates = [picture("1", [130, 100, 260, 210])]
        original = {"1": "stem"}
        self.assertEqual(option_assignments(stem="", options={}, kind="single_choice",
                                           candidates=candidates, assignments=original), original)
        self.assertEqual(option_assignments(stem="", options={}, kind="free_response", candidates=candidates,
                                           assignments=original, blocks=[marker("A.", [100, 100, 120, 120])]), original)
        self.assertEqual(self.resolve(candidates, [marker("A.", [700, 700, 720, 720])], original)["assignments"], original)

    def test_inputs_are_preserved_and_bad_geometry_is_rejected(self):
        candidates = [picture("1", [130, 100, 260, 210])]
        blocks = [marker("A.", [100, 100, 120, 120])]
        roles = {"1": "stem"}
        before = deepcopy((candidates, blocks, roles))
        result = self.resolve(candidates, blocks, roles)
        self.assertEqual((candidates, blocks, roles), before)
        self.assertIsNot(result["assignments"], roles)
        for bbox in ([float("nan"), 100, 260, 210], [130, 100, 130, 210], [True, 100, 260, 210],
                     [130, 100, 1100, 210]):
            with self.subTest(bbox=bbox):
                self.assertEqual(self.resolve([picture("1", bbox)], blocks)["assignments"], {})

    def test_duplicate_candidate_ids_and_oversized_input_are_not_guessed(self):
        candidates = [picture("1", [130, 100, 260, 210]), picture("1", [530, 100, 660, 210])]
        self.assertEqual(self.resolve(candidates, [marker("A.", [100, 100, 120, 120])])["assignments"], {})
        candidates = [picture(str(i), [130, 100, 260, 210]) for i in range(33)]
        self.assertEqual(self.resolve(candidates, [marker("A.", [100, 100, 120, 120])])["assignments"], {})
