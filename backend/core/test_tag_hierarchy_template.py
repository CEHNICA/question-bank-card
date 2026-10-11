"""Exercise optional hierarchy blueprints and AI context, offline only."""
from django.test import TestCase

from core import library_jobs, library_tagging
from core.models import LibraryTag, LibraryTagRelation
from core.tag_catalogue_seed import template_blueprint, seed_catalogue


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
        # The legacy migration seed stays frozen in meaning: no graph writes.
        seed_catalogue()
        self.assertEqual(LibraryTagRelation.objects.count(), 0)
        self.assertFalse(LibraryTag.objects.filter(name="二次函数在闭区间上的最值").exists())

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
