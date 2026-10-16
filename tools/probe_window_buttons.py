"""Press the window's own minimise and close buttons on the running app."""
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import time
import urllib.request

BASE = "http://127.0.0.1:8768"
user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows():
    found = []

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area > 400000:
            buffer = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buffer, 256)
            found.append((area, hwnd, buffer.value))
        return True

    user32.EnumWindows(EnumWindowsProc(visit), 0)
    return sorted(found, reverse=True)


def post(path):
    request = urllib.request.Request(BASE + path, data=b"", method="POST",
                                     headers={"Content-Type": "application/json", "X-QB-Request": "1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.read().decode("utf-8")


before = windows()
print("windows before:", [(h, name) for _, h, name in before])
if not before:
    print("FAILED: the app window is not on screen")
    raise SystemExit(1)
target = before[0][1]

print("IsIconic before:", bool(user32.IsIconic(target)))
code, body = post("/api/window/minimize")
print("minimize ->", code, body)
time.sleep(4)
print("IsIconic after 4s:", bool(user32.IsIconic(target)))

if user32.IsIconic(target):
    user32.ShowWindow(target, 9)  # SW_RESTORE
    time.sleep(2)
    print("restored, IsIconic:", bool(user32.IsIconic(target)))
else:
    print("FAILED: the minimise button still does nothing")

code, body = post("/api/window/close")
print("close ->", code, body)
for _ in range(20):
    time.sleep(2)
    if not user32.IsWindow(target):
        break
print("window still on screen:", bool(user32.IsWindow(target)))
