"""Exercise optional hierarchy blueprints and AI context, offline only."""
from django.test import TestCase

from core import library_jobs, library_tagging
from core.models import LibraryTag, LibraryTagRelation
from core.tag_catalogue_seed import seed_catalogue, template_blueprint


class HierarchyTemplateTests(TestCase):
    def test_blueprint_is_complete_acyclic_and_read_only(self):
        blueprint = template_blueprint()
        self.assertEqual(LibraryTag.objects.count(), 0)
        tags = {tag["key"]: tag for tag in blueprint["tags"]}
        self.assertEqual(len(tags), len(blueprint["tags"]))
        visited, active = set(), set()

        def visit(identity):
            self.assertNotIn(identity, active, "Template cannot contain a cycle")
            if identity in visited:
                return
            active.add(identity)
            row = tags[identity]
            for parent in row["parent_keys"]:
                self.assertIn(parent, tags)
                self.assertEqual(row["kind"], tags[parent]["kind"])
                visit(parent)
            for method in row["related_method_keys"]:
                self.assertIn(method, tags)
                self.assertEqual(row["kind"], "knowledge")
                self.assertEqual(tags[method]["kind"], "method")
            active.remove(identity)
            visited.add(identity)

        for identity in tags:
            visit(identity)
        self.assertGreater(len(visited), 250)
        square = next(tag for tag in tags.values() if tag["name"] == "正方形")
        self.assertEqual({tags[p]["name"] for p in square["parent_keys"]}, {"矩形", "菱形"})
        self.assertEqual(len([t for t in tags.values() if t["name"] == "二次函数"]), 1)
        self.assertTrue(any(t["name"] == "二次函数在闭区间上的最值" for t in tags.values()))
        # Reading the blueprint writes nothing; the live seed builds the tree.
        self.assertEqual(LibraryTag.objects.count(), 0)
        self.assertEqual(LibraryTagRelation.objects.count(), 0)
        seed_catalogue()
        chapters = LibraryTag.objects.filter(description="教材章节，用来给标签目录分层。")
        self.assertGreater(chapters.count(), 10)
        self.assertTrue(LibraryTagRelation.objects.exists())
        self.assertTrue(LibraryTag.objects.filter(name="二次函数在闭区间上的最值").exists())
        # Re-seeding the same blueprint must not duplicate a single row.
        before = (LibraryTag.objects.count(), LibraryTagRelation.objects.count())
        seed_catalogue()
        self.assertEqual((LibraryTag.objects.count(), LibraryTagRelation.objects.count()), before)

    def test_a_point_taught_in_both_stages_reaches_both_chapters(self):
        """A concept written under one chapter is still taught under another.

        The point keeps the concept it belongs to, and additionally gets an
        edge to the chapter its concept chain does not already reach. Without
        that edge the 初中 copy of the point disappears from the 初中 chapter,
        and a teacher browsing that chapter would not find it.
        """
        seed_catalogue()
        parents = {}
        for edge in LibraryTagRelation.objects.filter(relation_type=LibraryTagRelation.Type.HIERARCHY):
            parents.setdefault(str(edge.child_id), []).append(str(edge.parent_id))
        names = {str(tag.pk): tag.name for tag in LibraryTag.objects.all()}

        def chapters_above(tag_id, seen=frozenset()):
            if tag_id in seen:
                return set()
            above = parents.get(tag_id, [])
            if not above:
                return {names[tag_id]}
            return set().union(*(chapters_above(parent, seen | {tag_id}) for parent in above))

        for name in ("函数图象", "平面直角坐标系", "统计图表"):
            point = LibraryTag.objects.get(name=name)
            above = chapters_above(str(point.pk))
            self.assertTrue(any(item.startswith("初中 · ") for item in above), (name, above))
            self.assertTrue(any(item.startswith("高中 · ") for item in above), (name, above))

    def test_generation_snapshot_survives_later_graph_and_name_edits(self):
        library_tagging.catalogue_state()
        parent = LibraryTag.objects.create(name="虚构父概念", kind="knowledge")
        child = LibraryTag.objects.create(name="虚构具体概念", kind="knowledge")
        relation = LibraryTagRelation.objects.create(parent=parent, child=child)
        snapshot = library_jobs.tag_catalogue_snapshot([parent, child])
        prompt = library_jobs.tag_proposal_prompt({"stem": "虚构测试题"}, [parent, child], set(), None)
        self.assertIn(str(parent.pk), prompt)
        self.assertIn("parent_ids", prompt)
        self.assertIn("任何层级", prompt)
        self.assertIn("不要生成解题方法标签", prompt)
        relation.delete()
        child.name = "修改后的概念"
        child.save(update_fields=["name"])
        saved = next(t for t in snapshot["tags"] if t["id"] == str(child.pk))
        self.assertEqual(saved["name"], "虚构具体概念")
        self.assertEqual(saved["parent_ids"], [str(parent.pk)])
