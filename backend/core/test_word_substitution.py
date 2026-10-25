"""词被读成形近词时，差异检测只会报一半，漏掉的那一半还查不出来。

202510 胜利初四月考第 13 题：原卷印的是「若**树根**到墙角的距离」，MinerU 也读对了，
识读写成「若**根据**墙角的距离」——句子还通顺，卡是绿的，零旗标。

原因不在读，在比对：两边共享一个「根」字，``SequenceMatcher`` 于是把一处错拆成
「插入一个树」+「据→到」两段；插入段根本不看，替换段因为左右上下文各差一个字
（「图.若根」对「图.若树」）被当作对不上而丢掉。两半都够不着。

同类还有两处，都看过原图：微信图片卷第 17 题把印刷的「定制」读成「制定」；
991e3941 那份第 22 题把印刷的 ``FE=8`` 读成 ``EF=8``。把只隔一个相同字的两段差异
合成一段再判，三处都抓得到；隔两个字以上的不合并。

    .\\backend\\.venv\\Scripts\\python.exe backend\\manage.py test core.test_word_substitution
"""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from core import pipeline, readers, textnorm
from core.models import Question

# 真卷原样：识读把「树根」读成了「根据」，末尾 ____ 是填空横线（纸上被学生写了 40）。
READING_STEM = (
    "已知在阳光下，垂直于地面高1米的标杆的影长也为1米，同一时刻，树$AB$的影子投射在"
    "墙上的影高$CD$等于2米，如图．若根据墙角的距离$BC$等于8米，$AB\\perp\\overline{BC}$，"
    "$CD\\perp BC$，则树高$AB$等于____米．"
)
WITNESS = (
    "13. 已知在阳光下，垂直于地面高 1 米的标杆的影长也为 1 米，同一时刻，树 AB 的影子投射在墙上的"
    "影高 CD 等于 2 米，如图。若树根到墙角的距离 BC 等于 8 米， $AB \\perp BC$ ， $CD \\perp BC$ ，"
    "则树高 AB 等于 40 米。"
)


def spots(stem, witness):
    return textnorm.witness_objections({"stem": stem, "unclear": False}, witness)


class WordSubstitutionTests(SimpleTestCase):
    def test_the_real_word_substitution_is_caught(self):
        found = spots(READING_STEM, WITNESS)
        self.assertEqual([(s["reading"], s["mineru"]) for s in found], [("根据", "树根到")], found)

    def test_the_answers_written_into_the_blank_are_not_a_difference(self):
        # 纸上 ____ 处被学生写上了 40：那是纸上的字，不是识读错的地方。
        self.assertTrue(all(s["mineru"] != "40" for s in spots(READING_STEM, WITNESS)))

    def test_two_more_real_cards_are_caught(self):
        for stem, witness, pair in (
            (
                "为了使这批削笔器的日利润最大，应怎样制定这批削笔器的销售价格？",
                "（1）为了使这批削笔器的日利润最大，应怎样定制这批削笔器的销售价格？",
                ("制定", "定制"),
            ),
            (
                "若 $AD\\perp BD$ ， $AB=5$ ， $BC=3$ ， $EF=8$ ，求点 $D$ 到 $AF$ 的距离．",
                "（2）若 AD ⊥ BD，AB＝5，BC＝3，FE＝8，求点 D 到 AF 的距离．",
                ("EF", "FE"),
            ),
        ):
            found = [(s["reading"], s["mineru"]) for s in spots(stem, witness)]
            self.assertIn(pair, found, (stem, found))

    def test_a_one_for_one_substitution_is_still_caught(self):
        # 合并不能把原来就报得出的单字差异弄丢。
        found = spots(
            "某种商品有“__种添补”的方式，请说明理由和依据。", "9. 某种商品有“__种填补”的方式，请说明理由和依据。"
        )
        self.assertEqual([(s["reading"], s["mineru"]) for s in found], [("添", "填")], found)

    def test_two_matching_characters_end_a_disagreement(self):
        # 隔两个字以上就是两处独立的差异，不该并成一处长的（长的会被长度上限挡掉）。
        found = spots("甲乙丙丁戊己庚辛，戊己庚辛壬癸子丑寅卯。", "甲乙丙丁戊己庚辛，子丑寅卯辰巳午未申酉。")
        self.assertTrue(all(s["reading"] != "壬癸" for s in found), found)

    def test_two_separate_mistakes_two_characters_apart_stay_separate(self):
        # 两处各错一个字、中间隔着两个字：并成一处就看不出错在哪了，必须分开报。
        found = [
            (s["reading"], s["mineru"])
            for s in spots("甲乙丙z戊己庚辛壬癸q子丑寅卯，结论成立。", "甲乙丙w戊己庚辛壬癸x子丑寅卯，结论成立。")
        ]
        self.assertEqual(sorted(found), [("q", "x"), ("z", "w")], found)

    def test_a_long_difference_is_not_a_spot(self):
        self.assertEqual(
            spots(
                "已知甲乙丙丁戊己庚辛，答案是北纬三十四度三十四分。",
                "已知甲乙丙丁戊己庚辛，答案是东经一百一十度一十四分。",
            ),
            [],
        )

    def test_a_whole_run_of_wrong_characters_is_not_a_spot(self):
        # 整段都不对（一句话被读成了另一句）也不是「读错了一个字」那种问题。
        self.assertEqual(
            spots(
                "甲乙丙丁戊己庚辛，答案是子丑寅卯辰巳午，结论成立。", "甲乙丙丁戊己庚辛，答案是ABCDEFGHIJ，结论成立。"
            ),
            [],
        )

    def test_lookalikes_still_prove_nothing(self):
        # o/0 是 MinerU 的老毛病：吵起来不算证据，别报。
        self.assertEqual(
            spots("某单位 5o米 的线段长 5o米，另一端在 5o米 处。", "某单位 50米 的线段长 50米，另一端在 50米 处。"), []
        )

    def test_handwriting_mixed_into_the_paper_text_is_not_a_spot(self):
        # MinerU 把学生写的字并进了印刷文字：那一侧多出来的东西不是识读的错误。
        self.assertEqual(spots("甲乙丙丁戊己庚辛，结论成立。", "甲乙丙丁戊己庚辛（我算了三遍）甲，结论成立。"), [])

    def test_an_agreeing_reading_is_quiet(self):
        self.assertEqual(spots("甲乙丙丁戊己庚辛，结论成立。", "甲乙丙丁戊己庚辛，结论成立。"), [])

    def test_a_punctuation_slip_next_to_it_does_not_hide_it(self):
        # 同一处错，前面那句号又被读成逗号：差异检测照样得报出来。
        found = spots(
            "已知甲乙丙丁戊己，若根据墙角的距离是八米，求树高。", "已知甲乙丙丁戊己.若树根到墙角的距离是八米，求树高。"
        )
        self.assertEqual([(s["reading"], s["mineru"]) for s in found], [("根据", "树根到")], found)

    def test_a_bracket_matching_both_sides_still_anchors_the_spot(self):
        # 上下文里的括号两边一样，照样算对得上——这处不能因为改了锚定方式而丢掉。
        found = spots("补全 nt_UA 为 (A01) 的值。", "补全 nt_tA 为 (A01) 的值。")
        self.assertEqual([(s["reading"], s["mineru"]) for s in found], [("U", "t")], found)

    def test_a_spot_that_agrees_on_both_sides_is_not_a_difference(self):
        # 合并会把两边相同的字带进片段里，剥掉以后空了，就说明那里根本没有差异。
        self.assertEqual(spots("甲乙，丙丁戊己庚辛，结论成立。", "甲乙。丙丁戊己庚辛，结论成立。"), [])


READING = {
    "stem": READING_STEM,
    "options": {},
    "type": "fill_blank",
    "content_kind": "exercise",
    "figures": {},
    "missing_figure": False,
    "number_seen": 13,
    "figure_descriptions": [],
    "unclear": False,
    "unreadable": False,
}


class WordSubstitutionWiringTests(SimpleTestCase):
    def read(self, reading):
        snapshot = {
            "id": 1,
            "number": 13,
            "group_id": None,
            "regions": [{"page_idx": 1, "bbox": [38.0, 291.0, 514.0, 474.0]}],
            "candidates": [],
            "question_type": "fill_blank",
            "section": "二、填空题",
            "stem": "",
            "options": {},
            "edited": False,
            "source_kind": Question.SourceKind.UNKNOWN,
            "source_anchor_seq": 13,
            "segmentation_flags": [],
            "body_mode": "text",
            "start_source": "mineru",
            "witness": WITNESS,
            "witness_blocks": [
                {"page_idx": 1, "bbox": [40.0, 300.0, 510.0, 340.0], "text": WITNESS, "seq": 1, "type": "text"}
            ],
        }
        with (
            mock.patch.object(readers, "primary_engine", return_value=object()),
            mock.patch.object(readers, "read_question", return_value=reading),
            mock.patch.object(
                pipeline.imaging, "stack_regions", return_value=(SimpleNamespace(height=800, width=1200), [])
            ),
            mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"),
        ):
            return pipeline.read_card(snapshot, SimpleNamespace(load=lambda page: object()))

    def test_the_card_names_the_word_it_misread(self):
        result = self.read(READING)
        self.assertTrue(any("根据" in f and "树根" in f for f in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_reading_that_matches_the_paper_stays_quiet(self):
        good = READING_STEM.replace("若根据墙角的距离", "若树根到墙角的距离").replace("\\overline{}", "")
        result = self.read({**READING, "stem": good})
        self.assertFalse(any("根据" in f for f in result["flags"]), result["flags"])
