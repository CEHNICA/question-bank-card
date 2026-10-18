"""「更多筛选」里一张卡片出不出现，看的必须是题库的事实，不是当前筛选下的数字。

老师发来截图：「这个界面啊不够稳定啊，我选择了两三个标签之后，这个界面就变了，
格局就变了。」截图上题型、谁审核的、有没有答案的数字全变成 0，而「谁审核的」那张
卡片是凭空冒出来的 —— 它把下面所有卡片往下顶了两行。

原因有两层，缺一不可：
  · facet_counts 每一维都是「换到这个维度后还剩多少题」，所以筛选一变数字就变；
  · 界面用 emptied 兜底「别把这里本来还有什么一起收走」，于是筛到 0 时把
    本来在题库里就不存在的那一维也顶了出来。

所以 presence 必须是题库范围内的事实：有没有 AI 审核入库的题、有没有缺答案的题、
有没有打上正式标签的题。改筛选动不了它。
"""

import json

from django.test import TransactionTestCase

from . import library, library_browse
from .models import LibraryTag, LibraryTagAssignment, PublishedQuestion
from .test_v110_types_origin import TempDataMixin


class PanelStabilityTests(TempDataMixin, TransactionTestCase):
    def setUp(self):
        self.use_temp_data()

    def publication(self, number, *, answer="", review_source="human"):
        content = {"question_type": "free_response", "stem": f"第 {number} 题。", "options": {},
                   "figures": [], "sources": [], "answer": answer, "analysis": "", "source_filename": "panel.pdf"}
        return PublishedQuestion.objects.create(
            source_filename="panel.pdf", number=number, question_type="free_response", version=1,
            content=content, content_hash=library.content_hash(content), extras={},
            review_source=review_source)

    def listing(self, **params):
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return json.loads(self.client.get(f"/api/library?{query}").content.decode("utf-8"))

    def test_presence_ignores_the_current_filter(self):
        """筛到 0 道题以后，presence 必须和没筛时一模一样。"""
        kept = self.publication(1, answer="42")
        dropped = self.publication(2, review_source="ai")
        missing_answer = self.publication(3)
        tag = LibraryTag.objects.create(name="统计量", kind="knowledge", levels=["高中"])
        LibraryTagAssignment.objects.create(publication=kept, tag=tag, source="human", needs_review=False)

        wide = self.listing()["presence"]
        # 三道题：kept 有答案，dropped 是 AI 审核的、没答案，missing_answer 也没答案。
        self.assertEqual(wide, {"review_ai": 1, "answer_missing": 2, "tags": 1})

        # 换成一道也不匹配的题型：当前结果 0 道，三张卡片一个都不该凭空出现或消失。
        narrow = self.listing(type="single_choice")["presence"]
        self.assertEqual(narrow, wide,
                         "筛到 0 道题之后 presence 变了，面板就会重排 —— 老师的原话是"
                         "「我选择了两三个标签之后，这个界面就变了」")

        # 用了题库里根本不存在的题型 id，也不能影响。
        self.assertEqual(self.listing(document="not-a-uuid")["presence"], wide)

    def test_presence_stays_zero_for_a_library_that_has_never_had_these(self):
        """题库确实没有 AI 审核的题时，卡片就该一直不出现 —— 任何筛选下都一样。"""
        self.publication(1, answer="x")
        self.publication(2, answer="y")
        presence = self.listing()["presence"]
        self.assertEqual(presence["review_ai"], 0, "库里有两道人工核对的题，但一道 AI 审核的都没有")
        self.assertEqual(presence["answer_missing"], 0, "两道题都有原卷答案")
        self.assertEqual(presence["tags"], 0, "一道题都没打标签")

    def test_the_listing_carries_presence_next_to_the_filtered_facets(self):
        """两张数字不是一回事：facets 是当前筛选下的，presence 是题库的事实。"""
        self.publication(1, answer="42")
        self.publication(2)
        body = self.listing()
        self.assertIn("presence", body)
        # 当前没筛，所以两者的答案这一维恰好一致；有筛选时才可以分开。
        self.assertEqual(body["presence"]["answer_missing"], body["facets"]["answers"]["no"])

        filtered = self.listing(review="human")
        self.assertEqual(filtered["facets"]["answers"]["no"], 1)
        self.assertEqual(filtered["presence"]["answer_missing"], 1)
