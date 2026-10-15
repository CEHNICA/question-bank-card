"""页面自绘的窗口按钮：最小化与关闭。

宿主窗口去掉系统标题栏之后，最小化和关闭就只剩页面右上角这两个按钮了。浏览器
没有「最小化/关闭本窗口」的脚本接口 —— ``window.close()`` 对命令行 ``--app``
打开的窗口无效，所以网页那边点了没反应。

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
ACTIONS = {"minimize", "close"}


def control_path() -> Path:
    return Path(settings.DATA_ROOT) / CONTROL_NAME


@csrf_exempt
def window_control_view(request, action: str):
    """POST /api/window/minimize 或 /api/window/close"""
    from .views import _error, _guard

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if action not in ACTIONS:
        return _error("没有这个窗口操作：只支持最小化与关闭。", 404)
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return _error("请从本机题有据页面操作。", 403)
    target = control_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"action": action, "nonce": uuid.uuid4().hex}), encoding="utf-8")
    return JsonResponse({"ok": True, "action": action})
