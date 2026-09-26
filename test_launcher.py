"""启动器的离线测试：密钥只交给后台工作者；已在运行时不重复启动。不访问网络。"""

from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest.mock import Mock, patch

import start_question_bank as launcher


class LauncherTests(unittest.TestCase):
    def run_main(self, saved: dict[str, str]):
        environments = {}
        process = Mock()
        process.poll.return_value = 0

        def capture(args, environment, log_name):
            environments[log_name] = dict(environment)
            return process, io.BytesIO()

        with patch.object(launcher.os, "name", "nt"), \
                patch.dict(os.environ, {"MINERU_TOKEN": "", "MINIMAX_API_KEY": "", "SILICONFLOW_API_KEY": ""}), \
                patch.object(launcher, "_running_instance", return_value=None), \
                patch.object(launcher, "InstanceMutex") as mutex_type, \
                patch.object(launcher, "_prepare"), \
                patch.object(launcher, "load_credentials", return_value=saved), \
                patch.object(launcher, "save_credentials"), \
                patch.object(launcher, "_mineru_token_validity", return_value=True), \
                patch.object(launcher, "_available_port", return_value=8768), \
                patch.object(launcher, "ChildJob"), \
                patch.object(launcher, "_start", side_effect=capture), \
                patch.object(launcher, "_health"), \
                patch.object(launcher.webbrowser, "open_new_tab"), \
                patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()):
            mutex_type.return_value.acquired = True
            self.assertEqual(launcher.main(), 0)
        return environments

    def test_only_worker_receives_secrets(self):
        environments = self.run_main({"mineru_token": "m-token", "minimax_key": "mm-key", "siliconflow_key": "sf-key"})
        worker, web = environments["worker.log"], environments["web.log"]
        self.assertEqual((worker["MINERU_TOKEN"], worker["MINIMAX_API_KEY"], worker["SILICONFLOW_API_KEY"]),
                         ("m-token", "mm-key", "sf-key"))
        for name in ("MINERU_TOKEN", "MINIMAX_API_KEY", "SILICONFLOW_API_KEY"):
            self.assertNotIn(name, web)
        self.assertEqual((web["QB_MINERU_CONFIGURED"], web["QB_MINIMAX_CONFIGURED"], web["QB_SILICONFLOW_CONFIGURED"]),
                         ("1", "1", "1"))

    def test_missing_siliconflow_is_reported_to_web(self):
        environments = self.run_main({"mineru_token": "m-token", "minimax_key": "mm-key"})
        self.assertEqual(environments["web.log"]["QB_SILICONFLOW_CONFIGURED"], "0")
        self.assertNotIn("SILICONFLOW_API_KEY", environments["worker.log"])

    def test_existing_instance_is_reused(self):
        with patch.object(launcher.os, "name", "nt"), \
                patch.object(launcher, "_running_instance", return_value="http://127.0.0.1:8768"), \
                patch.object(launcher, "_prepare") as prepare, \
                patch.object(launcher.webbrowser, "open_new_tab") as browser, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(), 0)
        prepare.assert_not_called()
        browser.assert_called_once_with("http://127.0.0.1:8768")

    def test_busy_mutex_never_starts_a_second_copy(self):
        mutex = Mock(acquired=False)
        with patch.object(launcher.os, "name", "nt"), \
                patch.object(launcher, "_running_instance", return_value=None), \
                patch.object(launcher, "InstanceMutex", return_value=mutex), \
                patch.object(launcher, "_prepare") as prepare, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(), 0)
        prepare.assert_not_called()

    def test_recorded_random_port_is_checked(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit):
                return b'{"app":"question-bank-card"}'

        instance_file = Mock()
        instance_file.is_file.return_value = True
        instance_file.read_text.return_value = '{"port":49152}'
        with patch.object(launcher, "INSTANCE_FILE", instance_file), \
                patch.object(launcher.urllib.request, "urlopen", return_value=Response()) as urlopen:
            self.assertEqual(launcher._running_instance(), "http://127.0.0.1:49152")
        self.assertEqual(urlopen.call_args.args[0], "http://127.0.0.1:49152/api/health")


if __name__ == "__main__":
    unittest.main()
