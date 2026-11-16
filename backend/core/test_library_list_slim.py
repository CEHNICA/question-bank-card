"""题库列表瘦身：首屏拉全库，但别把详情页的胖字段一起拖回来。

背景（8768 服务在真库上实测，810 道题）：

    GET /api/library?bulk=1&limit=1000&offset=0
      明文 1.90 MB / gzip 338 KB / 稳态 320~376 ms

拆开看每一道题的行载荷（810 道合计 1.44 MB）：

    content.review         372,577 B  26%   每题的配图核验快照
    ai_answer              207,412 B  14%
    tag_assignments        134,956 B   9%   标签对象带 description/aliases/
                                       levels/parent_ids… 外加 AI 的
                                       rationale/evidence/generation_source
    content.source_group    36,863 B   3%

其中 content.review 和 content.source_group，题库工作台的列表**一个字节都不读**
（前端从来没读过这两个字段）；tag_assignments 里列表的标签芯片只要
id / name / kind / color / source / needs_review，其余全是详情页才要的。

`?slim=1` 把这些丢掉。口径要求：
  - 列表用得上的字段一个不少；
  - 详情页那条接口（/api/library/<id>）走完整载荷，不受影响；
  - 瘦身不许把模型上的 content 就地掏空。

这些测试盯的是**根因**，不是耗时（耗时会受机器影响）。
"""

from __future__ import annotations

import json

from django.test import Client, TestCase

from core.models import (
    LibraryTag,
    LibraryTagAssignment,
    Paper,
    PublishedQuestion,
    Question,
)


def _dumps(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


class SlimLibraryListTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.paper = Paper.objects.create(filename="胖卷.pdf", kind="pdf", sha256="e" * 64)
        question = Question.objects.create(
            paper=self.paper, number=1, question_type="single_choice", stem="题干", options={}
        )
        # 配图核验快照：列表不读，详情页要。810 道题合计 37 万字节。
        self.publication = PublishedQuestion.objects.create(
            question=question,
            paper=self.paper,
            source_filename="胖卷.pdf",
            number=1,
            question_type="single_choice",
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
            review_source="human",
            content={
                "stem": "题干 $x+1=2$，求 $x$。",
                "options": {"A": "1", "B": "2"},
                "answer": "B",
                "analysis": "因为 $x=1$。",
                "figures": [],
                "sources": [{"page_idx": 0, "bbox": [1, 2, 3, 4], "type": "region"}],
                "section": "第一章",
                "origin": "虚构出处",
                "source_filename": "胖卷.pdf",
                "question_type": "single_choice",
                "review": {
                    "figure_review": {
                        "status": "confirmed",
                        "source": "human",
                        "items": [
                            {"page_idx": index, "bbox": [10, 20, 300, 400], "type": "crop", "sha256": "a" * 64}
                            for index in range(8)
                        ],
                    }
                },
                "source_group": {"title": "胖资料组", "path": "a/b/c", "members": ["x", "y", "z"]},
            },
            content_hash="f" * 64,
            search_text="",
            extras={"ai_answer": {"answer": "B", "analysis": "因为 $x=1$。", "engine": "虚构模型"}},
            tags_text="",
        )
        self.tag = LibraryTag.objects.create(
            name="勾股定理",
            kind=LibraryTag.Kind.KNOWLEDGE,
            description="用直角边和斜边的平方关系计算或证明。" * 4,
            aliases=["勾股", "毕达哥拉斯", "勾股定理", "勾股定理式"],
            levels=["初中", "中考"],
            conditions="直角三角形" * 4,
        )
        LibraryTagAssignment.objects.create(
            publication=self.publication,
            tag=self.tag,
            scope={"whole": True},
            source=LibraryTagAssignment.Source.AI_ACCEPTED,
            generation_source="虚构模型 · 某版本 · 某批次",
            rationale="利用矩形中的直角三角形及勾股定理计算线段长度，并建立关键直角关系。" * 2,
            evidence="解析中“BE=√(AE²+AB²)=√3”，并使用勾股定理处理直角三角形。" * 2,
        )

    def get(self, query):
        response = self.client.get(query)
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content.decode("utf-8"))

    def item(self, query):
        payload = self.get(query)
        self.assertEqual(len(payload["items"]), 1)
        return payload["items"][0]

    # ---------- 瘦身模式丢什么 ----------

    def test_slim_drops_the_content_fields_the_list_never_reads(self):
        slim = self.item("/api/library?bulk=1&limit=1000&slim=1")
        content = slim["content"]
        self.assertNotIn("review", content, "配图核验快照列表不读（810 道题 37 万字节）")
        self.assertNotIn("source_group", content, "资料组信息列表不读")

    def test_slim_trims_the_tag_chip_to_what_the_list_reads(self):
        slim = self.item("/api/library?bulk=1&limit=1000&slim=1")
        assignments = slim["tag_assignments"]
        self.assertEqual(len(assignments), 1)
        row = assignments[0]
        self.assertEqual(
            set(row["tag"]), {"id", "name", "kind", "color"}, "标签芯片只认这四样"
        )
        self.assertEqual(row["tag"]["name"], "勾股定理")
        self.assertEqual(set(row), {"tag", "source", "needs_review"}, "AI 那几摊理由列表不读")

    # ---------- 瘦身模式留什么 ----------

    def test_slim_keeps_everything_the_list_actually_reads(self):
        slim = self.item("/api/library?bulk=1&limit=1000&slim=1")
        content = slim["content"]
        for needed in ("stem", "options", "answer", "analysis", "figures", "sources", "section", "origin"):
            self.assertIn(needed, content, f"列表要 {needed}")
        self.assertEqual(content["stem"], "题干 $x+1=2$，求 $x$。")
        self.assertEqual(content["options"], {"A": "1", "B": "2"})
        self.assertEqual(content["sources"][0]["page_idx"], 0, "页码定位靠 sources")
        for needed in ("id", "number", "question_type", "status", "published_at", "tags", "ai_answer"):
            self.assertIn(needed, slim, f"列表要 {needed}")
        self.assertEqual(slim["tags"], ["勾股定理"], "标签名要在")
        self.assertEqual(slim["ai_answer"]["answer"], "B", "AI 答案要在")
        self.assertIn("tag_revision", slim)
        self.assertIn("tag_suggestion", slim)

    # ---------- 不许影响别人 ----------

    def test_the_full_payload_is_untouched(self):
        """不加 slim 的老调用方（试题篮等）拿到的还是原来那一份。"""
        full = self.item("/api/library?bulk=1&limit=1000")
        self.assertIn("review", full["content"], "完整模式要带配图核验快照")
        self.assertIn("source_group", full["content"])
        self.assertIn("description", full["tag_assignments"][0]["tag"])
        self.assertIn("rationale", full["tag_assignments"][0])

    def test_slim_is_opt_in_and_only_the_exact_flag_triggers_it(self):
        """slim=0 / true / 空串都不算 slim —— 免得谁手一抖把详情字段丢了。"""
        for query in (
            "/api/library?bulk=1&limit=1000",
            "/api/library?bulk=1&limit=1000&slim=0",
            "/api/library?bulk=1&limit=1000&slim=true",
            "/api/library?bulk=1&limit=1000&slim=",
        ):
            full = self.item(query)
            self.assertIn("review", full["content"], f"{query} 不该被当成 slim")

    def test_slim_never_empties_the_stored_content(self):
        """content 是模型上那一份 dict。就地 del 会把这个实例掏空。"""
        self.item("/api/library?bulk=1&limit=1000&slim=1")
        self.publication.refresh_from_db()
        self.assertIn("review", self.publication.content, "库里的内容不许被瘦身改掉")
        self.assertIn("source_group", self.publication.content)

    def test_slim_works_without_bulk_too(self):
        """分页模式也可以瘦身。"""
        slim = self.item("/api/library?limit=40&slim=1")
        self.assertNotIn("review", slim["content"])

    def test_slim_payload_is_smaller_than_the_full_one(self):
        full_len = _dumps(self.get("/api/library?bulk=1&limit=1000"))
        slim_len = _dumps(self.get("/api/library?bulk=1&limit=1000&slim=1"))
        # 真库 810 道题：明文 1.44 MB → 瘦身掉 content.review、content.source_group
        # 和标签上的 AI 理由，大约省掉三分之一。
        self.assertLess(
            slim_len,
            full_len * 0.75,
            f"slim 模式的意义就是别把胖字段传出去（{full_len} → {slim_len}）",
        )
