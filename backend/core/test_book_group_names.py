"""教材题组的名字：以前一律叫「第 N 组」，现在用书上真实的章节名。

每张教材卡的审核页都会显示题组名，所以这个名字是老师天天看的东西。
这里钉住三件事：认得出章节名、重名会补序号、没有章节名就原样退回。
"""

from django.test import TestCase

from . import pipeline, segment


def header(seq, page, text):
    return {"seq": seq, "type": "header", "page_idx": page, "bbox": [80, 40, 400, 70], "text": text}


def line(seq, page, y, text):
    return {"seq": seq, "type": "text", "page_idx": page, "bbox": [80, y, 920, y + 40], "text": text}


PAGES = [{"page_idx": 0, "width": 1000, "height": 1400}, {"page_idx": 1, "width": 1000, "height": 1400}]


class ChapterNumberTests(TestCase):
    """``六`` 和 ``6`` 是同一章，否则一本书会平白多出一倍章节名。"""

    def test_chinese_and_arabic_read_the_same(self):
        self.assertEqual(segment._chapter_number("六"), 6)
        self.assertEqual(segment._chapter_number("6"), 6)
        self.assertEqual(segment._chapter_number("十"), 10)
        self.assertEqual(segment._chapter_number("十二"), 12)
        self.assertEqual(segment._chapter_number("二十"), 20)

    def test_rejects_nonsense(self):
        for value in ("二三", "", "六章", "0", "abc"):
            self.assertIsNone(segment._chapter_number(value), value)


class ChapterIndexTests(TestCase):
    def setUp(self):
        self.blocks = [
            header(0, 0, "第6章 特殊平行四边形"),
            line(1, 0, 100, "随堂练习 1. 求证：…"),
            # 每页页眉只有章号，章名被排掉了
            header(2, 0, "第六章"),
            header(3, 1, "第六章"),
            header(4, 1, "第7章 二次根式"),
            line(5, 1, 120, "练习 1. 化简：…"),
        ]

    def test_fullest_name_wins_over_the_running_head(self):
        index = segment.chapter_index(self.blocks)
        self.assertEqual(index["names"]["6章"], "特殊平行四边形")
        self.assertEqual(index["names"]["7章"], "二次根式")

    def test_label_uses_the_full_name_even_on_a_bare_page(self):
        index = segment.chapter_index(self.blocks)
        # seq 3 落在两处「第六章」页眉之间，但名字取自章首页
        self.assertEqual(segment.chapter_label(index, 3), "第6章 特殊平行四边形")
        self.assertEqual(segment.chapter_label(index, 5), "第7章 二次根式")

    def test_no_chapter_returns_none(self):
        index = segment.chapter_index([line(0, 0, 80, "1. 求 x 的值。")])
        self.assertEqual(index["headers"], [])
        self.assertIsNone(segment.chapter_label(index, 0))

    def test_block_without_seq_is_skipped(self):
        index = segment.chapter_index([{"seq": "x", "type": "header", "page_idx": 0, "text": "第一章"}])
        self.assertEqual(index["headers"], [])


class FakePaper:
    """只用到 material_type / pages / blocks 三个属性。"""

    material_type = "book"
    pages = PAGES


class BookGroupNameTests(TestCase):
    def setUp(self):
        self.blocks = [header(0, 0, "第6章 特殊平行四边形"), header(1, 1, "第六章"), header(2, 1, "第六章")]
        self.scopes = [
            {"pages": [0], "scope_anchor_seq": 0, "source_kind": "example"},
            {"pages": [0], "scope_anchor_seq": 1, "source_kind": "exercise"},
            {"pages": [1], "scope_anchor_seq": 2, "source_kind": "exercise"},
            {"pages": [1], "scope_anchor_seq": 2, "source_kind": "example"},
        ]
        self.paper = FakePaper()

    def names(self, blocks=None):
        return pipeline._book_group_names(self.scopes, self.paper, blocks or self.blocks)

    def test_chapter_and_kind_are_both_in_the_name(self):
        # fixture 里有两个例题组，所以两个都带序号
        self.assertEqual(self.names()[0], "第6章 特殊平行四边形 · 例题 1")
        self.assertEqual(self.names()[3], "第6章 特殊平行四边形 · 例题 2")

    def test_repeated_names_get_a_running_number(self):
        names = self.names()
        self.assertEqual(names[1], "第6章 特殊平行四边形 · 习题 1")
        self.assertEqual(names[2], "第6章 特殊平行四边形 · 习题 2")

    def test_every_name_is_unique(self):
        names = self.names()
        self.assertEqual(len(set(names.values())), len(names))

    def test_group_before_the_first_chapter_keeps_the_old_name(self):
        scopes = [{"pages": [0], "source_kind": "exercise"}]
        # 锚点在任何章标题之前 —— 目录页那几组不该硬套一个章节名
        self.assertEqual(pipeline._book_group_names(scopes, self.paper, self.blocks), {})

    def test_a_book_without_chapter_headers_falls_back_entirely(self):
        plain = [line(0, 0, 80, "1. 求 x 的值。")]
        self.assertEqual(pipeline._book_group_names(self.scopes, self.paper, plain), {})

    def test_scope_without_anchor_keeps_the_old_name(self):
        scopes = [
            {"pages": [0]},
            {"pages": [0], "scope_anchor_seq": 0},
            {"pages": [0], "scope_anchor_seq": 0, "source_kind": "exercise"},
        ]
        names = pipeline._book_group_names(scopes, self.paper, self.blocks)
        # 0 没有任何锚点，没法知道它在第几章，只能留旧名
        self.assertNotIn(0, names)
        # 1 有章名但不知道是例题还是习题 —— 章名本身就比「第 N 组」有用，照样改
        self.assertEqual(names[1], "第6章 特殊平行四边形")
        self.assertEqual(names[2], "第6章 特殊平行四边形 · 习题")


class BookGroupTitleWiringTests(TestCase):
    """接线：真的走到 _question_group_specs 里，而不是只测上面那两个函数。"""

    def test_book_group_title_comes_from_the_chapter(self):
        from .models import Paper as RealPaper

        paper = RealPaper(
            filename="九年级.pdf",
            kind="pdf",
            material_type=RealPaper.MaterialType.BOOK,
            sha256="d" * 64,
            source_path="/nowhere.pdf",
            pages=PAGES,
            status=RealPaper.Status.READY,
            structure={
                "suggested_scopes": [
                    {"pages": [0], "scope_anchor_seq": 0, "source_kind": "example"},
                    {"pages": [1], "scope_anchor_seq": 1, "source_kind": "exercise"},
                ]
            },
        )
        specs = pipeline._question_group_specs(
            paper, blocks=[header(0, 0, "第6章 特殊平行四边形"), header(1, 1, "第7章 二次根式")]
        )
        self.assertEqual([s["title"] for s in specs], ["第6章 特殊平行四边形 · 例题", "第7章 二次根式 · 习题"])

    def test_exam_paper_keeps_the_paper_name(self):
        from unittest import mock

        from .models import Paper as RealPaper

        paper = RealPaper(
            filename="月考.pdf",
            kind="pdf",
            material_type=RealPaper.MaterialType.EXAM,
            sha256="e" * 64,
            source_path="/nowhere.pdf",
            pages=PAGES,
            status=RealPaper.Status.READY,
            structure={"suggested_groups": [[0], [1]]},
        )
        # 试卷的组名是卷名。而且它不该为了改名去读一整份版面块 ——
        # 这一句是 is_book 那道闸门唯一能被测到的行为。
        with mock.patch.object(pipeline, "_block_dicts") as loader:
            specs = pipeline._question_group_specs(paper)
        loader.assert_not_called()
        self.assertEqual([s["title"] for s in specs], ["月考.pdf", "月考.pdf（2）"])
