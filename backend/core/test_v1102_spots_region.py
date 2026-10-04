"""1.10.2：MinerU 读法不同的地方标在原卷和题面上；框选识读。

全部离线：虚构题目 + 模拟模型输出，不调用任何服务。
"""

from __future__ import annotations

import json
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase

from . import pipeline, readers, region_reads, textnorm
from . import test_v110_types_origin as v110
from .models import Block, Question, RegionRead

STEM = "（1）当 $x>0$ 时，求 $y=2x+\\dfrac{1}{x^{3}}$ 最小值；\n（2）当 $0<x<1$ 时，求 $y=x(1-x)^{2}$ 最大值."
SPOT = {"reading": "3", "mineru": "2", "before": "1x^", "after": "最小值"}
LINE = {"page_idx": 0, "bbox": [519.0, 762.0, 695.0, 800.0],
        "text": "(1) 当 x > 0 时，求 $y = 2x + \\frac{1}{x^{2}}$ 最小值："}
OTHER = {"page_idx": 0, "bbox": [519.0, 810.0, 712.0, 836.0],
         "text": "(2) 当 0 < x < 1 时，求 $y = x(1 - x)^{2}$ 最大值："}


class SpotLocationTests(SimpleTestCase):
    def test_the_spot_is_found_to_the_character_in_the_text(self):
        start, end = textnorm.spot_range(STEM, SPOT)
        self.assertEqual(STEM[start:end], "3")
        self.assertEqual(STEM[start - 3:end + 1], "x^{3}")

    def test_a_spot_that_is_not_in_the_text_is_none(self):
        self.assertIsNone(textnorm.spot_range("已知 $a>b$，则（ ）", SPOT))
        self.assertIsNone(textnorm.spot_range("", SPOT))

    def test_the_box_is_minerus_line_with_its_reading(self):
        self.assertEqual(textnorm.spot_block(SPOT, [OTHER, LINE]), {"page_idx": 0, "bbox": LINE["bbox"]})
        self.assertIsNone(textnorm.spot_block(SPOT, [OTHER]))

    def test_located_spots_keep_the_flag_order_and_find_the_text(self):
        spots = pipeline._located_spots([SPOT, {"reading": "9", "mineru": "8", "before": "qq^", "after": "zzz"}], [LINE])
        self.assertEqual([spot["n"] for spot in spots], [1, 2])
        self.assertEqual(spots[0]["bbox"], LINE["bbox"])
        self.assertNotIn("bbox", spots[1])
        update = {"read_c": {"objections": [SPOT], "doubtful": spots}}
        pipeline._mark_spot_text(update, STEM, {})
        first = update["read_c"]["doubtful"][0]
        self.assertEqual((first["field"], STEM[first["start"]:first["end"]], first["text"]), ("stem", "3", "3"))
        self.assertNotIn("field", update["read_c"]["doubtful"][1])

    def test_the_arbiter_keeps_its_spots_beside_its_reading(self):
        record = {"stem": "x", "objection_check": {"doubtful": []}}
        self.assertIs(pipeline.spot_record(record), record["objection_check"])
        self.assertIsNone(pipeline.spot_record(None))


class ReadCardSpotTests(v110.TempDataMixin, TestCase):
    """Both readers say x^3, MinerU printed x^2, the spot check cannot decide."""

    setUp = v110.ReadCardTypeTests.setUp

    def test_the_card_records_where_to_compare(self):
        reading = readers.parse_reading(f"【题型】解答题\n【题干】\n{STEM}", 2)
        snapshot = {**self.snapshot, "witness": f"{LINE['text']}\n{OTHER['text']}",
                    "witness_blocks": [LINE, OTHER]}
        with mock.patch.object(readers, "read_question", return_value=reading), \
                mock.patch.object(readers, "spot_check") as spot_check:
            result = pipeline.read_card(snapshot, pipeline.PageStore(self.paper))
        spot_check.assert_not_called()
        self.assertTrue(any(flag.startswith(pipeline.WITNESS_FLAG_PREFIX) for flag in result["flags"]))
        spot = pipeline.spot_record(result["read_c"])["doubtful"][0]
        self.assertEqual((spot["n"], spot["reading"], spot["mineru"]), (1, "3", "2"))
        self.assertEqual((spot["page_idx"], spot["bbox"]), (0, LINE["bbox"]))
        self.assertEqual(result["stem"][spot["start"]:spot["end"]], "3")


class CheckSpotsJsonTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.flag = f"{pipeline.OBJECTION_FLAG_PREFIX}…1x^【3】最小值…（MinerU：2），请对照原卷"

    def test_stored_spots_reach_the_review_page_until_the_text_is_edited(self):
        start, end = textnorm.spot_range(STEM, SPOT)
        question = self.card(self.paper, stem=STEM, state=Question.State.YELLOW, flags=[self.flag],
                             read_c={"objections": [SPOT], "answers": ["mineru"], "doubtful": [
                                 {"n": 1, **SPOT, "page_idx": 0, "bbox": LINE["bbox"],
                                  "field": "stem", "start": start, "end": end, "text": "3"}]})
        data = Client().get(f"/api/papers/{self.paper.id}").json()
        shown = next(item for item in data["questions"] if item["id"] == question.id)["check_spots"]
        self.assertEqual(shown, [{"n": 1, "reading": "3", "mineru": "2", "page_idx": 0, "bbox": LINE["bbox"],
                                  "field": "stem", "start": start, "end": end, "text": "3"}])
        # A person fixes the exponent: the flag goes, and so do the marks.
        response = Client().post(f"/api/questions/{question.id}/text", data=json.dumps(
            {"stem": STEM.replace("x^{3}", "x^{2}"), "options": {}}),
            content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["question"]["check_spots"], [])

    def test_a_range_that_no_longer_holds_is_dropped_but_the_box_stays(self):
        question = self.card(self.paper, stem=STEM, flags=[self.flag], read_c={"doubtful": [
            {"n": 1, **SPOT, "page_idx": 0, "bbox": LINE["bbox"], "field": "stem", "start": 0, "end": 1, "text": "3"}]})
        self.assertEqual(pipeline.check_spots(question),
                         [{"n": 1, "reading": "3", "mineru": "2", "page_idx": 0, "bbox": LINE["bbox"]}])

    def test_cards_read_before_this_version_are_worked_out_from_minerus_blocks(self):
        Block.objects.create(paper=self.paper, seq=1, type="text", page_idx=0, bbox=LINE["bbox"], text=LINE["text"])
        regions = [{"page_idx": 0, "bbox": [500, 700, 1000, 900]}]
        question = self.card(self.paper, stem=STEM, flags=[self.flag], regions=regions,
                             read_c={"objections": [SPOT, {**SPOT, "before": "zz^"}], "answers": ["mineru", "reading"]})
        spots = pipeline.check_spots(question)
        self.assertEqual(len(spots), 1)
        self.assertEqual((spots[0]["page_idx"], spots[0]["bbox"], spots[0]["field"]), (0, LINE["bbox"], "stem"))

    def test_no_flag_no_spots(self):
        question = self.card(self.paper, stem=STEM, read_c={"doubtful": [{"n": 1, **SPOT}]})
        self.assertEqual(pipeline.check_spots(question), [])


class RegionReadTextTests(SimpleTestCase):
    def test_the_reply_is_the_option_text_alone(self):
        self.assertEqual(region_reads.clean("A. $f(1,5)=f(5,1)$", "A"), "$f(1,5)=f(5,1)$")
        self.assertEqual(region_reads.clean("```\n（A）$f(1,5)=f(5,1)$\n```", "A"), "$f(1,5)=f(5,1)$")
        self.assertEqual(region_reads.clean("<think>先看图</think>【A】$x>0$", "A"), "$x>0$")
        # Only the target's own letter is a label.
        self.assertEqual(region_reads.clean("B. 是增函数", "A"), "B. 是增函数")
        self.assertEqual(region_reads.clean("已知 $a>b$", "stem"), "已知 $a>b$")

    def test_nothing_printed_is_an_error(self):
        for reply in ("无", "", "（原卷此处为配图，无印刷文字）"):
            with self.assertRaises(region_reads.RegionError, msg=reply):
                region_reads.clean(reply, "A")

    def test_the_prompt_names_the_target_and_ignores_handwriting(self):
        self.assertIn("选项字母“A.”本身不要写", region_reads.prompt("A"))
        self.assertIn("印刷的字上画了 × 或圈，照样誊录印刷的字", region_reads.prompt("A"))
        self.assertIn("题号不要写", region_reads.prompt("stem"))


class RegionReadApiTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.question = self.card(self.paper, number=11, question_type="multiple_choice",
                                  stem="已知二元函数 $f(x,y)=(x+1)y$，则", options={"B": "$1$", "C": "$2$", "D": "$3$"})
        self.url = f"/api/questions/{self.question.id}/region-read"
        env = mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "test"})
        env.start()
        self.addCleanup(env.stop)

    def post(self, body, method="post"):
        return getattr(Client(), method)(self.url, data=json.dumps(body), content_type="application/json",
                                         HTTP_X_QB_REQUEST="1")

    def test_a_region_is_queued_and_shown_on_the_card(self):
        response = self.post({"page_idx": 0, "bbox": [540, 846, 640, 875], "target": "A"})
        self.assertEqual(response.status_code, 200)
        shown = response.json()["question"]["region_read"]
        self.assertEqual((shown["status"], shown["target"], shown["target_name"]), ("queued", "A", "选项 A"))
        # A new box replaces the last one; the card itself is not touched.
        self.post({"page_idx": 0, "bbox": [540, 840, 660, 880], "target": "A"})
        self.assertEqual(RegionRead.objects.filter(question=self.question).count(), 1)
        self.question.refresh_from_db()
        self.assertEqual(self.question.options, {"B": "$1$", "C": "$2$", "D": "$3$"})
        self.assertFalse(self.question.edited)
        # Closing the result removes it.
        response = self.post({}, method="delete")
        self.assertIsNone(response.json()["question"]["region_read"])

    def test_bad_requests_are_refused(self):
        for body in ({"page_idx": 0, "bbox": [540, 846, 640, 875], "target": "F"},
                     {"page_idx": 7, "bbox": [540, 846, 640, 875], "target": "A"},
                     {"page_idx": 0, "bbox": [540, 846, 542, 847], "target": "A"},
                     {"page_idx": 0, "bbox": "x", "target": "A"}):
            self.assertEqual(self.post(body).status_code, 400, body)
        self.assertFalse(RegionRead.objects.exists())

    def test_without_a_vision_service_it_says_so(self):
        with mock.patch.object(readers, "primary_engine", return_value=None):
            response = self.post({"page_idx": 0, "bbox": [540, 846, 640, 875], "target": "A"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("框选识读要用看图读题的服务", response.json()["error"])


class RegionReadWorkerTests(v110.TempDataMixin, TransactionTestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.question = self.card(self.paper, number=11)
        env = mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "test"})
        env.start()
        self.addCleanup(env.stop)

    def queue(self, target="A"):
        return RegionRead.objects.create(question=self.question, page_idx=0, bbox=[100, 80, 500, 120], target=target)

    def test_the_worker_reads_the_box_and_keeps_the_text(self):
        job = self.queue()
        calls = []

        def chat(engine, prompt, images, max_tokens=None):
            calls.append((prompt, images))
            return "A. $f(1,5)=f(5,1)$"

        with mock.patch.object(readers, "chat", side_effect=chat):
            self.assertEqual(region_reads.process_pending(), 1)
        job.refresh_from_db()
        self.assertEqual((job.status, job.text), ("done", "$f(1,5)=f(5,1)$"))
        self.assertTrue(job.engine)
        prompt, images = calls[0]
        self.assertIn("选项 A", prompt)
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].startswith("data:image/jpeg;base64,"))
        # Nothing left to do.
        self.assertEqual(region_reads.process_pending(), 0)

    def test_failures_are_kept_for_the_card(self):
        job = self.queue()
        with mock.patch.object(readers, "chat", return_value="无"):
            region_reads.process_pending()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertIn("没有读到印刷文字", job.error)
        job = self.queue()
        with mock.patch.object(readers, "primary_engine", return_value=None):
            region_reads.process_pending()
        job.refresh_from_db()
        self.assertEqual((job.status, job.error), ("failed", region_reads.NO_ENGINE))

    def test_a_job_left_running_goes_back_to_the_queue(self):
        job = self.queue()
        RegionRead.objects.filter(pk=job.pk).update(status="running")
        self.assertEqual(region_reads.recover_interrupted(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, "queued")

    def test_the_crop_is_enlarged_for_the_model(self):
        from PIL import Image
        page = Image.new("RGB", (3000, 2000), "white")
        piece = region_reads.crop(page, [100, 80, 200, 100])
        self.assertGreaterEqual(max(piece.size), region_reads.MIN_LONG_SIDE)


class InventedOptionTidyTests(v110.TempDataMixin, TestCase):
    """Cards read by 1.10.0 kept “（原卷此处为配图，无印刷文字）” as option A."""

    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()

    def test_unreviewed_cards_lose_the_note_and_ask_for_the_option(self):
        from . import library
        options = {"A": "（原卷此处为配图，无印刷文字）", "B": "$1$", "C": "$2$", "D": "$3$"}
        fresh = self.card(self.paper, number=11, question_type="multiple_choice", options=options,
                          stem="已知二元函数 $f(x,y)=(x+1)y$，则（ ）", state=Question.State.GREEN, flags=[])
        edited = self.card(self.paper, number=12, question_type="multiple_choice", options=options,
                           stem="已知二元函数 $f(x,y)=(x+1)y$，则（ ）", edited=True)
        library.tidy_saved_cards()
        fresh.refresh_from_db()
        edited.refresh_from_db()
        self.assertEqual(fresh.options, {"B": "$1$", "C": "$2$", "D": "$3$"})
        self.assertIn("选项 A 没有读出来，请对照原卷补上", fresh.flags)
        self.assertEqual(fresh.state, Question.State.YELLOW)
        self.assertEqual(edited.options["A"], "（原卷此处为配图，无印刷文字）")
        # Nothing more on the next start.
        self.assertEqual(library.tidy_saved_cards()["questions"], 0)

    def test_an_option_another_reading_had_is_taken_from_it(self):
        from . import library
        options = {"A": "（原卷此处为配图，无印刷文字）", "B": "$1$", "C": "$2$", "D": "$3$"}
        card = self.card(self.paper, number=11, question_type="multiple_choice", options=options,
                         stem="已知二元函数 $f(x,y)=(x+1)y$，则（ ）", flags=["两次识读不一致，已由第三次识读裁决"],
                         state=Question.State.YELLOW,
                         read_a={"stem": "已知", "options": {"A": "$f(1,5)=f(5,1)$", "B": "$1$"}},
                         read_c={"stem": "已知", "options": options})
        library.tidy_saved_cards()
        card.refresh_from_db()
        self.assertEqual(card.options["A"], "$f(1,5)=f(5,1)$")
        self.assertIn("选项 A 只有一次识读读到，已补上，请对照原卷核对", card.flags)
        self.assertNotIn("选项 A 没有读出来，请对照原卷补上", card.flags)

    def test_picture_options_lose_the_note_without_a_reminder(self):
        from . import library
        figure = {"slot": "A", "page_idx": 0, "bbox": [100, 300, 200, 360], "source": "auto"}
        card = self.card(self.paper, number=11, question_type="single_choice", state=Question.State.GREEN,
                         options={"A": "（原卷此处为配图，无印刷文字）", "B": "$1$"}, figures=[figure], flags=[],
                         stem="下列图象中，能表示函数关系的是（ ）")
        library.tidy_saved_cards()
        card.refresh_from_db()
        self.assertEqual(card.options, {"B": "$1$"})
        self.assertEqual(card.flags, [])
        self.assertEqual(card.state, Question.State.GREEN)


class ObjectionSpotPlaceTests(SimpleTestCase):
    """The spots are found in the stem and options read as one text."""

    def test_a_spot_at_the_start_of_an_option_is_in_that_option(self):
        reading = {"stem": "函数 $f(x)$ 的最小值为（ ）", "options": {"A": "$3$", "B": "$4$", "C": "$5$", "D": "$6$"}}
        spots = textnorm.witness_objections(reading, "函数 f(x) 的最小值为（ ）A. 8 B. 4 C. 5 D. 6")
        self.assertEqual([(spot["reading"], spot["mineru"]) for spot in spots], [("3", "8")])
        field, start, end = textnorm.spot_place(reading["stem"], reading["options"], spots[0])
        self.assertEqual((field, reading["options"]["A"][start:end]), ("A", "3"))

    def test_the_same_context_twice_takes_the_one_that_was_compared(self):
        stem = "甲店：乙的定价为 1 元；丙店：乙的定价为 1 元，问哪家便宜"
        witness = "甲店：乙的定价为 1 元；丙店：乙的售价为 1 元，问哪家便宜"
        spots = textnorm.witness_objections({"stem": stem}, witness)
        self.assertEqual([(spot["reading"], spot["mineru"]) for spot in spots], [("定", "售")])
        field, start, end = textnorm.spot_place(stem, {}, spots[0])
        self.assertEqual((field, start), ("stem", stem.rindex("乙的定价为") + 2))
        self.assertEqual(stem[start:end], "定")
        # Without the position (stored before it was kept) the first one is taken.
        legacy = {key: value for key, value in spots[0].items() if key != "at"}
        self.assertEqual(textnorm.spot_place(stem, {}, legacy)[1], stem.index("乙的定价为") + 2)

    def test_a_stem_with_a_table_is_searched_on_its_own(self):
        stem = "下表是 $x^{3}$ 最小值的记录\n| a | b |\n| --- | --- |\n| 1 | 2 |"
        field, start, end = textnorm.spot_place(stem, {}, {**SPOT, "before": "x^", "after": "最小值"})
        self.assertEqual((field, stem[start:end]), ("stem", "3"))


class StoredSpotsTidyTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()

    def test_old_flagged_cards_get_their_spots_stored_once_and_keep_their_approval(self):
        from . import library
        Block.objects.create(paper=self.paper, seq=1, type="text", page_idx=0, bbox=LINE["bbox"], text=LINE["text"])
        flag = f"{pipeline.OBJECTION_FLAG_PREFIX}…1x^【3】最小值…（MinerU：2），请对照原卷"
        card = self.card(self.paper, stem=STEM, flags=[flag], state=Question.State.YELLOW, question_type="free_response",
                         regions=[{"page_idx": 0, "bbox": [500, 700, 1000, 900]}],
                         read_c={"objections": [SPOT], "answers": ["mineru"]})
        before = (card.stem, card.options, card.flags, card.state, card.updated_at)
        library.tidy_saved_cards()
        card.refresh_from_db()
        self.assertEqual((card.stem, card.options, card.flags, card.state, card.updated_at), before)
        spot = card.read_c["doubtful"][0]
        self.assertEqual((spot["n"], spot["bbox"], spot["field"], spot["text"]), (1, LINE["bbox"], "stem", "3"))
        # Shown without looking at MinerU's blocks again.
        with self.assertNumQueries(0):
            shown = pipeline.check_spots(card)
        self.assertEqual(shown[0]["field"], "stem")
        stored = card.read_c
        library.tidy_saved_cards()
        card.refresh_from_db()
        self.assertEqual(card.read_c, stored)


class FigureDecisionFlagTests(v110.TempDataMixin, TestCase):
    """第 16 题：确认了“本题无图”，“别的题认为有一张图属于本题，已加上”却还在。"""

    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.figure = {"slot": "stem", "page_idx": 0, "bbox": [100, 300, 300, 420], "source": "other"}

    def post(self, question, body):
        return Client().post(f"/api/questions/{question.id}/figure-review", data=json.dumps(body),
                             content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_confirming_no_figure_answers_the_question_and_undo_asks_it_again(self):
        from .figure_policy import FLAG_FOREIGN_FIGURE
        card = self.card(self.paper, number=16, figures=[self.figure], flags=[FLAG_FOREIGN_FIGURE], question_type="free_response",
                         state=Question.State.YELLOW, figure_review={}, stem="已知函数，求它的最小值")
        response = self.post(card, {"decision": "confirm_no_figure"})
        self.assertEqual(response.status_code, 200, response.content)
        card.refresh_from_db()
        self.assertNotIn(FLAG_FOREIGN_FIGURE, card.flags)
        self.assertEqual(card.figures, [])
        response = self.post(card, {"decision": "reset"})
        self.assertEqual(response.status_code, 200, response.content)
        card.refresh_from_db()
        self.assertEqual([figure["source"] for figure in card.figures], ["other"])
        self.assertIn(FLAG_FOREIGN_FIGURE, card.flags)
        self.assertEqual(card.state, Question.State.YELLOW)

    def test_the_start_up_tidy_drops_the_stale_flag(self):
        from . import library
        from .figure_policy import FLAG_FOREIGN_FIGURE
        card = self.card(self.paper, number=16, flags=[FLAG_FOREIGN_FIGURE], state=Question.State.YELLOW,
                         question_type="free_response", stem="已知函数，求它的最小值")
        library.tidy_saved_cards()
        card.refresh_from_db()
        self.assertEqual(card.flags, [])
        self.assertEqual(card.state, Question.State.GREEN)


class RegionReadRaceTests(v110.TempDataMixin, TransactionTestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.question = self.card(self.paper, number=11)
        env = mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "test"})
        env.start()
        self.addCleanup(env.stop)

    def test_a_read_closed_or_replaced_while_running_is_not_written_back(self):
        job = RegionRead.objects.create(question=self.question, page_idx=0, bbox=[100, 80, 500, 120], target="A")

        def closed_meanwhile(engine, prompt, images, max_tokens=None):
            RegionRead.objects.filter(pk=job.pk).delete()
            return "A. $1$"

        with mock.patch.object(readers, "chat", side_effect=closed_meanwhile):
            self.assertEqual(region_reads.process_pending(), 1)
        self.assertFalse(RegionRead.objects.exists())

        job = RegionRead.objects.create(question=self.question, page_idx=0, bbox=[100, 80, 500, 120], target="A")

        def replaced_meanwhile(engine, prompt, images, max_tokens=None):
            RegionRead.objects.filter(pk=job.pk).update(status=RegionRead.Status.QUEUED)
            return "A. $1$"

        with mock.patch.object(readers, "chat", side_effect=replaced_meanwhile):
            region_reads.process_pending(limit=1)
        job.refresh_from_db()
        self.assertEqual((job.status, job.text), ("queued", ""))
