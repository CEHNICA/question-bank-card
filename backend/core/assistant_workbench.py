"""助手交上来的东西，和助手领走的活。

1.26.34。两块：

**提案** —— 助手对**已经入库**的题不能直接改字。直接改会把老师打过的勾弄没，
老师还不知道，而且看不出是谁改的。所以助手的改动先落成提案：题面一个字不动，
等老师在题库页「助手工作台」里点「采纳」才真的改；不采纳也要记下来，
同一个助手下次别再提一样的改法。

**租约** —— 助手领走一道活时记下到期时间。助手中途没了（断网、崩了、关掉了），
活自己回到池子里，别的助手能接着领。不记到期时间，这道活会一直占着，别人永远领不走。

这一层只认本机：助手在本机跑，任务、提案、老师的在场都只在本机之间流动。
"""

from __future__ import annotations

import uuid

from django.db import transaction
from django.utils import timezone

from . import library
from .models import AssistantProposal, LibraryJob, LibraryTagSuggestion, PublishedQuestion, Question, TeacherActivity

# 助手领一道活的租约：超时没交回来，活自己回到池子里。
LEASE_SECONDS = 30 * 60
# 老师「在场」的记多久：超过这个时间没动静，就当老师已经离开这道题。
PRESENCE_SECONDS = 5 * 60
# 提案里能动的字段。别的字段助手碰不到。
FACE_KEYS = ("stem", "options", "question_type", "answer", "analysis", "origin")
MAX_TEXT = 20000
MAX_OPTION = 4000
MAX_ORIGIN = 400


class WorkbenchError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _uuid(value, label):
    if not isinstance(value, str):
        raise WorkbenchError(f"请提供有效的{label} ID。", 400)
    try:
        return uuid.UUID(value)
    except ValueError:
        raise WorkbenchError(f"请提供有效的{label} ID。", 400) from None


def _number(value, label):
    """题目的编号是数字，不是 UUID。命令行传过来时可能是字符串。"""
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WorkbenchError(f"请提供有效的{label} ID。", 400)
    return value


def _agent(value):
    if not isinstance(value, str):
        raise WorkbenchError("请写清实际助手名称。", 400)
    agent = " ".join(value.split())
    if not agent or len(agent) > 40:
        raise WorkbenchError("助手名称需为 1–40 个字符。", 400)
    return agent


def _text(value, label, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise WorkbenchError(f"{label}格式不正确或超过长度限制（{maximum}）。", 400)
    text = value.strip()
    if not empty and not text:
        raise WorkbenchError(f"请填写{label}。", 400)
    return text


def _live_publication(identity, *, lock=False):
    rows = PublishedQuestion.objects
    if lock:
        rows = rows.select_for_update()
    publication = rows.filter(pk=identity).first()
    if publication is None:
        raise WorkbenchError("找不到这条入库题目。", 404)
    if publication.status != PublishedQuestion.Status.PUBLISHED:
        raise WorkbenchError("这道题已撤回或被新版替代；请按题库里现在的版本重新提。", 409)
    return publication


def _card(publication):
    """题库里那道题背后的题卡。"""
    if not publication.question_id:
        raise WorkbenchError("这条入库题没有对应的题卡，无法核对改动。", 409)
    question = Question.all_objects.select_related("paper", "group").filter(pk=publication.question_id).first()
    if question is None:
        raise WorkbenchError("这条入库题的题卡已经不在了，无法核对改动。", 409)
    return question


def _face(question):
    """题卡上人能看见的那几项。助手能提的，也就是这些。"""
    return {
        "stem": question.stem or "",
        "options": {key: (question.options or {}).get(key, "") for key in library.OPTION_KEYS},
        "question_type": question.question_type or "",
        "answer": question.answer or "",
        "analysis": question.analysis or "",
        "origin": question.origin or "",
    }


def _check_fields(fields):
    """助手交上来的改动：只认这几个字段，值也要像话。"""
    if not isinstance(fields, dict) or not fields:
        raise WorkbenchError(
            "请给出要改的内容（stem、options、question_type、answer、analysis、origin 里的任意几项）。", 400
        )
    unknown = set(fields) - set(FACE_KEYS)
    if unknown:
        raise WorkbenchError("这些字段助手改不了：" + "、".join(sorted(unknown)) + "。", 400)
    checked = {}
    for key, value in fields.items():
        if key == "options":
            if not isinstance(value, dict) or set(value) - set(library.OPTION_KEYS):
                raise WorkbenchError("选项只能写 A 到 E。", 400)
            if any(not isinstance(item, str) or len(item) > MAX_OPTION for item in value.values()):
                raise WorkbenchError(f"选项内容过长（最多 {MAX_OPTION} 字）。", 400)
            checked[key] = {name: item.strip() for name, item in value.items()}
        elif key == "origin":
            checked[key] = _text(value, "题源", MAX_ORIGIN, empty=True)
        elif key == "question_type":
            from . import qtypes

            if value not in qtypes.DECIDED_TYPES:
                raise WorkbenchError("题型请用：" + "、".join(qtypes.DECIDED_TYPES) + "。", 400)
            checked[key] = value
        else:
            checked[key] = _text(
                value, "答案" if key == "answer" else "解析" if key == "analysis" else "题干", MAX_TEXT
            )
    return checked


def _in_sync(publication, question):
    """题卡和题库里那道题是不是同一份。"""
    return library.content_hash(library.final_content(question)) == publication.content_hash


# ---------------------------------------------------------------- 租约


def expire_leases():
    """租约到期的活自己回到池子里；别的助手能接着领。"""
    now = timezone.now()
    rows = LibraryJob.objects.filter(
        executor=LibraryJob.Executor.ASSISTANT,
        status__in=(LibraryJob.Status.QUEUED, LibraryJob.Status.RUNNING),
        lease_expires_at__isnull=False,
        lease_expires_at__lte=now,
    )
    count = 0
    for job in rows:
        job.agent = ""
        job.claimed_at = None
        job.lease_expires_at = None
        if job.status == LibraryJob.Status.RUNNING:
            job.status = LibraryJob.Status.QUEUED
        job.save(update_fields=["agent", "claimed_at", "lease_expires_at", "status", "updated_at"])
        count += 1
    return count


def claim(payload):
    """助手领一批活。领到的每一道都带到期时间，助手没了活会自己回来。"""
    if not isinstance(payload, dict):
        raise WorkbenchError("领活格式不正确。", 400)
    agent = _agent(payload.get("agent"))
    limit = payload.get("limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise WorkbenchError("一次最多领 50 道。", 400)
    kinds = payload.get("kinds") or ["tags", "answer"]
    if not isinstance(kinds, list) or not kinds or any(kind not in ("tags", "answer") for kind in kinds):
        raise WorkbenchError("kinds 只能包含 tags 和 answer。", 400)
    ids = payload.get("ids")
    if ids is not None:
        if not isinstance(ids, list) or len(ids) > 500:
            raise WorkbenchError("题目 ID 列表最多 500 条。", 400)
        identities = [_uuid(item, "入库题目") for item in ids]
    else:
        identities = None

    expire_leases()
    now = timezone.now()
    deadline = now + timezone.timedelta(seconds=LEASE_SECONDS)
    rows = (
        LibraryJob.objects.select_for_update()
        .filter(
            executor=LibraryJob.Executor.ASSISTANT,
            status__in=(LibraryJob.Status.QUEUED, LibraryJob.Status.RUNNING),
            kind__in=kinds,
        )
        .select_related("publication")
    )
    if identities is not None:
        rows = rows.filter(publication_id__in=identities)
    claimed = []
    with transaction.atomic():
        for job in rows.order_by("created_at")[:limit]:
            # 别人还占着、租约没到的活，不抢。
            if job.lease_expires_at and job.lease_expires_at > now and job.agent and job.agent != agent:
                continue
            job.agent = agent
            job.claimed_at = now
            job.lease_expires_at = deadline
            job.attempts += 1
            job.save(update_fields=["agent", "claimed_at", "lease_expires_at", "attempts", "updated_at"])
            claimed.append(
                {
                    "job_id": str(job.pk),
                    "publication_id": str(job.publication_id),
                    "kind": job.kind,
                    "fingerprint": job.fingerprint,
                    "solution_scope": job.solution_scope,
                    "number": job.publication.number,
                    "source_filename": job.publication.source_filename,
                    "attempts": job.attempts,
                    "expires_at": deadline.isoformat(),
                }
            )
    return {
        "agent": agent,
        "claimed": claimed,
        "count": len(claimed),
        "lease_seconds": LEASE_SECONDS,
        "message": (
            f"领到 {len(claimed)} 道活。请现在就看原卷把活干完并提交；"
            f"超过 {LEASE_SECONDS // 60} 分钟没交回来，活会回到池子里，别的助手能接着领。"
        ),
    }


def release(payload):
    """助手把活交回池子。别人能接着领。"""
    if not isinstance(payload, dict):
        raise WorkbenchError("交回格式不正确。", 400)
    agent = _agent(payload.get("agent"))
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 500:
        raise WorkbenchError("请给出要交回的任务 ID（最多 500 条）。", 400)
    identities = [_uuid(item, "任务") for item in ids]
    with transaction.atomic():
        rows = list(LibraryJob.objects.select_for_update().filter(pk__in=identities))
        if len(rows) != len(identities):
            raise WorkbenchError("有任务不存在；未交回其他任务。", 404)
        if any(job.agent and job.agent != agent for job in rows):
            raise WorkbenchError("有任务是别的助手领的，未交回。", 409)
        count = 0
        for job in rows:
            if job.status not in (LibraryJob.Status.QUEUED, LibraryJob.Status.RUNNING):
                continue
            job.agent = ""
            job.claimed_at = None
            job.lease_expires_at = None
            if job.status == LibraryJob.Status.RUNNING:
                job.status = LibraryJob.Status.QUEUED
            job.save(update_fields=["agent", "claimed_at", "lease_expires_at", "status", "updated_at"])
            count += 1
    return {"released": count, "agent": agent}


# ---------------------------------------------------------------- 提案


def _change_set(before, after):
    """一次「改法」本身：哪些字段要改成什么。和当时题面长什么样无关。

    这样比较才公平：同一个改法，不管是在题面还是被改过之后的题面上提的，
    都认得出是同一个。
    """
    change = {}
    for key in FACE_KEYS:
        old = before.get(key)
        new = after.get(key)
        if (old or "") != (new or ""):
            change[key] = new
    return change


def _rejected_change(publication, agent, change):
    """这个助手是不是已经提过一模一样的改法、老师没采纳。

    找到了就返回那条被否的提案；没有就返回 None。
    """
    if not change:
        return None
    rows = AssistantProposal.objects.filter(
        publication=publication, agent=agent, status=AssistantProposal.Status.REJECTED
    ).order_by("-decided_at", "-created_at")
    for row in rows:
        if _change_set(row.before or {}, row.payload or {}) == change:
            return row
    return None


def propose(payload):
    """助手交一份题面改动的提案。题面不动，等人点头。"""
    if not isinstance(payload, dict) or "publication_id" not in payload or "agent" not in payload:
        raise WorkbenchError("提交提案需给出入库题目和实际助手名称。", 400)
    identity = _uuid(payload["publication_id"], "入库题目")
    agent = _agent(payload["agent"])
    fields = _check_fields(payload.get("fields"))
    with transaction.atomic():
        publication = _live_publication(identity, lock=True)
        question = _card(publication)
        if not _in_sync(publication, question):
            raise WorkbenchError("题卡后来改过、还没重新入库；请先核对当前题卡并入库，再提改动。", 409)
        fingerprint = library.generation_fingerprint(publication.content or {}, publication.pk)
        before = _face(question)
        proposed = {**before, **fields}
        if "options" in fields:
            # 选项是整体：只给 A，意思是「A 改了，其余照旧」。
            # 直接替换会把 B、C、D 抹掉 —— 老师点采纳就少内容了。
            merged = dict(before.get("options") or {})
            merged.update(fields["options"])
            proposed["options"] = merged
        if proposed == before:
            raise WorkbenchError("提议的改动和题库里现在的一模一样，不用提交。", 400)
        # 同一个助手提过、老师没采纳的同一个改法，别再提一遍：
        # 老师已经说过了，再提只是让待核对里多一条噪声。
        refused = _rejected_change(publication, agent, _change_set(before, proposed))
        if refused is not None:
            reason = (refused.message or "").strip()
            if not reason or reason == "老师不采纳这条改动。":
                detail = "你提过这个改法，老师上次没有采纳。"
            else:
                detail = f"你提过这个改法，老师上次没有采纳，理由是：{reason}"
            raise WorkbenchError(detail + " 换个改法再提。", 409)
        # 同一道题只留一条待核对的提案：助手再提一次，以新的一条为准。
        AssistantProposal.objects.filter(publication=publication, status=AssistantProposal.Status.PENDING).update(
            status=AssistantProposal.Status.STALE, message="助手又提了一次，以新的一条为准。", updated_at=timezone.now()
        )
        proposal = AssistantProposal.objects.create(
            publication=publication,
            question=question,
            agent=agent,
            kind=AssistantProposal.Kind.TEXT,
            payload=proposed,
            before=before,
            fingerprint=fingerprint,
            status=AssistantProposal.Status.PENDING,
            message="助手交上来的题面改动，等你点采纳。",
        )
    return {"proposal": proposal_json(proposal), "message": "提案已交上来，题面还没动；等你在题库页点采纳。"}


def proposal_json(proposal):
    changed = []
    before = proposal.before or {}
    after = proposal.payload or {}
    for key in FACE_KEYS:
        if before.get(key) != after.get(key):
            changed.append(key)
    publication = proposal.publication
    return {
        "id": str(proposal.pk),
        "publication_id": str(publication.pk),
        "number": publication.number,
        "source_filename": publication.source_filename,
        "agent": proposal.agent,
        "kind": proposal.kind,
        "status": proposal.status,
        "message": proposal.message,
        "changed": changed,
        "before": {key: before.get(key) for key in FACE_KEYS},
        "after": {key: after.get(key) for key in FACE_KEYS},
        "created_at": proposal.created_at.isoformat(),
        "decided_at": proposal.decided_at.isoformat() if proposal.decided_at else None,
        "decided_by": proposal.decided_by,
    }


def decide(payload):
    """人点采纳或不采纳。采纳前题面一个字不动。"""
    if not isinstance(payload, dict) or "proposal_id" not in payload:
        raise WorkbenchError("请给出要处理的提案。", 400)
    identity = _uuid(payload["proposal_id"], "提案")
    action = payload.get("action")
    if action not in ("accept", "reject"):
        raise WorkbenchError("action 只能是 accept 或 reject。", 400)
    by = _agent(payload.get("by") or "老师")
    stale_reason = ""
    result = {}
    with transaction.atomic():
        proposal = AssistantProposal.objects.select_for_update().filter(pk=identity).first()
        if proposal is None:
            raise WorkbenchError("找不到这条提案。", 404)
        if proposal.status != AssistantProposal.Status.PENDING:
            raise WorkbenchError("这条提案已经处理过了。", 409)
        publication = _live_publication(proposal.publication_id, lock=True)
        question = _card(publication)
        if action == "reject":
            proposal.status = AssistantProposal.Status.REJECTED
            proposal.decided_by = by
            proposal.decided_at = timezone.now()
            proposal.message = payload.get("reason") or "老师不采纳这条改动。"
            proposal.save(update_fields=["status", "decided_by", "decided_at", "message", "updated_at"])
            result = {
                "proposal": proposal_json(proposal),
                "applied": False,
                "message": "已记下不采纳；这条不会再出现在待核对里。",
            }
        else:
            # 采纳：题面在老师点下去的那一刻才动。
            if not _in_sync(publication, question):
                stale_reason = "题卡后来改过，这条提议作废；请让助手按现在的题面重提。"
            elif library.generation_fingerprint(publication.content or {}, publication.pk) != proposal.fingerprint:
                stale_reason = "题库里的版本已经变了，这条提议作废；请让助手按现在的版本重提。"
            else:
                from . import views

                before = _face(question)
                after = proposal.payload or {}
                views._apply_text_edit(
                    question, {key: after.get(key, before.get(key)) for key in FACE_KEYS}, ("ai", proposal.agent)
                )
                note = f"助手 {proposal.agent} 改了题面，请对照原卷核对后再通过"
                if note not in (question.flags or []):
                    question.flags = list(question.flags or []) + [note]
                question.state = Question.State.YELLOW
                question.content_revision += 1
                question.save()
                proposal.status = AssistantProposal.Status.ACCEPTED
                proposal.decided_by = by
                proposal.decided_at = timezone.now()
                proposal.message = "已采纳；题卡已改成助手提议的样子，卡上留了一条提醒，请对照原卷核对后再通过。"
                proposal.save(update_fields=["status", "decided_by", "decided_at", "message", "updated_at"])
                result = {
                    "proposal": proposal_json(proposal),
                    "applied": True,
                    "message": "已采纳：题卡改成助手提议的样子，卡上留了提醒；对照原卷核对后再点通过，题库才会更新。",
                }
            if stale_reason:
                # 作废这件事要留下来，所以先写库，再往外说 —— 不能跟着报错一起回滚。
                proposal.status = AssistantProposal.Status.STALE
                proposal.message = stale_reason
                proposal.decided_at = timezone.now()
                proposal.save(update_fields=["status", "message", "decided_at", "updated_at"])
    if stale_reason:
        raise WorkbenchError(stale_reason, 409)
    return result


# ---------------------------------------------------------------- 老师在场


def teacher_now():
    """老师此刻在看哪道题、有没有没保存的改动。"""
    cutoff = timezone.now() - timezone.timedelta(seconds=PRESENCE_SECONDS)
    row = TeacherActivity.objects.filter(updated_at__gte=cutoff).select_related("question", "paper").first()
    if row is None:
        return {"here": False, "message": "老师这会儿没在软件里改题。"}
    return {
        "here": True,
        "paper_id": str(row.paper_id),
        "paper_name": row.paper.display_name if hasattr(row.paper, "display_name") else row.paper.filename,
        "question_id": str(row.question_id),
        "number": row.question.number,
        "state": row.state,
        "state_text": "正在改字（有没保存的改动）" if row.state == "editing" else "正在看这道题",
        "updated_at": row.updated_at.isoformat(),
        "message": "老师正在动这道题，别去领它、也别提改法。",
    }


def set_presence(payload):
    """题库页/审核页报一声：老师此刻在这道题上做什么。"""
    if not isinstance(payload, dict):
        raise WorkbenchError("在场信息格式不正确。", 400)
    identity = payload.get("question_id")
    if identity is None:
        TeacherActivity.objects.all().delete()
        return {"here": False, "message": "老师已经离开软件。"}
    question_id = _number(identity, "题目")
    state = payload.get("state", "viewing")
    if state not in ("viewing", "editing"):
        raise WorkbenchError("state 只能是 viewing 或 editing。", 400)
    question = Question.objects.select_related("paper").filter(pk=question_id).first()
    if question is None:
        raise WorkbenchError("找不到这道题。", 404)
    TeacherActivity.objects.update_or_create(question=question, defaults={"paper": question.paper, "state": state})
    # 别的题上残留的旧记录清掉，免得助手以为老师还在那儿。
    TeacherActivity.objects.exclude(question=question).delete()
    return teacher_now()


def whereami():
    """助手问一句：老师在哪儿、我刚提的有没有被否决。"""
    expire_leases()
    rejected = AssistantProposal.objects.filter(status=AssistantProposal.Status.REJECTED).select_related("publication")[
        :10
    ]
    return {
        "teacher": teacher_now(),
        "rejected": [
            {
                "proposal_id": str(row.pk),
                "publication_id": str(row.publication_id),
                "number": row.publication.number,
                "agent": row.agent,
                "message": row.message,
                "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            }
            for row in rejected
        ],
        "message": "别去动老师正在改的题；不采纳过的改法不要再提一遍。",
    }


# ---------------------------------------------------------------- 工作台


def workbench(limit=50):
    """题库页里那块「助手工作台」读的东西：一屏看完助手交上来了什么。"""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise WorkbenchError("一次最多看 200 条。", 400)
    expire_leases()
    now = timezone.now()
    proposals = list(
        AssistantProposal.objects.filter(status=AssistantProposal.Status.PENDING)
        .select_related("publication")
        .order_by("-created_at")[:limit]
    )
    pending_tags = LibraryTagSuggestion.objects.filter(status=LibraryTagSuggestion.Status.PENDING).count()
    pending_answers = PublishedQuestion.objects.filter(extras__has_key="ai_answer").count()
    running = list(
        LibraryJob.objects.filter(
            executor=LibraryJob.Executor.ASSISTANT,
            status__in=(LibraryJob.Status.QUEUED, LibraryJob.Status.RUNNING),
            lease_expires_at__isnull=False,
            lease_expires_at__gt=now,
        ).select_related("publication")[:limit]
    )
    claimed = LibraryJob.objects.filter(executor=LibraryJob.Executor.ASSISTANT, attempts__gt=0).count()
    done = LibraryJob.objects.filter(executor=LibraryJob.Executor.ASSISTANT, status=LibraryJob.Status.DONE).count()
    rejected = AssistantProposal.objects.filter(status=AssistantProposal.Status.REJECTED).count()
    accepted = AssistantProposal.objects.filter(status=AssistantProposal.Status.ACCEPTED).count()
    return {
        "pending": {
            "text": len(proposals),
            "tags": pending_tags,
            "answer": pending_answers,
            "total": len(proposals) + pending_tags + pending_answers,
        },
        "proposals": [proposal_json(row) for row in proposals],
        "running": [
            {
                "job_id": str(job.pk),
                "publication_id": str(job.publication_id),
                "number": job.publication.number,
                "kind": job.kind,
                "agent": job.agent,
                "attempts": job.attempts,
                "expires_at": job.lease_expires_at.isoformat() if job.lease_expires_at else None,
                "source_filename": job.publication.source_filename,
            }
            for job in running
        ],
        "counts": {"claimed": claimed, "done": done, "accepted": accepted, "rejected": rejected},
        "teacher": teacher_now(),
        "message": "助手交上来的东西都在这儿；采纳前题面一个字不动。",
    }


def proposal_count():
    return AssistantProposal.objects.filter(status=AssistantProposal.Status.PENDING).count()
