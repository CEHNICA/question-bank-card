"""标签系统重构的行为验收。

全部用虚构题。矩阵编号对应《题有据-标签系统重构执行计划》第 10 节。

这里守住的每一条规矩，都是有代价才定下来的：

* 用途记在关联上，不记在概念身份上 —— 否则「分类讨论」没法既当考点又当手段。
* 范围键必须非空 —— SQLite 的 NULL 不参与唯一约束，NULL 范围会让整题记录重复。
* 小问和分支必须属于同一道题 —— 跨题绑定是数据污染，不是显示问题。
* 「没整理」不等于「没有」 —— 排除条件最会骗人的地方就在这儿。
* 方法 A、B 默认要同一个解法里都有 —— 暗中混用两种语义，结果就骗人了。
"""

from __future__ import annotations

import uuid

from django.test import TestCase

from core import library_concepts, library_query, library_tag_migration, library_tagging
from core.models import (
    LibraryChapter,
    LibraryChapterConcept,
    LibraryCollection,
    LibraryCollectionMember,
    LibraryMigrationMap,
    LibrarySolution,
    LibrarySolutionVariant,
    LibrarySubquestion,
    LibraryTag,
    LibraryTagAssignment,
    LibraryTagRelation,
    LibraryTagSuggestion,
    Paper,
    PublishedQuestion,
    Question,
)


class RefactorTestBase(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(filename="虚构卷.pdf", kind="pdf", sha256="f" * 64)
        self.serial = 0

    def make_publication(
        self, *, stem="虚构题：已知函数 f(x)=x^2+1，求最小值。", analysis="", answer="", qtype="free_response"
    ):
        self.serial += 1
        question = Question.objects.create(
            paper=self.paper, number=self.serial, question_type=qtype, stem=stem, state="green"
        )
        content = {
            "stem": stem,
            "options": {},
            "answer": answer,
            "analysis": analysis,
            "figures": [],
            "source_filename": "虚构卷.pdf",
            "section": {},
            "origin": "",
        }
        return PublishedQuestion.objects.create(
            question=question,
            paper=self.paper,
            source_filename="虚构卷.pdf",
            number=self.serial,
            question_type=qtype,
            version=1,
            content=content,
            content_hash=(f"h{self.serial}" * 16)[:64],
            search_text=stem + " " + analysis,
        )

    def make_concept(self, name, *, kind="knowledge", aliases=None, scope_note="", canonical=None):
        return LibraryTag.objects.create(
            name=name,
            kind=kind,
            canonical_key=canonical if canonical is not None else library_concepts.canonical_key_for(name),
            aliases=list(aliases or []),
            scope_note=scope_note,
        )

    def attach(
        self,
        publication,
        tag,
        *,
        role="primary",
        subquestion=None,
        variant=None,
        rationale="",
        evidence="",
        request_id=None,
    ):
        payload = {
            "publication_id": str(publication.pk),
            "tag_ids": [str(tag.pk)],
            "role": role,
            "client_request_id": str(request_id or uuid.uuid4()),
            "expected_revision": publication.tag_revision,
            "rationale": rationale,
            "evidence": evidence,
        }
        if subquestion is not None:
            payload["subquestion_id"] = str(subquestion.pk)
        if variant is not None:
            payload["variant_id"] = str(variant.pk)
        publication.refresh_from_db()
        return library_concepts.attach_assertion(payload)


# ---------------------------------------------------------------------------
# D：数据模型与关联行为
# ---------------------------------------------------------------------------


class ConceptIdentityTests(RefactorTestBase):
    def test_D01_same_concept_can_be_primary_and_method(self):
        """同一个概念身份，可以是主要考查，也可以是解法采用。两条关联各记各的用途。"""
        publication = self.make_publication()
        concept = self.make_concept("分类讨论")
        variant = library_concepts.ensure_variant(publication, "解法一")

        self.attach(publication, concept, role="primary", rationale="这题主要考分类讨论")
        publication.refresh_from_db()
        self.attach(publication, concept, role="method", variant=variant, rationale="这一问用分类讨论")

        rows = LibraryTagAssignment.objects.filter(publication=publication, tag=concept)
        self.assertEqual(rows.count(), 2)
        self.assertEqual({row.role for row in rows}, {"primary", "method"})
        # 用途是关联的属性，不是概念的属性 —— 概念本身没被拆成两个。
        self.assertEqual(LibraryTag.objects.filter(name="分类讨论").count(), 1)

    def test_D02_related_concepts_are_not_merged(self):
        """「中位线定理」和「构造中位线」不等价：两个概念，相关关系表达联系。"""
        theorem = self.make_concept("中位线定理")
        construction = self.make_concept("构造中位线")

        library_concepts.relate_concepts(str(theorem.pk), str(construction.pk), relation_type="related")

        self.assertEqual(LibraryTag.objects.filter(name__in=["中位线定理", "构造中位线"]).count(), 2)
        relation = LibraryTagRelation.objects.get(parent=theorem, child=construction)
        self.assertEqual(relation.relation_type, "related")
        # 相关关系不参与父级继承。
        self.assertNotIn(
            construction.pk, library_concepts.descendant_tag_ids([str(theorem.pk)], include_descendants=True)
        )

    def test_D03_similar_names_do_not_merge_by_default(self):
        """相近但不同的名字，各自保留；搜索命中时标歧义，不默认挑一个。"""
        first = self.make_concept("中位线性质")
        second = self.make_concept("中位线性质判定")
        self.assertNotEqual(first.pk, second.pk)

        hits = library_concepts.search_concepts("中位线性质")
        self.assertGreaterEqual(len(hits), 2)

    def test_D05_same_method_in_two_subquestions_keeps_separate_evidence(self):
        """同一个方法用于两个小问：两条记录各自独立，整题结果去重。"""
        publication = self.make_publication(stem="（1）求证 AB=CD。\n（2）求证 AB∥CD。")
        subquestions = library_concepts.ensure_subquestions(publication)
        self.assertEqual(len(subquestions), 2)
        method = self.make_concept("全等三角形", kind="method")
        variant = library_concepts.ensure_variant(publication, "解法一")

        self.attach(publication, method, role="method", subquestion=subquestions[0], variant=variant)
        publication.refresh_from_db()
        self.attach(publication, method, role="method", subquestion=subquestions[1], variant=variant)

        rows = LibraryTagAssignment.objects.filter(publication=publication, tag=method)
        self.assertEqual(rows.count(), 2)
        self.assertEqual({row.subquestion_id for row in rows}, {subquestions[0].pk, subquestions[1].pk})
        # 整题去重：检索结果里这题只出现一次。
        result = library_query.run_query({"method_groups": [{"ids": [str(method.pk)], "cooccurrence": "same_variant"}]})
        self.assertEqual(result["count"], 1)

    def test_D07_cross_question_binding_is_rejected(self):
        """小问和解法分支必须属于同一道题，跨题绑定服务端直接拒绝。"""
        first = self.make_publication()
        second = self.make_publication(stem="（1）求证 AB=CD。\n（2）求证 AB∥CD。")
        foreign_sub = library_concepts.ensure_subquestions(second)[0]
        concept = self.make_concept("构造中位线", kind="method")
        foreign_variant = library_concepts.ensure_variant(second, "另一题的解法")

        with self.assertRaises(library_concepts.AssertionError_):
            self.attach(first, concept, role="method", subquestion=foreign_sub)
        with self.assertRaises(library_concepts.AssertionError_):
            self.attach(first, concept, role="method", variant=foreign_variant)

    def test_D08_cycle_is_rejected(self):
        parent = self.make_concept("三角形")
        child = self.make_concept("等腰三角形")
        library_concepts.relate_concepts(str(parent.pk), str(child.pk), relation_type="hierarchy")
        with self.assertRaises(library_concepts.AssertionError_):
            library_concepts.relate_concepts(str(child.pk), str(parent.pk), relation_type="hierarchy")

    def test_D09_repeated_request_applies_once(self):
        """同一个请求 ID 重复提交，只产生一次正式修改。"""
        publication = self.make_publication()
        concept = self.make_concept("函数单调性")
        request_id = uuid.uuid4()

        first = self.attach(publication, concept, request_id=request_id)
        second = self.attach(publication, concept, request_id=request_id)

        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=publication, tag=concept).count(), 1)

    def test_D10_conflicting_revision_is_refused(self):
        """两个窗口同时改一道题，后来的那个看到冲突，不覆盖。"""
        publication = self.make_publication()
        concept = self.make_concept("圆的方程")
        payload = {
            "publication_id": str(publication.pk),
            "tag_ids": [str(concept.pk)],
            "role": "primary",
            "client_request_id": str(uuid.uuid4()),
            "expected_revision": publication.tag_revision,
        }
        library_concepts.attach_assertion(payload)
        # 另一个窗口手里的版本号已经过时。
        stale = dict(payload)
        stale["client_request_id"] = str(uuid.uuid4())
        with self.assertRaises(library_tagging.TaggingError):
            library_concepts.attach_assertion(stale)

    def test_undo_attach_puts_the_label_back(self):
        """撤销「贴标签」= 真的把那条关联移除。"""
        publication = self.make_publication()
        concept = self.make_concept("圆的方程")
        result = self.attach(publication, concept)

        publication.refresh_from_db()
        undone = library_concepts.undo_assertion(
            {
                "operation_id": result["operation"],
                "client_request_id": str(uuid.uuid4()),
                "expected_revision": publication.tag_revision,
            }
        )
        self.assertTrue(undone["ok"])
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=publication).count(), 0)

    def test_undo_detach_puts_the_label_back(self):
        """撤销「移除」= 按快照把那条关联原样建回来。"""
        publication = self.make_publication()
        concept = self.make_concept("圆的方程")
        self.attach(publication, concept, rationale="这题主要考它")
        row = LibraryTagAssignment.objects.get(publication=publication)
        publication.refresh_from_db()
        library_concepts.detach_assertion(
            {
                "assignment_id": str(row.pk),
                "client_request_id": str(uuid.uuid4()),
                "expected_revision": publication.tag_revision,
            }
        )
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=publication).count(), 0)

        publication.refresh_from_db()
        operation = library_tagging.LibraryTagOperation.objects.filter(action="detach_assertion").first()
        library_concepts.undo_assertion(
            {
                "operation_id": operation.pk,
                "client_request_id": str(uuid.uuid4()),
                "expected_revision": publication.tag_revision,
            }
        )
        restored = LibraryTagAssignment.objects.get(publication=publication)
        self.assertEqual(restored.tag_id, concept.pk)
        self.assertEqual(restored.role, "primary")
        self.assertEqual(restored.rationale, "这题主要考它")

    def test_undo_refuses_when_the_label_changed_underneath(self):
        """撤销带冲突检查：那条关联被人改过就别动，别盖掉别人的改动。"""
        publication = self.make_publication()
        concept = self.make_concept("圆的方程")
        result = self.attach(publication, concept)
        row = LibraryTagAssignment.objects.get(publication=publication)
        # 别人改了这条关联的用途。
        row.role = "supporting"
        row.save(update_fields=["role"])

        publication.refresh_from_db()
        with self.assertRaises(library_concepts.AssertionError_):
            library_concepts.undo_assertion(
                {
                    "operation_id": result["operation"],
                    "client_request_id": str(uuid.uuid4()),
                    "expected_revision": publication.tag_revision,
                }
            )
        # 别人的改动还在。
        row.refresh_from_db()
        self.assertEqual(row.role, "supporting")


# ---------------------------------------------------------------------------
# R：整理与候选
# ---------------------------------------------------------------------------


class AssertionWorkflowTests(RefactorTestBase):
    def test_R01_tagging_needs_no_catalogue_setup(self):
        """不用先维护完整目录，回车就能贴。"""
        publication = self.make_publication()
        concept = self.make_concept("抛物线的焦点")
        result = self.attach(publication, concept)
        self.assertEqual(result["created"] and len(result["created"]), 1)

    def test_R02_question_without_solution_gets_no_fabricated_method(self):
        """没有解析的题可以整理考点，但不会凭空长出解法依据。"""
        publication = self.make_publication(analysis="")
        concept = self.make_concept("判别式", kind="method")
        variant = library_concepts.ensure_variant(publication, "解法一")
        self.attach(publication, concept, role="method", variant=variant)

        rows = LibraryTagAssignment.objects.filter(publication=publication)
        self.assertEqual(rows.count(), 1)
        # 没有解析就没有依据记录。
        self.assertEqual(rows.first().evidences.count(), 0)

    def test_R03_ai_result_only_becomes_candidate(self):
        """AI 生成完成只新增候选，正式关联一条不多。"""
        publication = self.make_publication()
        before = LibraryTagAssignment.objects.filter(publication=publication).count()
        LibraryTagSuggestion.objects.create(
            publication=publication,
            status=LibraryTagSuggestion.Status.PENDING,
            suggestions=[{"name": "韦达定理", "kind": "knowledge", "scope": {"whole": True}}],
        )
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=publication).count(), before)

    def test_R09_legacy_assignment_stays_marked_as_legacy(self):
        """旧正式标签没有解法绑定：保留人工历史，标明旧记录，不冒充精确方法。"""
        publication = self.make_publication()
        concept = self.make_concept("二次函数")
        LibraryTagAssignment.objects.create(
            publication=publication,
            tag=concept,
            source=LibraryTagAssignment.Source.LEGACY_HUMAN,
            scope={"whole": True},
            role=LibraryTagAssignment.Role.LEGACY,
            scope_key="whole",
            evidence_status=LibraryTagAssignment.EvidenceStatus.UNBOUND,
        )
        result = library_query.run_query(
            {"method_groups": [{"ids": [str(concept.pk)], "cooccurrence": "same_variant"}]}
        )
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["legacy_count"], 1)


# ---------------------------------------------------------------------------
# S：搜索语义
# ---------------------------------------------------------------------------


class CandidateEditTests(RefactorTestBase):
    """R04：人工改过再采纳 —— 按改后的内容写入，原建议原样留着。"""

    def _pending(self, publication, *, role="primary"):
        from core.models import LibraryTagSuggestion

        catalogue = library_tagging.catalogue_state()
        tag = self.make_concept("候选概念")
        return LibraryTagSuggestion.objects.create(
            publication=publication,
            status=LibraryTagSuggestion.Status.PENDING,
            taxonomy_revision=catalogue.revision,
            assignment_revision=publication.tag_revision,
            suggestions=[
                {
                    "name": "候选概念",
                    "tag_id": str(tag.pk),
                    "kind": "knowledge",
                    "role": role,
                    "scope": {"whole": True},
                    "rationale": "虚构建议的理由。",
                    "evidence": "虚构建议的依据。",
                }
            ],
        )

    def test_accept_writes_the_role_the_teacher_chose(self):
        publication = self.make_publication()
        suggestion = self._pending(publication)
        result = library_tagging.resolve_suggestion(
            suggestion.pk,
            {
                "action": "accept",
                "indices": [0],
                "edits": {"0": {"role": "supporting"}},
                "request_id": str(uuid.uuid4()),
                "expected_revision": publication.tag_revision,
            },
        )
        self.assertTrue(result["ok"])
        row = LibraryTagAssignment.objects.get(publication=publication)
        self.assertEqual(row.role, "supporting")
        self.assertEqual(row.scope_key, "whole")
        self.assertEqual(row.review_status, LibraryTagAssignment.ReviewStatus.CONFIRMED)

    def test_accept_keeps_the_original_suggestion(self):
        """原建议原样留着 —— 采纳后还能看出当初模型是怎么说的。"""
        publication = self.make_publication()
        suggestion = self._pending(publication)
        original = list(suggestion.suggestions)
        library_tagging.resolve_suggestion(
            suggestion.pk,
            {
                "action": "accept",
                "indices": [0],
                "edits": {"0": {"role": "supporting"}},
                "request_id": str(uuid.uuid4()),
                "expected_revision": publication.tag_revision,
            },
        )
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, LibraryTagSuggestion.Status.ACCEPTED)
        self.assertEqual(len(suggestion.suggestions), 1)
        self.assertEqual(suggestion.suggestions[0]["name"], original[0]["name"])
        self.assertEqual(suggestion.suggestions[0]["rationale"], original[0]["rationale"])
        self.assertEqual(suggestion.suggestions[0]["decision"], "accepted")

    def test_accept_writes_the_scope_the_teacher_changed(self):
        """建议说第（2）问，老师改成第（1）问，落库就得是第（1）问。"""
        publication = self.make_publication(stem="（1）求证 AB=CD。\n（2）求证 AB∥CD。")
        subquestions = library_concepts.ensure_subquestions(publication)
        self.assertEqual(len(subquestions), 2)
        suggestion = self._pending(publication)
        library_tagging.resolve_suggestion(
            suggestion.pk,
            {
                "action": "accept",
                "indices": [0],
                "edits": {"0": {"scope": {"subquestions": ["1"]}}},
                "request_id": str(uuid.uuid4()),
                "expected_revision": publication.tag_revision,
            },
        )
        row = LibraryTagAssignment.objects.get(publication=publication)
        self.assertEqual(row.scope_key, f"sub:{subquestions[0].pk}")
        self.assertEqual(row.subquestion_id, subquestions[0].pk)

    def test_accept_adds_a_scoped_row_when_the_tag_is_already_on_the_question(self):
        """题上已经有整题关联了，采纳「用在第（1）问」得另加一条，不是当作没这回事。"""
        from core.models import LibraryTagSuggestion

        publication = self.make_publication(stem="（1）求证 AB=CD。\n（2）求证 AB∥CD。")
        subquestions = library_concepts.ensure_subquestions(publication)
        method = self.make_concept("候选方法", kind="method")
        # 题上已经有这个方法的关联（老师自己加过，落在某个解法分支上）。
        variant = library_concepts.ensure_variant(publication, "解法一")
        self.attach(publication, method, role="method", variant=variant, rationale="这个解法用了这个方法")
        publication.refresh_from_db()
        catalogue = library_tagging.catalogue_state()
        suggestion = LibraryTagSuggestion.objects.create(
            publication=publication,
            status=LibraryTagSuggestion.Status.PENDING,
            taxonomy_revision=catalogue.revision,
            assignment_revision=publication.tag_revision,
            suggestions=[
                {
                    "name": "候选方法",
                    "tag_id": str(method.pk),
                    "kind": "method",
                    "role": "method",
                    "scope": {"subquestions": ["1"]},
                    "rationale": "虚构建议的理由。",
                    "evidence": "虚构建议的依据。",
                }
            ],
        )

        library_tagging.resolve_suggestion(
            suggestion.pk,
            {
                "action": "accept",
                "indices": [0],
                "request_id": str(uuid.uuid4()),
                "expected_revision": publication.tag_revision,
            },
        )

        rows = LibraryTagAssignment.objects.filter(publication=publication, tag=method)
        self.assertEqual(rows.count(), 2, "原来那条留着，第（1）问那条另加")
        scoped = next(row for row in rows if row.scope_key.startswith("sub:"))
        self.assertEqual(scoped.subquestion_id, subquestions[0].pk)

    def test_accept_splits_a_multi_subquestion_suggestion_into_several_rows(self):
        """建议说用在第（1）（2）问，两问各记一条 —— 一条关联只带一个小问。"""
        from core.models import LibraryTagSuggestion

        publication = self.make_publication(stem="（1）求证 AB=CD。\n（2）求证 AB∥CD。")
        subquestions = library_concepts.ensure_subquestions(publication)
        method = self.make_concept("候选方法", kind="method")
        catalogue = library_tagging.catalogue_state()
        suggestion = LibraryTagSuggestion.objects.create(
            publication=publication,
            status=LibraryTagSuggestion.Status.PENDING,
            taxonomy_revision=catalogue.revision,
            assignment_revision=publication.tag_revision,
            suggestions=[
                {
                    "name": "候选方法",
                    "tag_id": str(method.pk),
                    "kind": "method",
                    "role": "method",
                    "scope": {"subquestions": ["1", "2"]},
                    "rationale": "虚构建议的理由。",
                    "evidence": "虚构建议的依据。",
                }
            ],
        )

        library_tagging.resolve_suggestion(
            suggestion.pk,
            {
                "action": "accept",
                "indices": [0],
                "request_id": str(uuid.uuid4()),
                "expected_revision": publication.tag_revision,
            },
        )

        rows = LibraryTagAssignment.objects.filter(publication=publication, tag=method)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(
            {row.subquestion_id for row in rows}, {subquestions[0].pk, subquestions[1].pk}, "两问各记各的，别混成一条"
        )
        self.assertEqual({row.scope_key for row in rows}, {f"sub:{subquestions[0].pk}", f"sub:{subquestions[1].pk}"})

    def test_accept_without_edit_uses_the_suggested_role(self):
        """没改就按建议里的用途写，不能落成「旧关联，作用未细分」。"""
        publication = self.make_publication()
        suggestion = self._pending(publication, role="primary")
        library_tagging.resolve_suggestion(
            suggestion.pk,
            {
                "action": "accept",
                "indices": [0],
                "request_id": str(uuid.uuid4()),
                "expected_revision": publication.tag_revision,
            },
        )
        row = LibraryTagAssignment.objects.get(publication=publication)
        self.assertEqual(row.role, "primary")
        self.assertNotEqual(row.role, LibraryTagAssignment.Role.LEGACY)


class SearchSemanticsTests(RefactorTestBase):
    def test_S01_supporting_does_not_impersonate_primary(self):
        """搜「主要考查」时，辅助用到和题面出现都不能冒充主考。"""
        publication = self.make_publication()
        primary = self.make_concept("导数的几何意义")
        supporting = self.make_concept("切线方程")

        self.attach(publication, primary, role="primary")
        publication.refresh_from_db()
        self.attach(publication, supporting, role="supporting")

        result = library_query.run_query(
            {"concept_groups": [{"ids": [str(primary.pk)], "roles": ["primary"], "all_or_any": "all"}]}
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["confirmed_count"], 1)

        result = library_query.run_query(
            {"concept_groups": [{"ids": [str(supporting.pk)], "roles": ["primary"], "all_or_any": "all"}]}
        )
        self.assertEqual(result["count"], 0)

    def test_S03_methods_in_different_variants_do_not_match_by_default(self):
        """方法 A、B 分属两个解法：默认同解法 AND 不命中；切到「分别存在」才命中。"""
        publication = self.make_publication()
        method_a = self.make_concept("构造中位线", kind="method")
        method_b = self.make_concept("全等三角形", kind="method")
        variant_one = library_concepts.ensure_variant(publication, "解法一")
        variant_two = library_concepts.ensure_variant(publication, "解法二")

        self.attach(publication, method_a, role="method", variant=variant_one)
        publication.refresh_from_db()
        self.attach(publication, method_b, role="method", variant=variant_two)

        same = library_query.run_query(
            {"method_groups": [{"ids": [str(method_a.pk), str(method_b.pk)], "cooccurrence": "same_variant"}]}
        )
        self.assertEqual(same["count"], 0)

        separate = library_query.run_query(
            {"method_groups": [{"ids": [str(method_a.pk), str(method_b.pk)], "cooccurrence": "separate"}]}
        )
        self.assertEqual(separate["count"], 1)

    def test_S05_include_descendants_widens_search_without_copying_tags(self):
        """包含下级只扩展检索范围，不给题目复制父级正式标签。"""
        parent = self.make_concept("三角形")
        child = self.make_concept("等腰三角形")
        library_concepts.relate_concepts(str(parent.pk), str(child.pk), relation_type="hierarchy")

        publication = self.make_publication()
        self.attach(publication, child, role="primary")

        result = library_query.run_query(
            {"concept_groups": [{"ids": [str(parent.pk)], "roles": ["primary"], "include_descendants": True}]}
        )
        self.assertEqual(result["count"], 1)
        # 父概念没有被复制成正式标签。
        self.assertFalse(LibraryTagAssignment.objects.filter(publication=publication, tag=parent).exists())

    def test_S06_exclusion_on_untouched_question_is_unknown(self):
        """排除某方法而题未整理：进未知范围，不当作已证实不用。"""
        publication = self.make_publication()
        method = self.make_concept("反证法", kind="method")

        result = library_query.run_query(
            {
                "method_groups": [{"ids": [str(method.pk)], "cooccurrence": "same_variant"}],
                "exclusions": {
                    "conditions": [{"ids": [str(method.pk)], "roles": ["method"]}],
                    "handling_of_unknown": "separate",
                },
            }
        )
        self.assertEqual(result["count"], 0)

    def test_S04_multi_path_hit_returns_one_question(self):
        """多路径命中同一道题：返回一题，理由都留着，计数不重复。"""
        publication = self.make_publication()
        concept_a = self.make_concept("函数单调性")
        concept_b = self.make_concept("单调区间")
        self.attach(publication, concept_a, role="primary")
        publication.refresh_from_db()
        self.attach(publication, concept_b, role="primary")

        result = library_query.run_query(
            {
                "concept_groups": [
                    {"ids": [str(concept_a.pk)], "roles": ["primary"], "all_or_any": "all"},
                    {"ids": [str(concept_b.pk)], "roles": ["primary"], "all_or_any": "all"},
                ]
            }
        )
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["items"]), 1)

    def test_S08_saved_search_restores_every_condition(self):
        """保存完整筛选：来源、题型、关键词、用途、范围、排序都恢复。"""
        query = {
            "keywords": ["虚构"],
            "filters": {"question_type": "free_response", "source": "虚构卷.pdf"},
            "concept_groups": [{"ids": ["x"], "roles": ["primary"], "all_or_any": "all"}],
            "method_groups": [{"ids": ["y"], "cooccurrence": "same_variant"}],
            "exclusions": {"conditions": [], "handling_of_unknown": "separate"},
            "include_clues": True,
            "sort": "number",
        }
        saved = library_query.save_search("我的找题", query)
        self.assertTrue(saved["ok"])

        restored = [item for item in library_query.list_saved_searches() if item["name"] == "我的找题"][0]
        stored = restored["query"]
        self.assertEqual(stored["keywords"], ["虚构"])
        self.assertEqual(stored["filters"]["question_type"], "free_response")
        self.assertEqual(stored["filters"]["source"], "虚构卷.pdf")
        self.assertEqual(stored["concept_groups"][0]["roles"], ["primary"])
        self.assertEqual(stored["method_groups"][0]["cooccurrence"], "same_variant")
        self.assertEqual(stored["sort"], "number")

    def test_S10_search_works_without_any_model(self):
        """没有 API key 也能找题：结构检索照常。"""
        publication = self.make_publication()
        concept = self.make_concept("圆的切线")
        self.attach(publication, concept, role="primary")
        result = library_query.run_query({"concept_groups": [{"ids": [str(concept.pk)], "roles": ["primary"]}]})
        self.assertEqual(result["count"], 1)

    def test_S11_natural_language_is_converted_but_not_guessed(self):
        """中文条件转成看得见、可改的条件；认不出来的留在关键词里。"""
        from core import library_nlquery

        concept = self.make_concept("构造中位线", kind="method")
        conversion = library_nlquery.convert("第（1）问用构造中位线 解法一 立体几何")

        query = conversion.query
        self.assertEqual(query["method_groups"][0]["ids"], [str(concept.pk)])
        self.assertEqual(query["method_groups"][0]["subquestion"], "1")
        self.assertTrue(query["keywords"])
        # 转换结果带解释，老师看得懂这句话被理解成了什么。
        self.assertTrue(conversion.notes)
        self.assertIn("立体几何", query["keywords"])

    def test_S11_unrecognised_text_stays_as_keywords(self):
        """完全认不出的句子不猜成正式条件，全部退回关键词。"""
        from core import library_nlquery

        conversion = library_nlquery.convert("给我找那道特别难喝的柠檬茶题")
        self.assertEqual(conversion.query["concept_groups"], [])
        self.assertEqual(conversion.query["method_groups"], [])
        self.assertTrue(conversion.query["keywords"])

    def test_S11_display_number_finds_the_subquestion(self):
        """用展示编号「第（1）问」也能定位到那个小问 —— 库里存的是 UUID，不是「1」。"""
        publication = self.make_publication(stem="（1）求证 AB=CD。\n（2）求证 AB∥CD。")
        subquestions = library_concepts.ensure_subquestions(publication)
        method = self.make_concept("配方法", kind="method")
        variant = library_concepts.ensure_variant(publication, "解法一")
        self.attach(publication, method, role="method", subquestion=subquestions[0], variant=variant)

        result = library_query.run_query(
            {"method_groups": [{"ids": [str(method.pk)], "subquestion": "1", "cooccurrence": "same_variant"}]}
        )
        self.assertEqual(result["count"], 1)
        # 用稳定键也认。
        by_key = library_query.run_query(
            {
                "method_groups": [
                    {"ids": [str(method.pk)], "subquestion": subquestions[0].stable_key, "cooccurrence": "same_variant"}
                ]
            }
        )
        self.assertEqual(by_key["count"], 1)

    def test_empty_query_reports_total_without_inventing_buckets(self):
        """空查询只报「一共多少题」—— 没逐题查过的不能报成「已确认匹配」。"""
        self.make_publication()
        result = library_query.run_query({"query": {}})
        self.assertEqual(result["count"], 1)
        self.assertIsNone(result["confirmed_count"])
        self.assertIsNone(result["legacy_count"])
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(len(result["items"]), 1)

    def test_S12_unmatched_question_is_not_reported_as_unreviewed(self):
        """不满足条件的题是不匹配，不是「未整理」—— 两个混在一起，计数就没意义了。"""
        publication = self.make_publication()
        concept = self.make_concept("圆的切线")
        self.attach(publication, concept, role="primary")

        other = self.make_concept("抛物线的焦点")
        result = library_query.run_query({"concept_groups": [{"ids": [str(other.pk)], "roles": ["primary"]}]})
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["unknown_count"], 0)


# ---------------------------------------------------------------------------
# C：教材目录与专题
# ---------------------------------------------------------------------------


class CatalogueAndCollectionTests(RefactorTestBase):
    def test_C01_question_in_two_collections_keeps_one_paper(self):
        """题属于两个专题：题面不复制，移出专题不删题。"""
        publication = self.make_publication()
        first = LibraryCollection.objects.create(name="周五小测")
        second = LibraryCollection.objects.create(name="易漏解")

        library_concepts.add_to_collection(str(first.pk), str(publication.pk))
        library_concepts.add_to_collection(str(second.pk), str(publication.pk))

        self.assertEqual(LibraryCollectionMember.objects.filter(publication=publication).count(), 2)
        self.assertEqual(PublishedQuestion.objects.filter(pk=publication.pk).count(), 1)

        library_concepts.remove_from_collection(str(first.pk), str(publication.pk))
        self.assertEqual(LibraryCollectionMember.objects.filter(publication=publication).count(), 1)
        self.assertTrue(PublishedQuestion.objects.filter(pk=publication.pk).exists())

    def test_C02_chapters_keep_their_own_tree(self):
        """同一概念出现在两套教材：概念共享，教材目录各自保留。"""
        concept = self.make_concept("一元二次方程")
        first_textbook = library_concepts.ensure_textbook("甲版", stage="初中")
        second_textbook = library_concepts.ensure_textbook("乙版", stage="初中")
        first_chapter = library_concepts.ensure_chapter("第十六章 二次方程", textbook_id=str(first_textbook.pk))
        second_chapter = library_concepts.ensure_chapter("第 12 章 二次方程", textbook_id=str(second_textbook.pk))

        for chapter in (first_chapter, second_chapter):
            LibraryChapterConcept.objects.create(chapter=chapter, tag=concept, confirmed=True)

        self.assertEqual(LibraryChapterConcept.objects.filter(tag=concept).count(), 2)
        self.assertEqual(LibraryChapter.objects.count(), 2)

    def test_C03_chapter_node_keys_are_stable_and_unique(self):
        """章节的规范化键不空 —— SQLite 的 NULL 不参与唯一约束。"""
        textbook = library_concepts.ensure_textbook("甲版", stage="高中")
        first = library_concepts.ensure_chapter("函数", textbook_id=str(textbook.pk))
        again = library_concepts.ensure_chapter("函数", textbook_id=str(textbook.pk))
        self.assertEqual(first.pk, again.pk)
        self.assertTrue(str(first.node_key).strip())

        child = library_concepts.ensure_chapter("单调性", textbook_id=str(textbook.pk), parent_id=str(first.pk))
        self.assertEqual(child.parent_id, first.pk)
        self.assertIn(str(first.pk), child.node_key)


# ---------------------------------------------------------------------------
# M：迁移
# ---------------------------------------------------------------------------


class MigrationTests(RefactorTestBase):
    def _legacy_fixture(self):
        """造一套旧结构：有章节容器、有正式关联、有旧 AI 候选、有一条重名。"""
        section = LibraryTag.objects.create(
            name="初中 · 函数",
            kind="knowledge",
            system_key="section:seed-group:3",
            description="教材章节，用来给标签目录分层。",
        )
        child = LibraryTag.objects.create(name="函数单调性", kind="knowledge", system_key="knowledge:函数单调性")
        LibraryTagRelation.objects.create(parent=section, child=child, relation_type=LibraryTagRelation.Type.HIERARCHY)
        formal = LibraryTag.objects.create(name="三角函数", kind="knowledge")
        LibraryTagAssignment.objects.create(
            publication=self.make_publication(),
            tag=formal,
            source=LibraryTagAssignment.Source.LEGACY_HUMAN,
            scope={"whole": True},
            scope_key="whole",
            role=LibraryTagAssignment.Role.LEGACY,
            evidence_status=LibraryTagAssignment.EvidenceStatus.UNBOUND,
        )
        legacy_dup = LibraryTag.objects.create(name="三角函数", kind="legacy")
        publication = self.make_publication()
        LibraryTagSuggestion.objects.create(
            publication=publication,
            status=LibraryTagSuggestion.Status.PENDING,
            suggestions=[{"name": "三角函数", "kind": "knowledge", "scope": {"whole": True}}],
        )
        LibraryTagSuggestion.objects.create(
            publication=publication, status=LibraryTagSuggestion.Status.QUEUED, suggestions=[]
        )
        return section, formal, legacy_dup, publication

    def test_M01_dry_run_changes_nothing(self):
        """dry-run 不改数据库内容，只输出影响与歧义。"""
        self._legacy_fixture()
        before_tags = list(LibraryTag.objects.values_list("pk", "canonical_key"))
        before_assignments = LibraryTagAssignment.objects.count()

        plan = library_tag_migration.plan_migration()

        self.assertEqual(list(LibraryTag.objects.values_list("pk", "canonical_key")), before_tags)
        self.assertEqual(LibraryTagAssignment.objects.count(), before_assignments)
        self.assertIn("counts", plan)
        self.assertTrue(plan["counts"]["tags"] >= 4)

    def test_M02_status_is_not_upgraded_by_migration(self):
        """迁移不提升任何状态：旧关联用途仍是「旧关联，作用未细分」。"""
        self._legacy_fixture()
        library_tag_migration.apply_migration("test-batch")
        for row in LibraryTagAssignment.objects.all():
            self.assertEqual(row.role, LibraryTagAssignment.Role.LEGACY)
            self.assertEqual(row.evidence_status, LibraryTagAssignment.EvidenceStatus.UNBOUND)

    def test_M03_repeated_migration_creates_nothing_new(self):
        """重复执行迁移：不重复建实体，不重复贴标。"""
        self._legacy_fixture()
        library_tag_migration.apply_migration("test-batch")
        tags_after_first = LibraryTag.objects.count()
        chapters_after_first = LibraryChapter.objects.count()
        entries_after_first = LibraryMigrationMap.objects.filter(batch="test-batch").count()

        library_tag_migration.apply_migration("test-batch")

        self.assertEqual(LibraryTag.objects.count(), tags_after_first)
        self.assertEqual(LibraryChapter.objects.count(), chapters_after_first)
        self.assertEqual(LibraryMigrationMap.objects.filter(batch="test-batch").count(), entries_after_first)

    def test_M04_verify_passes_after_apply(self):
        """迁移后核对要通过：一条关联没丢，一条候选没少。"""
        self._legacy_fixture()
        library_tag_migration.apply_migration("test-batch")
        result = library_tag_migration.verify_migration("test-batch")
        self.assertTrue(result["ok"], result["problems"])

    def test_M05_legacy_duplicate_is_merged_but_keeps_old_id(self):
        """历史标签和正式概念同名：归并到正式概念，旧 ID 保留做重定向。"""
        _, formal, legacy_dup, _ = self._legacy_fixture()
        library_tag_migration.apply_migration("test-batch")
        legacy_dup.refresh_from_db()
        formal.refresh_from_db()
        self.assertEqual(legacy_dup.merged_into_id, formal.pk)
        self.assertTrue(LibraryTag.objects.filter(pk=legacy_dup.pk).exists())

    def test_M06_sections_become_chapters_without_inventing_textbook(self):
        """旧章节不知道教材版次：落到通用教学目录，不编造版本。"""
        self._legacy_fixture()
        library_tag_migration.apply_migration("test-batch")
        chapters = list(LibraryChapter.objects.all())
        self.assertTrue(chapters)
        for chapter in chapters:
            self.assertTrue(chapter.textbook.unspecified)
            self.assertEqual(chapter.textbook.name, "通用教学目录")

    def test_M07_every_old_suggestion_is_accounted_for(self):
        """每条旧候选都要在迁移报告里查到处理结果，一条都不能静默丢掉。"""
        self._legacy_fixture()
        library_tag_migration.apply_migration("test-batch")
        entries = LibraryMigrationMap.objects.filter(batch="test-batch", old_kind="suggestion")
        self.assertEqual(entries.count(), LibraryTagSuggestion.objects.count())
