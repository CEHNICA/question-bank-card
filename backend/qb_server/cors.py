"""
本地与预览服务跨域支持中间件 (CORS Middleware)

NOTE: 该中间件用于支持开发和预览环境（如 8089 静态预览服务器、Electron 本地调试等）
直接安全访问 8768 后端 API，避免跨域拦截导致的数据撕裂或回退 Mock。
"""
from typing import Callable
from django.http import HttpRequest, HttpResponse


class LocalCorsMiddleware:
    """
    针对本地回环地址（127.0.0.1 与 localhost）的轻量跨域支持
    """
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "OPTIONS":
            response = HttpResponse()
        else:
            response = self.get_response(request)

        origin = request.headers.get("Origin")
        if origin and ("localhost" in origin or "127.0.0.1" in origin):
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type, X-QB-Request, Referer, Authorization, X-Requested-With"
            response["Access-Control-Allow-Credentials"] = "true"
        return response
