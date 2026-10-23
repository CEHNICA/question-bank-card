"""页面自绘的窗口按钮：最小化、最大化与关闭。

宿主窗口有系统标题栏，但老师常常看不见它 —— 窗口被拖到屏幕边缘、最大化、
或者整屏看题时，标题栏上的三个按钮就够不着了。页面右上角自己画一份，
无论窗口是什么样子，最小化、最大化、关闭都点得到。

浏览器没有「最小化/关闭本窗口」的脚本接口 —— ``window.close()`` 对命令行
``--app`` 打开的窗口无效，所以网页那边点了没反应。

于是这里把请求写成一个控制文件，由窗口启动器去操作真正的窗口句柄。两端各自
算出路径但指向同一个文件：启动器把自己的 DATA_ROOT 通过 QB_DATA_ROOT 传给服务
进程，这里再用 settings.DATA_ROOT 取回来。

每次写一个新的 nonce：启动器靠它判断「这是新请求」，而不是把上一次的动作重放一遍。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

CONTROL_NAME = "window-control.json"
ACTIONS = {"minimize", "maximize", "close"}


def control_path() -> Path:
    return Path(settings.DATA_ROOT) / CONTROL_NAME


@csrf_exempt
def window_control_view(request, action: str):
    """POST /api/window/minimize | maximize | close"""
    from .views import _error, _guard

    if request.method == "GET" and action == "available":
        # 页面右上角那三个按钮只在真的由桌面程序启动时才出现 ——
        # 普通浏览器标签页里点了也是摆设，不如不显示。
        return JsonResponse({"available": _desktop_shell()})
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if action not in ACTIONS:
        return _error("没有这个窗口操作：只支持最小化、最大化与关闭。", 404)
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return _error("请从本机题有据页面操作。", 403)
    target = control_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"action": action, "nonce": uuid.uuid4().hex}), encoding="utf-8")
    return JsonResponse({"ok": True, "action": action})


def _desktop_shell() -> bool:
    """这台服务是不是由桌面启动器带起来的。

    启动器起服务时会给它打上这个标记（它也是"能不能选文件夹"的判断依据）。
    """
    import os

    return os.name == "nt" and os.environ.get("QB_DESKTOP_EXPORT") == "1"
