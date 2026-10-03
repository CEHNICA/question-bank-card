"""Native export delivery uses private directories and synthetic output only."""
import json
import os
from pathlib import Path
import tempfile
from unittest import mock

from django.http import HttpResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path

from . import export_preferences as preferences, library_pdf, library_export
from . import native_folder_picker

urlpatterns = [path("api/export-preferences", preferences.preferences_view),
               path("api/export-preferences/select", preferences.select_export_directory_view),
               path("api/export-preferences/open", preferences.open_export_view),
               path("api/library/export-pdf", library_pdf.export_pdf_view),
               path("api/library/export-docx", library_export.export_docx_view)]


@override_settings(ROOT_URLCONF=__name__)
class ExportPreferenceTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.folder = self.root / "exports"
        self.folder.mkdir()
        environment = mock.patch.dict(os.environ, {"QB_USER_ROOT": str(self.root), "QB_DESKTOP_EXPORT": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        desktop = mock.patch.object(preferences, "desktop_capable", return_value=True)
        desktop.start()
        self.addCleanup(desktop.stop)
        preferences._receipts.clear()
        self.addCleanup(preferences._receipts.clear)
        self.headers = {"HTTP_X_QB_REQUEST": "1", "HTTP_ORIGIN": "http://testserver", "REMOTE_ADDR": "127.0.0.1"}
        self.secret = self.root / "credentials.bin"
        self.secret.write_bytes(b"opaque-secret-file-do-not-read")

    def post(self, value, route="/api/export-preferences", **changes):
        return self.client.post(route, json.dumps(value), content_type="application/json", **{**self.headers, **changes})

    def test_default_save_and_clear_are_separate_from_user_data(self):
        self.assertEqual(preferences.describe()["directory"], "")
        response = self.post({"directory": str(self.folder)})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["directory"], str(self.folder))
        self.assertEqual(preferences.preference_path(), self.root / "export-preferences.json")
        self.assertEqual(self.secret.read_bytes(), b"opaque-secret-file-do-not-read")
        self.assertEqual(list(self.folder.iterdir()), [], "Saving a directory preference does not create probe files")
        self.assertEqual(self.post({"directory": ""}).json()["directory"], "")

    def test_invalid_directories_and_payload_never_create_paths(self):
        for value in (str(self.root / "missing"), str(self.secret), "relative", "file:///C:/exports", "\\\\server\\share",
                      "\\\\?\\C:\\exports", True, 12, None):
            with self.subTest(value=value):
                self.assertEqual(self.post({"directory": value}).status_code, 400)
        for value in ({}, {"directory": "", "path": str(self.folder)}, {"directory": []}):
            self.assertEqual(self.post(value).status_code, 400)
        self.assertFalse((self.root / "missing").exists())
        self.assertFalse(preferences.preference_path().exists())

    def test_guards_reject_cross_site_nonlocal_or_non_desktop_mutations(self):
        for headers in ({"HTTP_ORIGIN": "http://evil.invalid"}, {"HTTP_ORIGIN": ""},
                        {"HTTP_X_QB_REQUEST": ""}, {"REMOTE_ADDR": "192.0.2.2"}):
            self.assertEqual(self.post({"directory": str(self.folder)}, **headers).status_code, 403)
        with mock.patch.object(preferences, "desktop_capable", return_value=False):
            self.assertEqual(self.post({"directory": str(self.folder)}).status_code, 409)
        self.assertFalse(preferences.preference_path().exists())

    def test_corrupt_preference_is_preserved_and_described(self):
        target = preferences.preference_path()
        target.write_bytes(b"corrupt-private-preference")
        self.assertTrue(preferences.describe()["warning"])
        self.assertEqual(self.post({"directory": str(self.folder)}).status_code, 400)
        self.assertEqual(target.read_bytes(), b"corrupt-private-preference")

    def request(self, **changes):
        from django.test import RequestFactory
        return RequestFactory().post("/api/library/export-pdf", "{}", content_type="application/json",
            **{**self.headers, "HTTP_X_QB_EXPORT_DELIVERY": "configured", **changes})

    def delivery(self, filename="数学练习.pdf", data=b"%PDF-synthetic-complete", request=None):
        attachment = HttpResponse(data, content_type="application/pdf")
        attachment["Content-Disposition"] = "attachment"
        return preferences.deliver(request or self.request(), attachment, data, filename, count=4, pages=2)

    def test_exclusive_save_collisions_keep_all_existing_bytes_and_receipt(self):
        preferences.save({"directory": str(self.folder)})
        original = self.folder / "数学练习.pdf"
        original.write_bytes(b"user-existing-file")
        first, second = self.delivery(), self.delivery()
        a, b = json.loads(first.content), json.loads(second.content)
        self.assertEqual(a["filename"], "数学练习 (2).pdf")
        self.assertEqual(b["filename"], "数学练习 (3).pdf")
        self.assertEqual(a["question_count"], 4)
        self.assertEqual(a["page_count"], 2)
        self.assertEqual(Path(a["path"]).read_bytes(), b"%PDF-synthetic-complete")
        self.assertEqual(original.read_bytes(), b"user-existing-file")
        self.assertNotEqual(a["file_token"], b["file_token"])

    def test_no_explicit_header_or_browser_server_never_writes(self):
        preferences.save({"directory": str(self.folder)})
        self.assertEqual(self.delivery(request=self.request(HTTP_X_QB_EXPORT_DELIVERY=""))["Content-Type"], "application/pdf")
        with mock.patch.object(preferences, "desktop_capable", return_value=False):
            self.assertEqual(self.delivery()["Content-Type"], "application/pdf")
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_invalid_origin_rejected_before_writing_configured_export(self):
        preferences.save({"directory": str(self.folder)})
        self.assertEqual(self.delivery(request=self.request(HTTP_ORIGIN="http://evil.invalid")).status_code, 403)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_filename_cannot_supply_path_device_extension_or_stream(self):
        preferences.save({"directory": str(self.folder)})
        for filename in ("../outside.pdf", "C:\\outside.pdf", "report.pdf:secret", "CON.pdf", "run.exe", "bad\nname.pdf", "a.pdf "):
            with self.subTest(filename=filename):
                response = self.delivery(filename=filename)
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertIn("X-QB-Export-Warning", response)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_missing_or_failed_directory_returns_original_complete_attachment(self):
        preferences.save({"directory": str(self.folder)})
        self.folder.rmdir()
        response = self.delivery()
        self.assertEqual(response.content, b"%PDF-synthetic-complete")
        self.assertIn("X-QB-Export-Warning", response)
        self.folder.mkdir()
        with mock.patch.object(preferences.os, "fsync", side_effect=OSError("disk full")):
            response = self.delivery()
        self.assertEqual(response.content, b"%PDF-synthetic-complete")
        self.assertEqual(list(self.folder.iterdir()), [], "A partial file is removed without changing any existing document")

    def test_open_only_server_issued_unchanged_file_and_token_is_once(self):
        preferences.save({"directory": str(self.folder)})
        receipt = json.loads(self.delivery().content)
        with mock.patch.object(preferences.os, "startfile", create=True) as opened:
            result = self.post({"target": "file", "file_token": receipt["file_token"]}, "/api/export-preferences/open")
            self.assertEqual(result.status_code, 200)
            opened.assert_called_once_with(receipt["path"])
            self.assertEqual(self.post({"target": "file", "file_token": receipt["file_token"]}, "/api/export-preferences/open").status_code, 409)
        receipt = json.loads(self.delivery().content)
        Path(receipt["path"]).write_bytes(b"changed document")
        with mock.patch.object(preferences.os, "startfile", create=True) as opened:
            self.assertEqual(self.post({"target": "file", "file_token": receipt["file_token"]}, "/api/export-preferences/open").status_code, 409)
            opened.assert_not_called()

    def test_open_directory_uses_only_saved_choice_and_rejects_arbitrary_path(self):
        preferences.save({"directory": str(self.folder)})
        with mock.patch.object(preferences.os, "startfile", create=True) as opened:
            self.assertEqual(self.post({"target": "directory"}, "/api/export-preferences/open").status_code, 200)
            opened.assert_called_once_with(str(self.folder))
            self.assertEqual(self.post({"target": "directory", "path": str(self.root)}, "/api/export-preferences/open").status_code, 400)
            self.assertEqual(self.post({"target": "file"}, "/api/export-preferences/open").status_code, 409)
            self.assertEqual(opened.call_count, 1)

    def test_read_requires_local_page_header_and_has_no_cache(self):
        self.assertEqual(self.client.get("/api/export-preferences").status_code, 403)
        response = self.client.get("/api/export-preferences", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertTrue(response.json()["desktop_capable"])

    def test_native_selection_returns_candidate_without_saving_or_creating_files(self):
        preferences.save({"directory": str(self.folder)})
        saved = preferences.preference_path().read_bytes()
        other = self.root / "另一导出文件夹"
        other.mkdir()
        with mock.patch.object(native_folder_picker, "choose_directory", return_value=str(other)) as choose:
            response = self.post({}, "/api/export-preferences/select")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"selected": True, "directory": str(other)})
        self.assertEqual(response["Cache-Control"], "no-store")
        choose.assert_called_once_with(str(self.folder))
        self.assertEqual(preferences.preference_path().read_bytes(), saved)
        self.assertEqual(list(other.iterdir()), [])
        self.assertEqual(self.secret.read_bytes(), b"opaque-secret-file-do-not-read")

    def test_native_selection_cancel_and_failure_keep_saved_preference_and_release_lock(self):
        preferences.save({"directory": str(self.folder)})
        saved = preferences.preference_path().read_bytes()
        for result in (None, str(self.root / "missing"), "\\\\server\\share", "",
                       native_folder_picker.FolderPickerError("offline native chooser unavailable")):
            with self.subTest(result=result), mock.patch.object(native_folder_picker, "choose_directory") as choose:
                if isinstance(result, Exception):
                    choose.side_effect = result
                else:
                    choose.return_value = result
                response = self.post({}, "/api/export-preferences/select")
                self.assertEqual(response.status_code, 200 if result is None else 409)
                if result is None:
                    self.assertEqual(response.json(), {"selected": False, "cancelled": True})
                self.assertEqual(preferences.preference_path().read_bytes(), saved)
                self.assertTrue(preferences._picker_lock.acquire(blocking=False))
                preferences._picker_lock.release()

    def test_native_selection_can_recover_deleted_saved_directory_but_not_corrupt_preferences(self):
        preferences.save({"directory": str(self.folder)})
        self.folder.rmdir()
        with mock.patch.object(native_folder_picker, "choose_directory", return_value=None) as choose:
            self.assertEqual(self.post({}, "/api/export-preferences/select").status_code, 200)
            choose.assert_called_once_with("")
        preferences.preference_path().write_bytes(b"private-corrupt-state")
        with mock.patch.object(native_folder_picker, "choose_directory") as choose:
            self.assertEqual(self.post({}, "/api/export-preferences/select").status_code, 409)
            choose.assert_not_called()
        self.assertEqual(preferences.preference_path().read_bytes(), b"private-corrupt-state")

    def test_native_selection_guards_fail_before_showing_any_window(self):
        with mock.patch.object(native_folder_picker, "choose_directory") as choose:
            self.assertEqual(self.client.get("/api/export-preferences/select", **self.headers).status_code, 405)
            for headers in ({"HTTP_ORIGIN": "http://evil.invalid"}, {"HTTP_ORIGIN": ""},
                            {"HTTP_X_QB_REQUEST": ""}, {"REMOTE_ADDR": "192.0.2.2"}):
                self.assertEqual(self.post({}, "/api/export-preferences/select", **headers).status_code, 403)
            for value in ({"directory": str(self.folder)}, {"target": "directory"}, {"initial": "arbitrary"}, []):
                self.assertEqual(self.post(value, "/api/export-preferences/select").status_code, 400)
            with mock.patch.object(preferences, "desktop_capable", return_value=False):
                self.assertEqual(self.post({}, "/api/export-preferences/select").status_code, 409)
            choose.assert_not_called()
        self.assertFalse(preferences.preference_path().exists())

    def test_duplicate_native_selection_never_opens_a_second_window(self):
        def while_open(initial):
            with mock.patch.object(native_folder_picker, "choose_directory") as another:
                response = self.post({}, "/api/export-preferences/select")
                self.assertEqual(response.status_code, 409)
                self.assertIn("已经打开", response.json()["error"])
                another.assert_not_called()
            return None
        with mock.patch.object(native_folder_picker, "choose_directory", side_effect=while_open) as choose:
            self.assertEqual(self.post({}, "/api/export-preferences/select").status_code, 200)
            choose.assert_called_once_with("")
        self.assertFalse(preferences.preference_path().exists())

    def test_native_dialog_always_releases_com_on_cancel_selection_or_exception(self):
        for result in (None, str(self.folder), native_folder_picker.FolderPickerError("offline COM failed"),
                       OSError("offline Windows dialog unavailable")):
            with self.subTest(result=result), mock.patch.object(native_folder_picker.os, "name", "nt"), \
                    mock.patch.object(native_folder_picker, "_WindowsFolderDialog") as factory:
                dialog = factory.return_value
                if isinstance(result, Exception):
                    dialog.choose.side_effect = result
                    with self.assertRaises(native_folder_picker.FolderPickerError):
                        native_folder_picker.choose_directory("synthetic initial folder")
                else:
                    dialog.choose.return_value = result
                    self.assertEqual(native_folder_picker.choose_directory("synthetic initial folder"), result)
                dialog.choose.assert_called_once_with("synthetic initial folder")
                dialog.release.assert_called_once_with()
        with mock.patch.object(native_folder_picker.os, "name", "posix"), \
                mock.patch.object(native_folder_picker, "_WindowsFolderDialog") as factory:
            with self.assertRaises(native_folder_picker.FolderPickerError):
                native_folder_picker.choose_directory()
            factory.assert_not_called()

    @mock.patch.object(native_folder_picker, "_method", wraps=native_folder_picker._method)
    def test_windows_com_adapter_without_opening_a_dialog(self, method):
        if os.name != "nt":
            self.skipTest("Windows COM capability probe only; no UI is shown")
        dialog = native_folder_picker._WindowsFolderDialog()
        try:
            dialog._check(dialog._call(9, [native_folder_picker.ctypes.c_uint32], 0x20 | 0x40 | 0x800 | 0x8 | 0x2000000))
            dialog._check(dialog._call(17, [native_folder_picker.ctypes.c_wchar_p], "隔离测试：不打开选择窗口"))
            dialog._check(dialog._call(18, [native_folder_picker.ctypes.c_wchar_p], "选择文件夹"))
        finally:
            dialog.release()
        self.assertFalse(dialog.initialized)
        self.assertFalse(dialog.interface)
        self.assertNotIn(3, [call.args[1] for call in method.call_args_list], "No IModalWindow.Show call in offline tests")

    def test_real_export_handlers_return_native_receipts_for_pdf_word_and_zip(self):
        preferences.save({"directory": str(self.folder)})
        formats = [(library_pdf, b"%PDF-complete-synthetic", "数学.pdf", "application/pdf", "/api/library/export-pdf"),
                   (library_export, b"PK\x03\x04complete-synthetic", "数学.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "/api/library/export-docx"),
                   (library_export, b"PK\x03\x04complete-synthetic", "数学-分卷.zip", "application/zip", "/api/library/export-docx")]
        for module, data, name, mime, route in formats:
            result = (data, name, 4, 2) if module is library_pdf else (data, name, mime, 4)
            with self.subTest(filename=name), mock.patch.object(module, "export", return_value=result):
                response = self.post({}, route, HTTP_X_QB_EXPORT_DELIVERY="configured")
                self.assertEqual(response.status_code, 200, response.content)
                receipt = response.json()
                self.assertEqual(receipt["question_count"], 4)
                self.assertEqual(Path(receipt["path"]).read_bytes(), data)
                self.assertTrue(receipt["saved"])
