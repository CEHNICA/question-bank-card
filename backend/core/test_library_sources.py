"""Offline source discovery: separate evidence, never merged histories/reviews."""

from copy import deepcopy
from unittest import mock

from django.test import Client, TestCase

from . import library
from .models import Paper, PublishedQuestion, Question


class LibrarySourceTests(TestCase):
    def setUp(self):
        self.serial = 0
        self.first = self.publication()

    def publication(self, *, paper=None, question=None, content=None, version=1, status="published", **changes):
        self.serial += 1
        if question:
            paper = question.paper
        paper = paper or Paper.objects.create(filename=f"资料{self.serial}.pdf", kind="pdf", sha256="a" * 64)
        question = question or Question.objects.create(paper=paper, number=1, question_type="single_choice",
                                                        stem="虚构题卡", state="green")
        content = deepcopy(content) if content is not None else {
            "question_type": "single_choice", "stem": "若 $x+1=3$，求 $x$ 的值。（ ）",
            "options": {"A": "$1$", "B": "$2$", "C": "$3$", "D": "$4$"},
            "answer": "", "analysis": "", "figures": [], "origin": "甲校月考",
        }
        content.update(changes)
        content.update(document_id=str(paper.id), source_filename=paper.filename)
        return PublishedQuestion.objects.create(
            paper=paper, question=question, source_filename=paper.filename, number=self.serial,
            question_type=content["question_type"], version=version, status=status, content=content,
            content_hash=library.content_hash(content), review_source="ai", review_agent="演示助手",
        )

    def detail(self, publication=None, **params):
        return Client().get(f"/api/library/{(publication or self.first).id}", params)

    def test_prose_layout_matches_but_each_source_keeps_its_own_history(self):
        second = self.publication(stem="若$x+1=3$, 求$x$的值.()", origin="另一份资料的题源")
        old = self.publication(question=second.question, content=second.content, version=2, status="superseded")
        self.publication(paper=self.first.paper)  # Same paper is not another source.
        before = list(PublishedQuestion.objects.values())
        body = self.detail().json()
        self.assertEqual([row["id"] for row in body["related_sources"]], [str(second.id)])
        row = body["related_sources"][0]
        self.assertEqual((row["version_count"], row["origin"], row["review"]["source"]),
                         (2, "另一份资料的题源", "ai"))
        self.assertEqual(row["content"], second.content)
        self.assertEqual([row["id"] for row in body["history"]], [str(self.first.id)])
        self.assertEqual(body["versions"], [])
        self.assertEqual(body["possible_sources"], [])
        self.assertEqual(self.detail(compare=str(second.id)).status_code, 404)
        self.assertEqual(list(PublishedQuestion.objects.values()), before)
        self.assertNotIn(str(old.id), [row["id"] for row in body["related_sources"]])

    def test_limited_tex_variants_are_candidates_requiring_inspection(self):
        first = self.publication(question_type="free_response", options={},
                                 stem=r"集合 $A=\{x| 1\leq x\leq 5\}$．求 $\complement_U A$．")
        candidate = self.publication(question_type="free_response", options={},
                                     stem=r"集合$A=\{x\mid 1\leq x\leq 5\}$.求$\complement_{U}A$.")
        body = self.detail(first).json()
        self.assertEqual(body["related_sources"], [])
        self.assertEqual([row["id"] for row in body["possible_sources"]], [str(candidate.id)])
        self.assertEqual(body["possible_sources_count"], 1)
        self.assertFalse(body["possible_sources_truncated"])
        self.assertEqual(self.detail(first, compare=str(candidate.id)).status_code, 404)

    def test_math_type_option_labels_and_answer_conflicts_do_not_match(self):
        changes = [
            {"stem": "若 $x-1=3$，求 $x$ 的值。（ ）"},
            {"stem": "若 $x+1=4$，求 $x$ 的值。（ ）"},
            {"stem": "若 $x +1=3$，求 $x$ 的值。（ ）"},  # Strict math bytes.
            {"question_type": "multiple_choice"},
            {"options": {"A": "$2$", "B": "$1$", "C": "$3$", "D": "$4$"}},
            {"question_type": "unknown"},
        ]
        for change in changes:
            self.publication(**change)
        body = self.detail().json()
        self.assertEqual((body["related_sources"], body["possible_sources"]), ([], []))
        answered = self.publication(answer="A", analysis="先移项。")
        self.publication(answer="B", analysis="先移项。")
        self.publication(answer="A", analysis="先平方。")
        body = self.detail(answered).json()
        # An original paper with no answer can still be inspected. A conflicting
        # answer or analysis must not acquire implied agreement.
        self.assertEqual([row["id"] for row in body["related_sources"]], [str(self.first.id)])

    def test_candidate_rules_do_not_change_numbers_operators_or_subscript_contents(self):
        first = self.publication(question_type="free_response", options={}, stem=r"求 $\complement_U A$。")
        for text in (r"求 $\complement_{V}A$。", r"求 $\complement_{UV}A$。",
                     r"求 $\complement_{U}A^2$。", r"求 $\complement_{U}A+B$。"):
            self.publication(question_type="free_response", options={}, stem=text)
        body = self.detail(first).json()
        self.assertEqual((body["related_sources"], body["possible_sources"]), ([], []))

    def test_candidate_separator_command_boundary_is_only_math_layout(self):
        first = self.publication(question_type="free_response", options={},
                                 stem=r"$A=\{x|x>1\}$，求 $\complement_U A$。")
        candidate = self.publication(question_type="free_response", options={},
                                     stem=r"$A=\{x\mid x>1\}$，求 $\complement_{U}A$。")
        different = self.publication(question_type="free_response", options={},
                                     stem=r"$A=\{x\mid x>2\}$，求 $\complement_{U}A$。")
        body = self.detail(first).json()
        self.assertEqual(body["related_sources"], [])
        self.assertEqual([row["id"] for row in body["possible_sources"]], [str(candidate.id)])
        self.assertNotIn(str(different.id), [row["id"] for row in body["possible_sources"]])
        self.assertNotEqual(library._source_match_text(r"$x +1$", possible=True),
                            library._source_match_text(r"$x+1$", possible=True))

    def test_picture_questions_and_missing_picture_references_are_not_auto_linked(self):
        for change in (
            {"figures": [{"slot": "stem", "url": "/figure.png"}]},
            {"stem": self.first.content["stem"] + "![配图](/figure.png)"},
            {"stem": self.first.content["stem"] + "<img src='/figure.png'>"},
            {"stem": self.first.content["stem"] + "如图"},
        ):
            other = self.publication(**change)
            self.assertIsNone(library.source_match_fingerprint(other))
            self.assertEqual(self.detail(other).json()["related_sources"], [])

    def test_normalization_does_not_join_english_or_rewrite_units_code_and_tables(self):
        for a, b in (("Find ab.", "Find a b."), ("面积为 2 cm²。", "面积为 2 cm2。"),
                     (r"比较 `$x_U B$`。", r"比较 `$x_{U}B$`。"),
                     ("| A | B |\n|--|--|", "|A|B|\n|--|--|"),
                     (r"说明 $\text{x_U A}$。", r"说明 $\text{x_{U}A}$。")):
            first = self.publication(stem=a)
            second = self.publication(stem=b)
            self.assertNotEqual(library.source_match_fingerprint(first, possible=True),
                                library.source_match_fingerprint(second, possible=True))

    def test_unknown_document_and_orphans_are_not_grouped_by_null_foreign_keys(self):
        second = self.publication()
        PublishedQuestion.objects.filter(pk=second.pk).update(question=None, paper=None)
        body = self.detail().json()
        self.assertEqual(body["related_sources"][0]["version_count"], 1)
        same_document = self.publication(paper=self.first.paper)
        PublishedQuestion.objects.filter(pk=same_document.pk).update(question=None, paper=None)
        unknown = self.publication()
        unknown.content.pop("document_id")
        unknown.paper = None
        unknown.save()
        self.assertEqual([row["id"] for row in self.detail().json()["related_sources"]], [str(second.id)])

    def test_live_sources_only_and_bounded_disclosure(self):
        self.publication(status="withdrawn")
        self.publication(status="superseded")
        for _ in range(4):
            self.publication()
        with mock.patch("core.library.RELATED_SOURCE_LIMIT", 2):
            # Source metadata uses one aggregate query, not one per match.
            with self.assertNumQueries(4):
                body = self.detail().json()
            self.assertEqual(len(body["related_sources"]), 2)
            self.assertEqual(body["related_sources_count"], 4)
            self.assertTrue(body["related_sources_truncated"])
            self.assertEqual(body["possible_sources_count"], 0)

    def test_exact_and_candidate_sources_are_disjoint_and_each_discloses_limits(self):
        first = self.publication(question_type="free_response", options={}, stem=r"求 $x_U A$。")
        exact = self.publication(question_type="free_response", options={}, stem=r"求$x_U A$.")
        for _ in range(3):
            self.publication(question_type="free_response", options={}, stem=r"求$x_{U}A$.")
        with mock.patch("core.library.RELATED_SOURCE_LIMIT", 2):
            body = self.detail(first).json()
        self.assertEqual([row["id"] for row in body["related_sources"]], [str(exact.id)])
        self.assertEqual(body["related_sources_count"], 1)
        self.assertFalse(body["related_sources_truncated"])
        self.assertEqual(len(body["possible_sources"]), 2)
        self.assertEqual(body["possible_sources_count"], 3)
        self.assertTrue(body["possible_sources_truncated"])

    def test_malformed_content_is_not_silently_used_for_source_identity(self):
        for content in ([], {"figures": {}}, {"question_type": "single_choice", "stem": "题目", "options": []}):
            self.first.content = content
            self.assertIsNone(library.source_match_fingerprint(self.first))
            self.assertEqual(library.related_publication_sources(self.first)["related_sources"], [])
