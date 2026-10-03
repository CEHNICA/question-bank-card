"""Long-PDF chunk execution and retry tests; no network calls are made."""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor, wait as real_wait
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings

from . import mineru, pipeline, readers
from .account_pool import AccountPoolError, reset_account_pools
from .mineru import MineruError
from .models import Block, ImportChunk, Paper


class LongPdfChunkPipelineTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        env = mock.patch.dict(
            "os.environ", {"MINERU_TOKEN": "test-token", "MINERU_TOKENS_JSON": ""},
        )
        env.start()
        self.addCleanup(env.stop)
        reset_account_pools()
        self.addCleanup(reset_account_pools)

        self.paper = Paper.objects.create(
            filename="四页书.pdf",
            kind="pdf",
            sha256="a" * 64,
            source_path="",
            pages=[
                {"page_idx": page, "width": 1000, "height": 1400}
                for page in range(4)
            ],
        )
        self.folder = self.temp / str(self.paper.pk)
        self.folder.mkdir()
        self.render = self.folder / "source.pdf"
        self.render.write_bytes(b"original four-page pdf")
        self.paper.source_path = str(self.render)
        self.paper.save(update_fields=["source_path"])
        ImportChunk.objects.bulk_create([
            ImportChunk(
                paper=self.paper,
                sequence=1,
                source_page_start=1,
                source_page_end=2,
                page_map=[1, 2],
            ),
            ImportChunk(
                paper=self.paper,
                sequence=2,
                source_page_start=3,
                source_page_end=4,
                page_map=[3, 4],
            ),
        ])

    def _legacy_plan(self, ranges):
        self.paper.import_chunks.all().delete()
        self.paper.pages = [{"page_idx": page, "width": 1000, "height": 1400}
                            for page in range(ranges[-1][1])]
        self.paper.save(update_fields=["pages"])
        folder = self.folder / "chunks"
        folder.mkdir()
        rows = []
        for sequence, (start, end, status) in enumerate(ranges, start=1):
            archive = folder / f"chunk_{sequence:03d}.zip"
            archive.with_suffix(".pdf").write_bytes(f"original slice {start}-{end}".encode())
            if status == ImportChunk.Status.PARSED:
                archive.write_bytes(f"completed cache {start}-{end}".encode())
            rows.append(ImportChunk.objects.create(
                paper=self.paper, sequence=sequence, source_page_start=start, source_page_end=end,
                page_map=list(range(start, end + 1)), status=status, artifact_path=str(archive),
                attempts=7, sha256="d" * 64 if status == ImportChunk.Status.PARSED else "",
            ))
        return rows

    @staticmethod
    def _legacy_blocks(archive, pages):
        if archive.read_bytes() == b"corrupt":
            raise MineruError("fixture cache is corrupt")
        return [{"seq": index, "page_idx": page, "type": "text", "bbox": [20, 30, 900, 80],
                 "text": f"{archive.name} page {page}"}
                for index, page in enumerate(sorted({0, pages - 1}))]

    def test_legacy_failed_600_page_chunk_is_split_without_reuploading_later_completed_cache(self):
        rows = self._legacy_plan([(1, 600, ImportChunk.Status.FAILED), (601, 800, ImportChunk.Status.PARSED)])
        cached = rows[1]
        archive = Path(cached.artifact_path)
        old_cache, old_slice, original = archive.read_bytes(), archive.with_suffix(".pdf").read_bytes(), self.render.read_bytes()
        with mock.patch.object(pipeline, "write_pdf_slice", side_effect=self._slice) as cut, \
                mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=self._successful_extract) as extract, \
                mock.patch.object(pipeline, "load_blocks", side_effect=self._legacy_blocks):
            blocks = pipeline._chunk_blocks(self.paper, self.render)
            chunks = list(self.paper.import_chunks.order_by("sequence"))
            self.assertEqual([(chunk.source_page_start, chunk.source_page_end) for chunk in chunks],
                             [(1, 200), (201, 400), (401, 600), (601, 800)])
            self.assertEqual([call.args[2] for call in extract.call_args_list], [200, 200, 200])
            self.assertEqual([(call.args[2], call.args[3]) for call in cut.call_args_list],
                             [(0, 200), (200, 400), (400, 600)])
            self.assertEqual([block["page_idx"] for block in blocks], [0, 199, 200, 399, 400, 599, 600, 799])
            extract.reset_mock()
            cut.reset_mock()
            self.assertEqual(pipeline._chunk_blocks(self.paper, self.render), blocks)
            extract.assert_not_called()
            cut.assert_not_called()
        cached.refresh_from_db()
        self.assertEqual((cached.pk, cached.source_page_start, cached.source_page_end, cached.page_map,
                          cached.status, cached.sha256, cached.attempts, cached.artifact_path),
                         (rows[1].pk, 601, 800, list(range(601, 801)), ImportChunk.Status.PARSED,
                          "d" * 64, 7, str(archive)))
        self.assertEqual(archive.read_bytes(), old_cache)
        self.assertEqual(archive.with_suffix(".pdf").read_bytes(), old_slice)
        self.assertEqual(self.render.read_bytes(), original)

    def test_legacy_completed_600_page_cache_is_kept_while_only_unfinished_201_pages_are_replanned(self):
        rows = self._legacy_plan([(1, 600, ImportChunk.Status.PARSED), (601, 801, ImportChunk.Status.FAILED)])
        archive = Path(rows[0].artifact_path)
        before = archive.read_bytes()
        with mock.patch.object(pipeline, "write_pdf_slice", side_effect=self._slice), \
                mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=self._successful_extract) as extract, \
                mock.patch.object(pipeline, "load_blocks", side_effect=self._legacy_blocks):
            blocks = pipeline._chunk_blocks(self.paper, self.render)
        self.assertEqual([call.args[2] for call in extract.call_args_list], [200, 1])
        self.assertEqual([block["page_idx"] for block in blocks], [0, 599, 600, 799, 800])
        rows[0].refresh_from_db()
        self.assertEqual((rows[0].source_page_start, rows[0].source_page_end, rows[0].artifact_path), (1, 600, str(archive)))
        self.assertEqual(archive.read_bytes(), before)

    def test_corrupt_completed_legacy_cache_is_reported_without_replanning_or_uploading(self):
        rows = self._legacy_plan([(1, 600, ImportChunk.Status.PARSED), (601, 801, ImportChunk.Status.FAILED)])
        archive = Path(rows[0].artifact_path)
        archive.write_bytes(b"corrupt")
        before = list(self.paper.import_chunks.order_by("sequence").values())
        with mock.patch.object(pipeline, "load_blocks", side_effect=self._legacy_blocks), \
                mock.patch.object(pipeline, "request_extract_file_from_pool") as extract, \
                self.assertRaisesRegex(MineruError, "任务备份恢复解析缓存"):
            pipeline._chunk_blocks(self.paper, self.render)
        extract.assert_not_called()
        self.assertEqual(list(self.paper.import_chunks.order_by("sequence").values()), before)
        self.assertEqual(archive.read_bytes(), b"corrupt")

    def _set_chunk_count(self, count: int) -> None:
        self.paper.import_chunks.all().delete()
        self.paper.pages = [
            {"page_idx": page, "width": 1000, "height": 1400}
            for page in range(count * 2)
        ]
        self.paper.save(update_fields=["pages"])
        ImportChunk.objects.bulk_create([
            ImportChunk(
                paper=self.paper,
                sequence=sequence,
                source_page_start=(sequence - 1) * 2 + 1,
                source_page_end=sequence * 2,
                page_map=list(range((sequence - 1) * 2 + 1, sequence * 2 + 1)),
            )
            for sequence in range(1, count + 1)
        ])

    @staticmethod
    def _slice(_source: Path, target: Path, start: int, end: int) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"slice:{start}:{end}".encode())

    @staticmethod
    def _blocks(archive: Path, page_count: int) -> list[dict]:
        assert page_count == 2
        chunk = int(archive.stem.rsplit("_", 1)[1])
        return [
            {
                "seq": chunk * 100 + local_page,
                "type": "text",
                "page_idx": local_page,
                "bbox": [20, 30 + local_page * 100, 900, 80 + local_page * 100],
                "text": f"chunk {chunk}, local page {local_page}",
            }
            for local_page in range(2)
        ]

    @staticmethod
    def _successful_extract(_source, archive, _page_count, heartbeat=None, cancel=None):
        archive.write_bytes(b"mock MinerU archive")
        if heartbeat:
            heartbeat()

    def test_two_chunks_are_cut_and_local_pages_are_mapped_back_to_original_pdf(self):
        with (
            mock.patch.object(pipeline, "write_pdf_slice", side_effect=self._slice) as cut,
            mock.patch.object(
                pipeline, "request_extract_file_from_pool", side_effect=self._successful_extract,
            ) as extract,
            mock.patch.object(pipeline, "load_blocks", side_effect=self._blocks),
        ):
            merged = pipeline._chunk_blocks(self.paper, self.render)

        self.assertEqual([block["page_idx"] for block in merged], [0, 1, 2, 3])
        self.assertEqual([block["seq"] for block in merged], [0, 1, 2, 3])
        self.assertEqual(
            [block["text"] for block in merged],
            [
                "chunk 1, local page 0", "chunk 1, local page 1",
                "chunk 2, local page 0", "chunk 2, local page 1",
            ],
        )
        self.assertEqual(
            sorted((call.args[2], call.args[3]) for call in cut.call_args_list),
            [(0, 2), (2, 4)],
        )
        self.assertEqual([call.args[2] for call in extract.call_args_list], [2, 2])
        chunks = list(self.paper.import_chunks.order_by("sequence"))
        self.assertEqual([chunk.status for chunk in chunks], [ImportChunk.Status.PARSED] * 2)
        self.assertEqual([chunk.attempts for chunk in chunks], [1, 1])
        self.paper.refresh_from_db()
        self.assertEqual((self.paper.progress, self.paper.total), (2, 2))

    def test_failed_chunk_is_isolated_and_retry_reuses_the_successful_archive(self):
        self._set_chunk_count(3)
        requests: list[int] = []
        fail_second = True

        def extract(source, archive, _page_count, heartbeat=None, cancel=None):
            nonlocal fail_second
            sequence = int(source.stem.rsplit("_", 1)[1])
            requests.append(sequence)
            if sequence == 2 and fail_second:
                raise MineruError("second chunk failed")
            if sequence == 3 and fail_second:
                time.sleep(0.04)
            archive.write_bytes(f"archive {sequence}".encode())
            if heartbeat:
                heartbeat()

        with (
            mock.patch.object(pipeline, "write_pdf_slice", side_effect=self._slice) as cut,
            mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract),
            mock.patch.object(pipeline, "load_blocks", side_effect=self._blocks) as load,
            mock.patch.object(pipeline, "account_pool", return_value=SimpleNamespace(size=3)),
        ):
            with self.assertRaisesRegex(MineruError, "second chunk failed"):
                pipeline._chunk_blocks(self.paper, self.render)

            first, second, third = list(self.paper.import_chunks.order_by("sequence"))
            self.assertEqual(first.status, ImportChunk.Status.PARSED)
            self.assertEqual(first.attempts, 1)
            self.assertEqual(first.error, "")
            self.assertEqual(second.status, ImportChunk.Status.FAILED)
            self.assertEqual(second.attempts, 1)
            self.assertIn("second chunk failed", second.error)
            self.assertTrue(Path(first.artifact_path).is_file())
            self.assertFalse(Path(second.artifact_path).is_file())
            self.assertEqual(third.status, ImportChunk.Status.PARSED)
            self.assertTrue(Path(third.artifact_path).is_file())
            self.assertEqual(sorted(requests), [1, 2, 3])

            fail_second = False
            merged = pipeline._chunk_blocks(self.paper, self.render)

        self.assertEqual([block["page_idx"] for block in merged], list(range(6)))
        self.assertEqual(sorted(requests), [1, 2, 2, 3])
        # All local slices survived the first attempt. Chunks 1 and 3 completed
        # after chunk 2 failed and their archives were reused without resubmission.
        self.assertEqual(cut.call_count, 3)
        self.assertEqual(load.call_count, 5)
        first, second, third = list(self.paper.import_chunks.order_by("sequence"))
        self.assertEqual([first.status, second.status, third.status], [ImportChunk.Status.PARSED] * 3)
        self.assertEqual([first.attempts, second.attempts, third.attempts], [1, 2, 1])
        self.assertEqual(second.error, "")

    def test_local_slice_error_does_not_persist_a_private_path(self):
        self._set_chunk_count(1)
        private_path = r"C:\Users\someone\Private Papers\secret.pdf"

        with mock.patch.object(
            pipeline, "write_pdf_slice", side_effect=OSError(private_path),
        ):
            with self.assertRaises(OSError):
                pipeline._chunk_blocks(self.paper, self.render)

        chunk = self.paper.import_chunks.get()
        self.assertEqual(chunk.status, ImportChunk.Status.FAILED)
        self.assertEqual(chunk.error, "分片处理失败（OSError）")
        self.assertNotIn(private_path, chunk.error)

    def test_account_pool_configuration_message_is_preserved(self):
        self._set_chunk_count(1)
        message = "账号池配置格式不正确，请重新保存 API 配置"

        with (
            mock.patch.object(pipeline, "write_pdf_slice", side_effect=self._slice),
            mock.patch.object(
                pipeline, "account_pool", side_effect=AccountPoolError(message),
            ),
        ):
            with self.assertRaisesRegex(MineruError, message):
                pipeline._chunk_blocks(self.paper, self.render)

    def test_parallel_limit_uses_the_reader_safe_bounds(self):
        self.assertEqual(pipeline.PARALLEL, readers._parallel_limit())
        with mock.patch.dict("os.environ", {"QB_PARALLEL": "invalid"}):
            self.assertEqual(readers._parallel_limit(), 4)
        with mock.patch.dict("os.environ", {"QB_PARALLEL": "0"}):
            self.assertEqual(readers._parallel_limit(), 1)

    def test_pool_size_caps_workers_and_out_of_order_completion_merges_by_sequence(self):
        self._set_chunk_count(3)
        workers: list[int] = []
        completed: list[int] = []
        slice_threads: list[int] = []
        network_threads: list[int] = []
        main_thread = threading.get_ident()
        second_finished = threading.Event()

        def executor_factory(*args, **kwargs):
            count = kwargs.get("max_workers", args[0] if args else None)
            workers.append(count)
            return RealThreadPoolExecutor(*args, **kwargs)

        def extract(source, archive, _page_count, heartbeat=None, cancel=None):
            del heartbeat
            network_threads.append(threading.get_ident())
            sequence = int(source.stem.rsplit("_", 1)[1])
            if sequence == 1:
                self.assertTrue(second_finished.wait(timeout=1))
            archive.write_bytes(f"archive {sequence}".encode())
            completed.append(sequence)
            if sequence == 2:
                second_finished.set()

        def cut(source, target, start, end):
            slice_threads.append(threading.get_ident())
            self._slice(source, target, start, end)

        with (
            mock.patch.object(pipeline, "write_pdf_slice", side_effect=cut),
            mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract),
            mock.patch.object(pipeline, "load_blocks", side_effect=self._blocks),
            mock.patch.object(pipeline, "account_pool", return_value=SimpleNamespace(size=2)),
            mock.patch.object(pipeline, "ThreadPoolExecutor", side_effect=executor_factory),
        ):
            merged = pipeline._chunk_blocks(self.paper, self.render)

        self.assertEqual(workers, [2])
        self.assertEqual(completed[0], 2)
        self.assertEqual(set(slice_threads), {main_thread})
        self.assertTrue(network_threads)
        self.assertTrue(all(thread != main_thread for thread in network_threads))
        self.assertEqual([block["seq"] for block in merged], list(range(6)))
        self.assertEqual(
            [block["text"] for block in merged],
            [
                "chunk 1, local page 0", "chunk 1, local page 1",
                "chunk 2, local page 0", "chunk 2, local page 1",
                "chunk 3, local page 0", "chunk 3, local page 1",
            ],
        )

    def test_account_pool_never_runs_one_token_concurrently(self):
        self._set_chunk_count(4)
        lock = threading.Lock()
        barrier = threading.Barrier(2)
        active_total = 0
        maximum_total = 0
        active_by_token: dict[str, int] = {}
        maximum_by_token: dict[str, int] = {}

        def network(token, _source, archive, _page_count, heartbeat=None, cancel=None):
            nonlocal active_total, maximum_total
            del heartbeat
            with lock:
                active_total += 1
                maximum_total = max(maximum_total, active_total)
                active_by_token[token] = active_by_token.get(token, 0) + 1
                maximum_by_token[token] = max(
                    maximum_by_token.get(token, 0), active_by_token[token],
                )
            try:
                barrier.wait(timeout=1)
                time.sleep(0.02)
                archive.write_bytes(b"mock archive")
                return archive
            finally:
                with lock:
                    active_total -= 1
                    active_by_token[token] -= 1

        with mock.patch.dict(
            "os.environ",
            {"MINERU_TOKENS_JSON": '["token-one", "token-two"]', "MINERU_TOKEN": ""},
        ):
            reset_account_pools()
            with (
                mock.patch.object(pipeline, "write_pdf_slice", side_effect=self._slice),
                mock.patch.object(mineru, "request_extract_file", side_effect=network),
                mock.patch.object(pipeline, "load_blocks", side_effect=self._blocks),
            ):
                pipeline._chunk_blocks(self.paper, self.render)
            reset_account_pools()

        self.assertEqual(maximum_total, 2)
        self.assertEqual(set(maximum_by_token), {"token-one", "token-two"})
        self.assertTrue(all(value == 1 for value in maximum_by_token.values()))

    def test_wait_timeout_heartbeats_only_from_the_main_thread(self):
        self._set_chunk_count(1)
        main_thread = threading.get_ident()
        heartbeat_threads: list[int] = []
        wait_calls = 0
        original_heartbeat = pipeline._paper_heartbeat

        def heartbeat(*args, **kwargs):
            heartbeat_threads.append(threading.get_ident())
            return original_heartbeat(*args, **kwargs)

        def wait_with_one_timeout(futures, **kwargs):
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls == 1:
                return set(), set(futures)
            return real_wait(futures, **kwargs)

        with (
            mock.patch.object(pipeline, "write_pdf_slice", side_effect=self._slice),
            mock.patch.object(
                pipeline, "request_extract_file_from_pool", side_effect=self._successful_extract,
            ),
            mock.patch.object(pipeline, "load_blocks", side_effect=self._blocks),
            mock.patch.object(pipeline, "account_pool", return_value=SimpleNamespace(size=1)),
            mock.patch.object(pipeline, "wait", side_effect=wait_with_one_timeout),
            mock.patch.object(pipeline, "_paper_heartbeat", side_effect=heartbeat),
        ):
            pipeline._chunk_blocks(self.paper, self.render)

        self.assertGreaterEqual(wait_calls, 2)
        self.assertGreaterEqual(len(heartbeat_threads), 3)
        self.assertEqual(set(heartbeat_threads), {main_thread})

    def test_non_chunk_pdf_uses_the_account_pool_entrypoint(self):
        paper = Paper.objects.create(
            filename="单页试卷.pdf",
            kind="pdf",
            sha256="n" * 64,
            source_path="",
            pages=[{"page_idx": 0, "width": 1000, "height": 1400}],
        )
        folder = self.temp / str(paper.pk)
        folder.mkdir()
        source = folder / "source.pdf"
        source.write_bytes(b"one page")
        paper.source_path = str(source)
        paper.save(update_fields=["source_path"])
        blocks = [{
            "seq": 7, "type": "text", "page_idx": 0,
            "bbox": [20, 30, 900, 80], "text": "第1题",
        }]

        states = []

        def extract(_source, archive, _page_count, heartbeat=None, on_state=None, restart=None, cancel=None):
            archive.write_bytes(b"mock archive")
            if heartbeat:
                heartbeat()
            # 1.10.6: what MinerU reports is noted beside the paper while it works.
            on_state({"state": "running", "pages": 1, "total_pages": 1})
            states.append(mineru.read_state(folder / mineru.MINERU_STATE_FILE))

        with (
            mock.patch.object(
                pipeline, "request_extract_file_from_pool", side_effect=extract,
            ) as request,
            mock.patch.object(pipeline, "load_blocks", return_value=blocks),
            mock.patch.object(pipeline, "_plan_structure", return_value=({}, False)),
        ):
            pipeline.parse(paper)

        self.assertEqual(request.call_count, 1)
        self.assertEqual((request.call_args.args[0], request.call_args.args[2]), (source, 1))
        self.assertEqual(request.call_args.args[1].name, "mineru_result.zip")
        self.assertTrue(request.call_args.args[1].parent.name.startswith(".parse-0-"))
        self.assertTrue(callable(request.call_args.kwargs["heartbeat"]))
        self.assertEqual(states[0]["state"], "running")
        self.assertEqual((states[0]["pages"], states[0]["total_pages"]), (1, 1))
        # …and removed once parsing is over.
        self.assertFalse((folder / mineru.MINERU_STATE_FILE).exists())
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.SEGMENTING)
        self.assertEqual(Block.objects.filter(paper=paper).count(), 1)
