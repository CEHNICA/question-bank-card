"""OneNote 知识库联动单元测试。"""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from PIL import Image

from . import library, library_onenote, tiyouju_protocol
from .models import PublishedQuestion


def _sample_png_bytes(width: int = 800, height: int = 200) -> bytes:
    """生成用于单元测试的小尺寸 PNG 字节流，避免启动无头浏览器。"""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


class OneNoteLinkTests(TestCase):
    """测试 OneNote 创建、打开、手动绑定与解绑逻辑。"""

    def setUp(self):
        # 测试不得改写本机 Windows 注册表的 tiyouju:// 协议关联
        reg_patcher = patch("core.tiyouju_protocol.ensure_registered", return_value=True)
        reg_patcher.start()
        self.addCleanup(reg_patcher.stop)

        library_onenote.clear_section_cache()
        self.item = PublishedQuestion.objects.create(
            source_filename="2026年高三模拟考.pdf",
            number=15,
            question_type="free_response",
            version=1,
            content={
                "stem": "已知函数 $f(x) = x^2 - 2x + 1$，求其单调区间。",
                "options": {},
                "answer": "[1, +\\infty)",
                "analysis": "",
                "source_filename": "2026年高三模拟考.pdf",
            },
            content_hash="a" * 64,
            extras={},
        )

    @patch("core.library_onenote._backfill_page_image")
    @patch("core.library_onenote.shell_open_onenote", return_value=True)
    @patch("core.library_onenote.cached_question_image", return_value=None)
    @patch("core.library_onenote.call_bridge")
    def test_create_or_sync_page_success(self, mock_call, _mock_cache, mock_shell, mock_backfill):
        """建页热路径不渲染题面图：先文字页打开，图丢后台补。"""
        captured_params = {}

        def fake_call(op, **params):
            if op == "list":
                return {"notebooks": [{"id": "{nb-1}", "name": "math", "sections": 1, "pages": 10}]}
            if op == "hierarchy":
                return {"xml": '<one:Section name="做题解答" ID="{sec-1}"/>'}
            if op == "create_xml":
                captured_params.update(params)
                return {"id": "{page-999}", "link": "onenote:https://d.docs.live.net/fake/math/page-999"}
            if op == "navigate":
                return {"id": params.get("id"), "navigated": True, "focused": True}
            return {}

        mock_call.side_effect = fake_call

        record = library_onenote.create_or_sync_page(self.item)
        mock_shell.assert_called_once()
        self.assertEqual(record["page_id"], "{page-999}")
        self.assertRegex(record["title"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertIn("onenote:", record["url"])
        self.assertNotIn("<one:Image", captured_params.get("pageXml", ""))
        self.assertIn(f"tiyouju://question/{self.item.pk}", captured_params.get("pageXml", ""))
        self.assertNotIn("pageId", captured_params)
        time.sleep(0.05)
        mock_backfill.assert_called()

        # 检查数据库是否正确持久化
        refreshed = PublishedQuestion.objects.get(pk=self.item.pk)
        self.assertIn("onenote", refreshed.extras)
        self.assertEqual(refreshed.extras["onenote"]["page_id"], "{page-999}")

        # 检查序列化 JSON 是否包含 onenote 字段
        json_data = library.publication_json(refreshed)
        self.assertIsNotNone(json_data["onenote"])
        self.assertEqual(json_data["onenote"]["page_id"], "{page-999}")

    @patch("core.library_onenote.open_page")
    @patch("core.library_onenote.shell_open_onenote", return_value=True)
    @patch("core.library_onenote.call_bridge")
    def test_existing_link_opens_without_recreate(self, mock_call, mock_shell, mock_open):
        """已有 OneNote 关联时再点创建接口：只打开，绝不 create_xml 覆盖原页。"""
        self.item.extras = {
            "onenote": {
                "page_id": "{page-old}",
                "url": "onenote:https://d.docs.live.net/fake/old",
                "title": "2026-11-15 12:00:00",
            }
        }
        self.item.save(update_fields=["extras"])

        record = library_onenote.create_or_sync_page(self.item)
        self.assertEqual(record["page_id"], "{page-old}")
        mock_shell.assert_called_once_with("onenote:https://d.docs.live.net/fake/old")
        mock_open.assert_called_once()
        mock_call.assert_not_called()

    def test_latex_to_plain_and_build_page_xml(self):
        """验证纯文本兜底去 LaTeX 符号转换与 XML 拼装结果。"""
        plain = library_onenote.latex_to_plain(r"已知 $AB=\sqrt{2}$，$\triangle DEF$ 与 $\frac{1}{2}$")
        self.assertNotIn("$", plain)
        self.assertIn("√2", plain)
        self.assertIn("△", plain)
        self.assertIn("(1)/(2)", plain)

        title = library_onenote._page_title(self.item)
        self.assertRegex(title, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertNotIn("[题有据]", title)

        page_title = "2026-11-15 15:54:32"
        xml_with_img = library_onenote.build_page_xml(self.item, page_title, _sample_png_bytes(1600, 400))
        self.assertIn('<one:Image format="png">', xml_with_img)
        self.assertIn("<one:Data>", xml_with_img)
        self.assertIn(f"tiyouju://question/{self.item.pk}", xml_with_img)
        self.assertNotIn("$f(x)", xml_with_img)
        self.assertNotIn("shadingColor", xml_with_img)
        self.assertNotIn("<one:Table", xml_with_img)
        self.assertNotIn("我的解答", xml_with_img)
        self.assertIn(page_title, xml_with_img)
        self.assertIn("在题有据中打开此题", xml_with_img)
        ET.fromstring(xml_with_img)

        self.item.source_filename = "微信图片_2025-10-10_000922_951 等 4 张照片"
        self.assertRegex(library_onenote._page_title(self.item), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        photo_xml = library_onenote.build_page_xml(self.item, "2026-11-15 15:54:32", None)
        self.assertNotIn("微信图片", photo_xml)
        self.item.source_filename = "2026年高三模拟考.pdf"

        xml_fallback = library_onenote.build_page_xml(self.item, "兜底页", None)
        self.assertNotIn("<one:Image", xml_fallback)
        self.assertNotIn("$", xml_fallback)
        self.assertIn("题面图生成失败", xml_fallback)
        self.assertNotIn("我的解答", xml_fallback)
        self.assertNotIn("shadingColor", xml_fallback)

    def test_tiyouju_protocol_parsing_and_ports(self):
        """验证 tiyouju:// URI 解析、目标 URL 生成与环境变量端口优先级。"""
        qid = "8762746b-08d4-4896-bd3d-9a2440536872"
        self.assertEqual(tiyouju_protocol.parse_question_id(f"tiyouju://question/{qid}"), qid)
        self.assertEqual(tiyouju_protocol.parse_question_id(f"tiyouju://question/{qid}/"), qid)
        self.assertIsNone(tiyouju_protocol.parse_question_id("https://example.com/123"))
        self.assertEqual(tiyouju_protocol.build_target_url(8769, qid), f"http://127.0.0.1:8769/eagle?q={qid}")
        self.assertEqual(
            tiyouju_protocol.extract_uri_from_argv(["--tiyouju-uri", f"tiyouju://question/{qid}"]),
            f"tiyouju://question/{qid}",
        )
        self.assertEqual(
            tiyouju_protocol.extract_uri_from_argv([f"tiyouju://question/{qid}"]), f"tiyouju://question/{qid}"
        )

        with patch.dict(os.environ, {"TIYOUJU_PORT": "9876"}, clear=False):
            ports = tiyouju_protocol.candidate_ports()
            self.assertEqual(ports[0], 9876)

        with (
            patch.object(tiyouju_protocol, "FROZEN", True),
            patch.object(tiyouju_protocol.sys, "executable", r"C:\Apps\QuestionBankCard.exe"),
        ):
            self.assertEqual(
                tiyouju_protocol.build_command_line(), r'"C:\Apps\QuestionBankCard.exe" --tiyouju-uri "%1"'
            )

        with patch.object(tiyouju_protocol, "FROZEN", False):
            cmd = tiyouju_protocol.build_command_line()
            self.assertIn("--tiyouju-uri", cmd)
            self.assertTrue(
                "app_launcher.pyw" in cmd or "app_window.py" in cmd, f"源码版协议应注册窗口启动器，实际: {cmd}"
            )
            self.assertNotIn("tiyouju_protocol.py", cmd)

        tiyouju_protocol.queue_deep_link(qid)
        pending = tiyouju_protocol.consume_deep_link()
        self.assertEqual(pending["q"], qid)
        self.assertIsNone(tiyouju_protocol.consume_deep_link())

    @patch("core.library_onenote.shell_open_onenote", return_value=False)
    @patch("core.library_onenote.call_bridge")
    def test_open_page_calls_navigate(self, mock_call, _mock_shell):
        """测试打开已关联 OneNote 页面时触发 navigate。"""
        self.item.extras = {
            "onenote": {
                "url": "onenote:https://d.docs.live.net/fake/page-1",
                "page_id": "{page-1}",
                "title": "已关联笔记",
            }
        }
        self.item.save(update_fields=["extras"])

        mock_call.return_value = {"id": "{page-1}", "navigated": True, "focused": True}

        result = library_onenote.open_page(self.item, wait=True)
        self.assertTrue(result["ok"])
        mock_call.assert_called_with("navigate", id="{page-1}", timeout_seconds=12)
        _mock_shell.assert_not_called()

    @patch("core.library_onenote.shell_open_onenote", return_value=True)
    @patch("core.library_onenote.call_bridge")
    def test_open_page_shell_opens_when_not_focused(self, mock_call, mock_shell):
        """COM 切了页但没抢到前台时，再用协议链接补一次。"""
        self.item.extras = {
            "onenote": {
                "url": "onenote:https://d.docs.live.net/fake/page-2",
                "page_id": "{page-2}",
                "title": "已关联笔记",
            }
        }
        self.item.save(update_fields=["extras"])
        mock_call.return_value = {"id": "{page-2}", "navigated": True, "focused": False}

        library_onenote._refine_open("{page-2}", "onenote:https://d.docs.live.net/fake/page-2")
        mock_shell.assert_called_once_with("onenote:https://d.docs.live.net/fake/page-2")

    def test_open_page_returns_immediately_by_default(self):
        """默认打开接口立刻返回，不堵在 COM 上。"""
        self.item.extras = {"onenote": {"url": "onenote:https://d.docs.live.net/fake/page-3", "page_id": "{page-3}"}}
        self.item.save(update_fields=["extras"])
        called = threading.Event()

        def mark(*_args, **_kwargs):
            called.set()

        with patch("core.library_onenote._refine_open", side_effect=mark):
            result = library_onenote.open_page(self.item)
        self.assertTrue(result["ok"])
        self.assertTrue(result["deferred"])
        self.assertTrue(called.wait(1.0))

    def test_open_page_unlinked_raises_error(self):
        """未关联题目调用 open_page 应抛出 OneNoteError。"""
        with self.assertRaises(library_onenote.OneNoteError):
            library_onenote.open_page(self.item)

    def test_unbind_api(self):
        """测试解除关联 API（手动绑定接口已按需求下线）。"""
        self.item.extras = {
            "onenote": {"url": "onenote:https://example.com/page", "page_id": "{page-1}", "title": "手工笔记"}
        }
        self.item.save(update_fields=["extras"])

        unbind_url = f"/api/library/{self.item.id}/onenote/unbind"
        resp_unbind = self.client.post(unbind_url)
        self.assertEqual(resp_unbind.status_code, 200)

        self.item.refresh_from_db()
        self.assertNotIn("onenote", self.item.extras)

    def test_manual_bind_api_removed(self):
        """手动绑定接口已下线，请求应返回 404。"""
        bind_url = f"/api/library/{self.item.id}/onenote/bind"
        resp = self.client.post(
            bind_url, data=json.dumps({"url": "onenote:https://example.com/page"}), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 404)

    def test_carried_extras_preserves_onenote(self):
        """题目重新发布生成新版本时，原 OneNote 关联不应丢失。"""
        self.item.extras = {"onenote": {"url": "onenote:https://d.docs.live.net/test", "page_id": "{p-1}"}}
        carried = library.carried_extras(self.item, self.item.content)
        self.assertIn("onenote", carried)
        self.assertEqual(carried["onenote"]["page_id"], "{p-1}")

    @patch("core.library_onenote.call_bridge")
    def test_resolve_target_section_skips_recycle_bin_and_prefers_question_section(self, mock_call):
        """解析目标分区时应跳过回收站分区并优先选择试题相关分区。"""

        def fake_call(op, **params):
            if op == "list":
                return {"notebooks": [{"id": "{nb-math}", "name": "math"}]}
            if op == "hierarchy":
                return {
                    "xml": (
                        '<one:Notebook name="math" ID="{nb-math}">'
                        '<one:SectionGroup name="OneNote_RecycleBin" isRecycleBin="true">'
                        '<one:Section name="删除的页面" ID="{sec-trash}" isInRecycleBin="true" isDeletedPages="true"/>'
                        "</one:SectionGroup>"
                        '<one:Section name="课本观点" ID="{sec-1}"/>'
                        '<one:Section name="数学试题" ID="{sec-math}"/>'
                        "</one:Notebook>"
                    )
                }
            return {}

        mock_call.side_effect = fake_call

        nb_id, sec_id, nb_name, sec_name = library_onenote.resolve_target_section()
        self.assertEqual(nb_id, "{nb-math}")
        self.assertEqual(nb_name, "math")
        self.assertEqual(sec_id, "{sec-math}")
        self.assertEqual(sec_name, "数学试题")

    @patch("core.library_onenote.subprocess.run")
    @patch("core.library_onenote.get_bridge_script")
    def test_call_bridge_uses_create_no_window_and_hidden_flags(self, mock_script, mock_run):
        """调用 PowerShell 桥接时必须隐藏窗口并传入 CREATE_NO_WINDOW，防止桌面端闪出蓝框。"""
        mock_script.return_value = library_onenote.BUNDLED_BRIDGE_PATH

        def fake_run(cmd, **kwargs):
            resp_path = cmd[-1]
            with open(resp_path, "w", encoding="utf-8") as f:
                json.dump({"ok": True, "data": {"notebooks": []}}, f)
            return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        mock_run.side_effect = fake_run

        data = library_onenote.call_bridge("list")
        self.assertEqual(data, {"notebooks": []})
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs.get("creationflags"), library_onenote.CREATE_NO_WINDOW)
        self.assertIn("-NonInteractive", cmd)
        idx = cmd.index("-WindowStyle")
        self.assertEqual(cmd[idx + 1], "Hidden")

    def test_get_bridge_script_resolves_frozen_meipass(self):
        """打包冻结模式下应能通过 sys._MEIPASS 命中随包分发的 onenote_bridge.ps1，而非回退旧脚本。"""
        with tempfile.TemporaryDirectory() as tmp:
            frozen_root = Path(tmp) / "bundle_internal"
            frozen_script = frozen_root / "backend" / "core" / "onenote_bridge.ps1"
            frozen_script.parent.mkdir(parents=True)
            frozen_script.write_text("# frozen bridge", encoding="utf-8")
            missing = Path(tmp) / "missing_bridge.ps1"
            with (
                patch.dict(os.environ, {"TIYOUJU_ONENOTE_BRIDGE": ""}, clear=False),
                patch.object(library_onenote, "BUNDLED_BRIDGE_PATH", missing),
                patch.object(library_onenote.sys, "_MEIPASS", str(frozen_root), create=True),
            ):
                resolved = library_onenote.get_bridge_script()
            self.assertEqual(resolved.resolve(), frozen_script.resolve())

    def test_onenote_open_view_supports_synchronous_wait(self):
        """前端传 wait=1 时，打开接口应同步等待唤起完成，避免等待进度条提前消失。"""
        self.item.extras = {"onenote": {"url": "onenote:https://d.docs.live.net/fake/page-wait", "page_id": "{page-w}"}}
        self.item.save(update_fields=["extras"])

        with patch("core.library_onenote.open_page", return_value={"ok": True, "deferred": False}) as mock_open:
            resp = self.client.post(f"/api/library/{self.item.id}/onenote/open?wait=1")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["deferred"])
        mock_open.assert_called_once_with(self.item, wait=True)

        with patch("core.library_onenote.open_page", return_value={"ok": True, "deferred": True}) as mock_open_async:
            resp_default = self.client.post(f"/api/library/{self.item.id}/onenote/open")
        self.assertEqual(resp_default.status_code, 200)
        mock_open_async.assert_called_once_with(self.item, wait=False)
