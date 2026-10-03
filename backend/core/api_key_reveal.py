"""Explicit local, non-cached viewing of one saved API credential."""
from urllib.parse import urlsplit

from django.core.exceptions import DisallowedHost
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from . import credential_settings, library_ai_settings


def _local_reveal_guard(request):
    from .views import _guard

    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "密钥只能在本机设置页查看。"}, status=403)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    try:
        host = request.get_host()
        origin = request.headers.get("Origin", "")
        local = urlsplit(f"{request.scheme}://{host}").hostname in {"127.0.0.1", "localhost", "::1"}
        allowed = (local and origin == f"{request.scheme}://{host}"
                   and request.headers.get("Sec-Fetch-Site", "same-origin") == "same-origin")
    except (DisallowedHost, ValueError):
        allowed = False
    if not allowed:
        return JsonResponse({"error": "请从本机设置页点击眼睛查看密钥。"}, status=403)
    return None


@csrf_exempt
@never_cache
def reveal_view(request):
    from .views import _body

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _local_reveal_guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request, limit=1024)
    if not isinstance(payload, dict) or set(payload) not in ({"provider"}, {"provider", "index"}):
        return JsonResponse({"error": "请明确选择要查看的服务商。"}, status=400)
    if (not isinstance(payload["provider"], str) or payload["provider"] not in library_ai_settings.DEFAULTS
            or ("index" in payload and (type(payload["index"]) is not int or payload["index"] != 0))):
        return JsonResponse({"error": "请选择有效的服务商和密钥序号。"}, status=400)
    with library_ai_settings._lock:
        config = library_ai_settings.saved_key_configuration(payload["provider"])
        if not config.get("key_configured"):
            return JsonResponse({"error": "这个服务商尚未保存 API 密钥。"}, status=409)
        try:
            key = library_ai_settings._key(config)
        except library_ai_settings.SettingsError:
            return JsonResponse({"error": "密钥无法读取，请重新填写并保存。"}, status=409)
    return JsonResponse({"provider": config["provider"], "key": key}
                        | ({"index": 0} if "index" in payload else {}))


@csrf_exempt
@never_cache
def credential_reveal_view(request):
    from .views import _body

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _local_reveal_guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request, limit=1024)
    if not isinstance(payload, dict) or set(payload) != {"service", "index"}:
        return JsonResponse({"error": "请明确选择读题服务和密钥序号。"}, status=400)
    try:
        key = credential_settings.reveal_saved_key(payload["service"], payload["index"])
    except credential_settings.CredentialValidationError:
        return JsonResponse({"error": "请选择有效的读题服务和密钥序号。"}, status=400)
    except credential_settings.CredentialStoreError:
        return JsonResponse({"error": "这条密钥无法读取或已移除，请重新读取设置或重新保存。"}, status=409)
    return JsonResponse({"service": payload["service"], "index": payload["index"], "key": key})
