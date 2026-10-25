"""Read-only health check over the teacher's real question bank.

Looks for questions that no count can explain — not approved, not deleted, and
not in any pending bucket — plus figures claimed by more than one question.
Never writes.
"""

import json
import os
import sqlite3
from collections import Counter, defaultdict

DB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "QuestionBankCard", "db.sqlite3")
CN_TZ = "+8 hours"


def question_number(cursor, question_id):
    """Turn the internal question id into the number printed on the card."""
    if question_id is None:
        return "?"
    row = cursor.execute("select number from core_question where id = ?", (question_id,)).fetchone()
    return row[0] if row else "?"


def main():
    if not os.path.isfile(DB):
        print("找不到题库：%s" % DB)
        print("这个检查要在装了题有据并导入过资料的 Windows 上跑。")
        return 1
    connection = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    cursor = connection.cursor()

    papers = cursor.execute("select id, filename, status from core_paper order by filename").fetchall()
    print("== 每份卷子的题数对账 ==")
    for paper_id, filename, _status in papers:
        rows = cursor.execute(
            "select state, approved_at is not null as ok, deleted_at is not null as gone, count(*) "
            "from core_question where paper_id = ? group by state, ok, gone",
            (paper_id,),
        ).fetchall()
        tally = Counter()
        for state, ok, gone, count in rows:
            if gone:
                tally["已删除"] += count
            elif ok:
                tally["已入库"] += count
            else:
                tally["待办:" + str(state)] += count
        total = sum(tally.values())
        print("  %-44s 共%4d  %s" % (filename[:44], total, "  ".join("%s=%d" % kv for kv in sorted(tally.items()))))

    print()
    print("== 既没入库、也没删掉、也不算待核的题 ==")
    found = 0
    for paper_id, filename, _status in papers:
        odd = cursor.execute(
            "select number, state, question_type, approved_at, deleted_at from core_question "
            "where paper_id = ? and approved_at is null and deleted_at is null "
            "and state not in ('green', 'yellow', 'red', 'waiting', 'reading')",
            (paper_id,),
        ).fetchall()
        for number, state, kind, _approved_at, _deleted_at in odd:
            found += 1
            print("  %s 第 %s 题  状态=%s 题型=%s" % (filename[:36], number, state, kind))
    if not found:
        print("  没有")

    print()
    print("== 已被入库、但题目里写着有内容没并进来 ==")
    for paper_id, filename, _status in papers:
        notes = cursor.execute("select notes from core_paper where id = ?", (paper_id,)).fetchone()[0]
        try:
            values = json.loads(notes) if isinstance(notes, str) else (notes or [])
        except Exception:
            values = []
        risky = [n for n in values if "待核查" in str(n) or "未并入" in str(n)]
        if risky:
            approved = cursor.execute(
                "select count(*) from core_question where paper_id = ? and approved_at is not null", (paper_id,)
            ).fetchone()[0]
            print("  %s（已入库 %d 题）" % (filename[:40], approved))
            for note in risky:
                print("     " + str(note)[:120])

    print()
    print("== 同一块图被两道题同时认领 ==")
    claims = defaultdict(list)
    for paper_id, _filename, _status in papers:
        for _qid, number, regions in cursor.execute(
            "select id, number, regions from core_question where paper_id = ?", (paper_id,)
        ):
            try:
                rows = json.loads(regions) if isinstance(regions, str) else (regions or [])
            except Exception:
                rows = []
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                page = row.get("page")
                box = row.get("bbox") or row.get("box") or row.get("rect")
                if page is None or not box:
                    continue
                claims[(paper_id, page, tuple(round(float(v), 1) for v in box))].append(number)
    duplicates = {key: value for key, value in claims.items() if len(value) > 1}
    if duplicates:
        for (paper_id, page, box), numbers in duplicates.items():
            name = cursor.execute("select filename from core_paper where id = ?", (paper_id,)).fetchone()[0]
            print("  %s 第 %s 页 %s → 第 %s 题" % (name[:36], page, box, numbers))
    else:
        print("  题目范围里没有")

    # The region check above cannot see this one: when a figure lands on the
    # boundary between two questions the conflict is recorded on the paper's
    # processing plan, not on either question. Without this section the tool
    # would report "no figure conflicts" while one is sitting in the bank.
    #
    # Shape (verified against the bank, 2026-10):
    #   figure_assignment_audit = {"schema": 1, "figures": [...], "conflicts": {...}}
    #   each figure = {"key", "page_idx", "bbox", "claims": [...], "status"}
    #
    # Only "conflict" is worth a teacher's attention: two questions both believe
    # the same rectangle is theirs, and the app blocks those cards until a person
    # decides. "unassigned" is the normal outcome for every formula and line of
    # text the cutter offered up as a candidate and then correctly dropped, so it
    # is counted rather than listed.
    print()
    print("== 切题时记下的配图冲突（两题争同一块图）==")
    conflicts = 0
    dropped = 0
    for paper_id, filename, _status in papers:
        raw = cursor.execute("select processing_plan from core_paper where id = ?", (paper_id,)).fetchone()[0]
        try:
            plan = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            plan = {}
        audit = (plan or {}).get("figure_assignment_audit") or {}
        figures = audit.get("figures") if isinstance(audit, dict) else None
        mine = 0
        for figure in figures if isinstance(figures, list) else []:
            if not isinstance(figure, dict):
                continue
            state = str(figure.get("status", "")).lower()
            if state == "unassigned":
                dropped += 1
            if state != "conflict":
                continue
            conflicts += 1
            mine += 1
            where = "%s 页 %s" % (figure.get("page_idx", "?"), figure.get("key") or figure.get("bbox") or "?")
            owners = []
            for claim in figure.get("claims") or []:
                if isinstance(claim, dict):
                    qid = claim.get("question_id") or claim.get("id")
                    mark = "（这道已经锁定，改不了）" if claim.get("protected") else ""
                    owners.append("第 %s 题（记录 %s）%s" % (question_number(cursor, qid), qid, mark))
                else:
                    owners.append("题目记录 %s" % claim)
            print("  %s  %s%s" % (filename[:36], where, ("  ← " + "、".join(owners)) if owners else ""))
    if not conflicts:
        print("  没有")
    print("  （另有 %d 处候选图无人认领：那是切题时扫到的公式和文字被正常丢弃，不是问题）" % dropped)
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
