"""Real Windows Job lifetime checks, using only owned Python sleeper processes."""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import start_question_bank as launcher

ROOT = Path(__file__).resolve().parent
WORKER = r'''
import json, pathlib, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from backend.core.app_update import UpdateError, _launch_detached_installer
folder = pathlib.Path(sys.argv[2])
while not (folder / "go").exists(): time.sleep(.01)
background = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=subprocess.CREATE_NO_WINDOW)
try:
    installer = _launch_detached_installer([sys.executable, "-c", "import time; time.sleep(60)"])
    result = {"background": background.pid, "installer": installer.pid}
except UpdateError as exc:
    result = {"background": background.pid, "error": str(exc)}
(folder / "result.tmp").write_text(json.dumps(result))
(folder / "result.tmp").replace(folder / "result.json")
time.sleep(60)
'''


@unittest.skipUnless(os.name == "nt", "Windows Job objects")
class UpdateHandoffTests(unittest.TestCase):
    def run_handoff(self, *, deny_breakaway=False):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handles = {}
        worker = None
        job = launcher.ChildJob()
        with tempfile.TemporaryDirectory(prefix="tiyouju-update-handoff-") as directory:
            folder = Path(directory)
            try:
                if deny_breakaway:
                    limits = launcher._ExtendedLimit()
                    limits.BasicLimitInformation.LimitFlags = 0x2000
                    self.assertTrue(job.kernel.SetInformationJobObject(job.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
                # Windows venv python.exe is a redirector which can create the
                # real interpreter before Job assignment. Start the interpreter
                # itself so the blocked worker is exactly the process we assign.
                environment = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
                worker = subprocess.Popen([sys._base_executable, "-c", WORKER, str(ROOT), directory], env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
                job.add(worker)
                (folder / "go").touch()
                deadline = time.monotonic() + 15
                result_file = folder / "result.json"
                while not result_file.exists() and time.monotonic() < deadline:
                    self.assertIsNone(worker.poll(), "Owned worker exited before handoff")
                    time.sleep(.02)
                self.assertTrue(result_file.exists(), "Handoff timed out")
                result = json.loads(result_file.read_text())
                for name in ("background", "installer"):
                    if name not in result:
                        continue
                    handle = kernel.OpenProcess(0x100000 | 0x1000 | 0x1, False, result[name])
                    self.assertTrue(handle, name)
                    handles[name] = handle
                job.close()  # Same lifetime boundary as the old app exiting.
                worker.wait(timeout=5)
                for name, handle in handles.items():
                    kernel.WaitForSingleObject(handle, 500)
                    code = ctypes.c_uint32()
                    self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                    self.assertEqual(code.value == 259, name == "installer", name)
                return result
            finally:
                job.close()
                for handle in handles.values():
                    kernel.TerminateProcess(handle, 0)
                    kernel.WaitForSingleObject(handle, 3000)
                    kernel.CloseHandle(handle)
                if worker:
                    if worker.poll() is None:
                        worker.terminate()
                    worker.wait(timeout=5)

    def test_installer_survives_app_exit_but_ordinary_children_are_cleaned(self):
        result = self.run_handoff()
        self.assertIn("installer", result)
        self.assertNotIn("error", result)

    def test_restricted_job_reports_failure_instead_of_starting_doomed_installer(self):
        result = self.run_handoff(deny_breakaway=True)
        self.assertNotIn("installer", result)
        self.assertIn("软件将保持打开", result["error"])
        self.assertIn("手动运行", result["error"])


if __name__ == "__main__":
    unittest.main()
