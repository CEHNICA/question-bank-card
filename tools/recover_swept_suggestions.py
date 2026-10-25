"""Bring back the pending proposals that a directory change retired by accident.

On 2026-10-14 the teacher added two labels in the morning. Each addition bumped the
directory revision, and the old code retired every pending proposal on sight, so 439
batches of AI suggestions became un-acceptable overnight. None of them actually pointed
at anything the directory no longer has, so they are safe to bring back.

Dry run by default. Pass --apply to write. The live database is copied first and every
touched row is recorded so the change can be reversed from the backup.
"""

import argparse
import json
import os
import shutil
import socket
import sqlite3
import sys
from datetime import UTC, datetime

ROOT = os.path.join(os.environ["LOCALAPPDATA"], "QuestionBankCard")
DB = os.path.join(ROOT, "db.sqlite3")
BACKUPS = os.path.join(ROOT, "backups")

SWEEP_MESSAGE = "标签目录已变化；旧建议保留在 AI 标签记录中。"
RECOVER_MESSAGE = "标签目录已变化，旧建议已按本次改动重新核对。"
SWEEP_BUCKET = "2026-10-13 22:43"


def port_is_free(port=8768):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        return probe.connect_ex(("127.0.0.1", port)) != 0
    finally:
        probe.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write to the live database")
    args = parser.parse_args()

    if not port_is_free():
        print("软件正在运行，请先关掉题有据再试。")
        return 2
    if not os.path.exists(DB):
        print("找不到题库文件:", DB)
        return 2

    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()
    revision = cursor.execute("select revision from core_librarytagcatalogue").fetchone()[0]

    targets = cursor.execute(
        "select id, publication_id, taxonomy_revision, prompt_version, status, message, "
        "resolved_at, updated_at, job_id from core_librarytagsuggestion "
        "where status = 'stale' and message = ? and substr(resolved_at, 1, 16) = ? "
        "order by id",
        (SWEEP_MESSAGE, SWEEP_BUCKET),
    ).fetchall()
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")
    print("标签目录当前版本:", revision)
    print("待捞回的批次:", len(targets))
    print("其中带任务的:", sum(1 for row in targets if row["job_id"]))
    print()
    for row in targets[:3]:
        print(
            "  例：题目 %s，生成时的目录版本 %s，提示版本 %s"
            % (row["publication_id"], row["taxonomy_revision"], row["prompt_version"])
        )
    if len(targets) > 3:
        print("  …其余 %d 批同型" % (len(targets) - 3))
    print()

    if not args.apply:
        print("这是干跑，没有改动任何东西。加 --apply 才会真正写回。")
        connection.close()
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(BACKUPS, exist_ok=True)
    backup = os.path.join(BACKUPS, "pre-recover-%s.sqlite3" % stamp)
    shutil.copy2(DB, backup)
    print("已备份到:", backup)

    ledger = {
        "created_at": stamp,
        "database": DB,
        "backup": backup,
        "catalogue_revision": revision,
        "suggestions": [dict(row) for row in targets],
    }
    ledger_path = os.path.join(ROOT, "suggestion-recover-%s.json" % stamp)
    with open(ledger_path, "w", encoding="utf-8") as handle:
        json.dump(ledger, handle, ensure_ascii=False, indent=2)
    print("撤销清单:", ledger_path)
    print()

    with connection:
        cursor.execute(
            "update core_librarytagsuggestion set status = 'pending', message = ?, "
            "resolved_at = null, taxonomy_revision = ?, updated_at = ? "
            "where status = 'stale' and message = ? and substr(resolved_at, 1, 16) = ?",
            (RECOVER_MESSAGE, revision, now, SWEEP_MESSAGE, SWEEP_BUCKET),
        )
        changed = connection.total_changes
        print("已写回:", changed, "批")

    remaining = cursor.execute(
        "select count(*) from core_librarytagsuggestion where status = 'stale' and message = ?", (SWEEP_MESSAGE,)
    ).fetchone()[0]
    pending = cursor.execute("select count(*) from core_librarytagsuggestion where status = 'pending'").fetchone()[0]
    print("仍是历史记录的旧建议:", remaining)
    print("现在待核对的批次:", pending)
    connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
