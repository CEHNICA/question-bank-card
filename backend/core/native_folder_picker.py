"""An explicit Windows folder choice, with no preference or file writes.

The desktop HTTP service has one native dialog at a time. Each calling thread
initializes its own COM apartment; all interfaces and returned memory are
released before that apartment is closed. No optional GUI package is needed.
"""
from __future__ import annotations

import ctypes
import os
import uuid


class FolderPickerError(RuntimeError):
    pass


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_string(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _method(interface, index, result_type, *argument_types):
    table = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)(table[index])


class _WindowsFolderDialog:
    def __init__(self):
        self.interface = ctypes.c_void_p()
        self.initialized = False
        self.ole = ctypes.WinDLL("ole32")
        self.shell = ctypes.WinDLL("shell32")
        self.user = ctypes.WinDLL("user32")
        self.ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.ole.CoInitializeEx.restype = ctypes.c_long
        self.ole.CoUninitialize.argtypes = []
        self.ole.CoUninitialize.restype = None
        self.ole.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
                                            ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.ole.CoCreateInstance.restype = ctypes.c_long
        self.ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        self.ole.CoTaskMemFree.restype = None
        self.shell.SHCreateItemFromParsingName.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p,
                                                          ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.shell.SHCreateItemFromParsingName.restype = ctypes.c_long
        self.user.GetForegroundWindow.argtypes = []
        self.user.GetForegroundWindow.restype = ctypes.c_void_p
        self._check(self.ole.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED
        self.initialized = True
        try:
            self._check(self.ole.CoCreateInstance(
                ctypes.byref(_GUID.from_string("dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7")), None, 1,
                ctypes.byref(_GUID.from_string("42f85136-db7e-439c-85f1-e4075d135fc8")), ctypes.byref(self.interface)))
        except Exception:
            self.release()
            raise

    @staticmethod
    def _check(result):
        if result < 0:
            raise FolderPickerError("系统文件夹选择窗口未能打开，请重试或填写完整路径。")

    def _call(self, index, argument_types, *arguments):
        return _method(self.interface, index, ctypes.c_long, *argument_types)(self.interface, *arguments)

    def choose(self, initial_directory):
        # Pick existing filesystem folders, without changing the process cwd or
        # adding the user's choice to Windows recent documents.
        options = 0x20 | 0x40 | 0x800 | 0x8 | 0x2000000
        self._check(self._call(9, [ctypes.c_uint32], options))
        self._check(self._call(17, [ctypes.c_wchar_p], "选择 Word 和 PDF 的导出文件夹"))
        self._check(self._call(18, [ctypes.c_wchar_p], "选择文件夹"))
        if initial_directory:
            item = ctypes.c_void_p()
            result = self.shell.SHCreateItemFromParsingName(initial_directory, None,
                ctypes.byref(_GUID.from_string("43826d1e-e718-42ee-bc55-a1e261c37bfe")), ctypes.byref(item))
            if item:
                try:
                    if result >= 0:
                        self._call(12, [ctypes.c_void_p], item)  # Best-effort initial folder.
                finally:
                    _method(item, 2, ctypes.c_ulong)(item)
        result = self._call(3, [ctypes.c_void_p], self.user.GetForegroundWindow())
        if result & 0xFFFFFFFF == 0x800704C7:  # HRESULT_FROM_WIN32(ERROR_CANCELLED)
            return None
        self._check(result)
        item, text = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            self._check(self._call(20, [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(item)))
            if not item:
                raise FolderPickerError("未能读取所选文件夹，请重新选择。")
            # SIGDN_FILESYSPATH: never a shell URI, virtual folder or display name.
            result = _method(item, 5, ctypes.c_long, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p))(
                item, 0x80058000, ctypes.byref(text))
            self._check(result)
            if not text:
                raise FolderPickerError("未能读取所选文件夹，请重新选择。")
            return ctypes.wstring_at(text)
        finally:
            if text:
                self.ole.CoTaskMemFree(text)
            if item:
                _method(item, 2, ctypes.c_ulong)(item)

    def release(self):
        if self.interface:
            _method(self.interface, 2, ctypes.c_ulong)(self.interface)
            self.interface = ctypes.c_void_p()
        if self.initialized:
            self.ole.CoUninitialize()
            self.initialized = False


def choose_directory(initial_directory=""):
    """Return the explicit choice, or None on Cancel; never save it implicitly."""
    if os.name != "nt":
        raise FolderPickerError("文件夹选择仅用于 Windows 桌面版。")
    dialog = None
    try:
        dialog = _WindowsFolderDialog()
        return dialog.choose(initial_directory)
    except FolderPickerError:
        raise
    except (OSError, ValueError, AttributeError):
        raise FolderPickerError("系统文件夹选择窗口不可用，请重试或填写完整路径。") from None
    finally:
        if dialog is not None:
            dialog.release()
