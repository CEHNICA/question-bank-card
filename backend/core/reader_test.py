"""1.26.13：给「看图读题」这一段补一个测试连接。

之前只有「标签与答案」那一段有测试按钮：老师填了看图读题的密钥，只能等真的读一份
资料才知道能不能用。这一段现在也有测试：发一张软件自己画的合成图（两个黑点），
问模型图里有几个点。

规矩和答题那边一致：
- 只发软件自己生成的合成图，不发老师的试卷、不带任何题库内容；
- 一次只测一家（老师勾了哪家就测哪家）；
- 密钥只从本机加密存储里取，测试完不落盘、不进日志；
- MinerU 没有公开的 Token 验证接口，测试不了 —— 那一段仍以保存成功为准。
"""

from __future__ import annotations

import base64
import io

import requests
from PIL import Image, ImageDraw

from . import credential_settings, readers
from .provider_catalog import VISION

# 每家看图服务的官方接口地址（读题 worker 用的就是这几个）。
SERVICE_URLS = {
    "minimax": readers.MINIMAX_DEFAULT_URL,
    "modelscope": readers.MODELSCOPE_URL,
    "siliconflow": readers.SILICONFLOW_URL,
}

PROMPT = "这是合成连接测试，不是用户试卷。图中一共有几个黑色圆点？只输出【答案】数字，不要解释。"


class ReadingTestError(Exception):
    """这次测试没法跑（不是服务商拒绝的那种失败）。"""


def _synthetic_image() -> str:
    """一张软件自己画的图：白底两个黑点。"""

    image = Image.new("RGB", (120, 70), "white")
    drawing = ImageDraw.Draw(image)
    for box in ((15, 20, 35, 40), (70, 20, 90, 40)):
        drawing.ellipse(box, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _saved_model(service: str) -> str:
    # The credential store lives outside Django; credential_settings already
    # loaded it by path, so reuse that module instead of importing again.
    configuration = credential_settings.store.load_model_configuration()
    return readers.provider_model(service, configuration)


def test_reading_connection(service: str) -> dict:
    """Send one synthetic picture to the saved reading key and report the truth.

    Returns ``{"ok": bool, "message": str, "model": str}``.  A service that
    answers but miscounts the dots is reported as a failure with a plain
    sentence, because a model that cannot see is not usable as a reader.
    """

    if service not in SERVICE_URLS:
        raise ReadingTestError("这家服务没有可用的连接测试方式。")
    try:
        key = credential_settings.reveal_saved_key(service, 0)
    except credential_settings.CredentialStoreError as exc:
        raise ReadingTestError(str(exc)) from None
    if not key:
        raise ReadingTestError("这家还没有保存密钥，先把密钥填进去保存，再来测。")

    model = _saved_model(service)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": _synthetic_image()}},
                ],
            }
        ],
        "max_tokens": 256,
    }
    try:
        response = requests.post(
            SERVICE_URLS[service],
            json=payload,
            timeout=(10, 90),
            allow_redirects=False,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    except requests.Timeout:
        return {"ok": False, "model": model, "message": "连不上这家服务商：请求超时了。稍后再试一次，或先看看网络。"}
    except requests.ConnectionError:
        return {"ok": False, "model": model, "message": "连不上这家服务商：网络不通或地址被挡住了。"}

    if response.status_code in (401, 403):
        return {
            "ok": False,
            "model": model,
            "message": "密钥没通过：这家服务商不认这串密钥（可能复制少了、有多余空格，或已经过期）。",
        }
    if response.status_code == 429:
        return {
            "ok": False,
            "model": model,
            "message": "这家服务商现在不接请求：额度用完了，或者请求太频繁。稍后再试。",
        }
    if response.status_code >= 400:
        return {
            "ok": False,
            "model": model,
            "message": f"这家服务商拒绝了这次请求（HTTP {response.status_code}）。多半是模型名不对，或这个账号没开通这个模型。",
        }

    try:
        body = response.json()
        text = str(body["choices"][0]["message"]["content"] or "")
    except (ValueError, KeyError, IndexError, TypeError):
        return {"ok": False, "model": model, "message": "服务有回应，但没给出可读的答案。"}
    if "2" in text:
        return {
            "ok": True,
            "model": model,
            "message": f"连接成功：{VISION[service]['label']} 的 {model} 能用，也看得懂图片。",
        }
    return {
        "ok": False,
        "model": model,
        "message": "服务有回应，但没数对图里的点：这个模型可能看不懂图片（纯文字模型），或者还没开通多模态。",
    }
