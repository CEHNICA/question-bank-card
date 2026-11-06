"""坏掉的标签建议不能把升级堵死。

老师的题库里躺着 38 条「生成失败」的标签建议，它们指着的任务行早没了。这次的
迁移要给任务表加字段，SQLite 只能把整张表重建一遍，重建收尾 Django 会跑一次
整库外键校验 —— 撞上这 38 条坏引用就直接报错，升级卡死，软件装不上。

这里照着老师的库造出同样的坏引用，确认升级能顺利跑完，而且这 38 条建议本身
一条都不能少（只是跟已经不在的任务断开）。
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone

DEAD_JOB_ID = "00000000-0000-4000-8000-0000000000ff"
LIVE_JOB_ID = "00000000-0000-4000-8000-0000000000aa"
SUGGESTION_ID = "11111111-1111-4111-8111-111111111111"


class DeadTagSuggestionMigrationTests(TransactionTestCase):
    """从 0029 升到 0030：坏引用要先修好，升级才走得完。"""

    migrate_from = [("core", "0029_realign_untouched_suggestions")]
    migrate_to = [("core", "0030_assistant_proposals_and_leases")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _state(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        return executor, executor.loader.project_state(self.migrate_from).apps

    def _publication(self, apps):
        PublishedQuestion = apps.get_model("core", "PublishedQuestion")
        return PublishedQuestion.objects.create(
            source_filename="synthetic.pdf",
            number=1,
            question_type="free_response",
            version=1,
            content={"stem": "题干"},
            content_hash="c" * 64,
        )

    def _insert_suggestion(self, publication_id, job_id, status):
        """用原生 SQL 造一条坏引用。

        SQLite 现在是拦外键的（外键一开，写进去就报错），而老师那本题库里的
        坏引用是当年还没拦的时候写下的 —— 所以这里得先把外键关掉再写。
        """
        now = timezone.now().isoformat(sep=" ")
        # SQLite 上 UUID 存的是不带横线的 32 位写法（uuid.hex），照抄 ORM 的值会写歪。
        job_id = job_id.replace("-", "")
        publication_id = str(publication_id).replace("-", "")
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
            try:
                cursor.execute(
                    """
                    INSERT INTO core_librarytagsuggestion (
                        id, question_fingerprint, taxonomy_revision, assignment_revision,
                        prompt_version, method_source, suggestions, status, message,
                        generation_source, created_at, updated_at, publication_id,
                        catalogue_snapshot, job_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        SUGGESTION_ID.replace("-", ""),
                        "f" * 64,
                        1,
                        0,
                        "tags-v3-hierarchy",
                        "{}",
                        "[]",
                        status,
                        "生成失败。",
                        "旧版 AI",
                        now,
                        now,
                        publication_id,
                        "{}",
                        job_id,
                    ],
                )
            finally:
                cursor.execute("PRAGMA foreign_keys = ON")

    def test_upgrade_finishes_when_a_suggestion_points_at_a_deleted_job(self):
        executor, old = self._state()
        publication = self._publication(old)
        self._insert_suggestion(publication.pk, DEAD_JOB_ID, "failed")

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)

        new = executor.loader.project_state(self.migrate_to).apps
        Suggestion = new.get_model("core", "LibraryTagSuggestion")
        row = Suggestion.objects.get(pk="11111111-1111-4111-8111-111111111111")
        self.assertIsNone(row.job_id)
        self.assertEqual(row.status, "failed")
        self.assertEqual(row.message, "生成失败。")
        self.assertEqual(Suggestion.objects.count(), 1)
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA foreign_key_check")
            self.assertEqual(cursor.fetchall(), [])

    def test_upgrade_keeps_the_link_when_the_job_is_still_there(self):
        executor, old = self._state()
        publication = self._publication(old)
        LibraryJob = old.get_model("core", "LibraryJob")
        LibraryJob.objects.create(id=LIVE_JOB_ID, publication=publication, kind="tags", status="done")
        self._insert_suggestion(publication.pk, LIVE_JOB_ID, "pending")

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)

        new = executor.loader.project_state(self.migrate_to).apps
        Suggestion = new.get_model("core", "LibraryTagSuggestion")
        row = Suggestion.objects.get(pk="11111111-1111-4111-8111-111111111111")
        self.assertEqual(str(row.job_id), LIVE_JOB_ID)
        self.assertEqual(row.status, "pending")
