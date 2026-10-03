"""Explicit local, non-cached viewing of the selected answer API credential."""
from urllib.parse import urlsplit

from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from . import library_ai_settings


@csrf_exempt
@never_cache
def reveal_view(request):
    from .views import _body, _guard

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    origin = request.headers.get("Origin", "")
    if (origin != f"{request.scheme}://{request.get_host()}"
            or urlsplit(origin).hostname not in {"127.0.0.1", "localhost", "::1"}
            or request.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"):
        return JsonResponse({"error": "请从本机设置页点击眼睛查看密钥。"}, status=403)
    payload = _body(request, limit=1024)
    if not isinstance(payload, dict) or set(payload) != {"provider"}:
        return JsonResponse({"error": "请明确选择要查看的服务商。"}, status=400)
    with library_ai_settings._lock:
        config = library_ai_settings._load()
        if payload["provider"] != config["provider"]:
            return JsonResponse({"error": "服务商配置已变化，请重新读取设置后查看。"}, status=409)
        if not config.get("key_configured"):
            return JsonResponse({"error": "当前服务商尚未保存 API 密钥。"}, status=409)
        try:
            key = library_ai_settings._key(config)
        except library_ai_settings.SettingsError:
            return JsonResponse({"error": "密钥无法读取，请重新填写并保存。"}, status=409)
    return JsonResponse({"provider": config["provider"], "key": key})
