"""本机 AI Agent（豆包 / WorkBuddy / MiniMax / MCP）接入状态检查、一键扫描与技能包导出视图。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

DEFAULT_PROMPT_INTRO = (
    "我的电脑已经安装了题有据。请先运行 tiyouju skill 读取当前版本的操作指南，"
    "接下来帮我查题库、核对试卷和总结 OneNote 笔记。"
)

DEFAULT_EXAMPLE_PROMPTS: tuple[dict[str, str], ...] = (
    {"label": "核对错漏题面", "prompt": "帮我看看最近上传那份试卷待核对的题目，对照原卷截图查查有没有错字或漏图。"},
    {"label": "总结 OneNote 笔记", "prompt": "只读获取我最近关联 OneNote 笔记的题目，分析手写推演卡点并给复习建议。"},
    {
        "label": "防撞车智慧组卷",
        "prompt": "从题库挑 10 道函数或导数题组一份周测卷，避开最近 30 天用过的题并保存成 A4 草稿。",
    },
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_assistant_setup():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    import assistant_setup as setup

    return setup


def _find_installed_app() -> Path | None:
    explicit = os.environ.get("TIYOUJU_APP_EXE", "").strip()
    candidates = [Path(explicit)] if explicit else []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "QuestionBankCard.exe")
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.append(Path(local) / "Programs" / "QuestionBankCard" / "QuestionBankCard.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _runtime_data_root() -> Path:
    data_root = Path(settings.DATA_ROOT)
    # 源码开发模式在 scratch 或 backend/data 下隔离生成，不污染用户真实目录。
    if not getattr(sys, "frozen", False):
        return data_root
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "QuestionBankCard"
    return data_root


def _guard_localhost(request: HttpRequest, *, require_qb_header: bool = True):
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "只能在当前电脑本机配置 AI 助手。"}, status=403)
    if require_qb_header and request.headers.get("X-QB-Request") != "1":
        return JsonResponse({"error": "请求需从题库页面发起"}, status=403)
    return None


def _build_connect_payload(
    *, home: Path | None = None, appdata: Path | None = None, data_root: Path | None = None
) -> dict[str, Any]:
    setup = _load_assistant_setup()
    app = _find_installed_app()
    effective_data_root = data_root if data_root is not None else _runtime_data_root()
    inspected = setup.inspect_mcp_config(app, home=home, appdata=appdata)
    skill_parents = setup.discover_agent_skill_parents(home=home)
    zip_path = setup.canonical_skill_zip(effective_data_root)
    cli_path = app.parent / "tiyouju.exe" if app else None
    mcp_servers = inspected.get("mcp_servers") or {"mcpServers": {"tiyouju": inspected.get("entry") or {}}}
    return {
        "cli_ready": bool(cli_path and cli_path.is_file()),
        "cli_path": str(cli_path) if cli_path and cli_path.is_file() else "",
        "command": str(inspected.get("command") or "tiyouju"),
        "skill_zip_path": str(zip_path),
        "skill_zip_ready": zip_path.is_file(),
        "mcp_servers": mcp_servers,
        "mcp_json": json.dumps(mcp_servers, ensure_ascii=False, indent=2),
        "codex_command": str(inspected.get("codex_command") or 'codex mcp add tiyouju -- "tiyouju" mcp'),
        "prompt_intro": DEFAULT_PROMPT_INTRO,
        "example_prompts": [dict(item) for item in DEFAULT_EXAMPLE_PROMPTS],
        "detected_agents": [agent_id for agent_id, _path in skill_parents],
        "clients": list(inspected.get("clients") or []),
    }


def _ensure_skill_zip(data_root: Path | None = None) -> Path:
    setup = _load_assistant_setup()
    zip_path = setup.canonical_skill_zip(data_root if data_root is not None else _runtime_data_root())
    if not zip_path.is_file():
        setup.build_skill_zip(setup.bundled_skill(), zip_path)
    return zip_path


@csrf_exempt
def connect_status_view(request: HttpRequest):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    refused = _guard_localhost(request, require_qb_header=True)
    if refused:
        return refused
    try:
        return JsonResponse(_build_connect_payload())
    except Exception as exc:  # noqa: BLE001 - 本机接口返回明确中文错误提示
        return JsonResponse({"error": f"读取 AI 助手接入状态失败：{exc}"}, status=500)


@csrf_exempt
def connect_setup_view(request: HttpRequest):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    refused = _guard_localhost(request, require_qb_header=True)
    if refused:
        return refused
    setup = _load_assistant_setup()
    try:
        app = _find_installed_app()
        effective_data_root = _runtime_data_root()
        result = setup.setup_agents(app, data_root=effective_data_root, update_path=bool(app and app.is_file()))
        return JsonResponse(
            {"ok": True, "setup": result, "connect": _build_connect_payload(data_root=effective_data_root)}
        )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": f"扫描接入失败：{exc}"}, status=500)


@csrf_exempt
def open_skill_zip_view(request: HttpRequest):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    refused = _guard_localhost(request, require_qb_header=True)
    if refused:
        return refused
    try:
        zip_path = _ensure_skill_zip()
        opened = False
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", f"/select,{zip_path}"], close_fds=True)
            opened = True
        return JsonResponse({"ok": True, "opened": opened, "path": str(zip_path)})
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": f"打开技能压缩包目录失败：{exc}"}, status=500)


@csrf_exempt
def download_skill_zip_view(request: HttpRequest):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    refused = _guard_localhost(request, require_qb_header=False)
    if refused:
        return refused
    try:
        zip_path = _ensure_skill_zip()
        response = HttpResponse(zip_path.read_bytes(), content_type="application/zip")
        response["Content-Disposition"] = 'attachment; filename="tiyouju-skill.zip"'
        return response
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": f"生成技能压缩包失败：{exc}"}, status=500)
