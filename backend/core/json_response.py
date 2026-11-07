r"""给接口用的 JSON 响应：中文不再转义成 \u。

Django 自带的 JsonResponse 默认会把中文写成 \uXXXX。题库列表这一屏有四万多处
转义，响应体凭空大了五倍多（实测 61 万字节 → 12 万字节），老师那边表现就是
「点开题库要等一会儿才出来」。这里直接输出 UTF-8 的中文，浏览器读到的东西
一模一样，传输量小一大截。

内容跟 JsonResponse 完全一致：同样的编码器（日期、UUID、小数都能写），
同样的 content-type，同样的 status。
"""

from __future__ import annotations

import json

from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse


def json_response(payload, *, status: int = 200) -> HttpResponse:
    """回一个 JSON。中文直接写，不转义。"""
    body = json.dumps(payload, ensure_ascii=False, cls=DjangoJSONEncoder, separators=(",", ":"))
    return HttpResponse(body, content_type="application/json", status=status)
