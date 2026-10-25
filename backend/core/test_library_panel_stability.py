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
        content = {
            "question_type": "free_response",
            "stem": f"第 {number} 题。",
            "options": {},
            "figures": [],
            "sources": [],
            "answer": answer,
            "analysis": "",
            "source_filename": "panel.pdf",
        }
        return PublishedQuestion.objects.create(
            source_filename="panel.pdf",
            number=number,
            question_type="free_response",
            version=1,
            content=content,
            content_hash=library.content_hash(content),
            extras={},
            review_source=review_source,
        )

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
        self.assertEqual(
            narrow,
            wide,
            "筛到 0 道题之后 presence 变了，面板就会重排 —— 老师的原话是「我选择了两三个标签之后，这个界面就变了」",
        )

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

    def test_selected_tags_keep_their_names_when_the_result_is_empty(self):
        """筛到 0 道题时，可选列表空了，但手上那几个标签还得显示名字。

        老师看得见、点得掉，才算数。以前这里显示的是数据库内部编号前 8 位
        （「已选标签 · 531aebeb6」）—— 那是给程序看的，不是给老师看的。
        """
        kept = self.publication(1, answer="42")
        other = self.publication(2)
        geometry = LibraryTag.objects.create(name="平行四边形", kind="knowledge", levels=["初中"])
        quadratic = LibraryTag.objects.create(name="一元二次方程", kind="knowledge", levels=["初中"])
        LibraryTagAssignment.objects.create(publication=kept, tag=geometry, source="human", needs_review=False)
        LibraryTagAssignment.objects.create(publication=other, tag=quadratic, source="human", needs_review=False)

        # 两个标签同时选，本来就凑不到一起 —— 也就是老师截图里那个 0 的状态。
        both = self.listing(tag_all=f"{geometry.pk},{quadratic.pk}")["selected_tag_names"]
        self.assertEqual(both, {str(geometry.pk): "平行四边形", str(quadratic.pk): "一元二次方程"})

        # 名字不受筛选影响：换个筛法，选中的标签还是那几个名字。
        narrowed = self.listing(tag_all=f"{geometry.pk},{quadratic.pk}", type="single_choice")
        self.assertEqual(narrowed["total"], 0, "这一组本来就该是 0 题，否则这个用例没在测那个状态")
        self.assertEqual(narrowed["selected_tag_names"], both)

        # 没选标签时不该凭空造出一堆名字。
        self.assertEqual(self.listing()["selected_tag_names"], {})

    def test_a_selected_tag_that_no_longer_exists_is_labelled_as_deleted(self):
        """标签被删掉后还挂在筛选上，得说清是「已被删除」，不能拿编号冒充名字。"""
        self.publication(1)
        gone = LibraryTag.objects.create(name="临时标签", kind="custom", levels=["初中"])
        tag_id = str(gone.pk)
        gone.delete()
        names = self.listing(tag_all=tag_id)["selected_tag_names"]
        self.assertEqual(names, {}, "查不到名字就是查不到，前端据此显示「标签已被删除」")

    def test_filtering_by_tag_name_still_works(self):
        """`tag` 是按名字筛的老写法，不是编号。

        查名字那一步要是把这个值也塞进编号列表里，整个列表页会直接 500 ——
        「集合间的基本关系」不是个能当编号用的字符串。这条把它钉住。
        """
        kept = self.publication(1, answer="42")
        other = self.publication(2)
        geometry = LibraryTag.objects.create(name="平行四边形", kind="knowledge", levels=["初中"])
        LibraryTagAssignment.objects.create(publication=kept, tag=geometry, source="human", needs_review=False)
        LibraryTagAssignment.objects.create(
            publication=other,
            tag=LibraryTag.objects.create(name="二次函数", kind="knowledge", levels=["初中"]),
            source="human",
            needs_review=False,
        )

        body = self.listing(tag="平行四边形")
        self.assertIn("total", body)
        self.assertEqual(body["selected_tag_names"], {}, "按名字筛的老写法没有编号要查名字，也不该因此报错")

        # 名字里带非 UUID 的字符、空格、逗号，一个都不能把这一页打成 500。
        for value in ["集合间的基本关系", "a,b", "  ", "平行四边形,二次函数", "12345"]:
            with self.subTest(tag=value):
                self.assertIn("selected_tag_names", self.listing(tag=value))
