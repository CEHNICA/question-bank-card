"""助手交上来的提案、助手领走的活、老师此刻在哪。

1.26.34。三条规矩各有测试守着：
- 提案没被采纳之前，题面一个字都不动；
- 领到的活带到期时间，助手没了活会自己回到池子里；
- 助手能问出老师此刻在哪，别去动老师正在改的题。
"""

from copy import deepcopy
from datetime import timedelta
from unittest import mock

from django.test import TransactionTestCase
from django.utils import timezone

from . import assistant_workbench as workbench, library
from .models import AssistantProposal, LibraryJob, Question, TeacherActivity
from .test_v110_types_origin import TempDataMixin


class WorkbenchTests(TempDataMixin, TransactionTestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.question = self.card(self.paper, question_type="free_response", stem="计算 $1+1$。", answer="原卷答案")
        self.approve(self.question)
        self.publication = library.publish(self.question)[0]

    def approve(self, question):
        question.approved = True
        question.approved_at = timezone.now()
        question.approved_content_hash = library.approval_hash(question)
        question.save()

    def propose(self, agent="离线测试助手", **fields):
        payload = {"publication_id": str(self.publication.pk), "agent": agent, "fields": fields}
        return workbench.propose(payload)

    def set_options(self, options):
        """改完选项再重新入库 —— 题卡和题库得是同一份，才谈得上提改动。"""
        self.question.options = dict(options)
        self.question.save()
        self.approve(self.question)
        self.publication = library.publish(self.question)[0]

    def assistant_job(self, kind="answer"):
        return LibraryJob.objects.create(
            publication=self.publication,
            kind=kind,
            executor="assistant",
            fingerprint=library.generation_fingerprint(self.publication.content or {}, self.publication.pk),
        )

    # ------------------------------------------------------------ 提案

    def test_a_proposal_leaves_the_question_untouched(self):
        before = Question.all_objects.filter(pk=self.question.pk).values().get()
        result = self.propose(stem="计算 $1+2$。")
        self.question.refresh_from_db()
        self.assertEqual(self.question.stem, "计算 $1+1$。")
        self.assertEqual(Question.all_objects.filter(pk=self.question.pk).values().get(), before)
        self.assertEqual(result["proposal"]["status"], "pending")
        self.assertEqual(result["proposal"]["changed"], ["stem"])
        self.assertEqual(result["proposal"]["agent"], "离线测试助手")

    def test_changing_one_option_keeps_the_others(self):
        """只给 A，意思是「A 改了，其余照旧」—— B、C、D 不能被抹掉。"""
        self.set_options({"A": "第一项", "B": "第二项", "C": "第三项"})
        result = self.propose(options={"A": "改过的第一项"})
        after = result["proposal"]["after"]["options"]
        self.assertEqual(after["A"], "改过的第一项")
        self.assertEqual(after["B"], "第二项")
        self.assertEqual(after["C"], "第三项")
        # 题面此刻一个字都没动。
        self.question.refresh_from_db()
        self.assertEqual(self.question.options["B"], "第二项")

    def test_accepting_a_partial_option_change_keeps_the_others(self):
        self.set_options({"A": "第一项", "B": "第二项", "C": "第三项"})
        self.propose(options={"A": "改过的第一项"})
        proposal = AssistantProposal.objects.get()
        workbench.decide({"proposal_id": str(proposal.pk), "action": "accept", "by": "老师"})
        self.question.refresh_from_db()
        self.assertEqual(self.question.options.get("A"), "改过的第一项")
        self.assertEqual(self.question.options.get("B"), "第二项")
        self.assertEqual(self.question.options.get("C"), "第三项")

    def test_a_rejected_change_cannot_be_proposed_again_by_the_same_assistant(self):
        self.propose(agent="豆包", stem="计算 $1+2$。")
        proposal = AssistantProposal.objects.get()
        workbench.decide({"proposal_id": str(proposal.pk), "action": "reject", "by": "老师", "reason": "原卷就是这样"})
        with self.assertRaises(workbench.WorkbenchError) as caught:
            self.propose(agent="豆包", stem="计算 $1+2$。")
        self.assertIn("原卷就是这样", str(caught.exception))
        # 再提一次不会多出一条待核对。
        self.assertEqual(AssistantProposal.objects.filter(status=AssistantProposal.Status.PENDING).count(), 0)

    def test_another_assistant_may_still_propose_the_same_change(self):
        self.propose(agent="豆包", stem="计算 $1+2$。")
        proposal = AssistantProposal.objects.get()
        workbench.decide({"proposal_id": str(proposal.pk), "action": "reject", "by": "老师"})
        result = self.propose(agent="另一个助手", stem="计算 $1+2$。")
        self.assertEqual(result["proposal"]["status"], "pending")

    def test_a_different_change_is_still_allowed_after_a_rejection(self):
        self.propose(agent="豆包", stem="计算 $1+2$。")
        proposal = AssistantProposal.objects.get()
        workbench.decide({"proposal_id": str(proposal.pk), "action": "reject", "by": "老师"})
        result = self.propose(agent="豆包", stem="计算 $1+3$。")
        self.assertEqual(result["proposal"]["status"], "pending")

    def test_a_proposal_that_changes_nothing_is_refused(self):
        with self.assertRaises(workbench.WorkbenchError):
            self.propose(stem="计算 $1+1$。")

    def test_a_proposal_may_not_touch_fields_it_does_not_own(self):
        with self.assertRaises(workbench.WorkbenchError):
            self.propose(number=3)

    def test_accepting_applies_the_change_leaves_a_note_and_clears_the_approval(self):
        self.propose(stem="计算 $1+2$。")
        proposal = AssistantProposal.objects.get()
        result = workbench.decide({"proposal_id": str(proposal.pk), "action": "accept", "by": "老师"})
        self.assertTrue(result["applied"])
        self.question.refresh_from_db()
        self.assertEqual(self.question.stem, "计算 $1+2$。")
        self.assertEqual(self.question.text_source, "assistant")
        self.assertFalse(self.question.approved)
        self.assertEqual(self.question.approved_content_hash, "")
        self.assertEqual(self.question.state, Question.State.YELLOW)
        self.assertTrue(any("助手 离线测试助手 改了题面" in flag for flag in self.question.flags))
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, AssistantProposal.Status.ACCEPTED)
        self.assertEqual(proposal.decided_by, "老师")
        # 题库里那一版还没变 —— 采纳改的是题卡，题卡核对通过后才会重新入库。
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.content["stem"], "计算 $1+1$。")

    def test_rejecting_records_the_reason_and_clears_it_from_the_workbench(self):
        self.propose(stem="计算 $1+2$。")
        proposal = AssistantProposal.objects.get()
        result = workbench.decide(
            {"proposal_id": str(proposal.pk), "action": "reject", "by": "老师", "reason": "原卷就是这样"}
        )
        self.assertFalse(result["applied"])
        self.question.refresh_from_db()
        self.assertEqual(self.question.stem, "计算 $1+1$。")
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, AssistantProposal.Status.REJECTED)
        self.assertEqual(proposal.message, "原卷就是这样")
        self.assertEqual(workbench.workbench()["pending"]["text"], 0)

    def test_a_second_proposal_replaces_the_first(self):
        self.propose(stem="计算 $1+2$。")
        first = AssistantProposal.objects.get()
        self.propose(stem="计算 $1+3$。")
        first.refresh_from_db()
        self.assertEqual(first.status, AssistantProposal.Status.STALE)
        self.assertEqual(AssistantProposal.objects.filter(status=AssistantProposal.Status.PENDING).count(), 1)

    def test_a_proposal_on_a_card_that_moved_on_is_refused(self):
        # 题卡后来改过、还没重新入库：题面已经不是助手看到的那份了。
        self.question.stem = "老师自己改过的题面"
        self.question.save()
        with self.assertRaises(workbench.WorkbenchError):
            self.propose(stem="计算 $1+2$。")

    def test_accepting_a_proposal_whose_card_moved_on_is_refused(self):
        self.propose(stem="计算 $1+2$。")
        proposal = AssistantProposal.objects.get()
        self.question.stem = "老师自己改过的题面"
        self.question.save()
        with self.assertRaises(workbench.WorkbenchError):
            workbench.decide({"proposal_id": str(proposal.pk), "action": "accept", "by": "老师"})
        self.question.refresh_from_db()
        self.assertEqual(self.question.stem, "老师自己改过的题面")
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, AssistantProposal.Status.STALE)

    def test_the_proposal_keeps_the_before_image_so_the_teacher_can_compare(self):
        result = self.propose(stem="计算 $1+2$。", answer="3")
        proposal = result["proposal"]
        self.assertEqual(proposal["before"]["stem"], "计算 $1+1$。")
        self.assertEqual(proposal["before"]["answer"], "原卷答案")
        self.assertEqual(proposal["after"]["stem"], "计算 $1+2$。")
        self.assertEqual(sorted(proposal["changed"]), ["answer", "stem"])

    def test_a_withdrawn_question_cannot_be_proposed_on(self):
        library.withdraw(self.publication)
        with self.assertRaises(workbench.WorkbenchError):
            self.propose(stem="计算 $1+2$。")

    # ------------------------------------------------------------ 领活

    def test_claiming_leases_the_work_with_a_deadline(self):
        job = self.assistant_job()
        result = workbench.claim({"agent": "离线测试助手", "limit": 5})
        self.assertEqual(result["count"], 1)
        claimed = result["claimed"][0]
        self.assertEqual(claimed["job_id"], str(job.pk))
        job.refresh_from_db()
        self.assertEqual(job.agent, "离线测试助手")
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.lease_expires_at)
        self.assertGreater(job.lease_expires_at, timezone.now())

    def test_another_assistant_cannot_take_leased_work(self):
        self.assistant_job()
        workbench.claim({"agent": "离线测试助手", "limit": 5})
        second = workbench.claim({"agent": "另一个助手", "limit": 5})
        self.assertEqual(second["count"], 0)

    def test_the_same_assistant_may_take_its_own_leased_work_again(self):
        self.assistant_job()
        workbench.claim({"agent": "离线测试助手", "limit": 5})
        again = workbench.claim({"agent": "离线测试助手", "limit": 5})
        self.assertEqual(again["count"], 1)

    def test_an_expired_lease_returns_the_work_to_the_pool(self):
        job = self.assistant_job()
        workbench.claim({"agent": "离线测试助手", "limit": 5})
        LibraryJob.objects.filter(pk=job.pk).update(lease_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(workbench.expire_leases(), 1)
        job.refresh_from_db()
        self.assertEqual(job.agent, "")
        self.assertIsNone(job.lease_expires_at)
        self.assertEqual(job.status, LibraryJob.Status.QUEUED)
        # 活回到了池子，别的助手能接着领。
        second = workbench.claim({"agent": "另一个助手", "limit": 5})
        self.assertEqual(second["count"], 1)
        job.refresh_from_db()
        self.assertEqual(job.agent, "另一个助手")
        self.assertEqual(job.attempts, 2)

    def test_releasing_hands_the_work_back(self):
        job = self.assistant_job()
        workbench.claim({"agent": "离线测试助手", "limit": 5})
        result = workbench.release({"agent": "离线测试助手", "ids": [str(job.pk)]})
        self.assertEqual(result["released"], 1)
        job.refresh_from_db()
        self.assertEqual(job.agent, "")
        self.assertIsNone(job.lease_expires_at)

    def test_releasing_someone_elses_work_is_refused(self):
        job = self.assistant_job()
        workbench.claim({"agent": "离线测试助手", "limit": 5})
        with self.assertRaises(workbench.WorkbenchError):
            workbench.release({"agent": "另一个助手", "ids": [str(job.pk)]})
        job.refresh_from_db()
        self.assertEqual(job.agent, "离线测试助手")

    # ------------------------------------------------------------ 工作台

    def test_the_workbench_counts_what_is_waiting_and_who_is_working(self):
        self.propose(stem="计算 $1+2$。")
        job = self.assistant_job()
        workbench.claim({"agent": "离线测试助手", "limit": 5})
        board = workbench.workbench()
        self.assertEqual(board["pending"]["text"], 1)
        self.assertEqual(board["pending"]["total"], 1)
        self.assertEqual(len(board["proposals"]), 1)
        self.assertEqual(board["running"][0]["job_id"], str(job.pk))
        self.assertEqual(board["running"][0]["agent"], "离线测试助手")
        self.assertEqual(board["counts"]["claimed"], 1)

    def test_the_workbench_reports_a_rejection(self):
        self.propose(stem="计算 $1+2$。")
        proposal = AssistantProposal.objects.get()
        workbench.decide({"proposal_id": str(proposal.pk), "action": "reject", "by": "老师"})
        board = workbench.workbench()
        self.assertEqual(board["counts"]["rejected"], 1)
        self.assertEqual(board["pending"]["text"], 0)

    # ------------------------------------------------------------ 老师在哪

    def test_whereami_tells_the_assistant_where_the_teacher_is(self):
        self.propose(stem="计算 $1+2$。")
        proposal = AssistantProposal.objects.get()
        workbench.decide({"proposal_id": str(proposal.pk), "action": "reject", "by": "老师", "reason": "原卷就是这样"})
        workbench.set_presence({"question_id": str(self.question.pk), "state": "editing"})
        answer = workbench.whereami()
        self.assertTrue(answer["teacher"]["here"])
        self.assertEqual(answer["teacher"]["question_id"], str(self.question.pk))
        self.assertEqual(answer["teacher"]["state"], "editing")
        self.assertEqual(answer["rejected"][0]["message"], "原卷就是这样")

    def test_presence_is_forgotten_after_a_while(self):
        workbench.set_presence({"question_id": str(self.question.pk), "state": "viewing"})
        TeacherActivity.objects.update(updated_at=timezone.now() - timedelta(minutes=30))
        self.assertFalse(workbench.teacher_now()["here"])

    def test_leaving_the_question_clears_the_presence(self):
        workbench.set_presence({"question_id": str(self.question.pk), "state": "viewing"})
        workbench.set_presence({})
        self.assertFalse(workbench.teacher_now()["here"])
        self.assertEqual(TeacherActivity.objects.count(), 0)
