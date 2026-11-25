"""tiyouju：让 AI 助手（豆包、Claude、Codex、Trae……）完整操作题有据的命令行。

题有据打开后在本机 127.0.0.1 上有一个服务，网页界面就是通过它干活的。
这个命令行调用同一套服务：上传卷子、等它读完、看每道题的原卷截图和读出的
文字、改字、处理配图、打勾、入库、查题库。``tiyouju mcp`` 把同样的能力
做成 MCP 服务器，给支持 MCP 的 AI 用。

只用 Python 标准库，打包成 ``tiyouju.exe`` 和题有据放在同一个文件夹。

几条规矩（写在这里，也写在 skills/tiyouju/SKILL.md 里）：
- AI 打的勾记成“AI 通过”，和人工通过分开显示；AI 不能撤销人工通过。
- 不碰密钥：缺密钥时请使用者在软件“设置 → 常用”里填。
- 删除题卡、撤回入库这类改不回来的操作不在命令行里。
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import assistant_setup as setup

try:
    from core.version import APP_VERSION as VERSION
except ImportError:  # source checkout: backend/ is not on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))
    try:
        from core.version import APP_VERSION as VERSION
    except ImportError:
        VERSION = "dev"

DEFAULT_PORT = 8768
DEFAULT_AGENT = "AI 助手"
APP_EXE = "QuestionBankCard.exe"
OPTION_KEYS = ("A", "B", "C", "D", "E")
SLOTS = ("stem", *OPTION_KEYS)
TYPES = {
    "single_choice": "单选题", "multiple_choice": "多选题", "fill_blank": "填空题", "true_false": "判断题",
    "free_response": "解答题", "unknown": "题型未定",
}
# 题型没读出来的题不能通过、不能入库（1.10）：先 fix --type 选一个。
TYPE_BLOCKED = "题型还没定：用 fix --type 选题型（single_choice/multiple_choice/fill_blank/true_false/free_response）后才能通过"
DECIDED_TYPES = [key for key in TYPES if key != "unknown"]
STATES = {
    "waiting": "等待识读", "reading": "识读中", "green": "识读完成", "yellow": "需核对", "red": "识读失败",
}
FIGURE_BLOCKS = {"blocked_missing": "可能漏图", "conflict": "配图冲突"}
ACTIVE = {"queued", "parsing", "segmenting", "reading"}
FILTERS = ("all", "todo", "green", "approved", "ai", "human")

# Exit codes an agent can branch on.
EXIT_ERROR = 1
EXIT_NOT_RUNNING = 2
EXIT_NEEDS_USER = 3


class CliError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- 连接本机的题有据


def user_root() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(local) / "QuestionBankCard" if local else Path.home() / ".questionbankcard"


def base_url() -> str:
    """Where the running app listens: TIYOUJU_URL, the launcher's instance file, or 8768."""
    explicit = os.environ.get("TIYOUJU_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    try:
        record = json.loads((user_root() / "runtime" / "instance.json").read_text(encoding="utf-8"))
        port = int(record.get("port"))
        if 0 < port < 65536:
            return f"http://127.0.0.1:{port}"
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return f"http://127.0.0.1:{DEFAULT_PORT}"


class Client:
    """Talks to the local app only; never through a system proxy."""

    def __init__(self, url: str | None = None, agent: str = DEFAULT_AGENT):
        self.url = (url or base_url()).rstrip("/")
        self.agent = agent
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _open(self, request: urllib.request.Request, timeout: float):
        try:
            return self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read().decode("utf-8")).get("error")
            except (ValueError, UnicodeDecodeError, AttributeError):
                message = None
            raise CliError(message or f"题有据返回错误 {error.code}") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
            raise CliError(
                f"连不上题有据（{self.url}）。软件没有打开的话，运行 `tiyouju start`。", EXIT_NOT_RUNNING,
            ) from error

    def get(self, path: str, timeout: float = 30) -> dict:
        with self._open(urllib.request.Request(self.url + path), timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_bytes(self, path: str, timeout: float = 60) -> bytes:
        with self._open(urllib.request.Request(self.url + path), timeout) as response:
            return response.read()

    def post(self, path: str, body: dict | None = None, timeout: float = 60) -> dict:
        data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.url + path, data=data, method="POST", headers={
            "Content-Type": "application/json", "X-QB-Request": "1",
        })
        with self._open(request, timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def patch(self, path: str, body: dict | None = None, timeout: float = 60) -> dict:
        data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.url + path, data=data, method="PATCH", headers={
            "Content-Type": "application/json", "X-QB-Request": "1",
        })
        with self._open(request, timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def upload(self, files: list[Path], fields: dict[str, str], timeout: float = 900) -> dict:
        boundary = f"----tiyouju{uuid.uuid4().hex}"
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
        for path in files:
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            quoted = urllib.parse.quote(path.name)
            head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                    f'filename="{quoted}"; filename*=UTF-8\'\'{quoted}\r\nContent-Type: {kind}\r\n\r\n')
            parts.append(head.encode("utf-8") + path.read_bytes() + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        request = urllib.request.Request(self.url + "/api/papers", data=b"".join(parts), method="POST", headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}", "X-QB-Request": "1",
        })
        with self._open(request, timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def running(self) -> bool:
        try:
            return self.get("/api/health", timeout=3).get("ok") is True
        except CliError:
            return False


# ---------------------------------------------------------------- 找试卷、找题


def short(paper_id: str) -> str:
    return str(paper_id)[:8]


def papers(client: Client) -> list[dict]:
    return client.get("/api/papers").get("papers", [])


def find_paper(client: Client, ref: str) -> dict:
    """By id (or its first characters), "latest", or part of the name."""
    items = papers(client)
    if not items:
        raise CliError("题有据里还没有试卷。先用 `tiyouju upload 文件` 上传一份。")
    text = str(ref or "").strip()
    if text in {"", "latest", "最新"}:
        return max(items, key=lambda item: item.get("created_at") or "")
    lowered = text.lower()
    by_id = [item for item in items if str(item["id"]).lower().startswith(lowered)]
    if len(by_id) == 1 or (by_id and len(lowered) >= 32):
        return by_id[0]
    by_name = [item for item in items
               if lowered in str(item.get("name", "")).lower() or lowered in str(item.get("filename", "")).lower()]
    if len(by_name) == 1:
        return by_name[0]
    matches = by_id or by_name
    if not matches:
        raise CliError(f"找不到试卷“{text}”。运行 `tiyouju papers` 看看有哪些。")
    listing = "；".join(f"{short(item['id'])} {item.get('name')}" for item in matches[:8])
    raise CliError(f"“{text}”对得上好几份试卷：{listing}。请用编号（前 8 位就行）。")


def paper_detail(client: Client, paper: dict) -> dict:
    return client.get(f"/api/papers/{paper['id']}")


def find_card(detail: dict, ref: str) -> dict:
    """By question number, or ``#<id>`` when numbers repeat (books restart numbering)."""
    text = str(ref).strip()
    questions = detail.get("questions", [])
    if text.startswith("#"):
        for question in questions:
            if str(question["id"]) == text[1:]:
                return question
        raise CliError(f"这份试卷里没有题卡 {text}")
    try:
        number = int(text.removeprefix("第").removesuffix("题"))
    except ValueError:
        raise CliError(f"题号“{text}”不对：用数字，例如 9；题号重复时用 #编号") from None
    matches = [question for question in questions if question.get("number") == number]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise CliError(f"这份试卷里没有第 {number} 题")
    listing = "；".join(f"#{item['id']}（{group_title(item) or '未分组'}）" for item in matches)
    raise CliError(f"第 {number} 题有好几道：{listing}。请用 #编号。")


def group_title(question: dict) -> str:
    group = question.get("group")
    return (group.get("title") or "") if isinstance(group, dict) else ""


# ---------------------------------------------------------------- 题卡的状态


def approval(question: dict) -> str:
    """human / ai / "" (the server only reports approvals of the current version)."""
    if not question.get("approved"):
        return ""
    return question.get("approved_by") or "human"


def issues(question: dict) -> list[str]:
    review = question.get("figure_review") or {}
    found = []
    if question.get("state") == "red":
        found.append(question.get("error") or "识读失败")
    if review.get("status") in FIGURE_BLOCKS:
        found.append(f"{FIGURE_BLOCKS[review['status']]}：{review.get('reason', '')}".rstrip("："))
    if question.get("approval_stale"):
        found.append("通过以后内容变了，需要重新核对")
    blocked = type_blocked(question)
    if blocked:
        found.append(TYPE_BLOCKED)
    for flag in question.get("flags") or []:
        if blocked and str(flag).startswith("题型没读出来"):
            continue
        if flag not in found and not any(flag in item for item in found):
            found.append(flag)
    return found


def type_blocked(question: dict) -> bool:
    return bool(question.get("type_blocked")) and question.get("state") in {"green", "yellow"}


def needs_check(question: dict) -> bool:
    review = question.get("figure_review") or {}
    return not approval(question) and (
        question.get("state") in {"yellow", "red"} or review.get("status") in FIGURE_BLOCKS
        or bool(question.get("approval_stale")) or type_blocked(question)
    )


def matches_filter(question: dict, name: str) -> bool:
    who = approval(question)
    if name == "todo":
        return needs_check(question)
    if name == "green":
        return not who and question.get("state") == "green" and not needs_check(question)
    if name == "approved":
        return bool(who)
    if name in {"ai", "human"}:
        return who == name
    return True


def card_summary(question: dict) -> dict:
    review = question.get("figure_review") or {}
    publication = question.get("publication") or {}
    return {
        "id": question["id"],
        "number": question.get("number"),
        "group": group_title(question),
        "type": question.get("question_type"),
        "type_blocked": type_blocked(question),
        "origin": question.get("origin") or "",
        "state": question.get("state"),
        "approved_by": approval(question),
        "approval_agent": question.get("approval_agent") or "",
        "needs_check": needs_check(question),
        "issues": issues(question),
        "figures": len(question.get("figures") or []),
        "figure_status": review.get("status", ""),
        "published": bool(publication),
        # mineru: MinerU's draft nobody has checked against the page yet (AI 助手读题).
        "text_source": question.get("text_source") or "",
        "stem": question.get("stem") or "",
        "body_mode": question.get("body_mode", "text"),
        "processing_mode": question.get("processing_mode", "auto"),
        "question_images": question.get("question_images") or [],
    }


def first_line(text: str, limit: int = 60) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[:limit - 1] + "…"


def candidate_key(item: dict) -> str | None:
    """The page/bbox identity the app uses for a figure candidate (figure_policy.candidate_key).

    1.25.97：这里的四舍五入必须和应用、和网页完全一样 —— 一位小数、逢五进位、
    夹到 0–1000、去尾零。以前这里用的是 Python 的 round(v, 1)（银行家舍入），
    90.55 会得 90.5，而应用和网页得 90.6：同一张候选图三个身份，助手按自己那份
    去核对，结论和界面对不上。
    """
    bbox = item.get("bbox") if isinstance(item, dict) else None
    if not isinstance(item.get("page_idx") if isinstance(item, dict) else None, int) \
            or not isinstance(bbox, list) or len(bbox) != 4:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in bbox):
        return None
    x0, y0, x1, y1 = (max(0, min(1000, float(value))) for value in bbox)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None

    def shown(number: float) -> str:
        return f"{number:.1f}".rstrip("0").rstrip(".")

    rounded = [float(Decimal(repr(float(value))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
               for value in (x0, y0, x1, y1)]
    return f"{item['page_idx']}:" + ",".join(shown(value) for value in rounded)


def candidates(question: dict) -> list[dict]:
    """Figure candidates numbered 图1、图2… as drawn on ``show``'s marked crop."""
    used = {}
    for figure in question.get("figures") or []:
        for piece in (figure, *(figure.get("parts") or [])):
            key = piece.get("candidate_key") or candidate_key(piece)
            if key:
                used[key] = figure.get("slot", "stem")
    result = []
    for index, item in enumerate(question.get("figure_candidates") or []):
        key = candidate_key(item)
        if key is None:
            continue
        result.append({"number": index + 1, "key": key, "page": item["page_idx"] + 1,
                       "bbox": item["bbox"], "used_as": used.get(key, "")})
    return result


# ---------------------------------------------------------------- 命令


READERS = ("assistant", "modelscope", "minimax", "siliconflow")
CHECKERS = ("auto", "modelscope", "minimax", "siliconflow")
PLANS = ("auto", "plus", "max", "ultra", "payg")
ASSISTANT_NOTE = "AI 助手读题：题卡先用 MinerU 识别的文字，由你（AI 助手）对照原卷截图逐题核对、改字"


def reading_summary(status: dict) -> dict:
    """Keys, who reads, and what the user still has to fill in -- never a key itself."""
    engines = status.get("engines") or {}
    choices = engines.get("choices") or []
    signup = engines.get("signup") or {}
    keys = {"mineru": bool(status.get("mineru"))}
    services = []
    for choice in choices:
        service = choice.get("provider_key")
        keys[service] = bool(choice.get("available"))
        services.append({"service": service, "label": choice.get("provider") or service,
                         "model": choice.get("model") or "", "has_key": keys[service],
                         "free": bool(choice.get("free")), "note": choice.get("note") or "",
                         "signup": signup.get(service, "")})
    assistant = bool(status.get("assistant_mode"))
    missing = []
    if not keys["mineru"]:
        missing.append({"what": "mineru", "label": "MinerU Token（到期日和账户额度见管理页）",
                        "signup": signup.get("mineru", "")})
    if not assistant and not any(item["has_key"] for item in services):
        missing.append({
            "what": "vision", "label": "一家看图读题服务的密钥",
            "options": [{key: item[key] for key in ("service", "label", "note", "signup")}
                        for item in services if item["free"]],
            "or": "tiyouju config --reader assistant（不用看图密钥，由 AI 助手核对；先征得使用者同意）",
        })
    saved = engines.get("saved") or engines.get("selected") or {}
    vendor = {item.get("key"): item.get("provider") for item in choices}

    def named(role: str) -> str | None:
        # "魔搭 Qwen3.5-35B-A3B": the model ID alone does not say whose it is.
        model = status.get(role)
        if assistant or not model:
            return None
        return " ".join(filter(None, (vendor.get(engines.get("primary" if role == "reader" else role)), model)))

    return {
        "keys": keys,
        "assistant_mode": assistant,
        "reader": named("reader"),
        "checker": named("checker"),
        "minimax_plan": ((engines.get("saved") or {}).get("plans") or engines.get("plans") or {}).get("minimax"),
        "chosen": {"reader": saved.get("primary"), "checker": saved.get("checker")},
        "pending_change": bool(engines.get("pending_change")),
        "services": services,
        "missing": missing,
    }


def cmd_status(client: Client, args) -> dict:
    if not client.running():
        raise CliError("题有据没有打开。运行 `tiyouju start` 打开它。", EXIT_NOT_RUNNING)
    status = client.get("/api/status")
    items = papers(client)
    counts: dict[str, int] = {}
    for item in items:
        label = item.get("status_label") or item.get("status")
        counts[label] = counts.get(label, 0) + 1
    return {
        "running": True,
        "url": client.url,
        "app_version": status.get("app_version"),
        "cli_version": VERSION,
        "upload_enabled": bool(status.get("upload_enabled")),
        "automatic_parse_ready": bool(status.get("automatic_parse_ready", status.get("upload_enabled"))),
        **reading_summary(status),
        "papers": len(items),
        "papers_by_status": counts,
    }


def reading_lines(result: dict) -> list[str]:
    labels = {"mineru": "MinerU", **{item["service"]: item["label"] for item in result["services"]}}
    mark = lambda ok: "已填" if ok else "未填"  # noqa: E731
    lines = ["密钥：" + " · ".join(f"{labels.get(service, service)} {mark(ok)}" for service, ok in result["keys"].items())]
    if result["assistant_mode"]:
        lines.append(f"读题：{ASSISTANT_NOTE}")
    else:
        lines.append(f"读题：{result.get('reader') or '没有可用的读题模型'}"
                     + (f"，复核：{result['checker']}" if result.get("checker") else ""))
    if result["pending_change"]:
        lines.append("（刚改过的读题设置从下一份新卷子开始生效）")
    for item in result["missing"]:
        if item["what"] == "mineru":
            lines.append(f"缺：{item['label']}，申请：{item['signup']}")
        else:
            options = "；".join(f"{option['label']}（{option['note']}）{option['signup']}" for option in item["options"])
            lines.append(f"缺：{item['label']}，免费的有：{options}；或者 {item['or']}")
    return lines


def show_status(result: dict) -> str:
    lines = [
        f"题有据 {result['app_version']} 正在运行（{result['url']}）",
        *reading_lines(result),
        f"可以导入原卷：{'是，可手工切题或本地处理文字 PDF' if result['upload_enabled'] else '当前不可用，请查看软件中的具体错误'}",
        f"自动云识读：{'已配置，真实服务仍以任务结果为准' if result.get('automatic_parse_ready') else '需要配置读题服务；手工录入可以继续'}",
        f"试卷：{result['papers']} 份" + (
            "（" + "，".join(f"{label} {count}" for label, count in result["papers_by_status"].items()) + "）"
            if result["papers_by_status"] else ""),
    ]
    return "\n".join(lines)


def cmd_config(client: Client, args) -> dict:
    """Show or change how papers are read (never keys: those stay in the app)."""
    status = client.get("/api/status")
    engines = status.get("engines") or {}
    engine_of = {item.get("provider_key"): item.get("key") for item in engines.get("choices") or []}
    current = engines.get("saved") or engines.get("selected") or {}
    changes: dict = {}
    for option, role, keep in ((args.reader, "primary", "assistant"), (args.checker, "checker", "auto")):
        if not option:
            continue
        if option != keep and option not in engine_of:
            raise CliError(f"这个版本的题有据不认识“{option}”。请先更新题有据。")
        changes[role] = option if option == keep else engine_of[option]
    if changes or args.minimax_plan:
        body = {"primary": current.get("primary"), "checker": current.get("checker"),
                "arbiter": current.get("arbiter"), **changes}
        if args.minimax_plan:
            body["plans"] = {"minimax": args.minimax_plan}
        client.post("/api/settings/models", body)
        status = client.get("/api/status")
    return {"changed": bool(changes or args.minimax_plan), "upload_enabled": bool(status.get("upload_enabled")),
            **reading_summary(status)}


def show_config(result: dict) -> str:
    lines = ["已保存，从下一份新卷子开始生效（正在读的卷子不受影响）。"] if result["changed"] else []
    lines += reading_lines(result)
    lines.append("可选的看图读题服务（tiyouju config --reader 名字）：")
    for item in result["services"]:
        free = "免费 · " if item["free"] else ""
        key = "已填密钥" if item["has_key"] else "未填密钥"
        lines.append(f"  {item['service']:<12}{item['label']} {item['model']}（{free}{key}）")
    lines.append(f"  {'assistant':<12}{ASSISTANT_NOTE}")
    return "\n".join(lines)


def app_executable() -> Path | None:
    explicit = os.environ.get("TIYOUJU_APP", "").strip()
    places = [Path(explicit)] if explicit else []
    if getattr(sys, "frozen", False):
        places.append(Path(sys.executable).resolve().parent / APP_EXE)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        places.append(Path(local) / "Programs" / "QuestionBankCard" / APP_EXE)
    return next((place for place in places if place.is_file()), None)


def cmd_start(client: Client, args) -> dict:
    if client.running():
        return {"running": True, "started": False, "url": client.url}
    exe = app_executable()
    if exe is None:
        raise CliError("没找到题有据。先安装：见 https://github.com/CEHNICA/question-bank-card 的“让 AI 助手帮你用”。")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        time.sleep(1.5)
        fresh = Client(agent=client.agent)  # the port is known once the launcher wrote it
        if fresh.running():
            client.url = fresh.url
            return {"running": True, "started": True, "url": fresh.url}
    raise CliError(f"题有据 {args.timeout} 秒内没有启动好。请让使用者看一下电脑上的题有据窗口。", EXIT_NEEDS_USER)


def cmd_assistant_setup(_client, args) -> dict:
    """Installation facts and explicitly requested local finishing steps only."""
    try:
        return setup.assistant_setup(
            app_executable(), skill_dir=Path(args.skill_dir) if args.skill_dir else None,
            replace_skill=args.replace_skill, desktop=args.desktop,
        )
    except (setup.SetupError, OSError) as error:
        raise CliError(str(error), EXIT_NEEDS_USER) from error


def cmd_setup_agents(_client, args) -> dict:
    """自动将题有据技能与命令行接入本机主流 AI Agent（参考 egolite 配置方式）。"""
    try:
        result = setup.setup_agents(
            app_executable(),
            home=Path(args.home) if getattr(args, "home", None) else None,
            update_path=not getattr(args, "no_path", False),
            replace=bool(getattr(args, "replace", False)),
        )
        result["quiet"] = bool(getattr(args, "quiet", False))
        return result
    except (setup.SetupError, OSError) as error:
        raise CliError(str(error), EXIT_NEEDS_USER) from error


def cmd_skill(_client, _args) -> dict:
    """输出与当前安装版本完全匹配的 AI Agent 接管指南（对应 ego-browser skill）。"""
    app = app_executable()
    guide_lines = [
        f"# 题有据（tiyouju）AI Agent 接管指南 · v{VERSION}",
        "",
        "以下指南与当前本机已安装的 `tiyouju` 版本完全匹配，可直接通过命令行接管题有据。",
        f"- 当前 CLI 版本：`{VERSION}`",
        f"- 主程序路径：`{app}`" if app else "- 主程序路径：未在默认安装目录检测到 QuestionBankCard.exe",
        "- 默认本地服务地址：`http://127.0.0.1:8768`（工作台 `/eagle` · 录题 `/ ` · 设置 `/settings`）",
        "",
        "## 1. 启动与配置软件",
        "- `tiyouju status --json`：查看本机服务是否运行、版本、已配置密钥状态及读题配置。",
        "- `tiyouju start`：启动题有据本机服务并就绪（退出码 `2` 表示应用未启动）。",
        "- `tiyouju config --reader <engine> --checker <checker>`：查看或调整读题与复核引擎（不碰密钥；更改前先与用户确认）。",
        "- `tiyouju features`：查看或明确启用/关闭标签候选题、参考答案等开关。",
        "- `tiyouju setup-agents`：把技能重新同步并链接到所有本地 AI Agent 目录，并将命令目录加入用户 PATH。",
        "",
        "## 2. 录入试卷与自主探索切题",
        "- `tiyouju papers --json`：列出所有待处理或已导入的试卷与资料。",
        "- `tiyouju upload <文件...> [--parse-mode auto|manual|native|mineru] [--allow-cloud] [--cut-only] [--scan-only] [--book] [--wait]`：导入 PDF/Word/照片；`--scan-only` 仅保留原页和版面扫描块(`blocks`)并停在就绪状态；`--cut-only` 只切图不识读。",
        "- `tiyouju pages <paper> [--pages 0,1 | 0-3] [--grid] [--out DIR]`：导出原页高清图（加 `--grid` 叠加 0–1000 坐标网格便于精准框题）。",
        "- `tiyouju blocks <paper> [--pages 0,1] --json`：读取 MinerU 或本地 PDF 提取出的文本/表格/图片块及 `bbox` 坐标与结构建议。",
        "- `tiyouju cut <paper> --file <json>`（或 `--region/--stem/--figure`）：由助手自主切分题卡并支持双轨提交：传入 `stem` 则跳过内置 OCR 直接录文字（`assistant`）；不传 `stem` 则存为原图题卡，必要时用 `tiyouju read-cuts <paper>` 交给内置看图模型识读。",
        "- `tiyouju layout <regions|renumber|split|merge|undo> <paper> ...`：精细调整范围、改题号、拆题、合题或整批撤销切题。",
        "- `tiyouju cards <paper> --filter todo --json`：列出待核对题卡与疑点。",
        "- `tiyouju show <paper> <n>`：导出原卷裁图与配图，必须先看图核对文字、公式、选项和配图后再修改或打勾。",
        "- `tiyouju fix <paper> <n> --stem-file <path>`：修改未入库题卡（支持 `$...$` LaTeX，可用 `--section` 设置章节小节）。",
        "- `tiyouju figures <paper> <n> --use <idx>`：指定或更新题卡配图。",
        "- `tiyouju approve <paper> <n>` / `tiyouju publish <paper>`：核对无误后标记 AI 通过并发布入库。",
        "",
        "## 3. 组织题库内容与整理文件夹",
        "- `tiyouju folders list --json`：查看「我的文件夹」、试卷夹、教材章节树、智能文件夹、排序及标签个人分组。",
        "- `tiyouju folders create/rename/move/delete/add/remove/sort`：直接建夹建子夹、归档移出题目、设置自然序/名称/时间排序（可撤销）。",
        "- `tiyouju folders set-section`：批量整理已入库教材/教辅的章节与小节路径（直接生效且可撤销）。",
        "- `tiyouju folders paper <paper> [--name 名称] [--type exam|book]`：重命名试卷/教材或切换「试卷 ↔ 教材」。",
        "- `tiyouju folders tag-group ...`：整理标签管理里的个人分组及分组内标签。",
        "- `tiyouju folders undo`：撤销最近一次文件夹或章节整理操作。",
        "- `tiyouju locate <定位码或位置文本> [--json]`：支持 `Q-xxxxxxxx`、UUID 或用户复制的一整段题目定位文本，秒查完整题干、标签与本机原图。",
        "- `tiyouju library [关键词] --json`：按题干、标签、题型检索正式题库内容（每条均带 `short_id`）。",
        "- `tiyouju whereami --json`：确认老师当前正在题库工作台查看/编辑哪道题，避免冲突。",
        "- `tiyouju claim` / `tiyouju release` / `tiyouju workbench`：领取和查看助手工作台任务（支持 `workbench --clear` 批量清空提案）。",
        "- `tiyouju solutions import --file <json> --dry-run`：批量将标准答案书/官方解析写入解析层（不动源题卡、不清通过勾、默认不覆盖已有解析；先干跑抽查，确认客观/填空题填好 `answer` 后再写入；支持 `tiyouju solutions undo` 整批撤销）。",
        "- `tiyouju propose <publication_id> ...`：仅限单题题面勘误交成提案（等老师采纳；严禁用 `propose` 批量灌整本答案书）。",
        "- `tiyouju enrich tasks` / `prepare` / `submit`：按任务领取原图并交回标签候选或参考答案初稿。",
        "- `tiyouju backup [--dest DIR]`：打包备份题库或指定试卷/文件夹。",
        "",
        "## 4. 关键安全规则与退出码",
        "- 绝不索要、读取或输出真实 API 密钥；缺密钥请引导用户自己在「设置 → API 配置」填写。",
        "- 已入库题目严禁直接篡改，一律走 `tiyouju propose` 提案；不做无人值守自动通过入库。",
        "- 退出码：`0` 成功，`1` 执行错误，`2` 应用未启动（先运行 `tiyouju start`），`3` 需用户处理（停下并说明具体原因）。",
        "- 如客户端只能使用 stdio MCP，可配置命令 `tiyouju mcp`；支持本机命令行时优先使用 `tiyouju` CLI。",
        "",
        f"[tiyouju:skill] end of guide (version {VERSION})",
    ]
    return {"version": VERSION, "guide": "\n".join(guide_lines)}


def show_skill(result: dict) -> str:
    return str(result.get("guide") or "")


def show_setup_agents(result: dict) -> str:
    if result.get("quiet"):
        return ""
    return setup.format_setup_agents_report(result)


def paper_summary(item: dict) -> dict:
    counts = item.get("counts") or {}
    return {
        "id": item["id"], "name": item.get("name"), "status": item.get("status"),
        "status_label": item.get("status_label"), "material_type": item.get("material_type"),
        "demo": bool(item.get("demo")), "created_at": item.get("created_at"),
        "total": counts.get("total", item.get("total", 0)), "approved": counts.get("approved", 0),
        "published": counts.get("published", 0), "yellow": counts.get("yellow", 0), "red": counts.get("red", 0),
        "error": item.get("error") or "",
        "processing_plan": item.get("processing_plan") or {},
    }


def cmd_papers(client: Client, args) -> list[dict]:
    return [paper_summary(item) for item in sorted(papers(client), key=lambda item: item.get("created_at") or "",
                                                   reverse=True)]


def show_papers(result: list[dict]) -> str:
    if not result:
        return "还没有试卷。"
    lines = []
    for item in result:
        tail = f"{item['total']} 题 · 通过 {item['approved']} · 入库 {item['published']}" if item["total"] else ""
        demo = "（练习用）" if item["demo"] and "练习" not in str(item["name"]) else ""
        lines.append(f"{short(item['id'])}  {item['name']}{demo}  [{item['status_label']}]  {tail}".rstrip())
    return "\n".join(lines)


def cmd_upload(client: Client, args) -> dict:
    files = [Path(item).expanduser() for item in args.files]
    missing = [str(item) for item in files if not item.is_file()]
    if missing:
        raise CliError("找不到文件：" + "、".join(missing))
    parameters = {"material_type": "book" if args.book else "exam"}
    parse_mode = getattr(args, "parse_mode", None) or "auto"
    if parse_mode not in {"auto", "manual", "native", "mineru"}:
        raise CliError("处理方式只能是 auto、manual、native 或 mineru")
    parameters["parse_mode"] = parse_mode
    parameters["allow_cloud"] = "1" if getattr(args, "allow_cloud", False) is True else "0"
    if getattr(args, "cut_only", False) is True:
        parameters["cut_only"] = "1"
    if getattr(args, "scan_only", False) is True:
        parameters["scan_only"] = "1"
    result = client.upload(files, parameters)
    paper = result["paper"]
    summary = {"paper": paper_summary(paper), "duplicate": bool(result.get("duplicate"))}
    if args.wait:
        summary["wait"] = wait_for(client, paper, args.timeout, quiet=args.json)
    return summary


def show_upload(result: dict) -> str:
    paper = result["paper"]
    head = "这份文件以前上传过，" if result["duplicate"] else "已上传，"
    text = f"{head}试卷编号 {short(paper['id'])}（{paper['name']}），状态：{paper['status_label']}。"
    if "wait" in result:
        text += "\n" + show_wait(result["wait"])
    else:
        plan = paper.get("processing_plan") or {}
        if plan.get("scan_only"):
            text += f"\n已启用仅扫描模式（scan_only）；就绪后可用 `tiyouju blocks {short(paper['id'])} --json` 或 `tiyouju pages {short(paper['id'])} --grid` 探查并切题。"
        elif plan.get("word_direct"):
            text += f"\n已从 Word 原件直接提取题面与答案解析，可用 `tiyouju cards {short(paper['id'])}` 对照原卷核对。"
        elif plan.get("mode") == "manual":
            text += "\n原卷已在本机准备好，可在软件中手工框题或由助手 `tiyouju pages`/`cut`；没有发起识读。"
        else:
            text += f"\n用 `tiyouju wait {short(paper['id'])}` 等它读完。"
    return text


def wait_for(client: Client, paper: dict, timeout: float, quiet: bool = False, interval: float = 5) -> dict:
    deadline = time.monotonic() + max(1, timeout)
    last = None
    while True:
        detail = paper_detail(client, paper)
        current = detail["paper"]
        status = current.get("status")
        line = f"{current.get('status_label')} {current.get('progress') or 0}/{current.get('total') or '?'}"
        if line != last and not quiet:
            print(f"  … {line}", file=sys.stderr, flush=True)
            last = line
        if status == "ready":
            cards = [card_summary(question) for question in detail.get("questions", [])]
            return {"paper": paper_summary(current), "done": True,
                    "cards": len(cards), "todo": sum(card["needs_check"] for card in cards),
                    "green": sum(matches_filter(question, "green") for question in detail.get("questions", []))}
        if status == "failed":
            raise CliError(f"这份试卷处理失败：{current.get('error') or '原因不明'}。可以让使用者在软件里点“重试”。")
        if status == "needs_grouping":
            raise CliError("这份资料的题号重复或重新开始了，需要使用者在软件里确认是一份资料还是几份。", EXIT_NEEDS_USER)
        if current.get("recoverable_pause"):
            raise CliError("读题服务的额度用完了，已暂停。请使用者充值或换密钥后在软件里继续。", EXIT_NEEDS_USER)
        if time.monotonic() >= deadline:
            return {"paper": paper_summary(current), "done": False}
        time.sleep(interval)


def cmd_wait(client: Client, args) -> dict:
    return wait_for(client, find_paper(client, args.paper), args.timeout, quiet=args.json)


def show_wait(result: dict) -> str:
    paper = result["paper"]
    if not result["done"]:
        return f"{short(paper['id'])} 还在处理（{paper['status_label']}），再运行一次 wait 接着等。"
    if paper.get("processing_plan", {}).get("mode") == "manual":
        return f"{short(paper['id'])} 原卷准备好了：{result['cards']} 道手工题，需核对 {result['todo']} 道。可以继续手工框题和配图。"
    return (f"{short(paper['id'])} 读完了：{result['cards']} 道题，需逐题核对 {result['todo']} 道，"
            f"绿卡 {result['green']} 道，仍须对照原卷。下一步：`tiyouju cards {short(paper['id'])} --filter todo`。")


def summarize_cards(cards: list[dict]) -> dict:
    """统计试卷题卡的状态大盘与疑点分类。"""
    from collections import Counter

    total = len(cards)
    human_approved = sum(1 for c in cards if c.get("approved_by") == "human")
    ai_approved = sum(1 for c in cards if c.get("approved_by") == "ai")
    approved = human_approved + ai_approved
    todo = sum(1 for c in cards if c.get("needs_check"))
    green = sum(1 for c in cards if not c.get("approved_by") and c.get("state") == "green" and not c.get("needs_check"))
    published = sum(1 for c in cards if c.get("published"))

    type_blocked_cnt = sum(1 for c in cards if c.get("type_blocked"))
    figure_issues_cnt = sum(1 for c in cards if c.get("figure_status") in FIGURE_BLOCKS or any("图" in str(iss) for iss in c.get("issues", [])))
    source_image_cnt = sum(1 for c in cards if c.get("body_mode") == "source_image")

    issue_counter: Counter[str] = Counter()
    for c in cards:
        for iss in c.get("issues", []):
            issue_counter[str(iss)] += 1

    return {
        "total": total,
        "approved": approved,
        "human_approved": human_approved,
        "ai_approved": ai_approved,
        "todo": todo,
        "green": green,
        "published": published,
        "type_blocked": type_blocked_cnt,
        "figure_issues": figure_issues_cnt,
        "source_image_mode": source_image_cnt,
        "top_issues": issue_counter.most_common(5),
    }


def cmd_cards(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    detail = paper_detail(client, paper)
    all_cards = [card_summary(question) for question in detail.get("questions", [])
                 if matches_filter(question, args.filter)]
    summary = summarize_cards(all_cards)
    only_summary = getattr(args, "summary", False)
    return {
        "paper": paper_summary(detail["paper"]),
        "filter": args.filter,
        "summary": summary,
        "cards": [] if only_summary else all_cards,
    }


def approval_label(card: dict) -> str:
    if card["approved_by"] == "human":
        return "人工通过"
    if card["approved_by"] == "ai":
        return f"{card['approval_agent'] or 'AI'} 通过"
    return ""


def show_cards(result: dict) -> str:
    paper = result["paper"]
    summary = result.get("summary")
    count_str = f"{summary['total']} 道" if summary else f"{len(result['cards'])} 道"
    lines = [f"{short(paper['id'])} {paper['name']}：{count_str}（筛选：{result['filter']}）"]
    if summary:
        lines.append(f"  审核状态：已通过 {summary['approved']}（人工 {summary['human_approved']} / AI {summary['ai_approved']}），"
                     f"待核对 {summary['todo']}，就绪绿卡 {summary['green']}，已入库 {summary['published']}")
        blocks = []
        if summary.get("type_blocked"):
            blocks.append(f"题型未定 {summary['type_blocked']} 道")
        if summary.get("figure_issues"):
            blocks.append(f"配图待确认 {summary['figure_issues']} 道")
        if summary.get("source_image_mode"):
            blocks.append(f"原图题 {summary['source_image_mode']} 道")
        if blocks:
            lines.append("  拦截疑点：" + "，".join(blocks))
        if summary.get("top_issues"):
            lines.append("  高频疑点：")
            for issue, count in summary["top_issues"][:5]:
                lines.append(f"    - {issue} ({count} 道)")
    for card in result.get("cards", []):
        state = approval_label(card) or STATES.get(card["state"], card["state"])
        group = f"[{card['group']}] " if card["group"] else ""
        caption = "原卷图片题" if card.get("body_mode") == "source_image" else first_line(card["stem"], 46)
        lines.append(f"  第 {card['number']} 题 {group}#{card['id']}  {state}  {caption}")
        for issue in card["issues"][:3]:
            lines.append(f"      ! {issue}")
    return "\n".join(lines)


def images_dir(paper: dict, out: str | None) -> Path:
    folder = Path(out).expanduser() if out else Path(tempfile.gettempdir()) / "tiyouju" / short(paper["id"])
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def card_detail(client: Client, paper_ref: str, card_ref: str, out: str | None = None,
                images: bool = True) -> dict:
    paper = find_paper(client, paper_ref)
    detail = paper_detail(client, paper)
    question = find_card(detail, card_ref)
    reads = question.get("reads") or {}
    result = {
        **card_summary(question),
        "paper": paper_summary(detail["paper"]),
        "options": question.get("options") or {},
        "answer": question.get("answer") or "",
        "analysis": question.get("analysis") or "",
        "figure_reason": (question.get("figure_review") or {}).get("reason", ""),
        "candidates": candidates(question),
        "figure_slots": [figure.get("slot", "stem") for figure in question.get("figures") or []],
        "readings": {name: {"stem": (reads.get(name) or {}).get("stem", ""),
                            "options": (reads.get(name) or {}).get("options") or {}}
                     for name in ("a", "b", "c") if (reads.get(name) or {}).get("stem")},
        "images": {},
    }
    if images:
        folder = images_dir(detail["paper"], out)
        tag = f"q{question.get('number')}-{question['id']}"
        crop = folder / f"{tag}-crop.png"
        crop.write_bytes(client.get_bytes(f"/api/questions/{question['id']}/crop"))
        result["images"]["crop"] = str(crop)
        if result["candidates"]:
            marked = folder / f"{tag}-candidates.png"
            marked.write_bytes(client.get_bytes(f"/api/questions/{question['id']}/crop?marks=1"))
            result["images"]["candidates"] = str(marked)
        figures = []
        for index, figure in enumerate(question.get("figures") or []):
            target = folder / f"{tag}-figure{index + 1}.png"
            target.write_bytes(client.get_bytes(f"/api/questions/{question['id']}/figures/{index}"))
            figures.append(str(target))
        result["images"]["figures"] = figures
        if question.get("body_mode") == "source_image":
            fragments = []
            for index, fragment in enumerate(question.get("question_images") or []):
                target = folder / f"{tag}-question{index + 1}.png"
                target.write_bytes(client.get_bytes(f"/api/questions/{question['id']}/question-images/{index}"))
                fragments.append(str(target))
            result["images"]["question_images"] = fragments
    return result


def cmd_show(client: Client, args) -> dict:
    return card_detail(client, args.paper, args.card, args.out, images=not args.no_images)


def show_card(result: dict) -> str:
    lines = [f"第 {result['number']} 题 #{result['id']}  {TYPES.get(result['type'], result['type'])}  "
             f"{approval_label(result) or STATES.get(result['state'], result['state'])}"]
    if result.get("origin"):
        lines.append(f"题源：{result['origin']}（题干前印的出处，单独存放）")
    if result.get("body_mode") == "source_image":
        lines.append("题面：原卷图片题，按下列片段顺序核对；可以后补识读文字。")
    else:
        lines += [f"题干：{result['stem'] or '（空）'}"]
    for key, value in result["options"].items():
        lines.append(f"  {key}. {value}")
    if result["answer"]:
        lines.append(f"答案：{result['answer']}")
    if result["analysis"]:
        lines.append(f"解析：{result['analysis']}")
    for issue in result["issues"]:
        lines.append(f"! {issue}")
    if len({reading["stem"] for reading in result["readings"].values()}) > 1:
        lines.append("几次识读（写法不同不一定是错，以原卷为准）：")
        for name, reading in result["readings"].items():
            options = "；".join(f"{key}.{value}" for key, value in reading["options"].items())
            lines.append(f"  [{name}] {reading['stem']}" + (f"  {options}" if options else ""))
    if result["candidates"] or result["figure_slots"]:
        lines.append(f"配图：{len(result['figure_slots'])} 张（{result['figure_reason'] or result['figure_status']}）")
        for item in result["candidates"]:
            used = f"→ 用作{'题干' if item['used_as'] == 'stem' else '选项' + item['used_as']}" if item["used_as"] else "→ 没用上"
            lines.append(f"  图{item['number']}（候选图编号截图里蓝框上的 {item['number']}）：第 {item['page']} 页 {used}")
    images = result.get("images") or {}
    if images:
        lines.append("图片（请打开看）：")
        lines.append(f"  原卷截图：{images['crop']}")
        for index, path in enumerate(images.get("question_images") or [], 1):
            lines.append(f"  题目片段 {index}：{path}")
        if images.get("candidates"):
            lines.append(f"  候选图编号：{images['candidates']}")
        for path in images.get("figures") or []:
            lines.append(f"  配图：{path}")
    return "\n".join(lines)


def read_text_arg(value: str | None, file: str | None) -> str | None:
    if file:
        return Path(file).expanduser().read_text(encoding="utf-8-sig")
    if value == "-":
        return sys.stdin.read()
    return value


def guard_human(question: dict, force: bool) -> None:
    if approval(question) == "human" and not force:
        raise CliError("这道题使用者已经人工通过了。除非使用者让你改，否则不要动它（确实要改就加 --force）。",
                       EXIT_NEEDS_USER)


def cmd_fix(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    detail = paper_detail(client, paper)
    question = find_card(detail, args.card)
    guard_human(question, args.force)
    stem = read_text_arg(args.stem, args.stem_file)
    options = dict(question.get("options") or {})
    for item in args.option or []:
        key, sep, value = item.partition("=")
        key = key.strip().upper()
        if not sep or key not in OPTION_KEYS:
            raise CliError(f"--option 写成 A=内容（A 到 E），收到的是“{item}”")
        options[key] = value
    for key in args.clear_option or []:
        options.pop(key.strip().upper(), None)
    kind = args.type or question.get("question_type")
    if kind not in TYPES:
        raise CliError("--type 只能是 " + "、".join(TYPES))
    origin = getattr(args, "origin", None)
    section = getattr(args, "section", None)
    text_given = any(value is not None for value in (stem, args.answer, args.analysis, origin)) \
        or bool(args.option or args.clear_option)
    if args.type and not text_given and section is None:
        # Only the type: keep the text, its reading record and the other reminders.
        if args.type == "unknown":
            raise CliError("请选一个题型：" + "、".join(DECIDED_TYPES))
        saved = client.post(f"/api/questions/{question['id']}/type",
                            {"question_type": args.type, "by": "ai", "agent": client.agent})["question"]
        return {"saved": True, **card_summary(saved), "options": saved.get("options") or {}}
    if section is not None and not text_given and not args.type:
        saved = client.post(f"/api/questions/{question['id']}/section", {
            "section": section, "by": "ai", "agent": client.agent, "force": bool(args.force),
        })["question"]
        return {"saved": True, **card_summary(saved), "options": saved.get("options") or {}}
    body = {
        "stem": stem if stem is not None else question.get("stem", ""),
        "options": {key: value for key, value in options.items() if str(value).strip()},
        "question_type": kind,
        "answer": args.answer if args.answer is not None else question.get("answer", ""),
        "analysis": read_text_arg(args.analysis, None) if args.analysis is not None else question.get("analysis", ""),
        "approve": False, "by": "ai", "agent": client.agent,
    }
    if origin is not None:
        body["origin"] = origin
    if section is not None:
        body["section"] = section
    saved = client.post(f"/api/questions/{question['id']}/text", body)["question"]
    return {"saved": True, **card_summary(saved), "options": saved.get("options") or {}}


def show_fix(result: dict) -> str:
    text = f"第 {result['number']} 题已保存。现在：{STATES.get(result['state'], result['state'])}"
    if result["issues"]:
        text += "；还有：" + "；".join(result["issues"])
    return text + "。再用 show 看一眼，确认无误后 approve。"


def parse_uses(values: list[str]) -> list[tuple[int, str]]:
    uses = []
    for value in values:
        for piece in value.replace("，", ",").split(","):
            piece = piece.strip()
            if not piece:
                continue
            number, _sep, slot = piece.partition(":")
            slot = (slot.strip() or "stem")
            slot = slot if slot == "stem" else slot.upper()
            try:
                uses.append((int(number.strip().removeprefix("图")), slot))
            except ValueError:
                raise CliError(f"--use 写成 1 或 1:stem、2:A（图的编号:用在哪），收到的是“{piece}”") from None
            if slot not in SLOTS:
                raise CliError(f"配图只能用在 stem（题干）或 A–E 选项，收到的是“{slot}”")
    return uses


def cmd_figures(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    detail = paper_detail(client, paper)
    question = find_card(detail, args.card)
    guard_human(question, args.force)
    tag = {"by": "ai", "agent": client.agent}
    if args.none:
        saved = client.post(f"/api/questions/{question['id']}/figure-review",
                            {"decision": "confirm_no_figure", **tag})["question"]
    else:
        listed = candidates(question)
        by_number = {item["number"]: item for item in listed}
        if args.keep:
            figures = [{key: figure[key] for key in ("slot", "page_idx", "bbox", "candidate_key", "parts", "label_offset")
                        if key in figure} for figure in question.get("figures") or []]
            if not figures:
                raise CliError("这道题现在没有配图可保留。用 --use 选候选图，或 --none 确认无图。")
        else:
            uses = parse_uses(args.use or [])
            if not uses:
                raise CliError("请用 --use 选候选图（编号见 show），或 --keep 保留现在的配图，或 --none 确认无图。")
            figures = []
            for number, slot in uses:
                item = by_number.get(number)
                if item is None:
                    raise CliError(f"没有图{number}。这道题的候选图是：" +
                                   ("、".join(f"图{entry['number']}" for entry in listed) or "没有"))
                page_idx = int(item["key"].split(":", 1)[0])
                figures.append({"slot": slot, "page_idx": page_idx, "bbox": item["bbox"], "candidate_key": item["key"]})
        chosen = {piece.get("candidate_key") for figure in figures for piece in (figure, *(figure.get("parts") or []))}
        # The assistant looked at every candidate: the ones not chosen are not this question's figures.
        ignored = sorted(item["key"] for item in listed if item["key"] not in chosen)
        saved = client.post(f"/api/questions/{question['id']}/figures",
                            {"figures": figures, "ignored_candidates": ignored, **tag})["question"]
    return {"saved": True, **card_summary(saved), "figure_reason": (saved.get("figure_review") or {}).get("reason", "")}


def show_figures(result: dict) -> str:
    return (f"第 {result['number']} 题的配图已保存：{result['figures']} 张（{result['figure_reason']}）。"
            "用 show 看一眼配图图片，确认无误后 approve。")


def cmd_approve(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    tag = {"by": "ai", "agent": client.agent}
    if args.green:
        result = client.post(f"/api/papers/{paper['id']}/approve-green", tag)
        return {"paper": paper_summary(result["paper"]), "approved": result.get("approved", 0), "results": []}
    if not args.cards:
        raise CliError("请写已对照原卷核对的题号（可以写好几个），或用 --green 一起通过已核对的绿卡。")
    detail = paper_detail(client, paper)
    results = []
    for ref in args.cards:
        try:
            question = find_card(detail, ref)
            saved = client.post(f"/api/questions/{question['id']}/approve", {"approved": True, **tag})["question"]
            results.append({"card": ref, "ok": True, "approved_by": approval(saved)})
        except CliError as error:
            results.append({"card": ref, "ok": False, "error": str(error)})
    return {"paper": paper_summary(paper_detail(client, paper)["paper"]),
            "approved": sum(item["ok"] for item in results), "results": results}


def show_approve(result: dict) -> str:
    lines = [f"通过了 {result['approved']} 道（记为 AI 通过，使用者会在题卡上看到并核对）。"]
    for item in result["results"]:
        if not item["ok"]:
            lines.append(f"  第 {item['card']} 题没通过：{item['error']}")
        elif item["approved_by"] == "human":
            lines.append(f"  第 {item['card']} 题本来就是人工通过的，没有改动。")
    return "\n".join(lines)


def cmd_unapprove(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    question = find_card(paper_detail(client, paper), args.card)
    saved = client.post(f"/api/questions/{question['id']}/approve",
                        {"approved": False, "by": "ai", "agent": client.agent})["question"]
    return {"saved": True, **card_summary(saved)}


def cmd_reread(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    question = find_card(paper_detail(client, paper), args.card)
    guard_human(question, args.force)
    saved = client.post(f"/api/questions/{question['id']}/reread",
                        {"by": "ai", "agent": client.agent, "force": args.force is True})["question"]
    return {"saved": True, **card_summary(saved)}


def show_unapprove(result: dict) -> str:
    return f"已撤销第 {result['number']} 题上 AI 打的勾。"


def show_reread(result: dict) -> str:
    return f"第 {result['number']} 题已交给软件重读，过一会儿用 `tiyouju show` 看结果。"


def cmd_publish(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    detail = paper_detail(client, paper)
    waiting = [question.get("number") for question in detail.get("questions", []) if not approval(question)]
    result = client.post(f"/api/papers/{paper['id']}/publish", {})
    return {"paper": paper_summary(result.get("paper") or detail["paper"]), "created": result.get("created", 0),
            "unchanged": result.get("unchanged", 0), "problems": result.get("problems") or [],
            "not_approved": waiting}


def show_publish(result: dict) -> str:
    lines = [f"入库：新增 {result['created']} 道，没变的 {result['unchanged']} 道。"]
    if result["not_approved"]:
        lines.append(f"还没通过、没有入库的：第 {'、'.join(map(str, result['not_approved'][:30]))} 题。")
    lines += [f"  ! {problem}" for problem in result["problems"]]
    return "\n".join(lines)


def cmd_library(client: Client, args) -> dict:
    query = {"limit": str(args.limit)}
    if args.keywords:
        query["q"] = " ".join(args.keywords)
    if args.paper:
        query["document"] = find_paper(client, args.paper)["id"]
    if args.type:
        query["type"] = args.type
    if args.review:
        query["review"] = args.review
    if getattr(args, "answer", None):
        query["answer"] = args.answer
    if getattr(args, "tag", None):
        query["tag"] = args.tag
    result = client.get("/api/library?" + urllib.parse.urlencode(query))
    items = [{
        "id": item["id"], "short_id": item.get("short_id") or f"Q-{str(item['id']).replace('-', '')[:8].lower()}",
        "source": item.get("source_filename"), "number": item.get("number"),
        "type": item.get("question_type"), "version": item.get("version"),
        "review": (item.get("review") or {}).get("source", "human"),
        "origin": item.get("origin") or "",
        "section": (item.get("content") or {}).get("section", ""),
        "stem": (item.get("content") or {}).get("stem", ""),
        "options": (item.get("content") or {}).get("options") or {},
        "answer": (item.get("content") or {}).get("answer", ""),
        "tags": item.get("tags") or [],
        "subquestions": item.get("subquestions") or 0,
    } for item in result.get("items", [])]
    return {"total": result.get("total", 0), "items": items}


def show_library(result: dict) -> str:
    lines = [f"题库里找到 {result['total']} 道" + (f"，下面是前 {len(result['items'])} 道：" if result["items"] else "。")]
    for item in result["items"]:
        review = "（AI 审核）" if item["review"] == "ai" else ""
        origin = f"〔{item['origin']}〕" if item.get("origin") else ""
        sid = item.get("short_id") or f"Q-{str(item['id']).replace('-', '')[:8].lower()}"
        lines.append(f"  [{sid}] {item['source']} 第 {item['number']} 题{review}{origin}：{first_line(item['stem'], 50)}")
        if item.get("tags"):
            lines.append(f"      知识点：{'、'.join(item['tags'])}")
    return "\n".join(lines)


def resolve_publication_id(client: Client, ref: str) -> str:
    """支持将 Q-xxxxxxxx 短码、粘贴定位文本或 UUID 解析为标准题目 UUID。"""
    text = str(ref or "").strip()
    if not text:
        raise CliError("请给出题目 UUID 或定位码（如 Q-xxxxxxxx）")
    try:
        return str(uuid.UUID(text))
    except (ValueError, TypeError, AttributeError):
        pass
    located = client.get("/api/library/locate?" + urllib.parse.urlencode({"ref": text}))
    pub = located.get("publication") or {}
    pub_id = str(pub.get("id") or "").strip()
    if not pub_id:
        raise CliError(f"未能定位到题目：{text}")
    return pub_id


def cmd_locate(client: Client, args) -> dict:
    """按短码（Q-xxxxxxxx）、UUID 或整段复制的位置信息快速定位一道已入库题。"""
    raw_ref = " ".join(args.ref).strip()
    if not raw_ref:
        raise CliError("请给出要定位的题目编号或复制的位置文本")
    return client.get("/api/library/locate?" + urllib.parse.urlencode({"ref": raw_ref}))


def show_locate(result: dict) -> str:
    pub = result.get("publication") or {}
    locator = result.get("locator") or pub.get("locator") or {}
    content = pub.get("content") or {}
    tags = [t.get("name") for t in (pub.get("tags") or []) if isinstance(t, dict) and t.get("name")]
    lines = [
        f"定位成功：{locator.get('copy_text') or pub.get('id')}",
        f"题型：{TYPES.get(pub.get('question_type'), pub.get('question_type') or '')}  版本：v{pub.get('version', 1)}  UUID：{pub.get('id')}",
        f"题干：{content.get('stem') or '(无文字题干)'}",
    ]
    options = content.get("options") or {}
    for key in OPTION_KEYS:
        if str(options.get(key) or "").strip():
            lines.append(f"  {key}. {options[key]}")
    if str(content.get("answer") or "").strip():
        lines.append(f"原卷答案：{content['answer']}")
    if tags:
        lines.append(f"当前标签：{'、'.join(tags)}")
    for img in result.get("local_images") or []:
        lines.append(f"本机原图：{img}")
    return "\n".join(lines)


ENRICH_FEATURES = ("knowledge_tags", "ai_answer", "auto_tag_background")
ENRICH_KINDS = ("tags", "answer")
ENRICH_RESULT_BYTES = 256 * 1024


def cmd_features(client: Client, args) -> dict:
    """Read by default; changes only come from explicit flags or MCP choices."""
    enabled, disabled = getattr(args, "enable", None) or [], getattr(args, "disable", None) or []
    if not isinstance(enabled, (list, tuple)) or not isinstance(disabled, (list, tuple)):
        raise CliError("启用/关闭项目需为功能名称列表")
    if any(key not in ENRICH_FEATURES for key in [*enabled, *disabled]):
        raise CliError("这里只能设置 knowledge_tags 或 ai_answer")
    if set(enabled) & set(disabled):
        raise CliError("同一个功能不能同时启用和关闭")
    changes = {**dict.fromkeys(enabled, True), **dict.fromkeys(disabled, False)}
    return client.post("/api/settings/features", {"features": changes}) if changes else client.get("/api/settings/features")


def _enrichment_ids(values) -> list[str]:
    if not isinstance(values, (list, tuple)) or len(values) > 500:
        raise CliError("最多指定 500 个入库题目编号")
    result = []
    for value in values:
        try:
            normalized = str(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            raise CliError("请使用完整的入库题目 UUID（library 会列出）") from None
        if normalized not in result:
            result.append(normalized)
    return result


def _enrichment_agent(client: Client, args) -> str:
    value = " ".join(str(getattr(args, "agent", None) or client.agent).split())
    if not value or len(value) > 40:
        raise CliError("助手名字需为 1–40 个字符")
    return value


def cmd_enrich_tasks(client: Client, args) -> dict:
    limit = getattr(args, "limit", 50)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise CliError("任务数量 limit 需为 1–50")
    ids = _enrichment_ids(getattr(args, "ids", None) or [])
    query = {"limit": str(limit)}
    if ids:
        query["ids"] = ",".join(ids)
    return client.get("/api/library/assistant/tasks?" + urllib.parse.urlencode(query))


def cmd_enrich_auto(client: Client, args) -> dict:
    changes = {}
    for kind in ENRICH_KINDS:
        value = getattr(args, kind, None)
        if value is None:
            continue
        if isinstance(value, bool):
            changes[kind] = value
        elif value in ("on", "off"):
            changes[kind] = value == "on"
        else:
            raise CliError("入库时自动排队只能设置 on/off（MCP 用 true/false）")
    return client.post("/api/settings/library-ai", {"on_intake": changes}) if changes else client.get("/api/settings/library-ai")


def _enrichment_resource(client: Client, publication_id: str, url) -> tuple[str, str]:
    """The app supplies local images, never arbitrary remote URLs or files."""
    if not isinstance(url, str) or not url:
        raise CliError("任务图片地址缺失")
    absolute = urllib.parse.urljoin(client.url + "/", url)
    parsed, local = urllib.parse.urlsplit(absolute), urllib.parse.urlsplit(client.url)
    prefix = f"/api/library/{publication_id}/"
    suffix = parsed.path[len(prefix):] if parsed.path.startswith(prefix) else ""
    parts = suffix.split("/")
    allowed = suffix == "crop" or (len(parts) == 2 and (
        (parts[0] == "pages" and parts[1].isdigit())
        or (parts[0] == "figures" and re.fullmatch(r"figure-\d{1,3}\.png", parts[1]))))
    allowed = allowed or (len(parts) == 2 and parts[0] == "question-images" and bool(re.fullmatch(r"question-\d{1,3}\.png", parts[1])))
    if (parsed.scheme, parsed.netloc) != (local.scheme, local.netloc) or parsed.fragment or not allowed:
        raise CliError("任务图片必须来自本机同一道入库题的原卷或配图接口")
    path = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
    return path, absolute


def cmd_enrich_prepare(client: Client, args) -> dict:
    publication_id = _enrichment_ids([args.publication_id])[0]
    kinds = getattr(args, "kinds", None) or list(ENRICH_KINDS)
    if not isinstance(kinds, (list, tuple)) or not kinds or any(kind not in ENRICH_KINDS for kind in kinds):
        raise CliError("kinds 只能包含 tags 和 answer")
    payload = {
        "publication_id": publication_id, "kinds": list(dict.fromkeys(kinds)), "agent": _enrichment_agent(client, args),
    }
    if getattr(args, "job_id", None):
        payload["job_id"] = _enrichment_ids([args.job_id])[0]
    result = client.post("/api/library/assistant/prepare", payload)
    result = json.loads(json.dumps(result))
    images = result.get("images") or {}
    resources = []
    if images.get("crop"):
        path, images["crop"] = _enrichment_resource(client, publication_id, images["crop"])
        resources.append(("crop.png", path))
    for index, figure in enumerate(images.get("figures") or [], 1):
        path, figure["url"] = _enrichment_resource(client, publication_id, figure.get("url"))
        resources.append((f"figure-{index}.png", path))
    for index, fragment in enumerate(images.get("question_images") or [], 1):
        path, fragment["url"] = _enrichment_resource(client, publication_id, fragment.get("url"))
        resources.append((f"question-{index}.png", path))
    for original in images.get("originals") or []:
        _, original["url"] = _enrichment_resource(client, publication_id, original.get("url"))
    result["images"] = images
    local_images = {"crop": None, "figures": []}
    if images.get("question_images"):
        local_images["question_images"] = []
    if not getattr(args, "no_images", False) and resources:
        try:
            folder = Path(args.out).expanduser() if getattr(args, "out", None) else Path(tempfile.mkdtemp(prefix="tiyouju-enrich-"))
            folder.mkdir(parents=True, exist_ok=True)
            for filename, path in resources:
                target = folder / filename
                target.write_bytes(client.get_bytes(path))
                if filename == "crop.png":
                    local_images["crop"] = str(target.resolve())
                elif filename.startswith("question-"):
                    local_images["question_images"].append(str(target.resolve()))
                else:
                    local_images["figures"].append(str(target.resolve()))
        except OSError as error:
            raise CliError("任务已准备，但图片未能存到本机；请检查输出目录或使用 --no-images 查看本机图片地址") from error
    result["local_images"] = local_images
    return result


def _enrichment_text_file(filename: str) -> str:
    try:
        with Path(filename).expanduser().open("rb") as stream:
            data = stream.read(ENRICH_RESULT_BYTES + 1)
        if len(data) > ENRICH_RESULT_BYTES:
            raise CliError("单次参考结果文件不能超过 256 KiB")
        return data.decode("utf-8-sig")
    except (OSError, UnicodeError) as error:
        raise CliError("无法读取 UTF-8 参考结果文件") from error


def cmd_enrich_submit(client: Client, args) -> dict:
    job_id = _enrichment_ids([args.job_id])[0]
    fingerprint = getattr(args, "fingerprint", None)
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise CliError("请原样提交 prepare 返回的任务 fingerprint")
    if getattr(args, "result_file", None):
        if any(getattr(args, field, None) is not None for field in ("tags", "answer", "answer_file", "analysis", "analysis_file")):
            raise CliError("使用 --result-file 时不要再单独传标签、答案或解析")
        try:
            result = json.loads(_enrichment_text_file(args.result_file))
        except ValueError:
            raise CliError("参考结果文件不是有效的 JSON") from None
    else:
        result = {}
        if getattr(args, "tags", None) is not None:
            result["tags"] = args.tags
        if getattr(args, "answer", None) is not None:
            result["answer"] = args.answer
        elif getattr(args, "answer_file", None):
            result["answer"] = _enrichment_text_file(args.answer_file)
        if getattr(args, "analysis", None) is not None:
            result["analysis"] = args.analysis
        elif getattr(args, "analysis_file", None):
            result["analysis"] = _enrichment_text_file(args.analysis_file)
    if not isinstance(result, dict) or not result or set(result) - {"tags", "answer", "analysis"}:
        raise CliError("结果只允许 tags，或 answer 和可选 analysis；不能改题面、审核或来源")
    if "tags" in result:
        tags = result["tags"]
        if set(result) != {"tags"} or not isinstance(tags, list) or not 1 <= len(tags) <= 3 or not all(isinstance(tag, str) and tag.strip() for tag in tags):
            raise CliError("标签任务需单独提交目录里的 1–3 个标签")
    elif not isinstance(result.get("answer"), str) or not result["answer"].strip() or ("analysis" in result and not isinstance(result["analysis"], str)):
        raise CliError("答案任务需要非空 answer 和可选的文字 analysis")
    return client.post("/api/library/assistant/complete", {
        **result, "job_id": job_id, "fingerprint": fingerprint, "agent": _enrichment_agent(client, args),
    })


def cmd_enrich(client: Client, args) -> dict:
    return {"tasks": cmd_enrich_tasks, "auto": cmd_enrich_auto,
            "prepare": cmd_enrich_prepare, "submit": cmd_enrich_submit}[args.enrich_command](client, args)


# ---------------------------------------------------------------- 助手工作台（1.26.34）


def cmd_claim(client: Client, args) -> dict:
    """领一批助手活。

    每道活都带一个到期时间：助手中途没了（断网、崩了、关掉了），这道活会自己回到
    池子里，别的助手能接着领；不会一直占着，别人永远领不走。
    """
    limit = getattr(args, "limit", 10)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise CliError("一次最多领 50 道")
    payload = {
        "agent": _enrichment_agent(client, args),
        "limit": limit,
        "kinds": list(getattr(args, "kinds", None) or ENRICH_KINDS),
    }
    ids = getattr(args, "ids", None)
    if ids:
        payload["ids"] = _enrichment_ids(ids)
    return client.post("/api/library/assistant/claim", payload)


def cmd_release(client: Client, args) -> dict:
    """把没干完的助手活交回池子，别的助手能接着领。"""
    job_ids = list(getattr(args, "job_id", None) or [])
    if not job_ids:
        raise CliError("请给出要交回的任务 ID（--job-id）")
    return client.post("/api/library/assistant/release", {
        "agent": _enrichment_agent(client, args), "ids": _enrichment_ids(job_ids),
    })


def cmd_propose(client: Client, args) -> dict:
    """交一份题面改动的提案。

    已经入库的题，助手不要直接改字 —— 直接改会把老师打过的勾弄没。改法先交成提案：
    题面一个字不动，等老师在题库页点「采纳」。
    """
    publication_id = resolve_publication_id(client, args.publication_id)
    fields = {}
    stem = read_text_arg(args.stem, getattr(args, "stem_file", None))
    if stem is not None:
        fields["stem"] = stem
    options = {}
    for item in args.option or []:
        key, sep, value = item.partition("=")
        key = key.strip().upper()
        if not sep or key not in OPTION_KEYS:
            raise CliError(f"--option 写成 A=内容（A 到 E），收到的是“{item}”")
        if str(value).strip():
            options[key] = value
    if options:
        fields["options"] = options
    if args.type:
        fields["question_type"] = args.type
    if args.answer is not None:
        fields["answer"] = args.answer
    if args.analysis is not None:
        fields["analysis"] = args.analysis
    if getattr(args, "origin", None) is not None:
        fields["origin"] = args.origin
    if not fields:
        raise CliError("请至少写一项要改的内容")
    return client.post("/api/library/assistant/propose", {
        "publication_id": publication_id, "agent": _enrichment_agent(client, args), "fields": fields,
    })


def cmd_workbench(client: Client, args) -> dict:
    """读助手工作台，或按助手/范围批量清理待核对提案（CLI 仅允许批量拒绝清理，不代老师批量采纳）。"""
    if getattr(args, "clear", False):
        payload: dict = {
            "action": "reject",
            "scope": getattr(args, "scope", None) or "all",
            "by": _enrichment_agent(client, args),
        }
        if getattr(args, "from_agent", None):
            payload["agent"] = args.from_agent
        if getattr(args, "reason", None):
            payload["reason"] = args.reason
        return client.post("/api/library/assistant/proposals/batch-decide", payload)

    query: dict[str, str] = {"limit": str(int(getattr(args, "limit", None) or 50))}
    if getattr(args, "from_agent", None):
        query["agent"] = args.from_agent
    if getattr(args, "scope", None):
        query["scope"] = args.scope
    return client.get("/api/library/assistant/workbench?" + urllib.parse.urlencode(query))


def show_workbench(result: dict) -> str:
    if "processed" in result and result.get("action") == "reject":
        return f"{result.get('message', '已批量清理待核对提案。')}（剩余待核对：{result.get('remaining', 0)} 条）"
    pending = result.get("pending") or {}
    lines = [
        f"助手工作台：共 {pending.get('text', 0)} 条待核对提案"
        f"（仅改答案解析 {pending.get('solution_only', 0)} 条，涉及题面 {pending.get('face', 0)} 条；"
        f"当前返回 {pending.get('shown', len(result.get('proposals') or []))} 条）"
    ]
    by_agent = result.get("by_agent") or []
    if by_agent:
        lines.append("按助手汇总：")
        for item in by_agent:
            lines.append(
                f"  - {item.get('agent')}：{item.get('total', 0)} 条"
                f"（纯答案解析 {item.get('solution_only', 0)}，题面 {item.get('face', 0)}）"
            )
    proposals = result.get("proposals") or []
    for p in proposals[:20]:
        flags = ["纯答案解析"] if p.get("solution_only") else ["改了题面"]
        changed = "、".join(p.get("changed") or [])
        lines.append(f"  · 第 {p.get('number')} 题（{p.get('source_filename')}）| {p.get('agent')} | {changed} | {'/'.join(flags)} | id={p.get('id')}")
    if len(proposals) > 20:
        lines.append(f"  ……其余 {len(proposals) - 20} 条可用 --json 查看")
    teacher = result.get("teacher") or {}
    if teacher.get("here"):
        lines.append(f"老师当前位置：第 {teacher.get('number')} 题（{teacher.get('state_text')}）")
    return "\n".join(lines)


def _load_solutions_file(path: str) -> list[dict]:
    try:
        raw_text = Path(path).expanduser().read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise CliError(f"无法读取答案解析 JSON 文件：{exc}") from None
    try:
        parsed = json.loads(raw_text)
    except ValueError as exc:
        raise CliError(f"答案解析文件不是合法 JSON：{exc}") from None

    raw_items: list
    if isinstance(parsed, dict) and "items" in parsed:
        raw_items = parsed["items"]
    elif isinstance(parsed, list):
        raw_items = parsed
    elif isinstance(parsed, dict):
        raw_items = []
        for pub_id, val in parsed.items():
            if not isinstance(val, dict):
                raise CliError("映射格式中每道题的值必须是包含 answer / analysis 的 JSON 对象")
            raw_items.append({"publication_id": str(pub_id), **val})
    else:
        raise CliError("答案解析文件格式应为 {\"items\": [...]}、[...] 或 {题目ID: {\"answer\": ..., \"analysis\": ...}}")

    if not isinstance(raw_items, list) or not raw_items:
        raise CliError("答案解析列表不能为空")
    if len(raw_items) > 500:
        raise CliError(f"单批最多导入 500 条解析，请分批后重试（当前 {len(raw_items)} 条）")

    items: list[dict] = []
    for idx, entry in enumerate(raw_items):
        if not isinstance(entry, dict):
            raise CliError(f"第 {idx + 1} 条必须是 JSON 对象")
        pub_id = entry.get("publication_id")
        if pub_id is None:
            pub_id = entry.get("id")
        item: dict = {"publication_id": str(pub_id or "").strip()}
        if "answer" in entry and entry["answer"] is not None:
            item["answer"] = entry["answer"]
        if "analysis" in entry and entry["analysis"] is not None:
            item["analysis"] = entry["analysis"]
        items.append(item)
    return items


def cmd_solutions(client: Client, args) -> dict:
    """批量将标准答案解析直接写入版本化解析层（LibrarySolution），支持干跑与整批撤销。"""
    sub = getattr(args, "solutions_command", None) or "batches"
    if sub == "import":
        items = _load_solutions_file(args.file)
        payload: dict = {
            "items": items,
            "overwrite": bool(getattr(args, "overwrite", False)),
            "dry_run": bool(getattr(args, "dry_run", False)),
            "agent": _enrichment_agent(client, args),
        }
        if getattr(args, "note", None) is not None:
            payload["note"] = args.note
        return client.post("/api/library/solutions/batch", payload, timeout=120)
    if sub == "batches":
        limit = int(getattr(args, "limit", None) or 20)
        return client.get(f"/api/library/solutions/batches?limit={limit}")
    if sub == "undo":
        payload = {}
        if getattr(args, "batch_id", None):
            payload["batch_id"] = args.batch_id
        return client.post("/api/library/solutions/undo", payload)
    raise CliError(f"不支持的 solutions 子命令：{sub}")


def show_solutions(result: dict) -> str:
    if "batches" in result:
        batches = result.get("batches") or []
        if not batches:
            return "暂无批量解析导入记录。"
        lines = [f"批量解析导入记录（共 {result.get('total', len(batches))} 批，展示最近 {len(batches)} 批）："]
        for item in batches:
            state = f"已撤销于 {item['undone_at']}" if item.get("undone_at") else "有效"
            note = f" | 说明：{item['note']}" if item.get("note") else ""
            lines.append(
                f"  - {item['batch_id']} | {item.get('count', 0)} 题 | {item.get('agent') or '老师'} | {state}{note}"
            )
        return "\n".join(lines)
    if "restored" in result:
        return f"{result.get('message', '已撤销批量解析写入。')}（batch_id={result.get('batch_id')}）"
    if result.get("dry_run"):
        lines = [
            f"干跑预览完成（共 {result.get('total', 0)} 题，未写入题库）："
            f"拟新增 {result.get('would_create', 0)}，拟覆盖 {result.get('would_overwrite', 0)}，"
            f"拟跳过已有 {result.get('would_skip_existing', 0)}，校验错误 {result.get('invalid', 0)}，"
            f"仅解析无答案 {result.get('analysis_only_count', 0)}。"
        ]
        for warn in (result.get("warnings") or [])[:10]:
            lines.append(f"  [提醒] {warn}")
        for err in (result.get("errors") or [])[:10]:
            lines.append(f"  [错误] 第 {err.get('index', 0) + 1} 条：{err.get('error')}")
        return "\n".join(lines)
    lines = [
        f"批量解析写入完成（共 {result.get('total', 0)} 题）："
        f"新增 {result.get('created', 0)}，覆盖 {result.get('overwritten', 0)}，"
        f"跳过已有 {result.get('skipped_existing', 0)}，仅解析无答案 {result.get('analysis_only_count', 0)}。"
    ]
    if result.get("batch_id"):
        lines.append(f"批次 ID：{result['batch_id']}（可用 `tiyouju solutions undo --batch-id {result['batch_id']}` 整批撤销）")
    for warn in (result.get("warnings") or [])[:10]:
        lines.append(f"  [提醒] {warn}")
    return "\n".join(lines)


def cmd_whereami(client: Client, args) -> dict:
    """问一句：老师此刻在哪、我刚提的有没有被否决。"""
    return client.get("/api/library/assistant/whereami")


def cmd_backup(client: Client, args) -> dict:
    """手动备份题库（支持打包整库、指定卷子/书籍或目录文件夹到指定目录）。"""
    dest = getattr(args, "dest", None)
    raw_papers = getattr(args, "papers", None)
    raw_folders = getattr(args, "folders", None)
    papers = [str(item) for item in raw_papers] if isinstance(raw_papers, (list, tuple)) and raw_papers else None
    folders = [str(item) for item in raw_folders] if isinstance(raw_folders, (list, tuple)) and raw_folders else None
    if papers and folders:
        raise CliError("一次只能指定 --paper 或 --folder 其中一种。")
    if not dest:
        raise CliError("请用 --dest 指定备份包保存文件夹。")
    dest_path = Path(dest).resolve()
    if not dest_path.is_dir():
        raise CliError(f"指定的目录不存在：{dest}")
    payload: dict = {"directory": str(dest_path)}
    if papers:
        payload["paper_ids"] = papers
    if folders:
        payload["tag_ids"] = folders
    return client.post("/api/backup/export", payload)


def show_backup(result: dict) -> str:
    name = result.get("name") or result.get("file") or "题库备份包"
    size = result.get("size")
    size_str = f" ({size / (1024 * 1024):.2f} MB)" if isinstance(size, (int, float)) else ""
    return f"备份完成：{name}{size_str}"


SORT_MODES = ("time_desc", "time_asc", "name", "count_desc", "default")


def _load_section_map(path: str) -> list[dict]:
    raw_text = Path(path).expanduser().read_text(encoding="utf-8-sig")
    try:
        parsed = json.loads(raw_text)
    except ValueError as exc:
        raise CliError(f"章节映射文件不是合法 JSON：{exc}") from None
    if isinstance(parsed, dict):
        return [{"publication_id": str(k), "section": str(v or "")} for k, v in parsed.items()]
    if isinstance(parsed, list):
        items = []
        for entry in parsed:
            if not isinstance(entry, dict) or not entry.get("publication_id"):
                raise CliError("章节映射列表每项必须包含 publication_id 和 section")
            items.append({"publication_id": str(entry["publication_id"]), "section": str(entry.get("section") or "")})
        return items
    raise CliError("章节映射文件格式应为 {题目ID: 章节名} 或 [{publication_id, section}]")


def cmd_folders(client: Client, args) -> dict:
    sub = getattr(args, "folders_command", None) or "list"
    if sub in {"list", "tree"}:
        return client.get("/api/library/folders")
    if sub == "create":
        payload: dict = {"action": "create", "name": args.name}
        if getattr(args, "parent", None):
            payload["parent_id"] = args.parent
        if getattr(args, "questions", None):
            payload["question_ids"] = list(args.questions)
        return client.post("/api/library/folders", payload)
    if sub == "rename":
        return client.post("/api/library/folders", {
            "action": "rename", "folder_id": args.folder, "name": args.name,
        })
    if sub == "move":
        return client.post("/api/library/folders", {
            "action": "move", "folder_id": args.folder, "parent_id": getattr(args, "parent", None),
        })
    if sub == "delete":
        return client.post("/api/library/folders", {"action": "delete", "folder_id": args.folder})
    if sub == "add":
        return client.post("/api/library/folders", {
            "action": "add_questions", "target_id": args.target, "question_ids": list(args.questions),
        })
    if sub == "remove":
        return client.post("/api/library/folders", {
            "action": "remove_questions", "target_id": args.target, "question_ids": list(args.questions),
        })
    if sub == "sort":
        return client.post("/api/library/folders", {
            "action": "set_sort", "target_id": args.target, "mode": args.mode,
        })
    if sub == "set-section":
        if getattr(args, "map_file", None):
            items = _load_section_map(args.map_file)
            return client.post("/api/library/folders", {"action": "set_section", "items": items})
        if args.section is None or not getattr(args, "questions", None):
            raise CliError("请提供 --section 和至少一道题目编号，或使用 --map-file 指定映射 JSON")
        return client.post("/api/library/folders", {
            "action": "set_section", "section": args.section, "question_ids": list(args.questions),
        })
    if sub == "paper":
        paper = find_paper(client, args.paper)
        body: dict = {}
        if getattr(args, "name", None) is not None:
            body["name"] = args.name
        if getattr(args, "type", None) is not None:
            body["material_type"] = args.type
        if not body:
            raise CliError("请指定 --name 新名称或 --type exam|book")
        return client.patch(f"/api/papers/{paper['id']}", body)
    if sub == "tag-group":
        tg_sub = getattr(args, "tag_group_command", None) or "list"
        if tg_sub == "list":
            return client.get("/api/library/tag-groups")
        if tg_sub == "create":
            payload = {"action": "create_group", "name": args.name}
            if getattr(args, "color", None):
                payload["color"] = args.color
            if getattr(args, "tags", None):
                payload["tag_ids"] = list(args.tags)
            return client.post("/api/library/tag-groups", payload)
        if tg_sub == "rename":
            return client.post("/api/library/tag-groups", {
                "action": "update_group", "group_id": args.group, "name": args.name,
            })
        if tg_sub == "delete":
            return client.post("/api/library/tag-groups", {"action": "delete_group", "group_id": args.group})
        if tg_sub == "add-tags":
            return client.post("/api/library/tag-groups", {
                "action": "add_tags", "group_id": args.group, "tag_ids": list(args.tags),
            })
        if tg_sub == "remove-tags":
            return client.post("/api/library/tag-groups", {
                "action": "remove_tags", "group_id": args.group, "tag_ids": list(args.tags),
            })
        if tg_sub == "set-tags":
            return client.post("/api/library/tag-groups", {
                "action": "set_tags", "group_id": args.group, "tag_ids": list(getattr(args, "tags", None) or []),
            })
        if tg_sub == "undo":
            payload = {"action": "undo"}
            if getattr(args, "operation_id", None):
                payload["operation_id"] = args.operation_id
            return client.post("/api/library/tag-groups", payload)
        raise CliError(f"不支持的个人分组操作：{tg_sub}")
    if sub == "undo":
        payload = {}
        if getattr(args, "operation_id", None):
            payload["operation_id"] = args.operation_id
        return client.post("/api/library/folders/undo", payload)
    raise CliError(f"不支持的文件夹命令：{sub}")


def _format_uf_tree(nodes: list[dict], indent: int = 2) -> list[str]:
    lines: list[str] = []
    pad = " " * indent
    for node in nodes:
        sort_tag = f" [排序:{node['sort_mode']}]" if node.get("sort_mode") else ""
        lines.append(f"{pad}- 📁 {node['name']} ({node.get('count', 0)} 题){sort_tag}  id={node['id']}")
        if node.get("children"):
            lines.extend(_format_uf_tree(node["children"], indent + 2))
    return lines


def show_folders(result: dict) -> str:
    if "paper" in result and "tree" not in result:
        p = result["paper"]
        kind = "教材" if p.get("material_type") == "book" else "试卷"
        return f"已更新资料：{p.get('name')}（{kind}，id={short(p.get('id', ''))}）"
    if "groups" in result and "tree" not in result:
        lines = [result["message"]] if result.get("message") else []
        groups = result.get("groups") or []
        lines.append(f"个人分组（共 {len(groups)} 个）：")
        for g in groups:
            tag_names = "、".join(t["name"] for t in (g.get("tags") or [])) or "空"
            lines.append(f"  - {g['name']}（id={short(g['id'])}）：{tag_names}")
        return "\n".join(lines)
    lines: list[str] = []
    if result.get("message"):
        lines.append(result["message"])
    tree = result.get("tree")
    if not isinstance(tree, dict):
        return "\n".join(lines) or "文件夹已更新。"
    lines.append(f"题库文件夹树（全局排序：{tree.get('global_sort_mode', 'time_desc')}，版本 rev={result.get('revision', 0)}）：")
    lines.append("我的文件夹：")
    uf_lines = _format_uf_tree(tree.get("user_folders") or [])
    lines.extend(uf_lines or ["  （暂无自建文件夹）"])
    papers_list = tree.get("papers") or []
    lines.append(f"试卷（{len(papers_list)} 份）：")
    for p in papers_list[:30]:
        lines.append(f"  - 📄 {p['name']} ({p.get('folder_count', 0)} 题)  node={p['node_id']}")
        lines.extend(_format_uf_tree(p.get("subfolders") or [], 4))
    books_list = tree.get("books") or []
    lines.append(f"教材版本（{len(books_list)} 本）：")
    for b in books_list[:30]:
        lines.append(f"  - 📚 {b['name']} ({b.get('folder_count', 0)} 题)  node={b['node_id']}")
        for sec in (b.get("sections") or [])[:20]:
            lines.append(f"    · {sec['name']} ({sec['count']} 题)")
        lines.extend(_format_uf_tree(b.get("subfolders") or [], 4))
    tgroups = tree.get("tag_groups") or []
    lines.append(f"标签个人分组（{len(tgroups)} 个）：")
    for g in tgroups:
        tag_names = "、".join(t["name"] for t in (g.get("tags") or [])) or "空"
        lines.append(f"  - 🏷 {g['name']}（id={short(g['id'])}）：{tag_names}")
    return "\n".join(lines)


def _parse_pages_arg(raw: str | None, total_pages: int) -> list[int]:
    if total_pages <= 0:
        return []
    if raw is None or not str(raw).strip():
        return list(range(total_pages))
    pages: list[int] = []
    seen: set[int] = set()
    for part in str(raw).split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_s, _, end_s = token.partition("-")
            try:
                start, end = int(start_s.strip()), int(end_s.strip())
            except ValueError:
                raise CliError(f"页码范围格式不正确：{token}（例：0-3 或 0,2,4）") from None
            if start < 0 or end >= total_pages or start > end:
                raise CliError(f"页码范围越界：{token}（有效页码 0–{total_pages - 1}）")
            for p in range(start, end + 1):
                if p not in seen:
                    seen.add(p)
                    pages.append(p)
        else:
            try:
                p = int(token)
            except ValueError:
                raise CliError(f"页码格式不正确：{token}") from None
            if not 0 <= p < total_pages:
                raise CliError(f"页码越界：{p}（有效页码 0–{total_pages - 1}）")
            if p not in seen:
                seen.add(p)
                pages.append(p)
    if not pages:
        raise CliError("请至少指定一个有效页码")
    return pages


def _parse_region_spec(raw: str) -> dict:
    page_part, sep, box_part = str(raw).strip().partition(":")
    if not sep:
        raise CliError(f"区域格式应为 页码:x0,y0,x1,y1，收到：{raw}")
    try:
        page_idx = int(page_part.strip())
        coords = [float(v.strip()) for v in box_part.split(",")]
    except ValueError:
        raise CliError(f"区域数字格式不正确：{raw}") from None
    if len(coords) != 4:
        raise CliError(f"区域需要 4 个 0–1000 坐标值：{raw}")
    return {"page_idx": page_idx, "bbox": coords}


def _parse_figure_spec(raw: str) -> dict:
    parts = [segment.strip() for segment in str(raw).strip().split(":")]
    if len(parts) == 2:
        slot = "stem"
        region_str = f"{parts[0]}:{parts[1]}"
    elif len(parts) == 3:
        slot = parts[0]
        region_str = f"{parts[1]}:{parts[2]}"
    else:
        raise CliError(f"配图格式应为 [槽位:]页码:x0,y0,x1,y1，收到：{raw}")
    parsed = _parse_region_spec(region_str)
    parsed["slot"] = slot
    return parsed


def cmd_pages(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    detail = paper_detail(client, paper)
    raw_pages = detail["paper"].get("pages") or []
    total_pages = len(raw_pages)
    if total_pages == 0:
        raise CliError("这份资料还没有渲染出页面。")
    selected = _parse_pages_arg(getattr(args, "pages", None), total_pages)
    grid = bool(getattr(args, "grid", False))
    folder = images_dir(detail["paper"], getattr(args, "out", None))
    page_info = {p["page_idx"]: p for p in raw_pages if isinstance(p, dict) and "page_idx" in p}
    saved_pages: list[dict] = []
    image_paths: list[str] = []
    suffix = "-grid" if grid else ""
    query = "?grid=1" if grid else ""
    for page_idx in selected:
        target = folder / f"page-{page_idx + 1}{suffix}.jpg"
        target.write_bytes(client.get_bytes(f"/api/papers/{paper['id']}/pages/{page_idx}/preview{query}"))
        meta = page_info.get(page_idx, {})
        path_str = str(target)
        saved_pages.append({
            "page_idx": page_idx,
            "width": meta.get("width"),
            "height": meta.get("height"),
            "grid": grid,
            "path": path_str,
        })
        image_paths.append(path_str)
    return {
        "paper": paper_summary(detail["paper"]),
        "total_pages": total_pages,
        "grid": grid,
        "pages": saved_pages,
        "image_paths": image_paths,
    }


def show_pages(result: dict) -> str:
    tag = "（含 0–1000 网格）" if result.get("grid") else ""
    lines = [f"{result['paper']['name']} · 共 {result['total_pages']} 页，已导出 {len(result['pages'])} 页{tag}："]
    for item in result["pages"]:
        lines.append(f"  第 {item['page_idx'] + 1} 页 (page_idx={item['page_idx']}): {item['path']}")
    return "\n".join(lines)


def cmd_blocks(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    raw_pages = getattr(args, "pages", None)
    query = ""
    if raw_pages and str(raw_pages).strip():
        total_pages = len(paper.get("pages") or []) or len((paper_detail(client, paper)["paper"]).get("pages") or [])
        selected = _parse_pages_arg(raw_pages, total_pages) if total_pages else []
        if selected:
            query = "?pages=" + ",".join(str(p) for p in selected)
    data = client.get(f"/api/papers/{paper['id']}/blocks{query}")
    blocks = data.get("blocks") or []
    return {
        "paper": paper_summary(data["paper"]),
        "pages": data.get("pages") or [],
        "structure": data.get("structure") or {},
        "count": len(blocks),
        "blocks": blocks,
    }


def show_blocks(result: dict) -> str:
    lines = [f"{result['paper']['name']} · 共 {len(result.get('pages') or [])} 页 · {result['count']} 个版面块："]
    for block in result.get("blocks", [])[:60]:
        preview = " ".join((block.get("table_markdown") or block.get("text") or "").split())[:90]
        lines.append(f"  [p{block.get('page_idx')} #{block.get('seq')} {block.get('type')}] bbox={block.get('bbox')}  {preview}")
    if result["count"] > 60:
        lines.append(f"  …… 其余 {result['count'] - 60} 个块请用 --json 或按 --pages 分页查看。")
    return "\n".join(lines)


def cmd_cut(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    if getattr(args, "file", None):
        raw_text = sys.stdin.read() if args.file == "-" else Path(args.file).expanduser().read_text(encoding="utf-8-sig")
        try:
            loaded = json.loads(raw_text)
        except json.JSONDecodeError as error:
            raise CliError(f"JSON 文件解析失败：{error}") from None
        body = {"questions": loaded} if isinstance(loaded, list) else dict(loaded or {})
    else:
        raw_regions = getattr(args, "region", None) or []
        if not raw_regions:
            raise CliError("请提供 --file <json> 或至少一个 --region 页码:x0,y0,x1,y1")
        item: dict = {"regions": [_parse_region_spec(r) for r in raw_regions]}
        if getattr(args, "figure", None):
            item["figures"] = [_parse_figure_spec(f) for f in args.figure]
        if getattr(args, "confirm_no_figure", False):
            item["confirm_no_figure"] = True
        if getattr(args, "number", None) is not None:
            item["number"] = args.number
        if getattr(args, "section", None) is not None:
            item["section"] = args.section
        if getattr(args, "group_id", None) is not None:
            item["group_id"] = args.group_id
        if getattr(args, "group_title", None) is not None:
            item["group_title"] = args.group_title
        if getattr(args, "source_kind", None):
            item["source_kind"] = args.source_kind
        if getattr(args, "type", None):
            item["question_type"] = args.type
        stem = read_text_arg(getattr(args, "stem", None), getattr(args, "stem_file", None))
        if stem is not None:
            item["stem"] = stem
        if getattr(args, "option", None):
            opts: dict[str, str] = {}
            for opt in args.option:
                key, sep, val = opt.partition("=")
                key = key.strip().upper()
                if not sep or key not in OPTION_KEYS:
                    raise CliError(f"--option 写成 A=内容，收到：{opt}")
                opts[key] = val
            item["options"] = opts
        if getattr(args, "answer", None) is not None:
            item["answer"] = args.answer
        if getattr(args, "analysis", None) is not None:
            item["analysis"] = args.analysis
        if getattr(args, "origin", None) is not None:
            item["origin"] = args.origin
        body = {"questions": [item]}

    if getattr(args, "replace_scope", None) and args.replace_scope != "none":
        body["replace_scope"] = args.replace_scope
    if getattr(args, "replace_pages", None):
        total_pages = len(paper.get("pages") or []) or len((paper_detail(client, paper)["paper"]).get("pages") or [])
        body["replace_pages"] = _parse_pages_arg(args.replace_pages, total_pages)
    if getattr(args, "replace_group_id", None) is not None:
        body["replace_group_id"] = args.replace_group_id
    if getattr(args, "replace_question_ids", None):
        try:
            body["replace_question_ids"] = [int(x.strip()) for x in str(args.replace_question_ids).split(",") if x.strip()]
        except ValueError:
            raise CliError("--replace-question-ids 需要逗号分隔的整数题目 ID") from None
    if getattr(args, "layout_revision", None) is not None:
        body["layout_revision"] = args.layout_revision
    body.setdefault("agent", client.agent)

    resp = client.post(f"/api/papers/{paper['id']}/agent-cut", body)
    created_set = set(resp.get("created_ids") or [])
    return {
        "paper": paper_summary(resp["paper"]),
        "layout_revision": resp.get("layout_revision"),
        "operation_id": (resp.get("operation") or {}).get("id"),
        "already_applied": bool(resp.get("already_applied")),
        "created_ids": resp.get("created_ids") or [],
        "replaced_ids": resp.get("replaced_ids") or [],
        "skipped_protected": resp.get("skipped_protected") or [],
        "questions": [card_summary(q) for q in (resp.get("questions") or []) if q.get("id") in created_set],
    }


def show_cut(result: dict) -> str:
    lines = [
        f"切题已保存（operation_id={result.get('operation_id')} · layout_revision={result.get('layout_revision')}）："
        f"新建 {len(result.get('created_ids') or [])} 道，替换草稿 {len(result.get('replaced_ids') or [])} 道。"
    ]
    if result.get("skipped_protected"):
        lines.append(f"受保护未覆盖（{len(result['skipped_protected'])} 道）：")
        for item in result["skipped_protected"]:
            lines.append(f"  - 第 {item.get('number')} 题 #{item.get('id')}：{item.get('reason')}")
    for q in result.get("questions") or []:
        mode = "原图待核对/识读" if q.get("body_mode") == "source_image" else "助手直录文字"
        lines.append(f"  第 {q['number']} 题 #{q['id']}  [{mode}]  {q.get('section') or ''}".rstrip())
    return "\n".join(lines)


def cmd_layout(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    layout_state = client.get(f"/api/papers/{paper['id']}/question-layout")
    layout_revision = layout_state.get("layout_revision")
    action = args.layout_action
    if action == "undo":
        op_id = getattr(args, "operation_id", None) or (layout_state.get("latest_operation") or {}).get("id")
        if not op_id:
            raise CliError("当前没有可撤销的原卷布局操作。")
        resp = client.post(
            f"/api/papers/{paper['id']}/question-layout/{op_id}/undo",
            {"layout_revision": layout_revision, "client_request_id": str(uuid.uuid4())},
        )
        return {
            "paper": paper_summary(resp["paper"]),
            "layout_revision": resp.get("layout_revision"),
            "operation": resp.get("operation"),
            "already_undone": bool(resp.get("already_undone")),
        }

    def src_entry(card_row: dict) -> dict:
        return {
            "id": card_row["id"],
            "revision": card_row["content_revision"],
            "fingerprint": card_row["layout_fingerprint"],
        }

    if action == "regions":
        card = find_card(layout_state, args.card)
        regions = [_parse_region_spec(r) for r in (args.region or [])]
        if not regions:
            raise CliError("请提供至少一个 --region 页码:x0,y0,x1,y1")
        payload = {
            "kind": "regions",
            "layout_revision": layout_revision,
            "client_request_id": str(uuid.uuid4()),
            "sources": [src_entry(card)],
            "targets": [{"regions": regions}],
        }
    elif action == "renumber":
        card = find_card(layout_state, args.card)
        payload = {
            "kind": "renumber",
            "layout_revision": layout_revision,
            "client_request_id": str(uuid.uuid4()),
            "sources": [src_entry(card)],
            "targets": [{"number": int(args.number)}],
        }
    elif action == "split":
        card = find_card(layout_state, args.card)
        if getattr(args, "file", None):
            raw = sys.stdin.read() if args.file == "-" else Path(args.file).expanduser().read_text(encoding="utf-8-sig")
            targets = json.loads(raw)
        else:
            parts = getattr(args, "part", None) or []
            if len(parts) < 2:
                raise CliError("split 需要至少 2 个 --part（或传 --file JSON 列表）")
            used = {
                int(q["number"])
                for q in (layout_state.get("questions") or [])
                if (q.get("group") or {}).get("id") == (card.get("group") or {}).get("id")
                and q.get("id") != card.get("id")
            }
            next_num = max(used | {int(card.get("number") or 1)}) + 1
            targets = []
            for idx, part in enumerate(parts):
                regions = [_parse_region_spec(seg.strip()) for seg in str(part).split(";") if seg.strip()]
                num = card["number"] if idx == 0 and card["number"] not in used else next_num
                if num == next_num:
                    next_num += 1
                targets.append({
                    "number": num,
                    "question_type": card.get("question_type") or "unknown",
                    "regions": regions,
                })
        payload = {
            "kind": "split",
            "layout_revision": layout_revision,
            "client_request_id": str(uuid.uuid4()),
            "sources": [src_entry(card)],
            "targets": targets,
        }
    elif action == "merge":
        refs = getattr(args, "cards", None) or []
        if len(refs) < 2:
            raise CliError("merge 需要至少 2 个题卡编号")
        source_cards = [find_card(layout_state, ref) for ref in refs]
        combined_regions = [region for row in source_cards for region in (row.get("regions") or [])]
        target_num = getattr(args, "number", None) or source_cards[0]["number"]
        payload = {
            "kind": "merge",
            "layout_revision": layout_revision,
            "client_request_id": str(uuid.uuid4()),
            "sources": [src_entry(c) for c in source_cards],
            "targets": [{
                "number": target_num,
                "question_type": source_cards[0].get("question_type") or "unknown",
                "regions": combined_regions,
            }],
        }
    else:
        raise CliError(f"不支持的布局操作：{action}")

    resp = client.post(f"/api/papers/{paper['id']}/question-layout", payload)
    op = resp.get("operation") or {}
    target_set = set(op.get("target_ids") or [])
    return {
        "paper": paper_summary(resp["paper"]),
        "layout_revision": resp.get("layout_revision"),
        "operation": op,
        "already_applied": bool(resp.get("already_applied")),
        "questions": [card_summary(q) for q in (resp.get("questions") or []) if q.get("id") in target_set],
    }


def show_layout(result: dict) -> str:
    op = result.get("operation") or {}
    if op.get("undone_at"):
        return f"已撤销布局操作 {op.get('id')}（layout_revision={result.get('layout_revision')}）。"
    lines = [f"布局操作完成（{op.get('kind')} · operation_id={op.get('id')} · layout_revision={result.get('layout_revision')}）："]
    for q in result.get("questions") or []:
        lines.append(f"  第 {q['number']} 题 #{q['id']}")
    return "\n".join(lines)


def cmd_read_cuts(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    detail = paper_detail(client, paper)
    plan_rev = int((detail["paper"].get("processing_plan") or {}).get("revision", 0))
    body: dict = {"revision": plan_rev}
    if getattr(args, "cards", None):
        selected = [find_card(detail, ref) for ref in args.cards]
        body["question_ids"] = [q["id"] for q in selected]
        body["revisions"] = {str(q["id"]): int(q.get("content_revision") or 0) for q in selected}
    else:
        body["question_ids"] = []
    resp = client.post(f"/api/papers/{paper['id']}/read-cut-questions", body)
    result = {
        "paper": paper_summary(resp["paper"]),
        "queued": resp.get("queued", 0),
        "queued_ids": resp.get("queued_ids") or [],
        "converted_ids": resp.get("converted_ids") or [],
        "skipped": resp.get("skipped") or [],
        "message": resp.get("message") or "",
    }
    if getattr(args, "wait", False) and result["queued"] > 0:
        result["wait"] = wait_for(client, resp["paper"], getattr(args, "timeout", 1800), quiet=args.json)
    return result


def show_read_cuts(result: dict) -> str:
    lines = [result.get("message") or f"已提交 {result['queued']} 道原图题卡进行看图识读。"]
    if result.get("skipped"):
        lines.append(f"已跳过 {len(result['skipped'])} 道：")
        for item in result["skipped"][:10]:
            lines.append(f"  - #{item.get('id')}：{item.get('reason')}")
    if "wait" in result:
        lines.append(show_wait(result["wait"]))
    return "\n".join(lines)


SHOWERS = {
    "status": show_status, "config": show_config, "papers": show_papers, "upload": show_upload, "wait": show_wait,
    "pages": show_pages, "blocks": show_blocks, "cut": show_cut, "layout": show_layout, "read-cuts": show_read_cuts,
    "cards": show_cards,
    "show": show_card, "fix": show_fix, "figures": show_figures, "approve": show_approve, "publish": show_publish,
    "unapprove": show_unapprove, "reread": show_reread,
    "library": show_library,
    "locate": show_locate,
    "backup": show_backup,
    "folders": show_folders,
    "solutions": show_solutions,
    "workbench": show_workbench,
    "skill": show_skill,
    "setup-agents": show_setup_agents,
}


# ---------------------------------------------------------------- MCP（给支持 MCP 的 AI 用）

MCP_INSTRUCTIONS = (
    "题有据：把扫描/拍照的数学试卷变成核对过的题库。支持两条录题路线：\n"
    "1) 传统流水线：upload_paper → wait_paper → list_cards(filter=todo) → show_card → fix_card / set_figures → approve_cards → publish_paper。\n"
    "2) 助手自主探查与切题（推荐复杂教辅或无看图密钥时）：upload_paper(scan_only=true 或 manual) → wait_paper → "
    "get_paper_blocks（读 MinerU/本地文字块及 bbox）与 get_paper_pages(grid=true，查看原页高清图与 0–1000 网格）→ "
    "由你自己分析章节与题号规则后调用 cut_questions 批量切题；cut_questions 支持双轨：传入 stem 直接落库为 text（跳过内置 OCR 队列），"
    "或不传 stem 只存切图范围（body_mode=source_image），之后可按需调用 read_cut_questions 交给内置看图模型识读；局部微调用 edit_question_layout。\n"
    "你打的勾记成“AI 通过”，使用者会再核对。不要碰密钥；缺密钥请使用者在软件“设置 → 常用”里填写"
    "（status 会给出申请网址，费用与额度按当前账户规则）。题卡写着“MinerU 初稿”或助手直录时（AI 助手读题），"
    "题面还没人看图核对过：每一道都要对照截图逐字核对。"
    "题型没读出来（type_blocked）的题不能通过：先用 fix_card 只传 type 选好题型。"
    "用户明确需要标签或参考答案时，当前助手自己完成，不需要豆包API：configure_features明确打开所需开关，"
    "list_enrichment_tasks → prepare_enrichment（看题面、原卷和配图，按目录选标签或自己解题复核）→ submit_enrichment。"
    "按每个kind任务的fingerprint提交，不改原卷答案或审核；结果仍是AI附加内容、未核对。独立模型API只是可选方式。"
    "用户要整批生成时，逐个入库题prepare，再按每个kind分别submit，不把多道题合成一段答案。"
    "用户明确要以后新题入库时自动处理，可configure_enrichment_auto设置自动排队（默认关闭），所需feature仍需显式启用。"
    "自动任务在新题入库之后创建；助手模式由当前助手继续领取完成，不声称已在OCR前生成答案或已常驻豆包。"
    "已经入库的题不要直接改字：单题题面勘误用 propose_question_change 交成提案，题面不动，等老师点采纳。"
    "严禁用 propose_question_change 批量灌整本答案书：批量写入标准答案书/官方解析必须使用 import_solutions（直接写入版本化解析层、不打回黄卡、默认保护已有解析且支持 undo_solutions 整批撤销）。"
    "但整理文件夹（list_folders / manage_folder / set_question_sections / update_paper_folder / manage_tag_groups）直接生效并支持撤销。"
    "批量干活先 claim_work 领一批（每道活带到期时间，助手没了活会自己回到池子里），干不完 release_work 交回。"
    "where_is_the_teacher 告诉你老师此刻在哪、你刚提的有没有被否决。"
)


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


PAPER = {"type": "string", "description": "试卷：编号（前 8 位即可）、latest（最新一份）或名字里的一段"}
CARD = {"type": "string", "description": "题号，例如 \"9\"；题号重复时用 \"#题卡编号\""}

MCP_TOOLS = [
    ("status", "看题有据是否在运行、密钥是否填好、有多少试卷。", _schema({})),
    ("start_app", "题有据没开时把它打开。", _schema({})),
    ("configure_reading", "看或改读题方式（先征得使用者同意）。reader=assistant：没有看图密钥时由你对照原卷核对 MinerU 的初稿；"
     "或选 modelscope（免费）、minimax、siliconflow。不传参数就只看现状。", _schema({
         "reader": {"type": "string", "enum": list(READERS)}, "checker": {"type": "string", "enum": list(CHECKERS)},
         "minimax_plan": {"type": "string", "enum": list(PLANS)},
     })),
    ("list_papers", "列出所有试卷和进度。", _schema({})),
    ("upload_paper", "上传一份试卷（PDF、Word，或几张照片合成一份）。", _schema({
        "paths": {"type": "array", "items": {"type": "string"}, "description": "本机文件的完整路径"},
        "book": {"type": "boolean", "description": "是一本书/讲义而不是一份试卷"},
        "parse_mode": {"type": "string", "enum": ["auto", "manual", "native", "mineru"],
                       "description": "默认auto先处理本地文字，无可靠题卡则手工；manual手工；native本地文字；mineru明确云解析"},
        "allow_cloud": {"type": "boolean", "description": "默认false；明确同意auto本地无可靠结果时尝试已配置MinerU，不另选付费服务"},
        "cut_only": {"type": "boolean", "description": "默认false；只切题不调用看图识读，保留原图题卡"},
        "scan_only": {"type": "boolean", "description": "默认false；仅保留原页及版面扫描块(blocks)，跳过自动切题与自动分组停在READY，供助手自主探查切题"},
    }, ["paths"])),
    ("wait_paper", "等一份试卷读完（最多等 timeout 秒，没读完可以再调一次）。", _schema({
        "paper": PAPER, "timeout": {"type": "integer", "minimum": 5, "maximum": 600},
    }, ["paper"])),
    ("list_cards", "列出一份试卷的题卡和疑点。filter：todo 需逐题核对、green 识读完成未通过、approved、ai、human、all。",
     _schema({"paper": PAPER, "filter": {"type": "string", "enum": list(FILTERS)}}, ["paper"])),
    ("show_card", "看一道题：读出的文字、疑点、识读记录，以及原卷截图（有候选图时另给一张标了 图1、图2… 的截图）和配图。",
     _schema({"paper": PAPER, "card": CARD}, ["paper", "card"])),
    ("fix_card", "改一道题的文字。只传要改的字段；公式用 $...$ 包住的 LaTeX；表格用 Markdown。", _schema({
        "paper": PAPER, "card": CARD, "stem": {"type": "string"},
        "options": {"type": "object", "description": "要改的选项，例如 {\"B\": \"-3\"}；值为空字符串表示删掉该选项",
                    "additionalProperties": {"type": "string"}},
        "type": {"type": "string", "enum": list(TYPES), "description": "只传 type 时只改题型"},
        "answer": {"type": "string"}, "analysis": {"type": "string"},
        "origin": {"type": "string", "description": "题源：题干前印的出处，如“2026××中学月考”"},
        "section": {"type": "string", "description": "章节小节目录（如 第1章 / 1.1 或 第1节 公式法 · 例题）"},
    }, ["paper", "card"])),
    ("set_figures", "处理一道题的配图：use 选候选图（编号见 show_card，如 [\"1\", \"2:A\"]）、keep 保留现有配图、none 确认无图。",
     _schema({"paper": PAPER, "card": CARD, "use": {"type": "array", "items": {"type": "string"}},
              "keep": {"type": "boolean"}, "none": {"type": "boolean"}}, ["paper", "card"])),
    ("approve_cards", "逐题对照原卷无误后打勾（记为 AI 通过）。cards 写题号；green=true 一起通过已核对的绿卡。",
     _schema({"paper": PAPER, "cards": {"type": "array", "items": {"type": "string"}}, "green": {"type": "boolean"}},
             ["paper"])),
    ("unapprove_card", "撤销自己（AI）打的勾。人工通过的不能撤。", _schema({"paper": PAPER, "card": CARD}, ["paper", "card"])),
    ("reread_card", "让题有据的读题模型重读一道题。", _schema({"paper": PAPER, "card": CARD}, ["paper", "card"])),
    ("publish_paper", "把已通过的题入库（AI 通过的题在题库里标着“AI 审核”）。", _schema({"paper": PAPER}, ["paper"])),
    ("search_library", "在正式题库里搜题（可按题源、知识点、有无答案找）。", _schema({
        "keywords": {"type": "string"}, "review": {"type": "string", "enum": ["human", "ai"]},
        "answer": {"type": "string", "enum": ["yes", "no"], "description": "yes 只要有答案的，no 只要原卷没答案的"},
        "tag": {"type": "string", "description": "知识点标签（设置里打开“知识点标签”后才有）"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    })),
    ("configure_features", "只读查看标签/参考答案开关，或按用户明确生成需求启用/关闭。默认关闭；不配置API、不生成内容。", _schema({
        "enable": {"type": "array", "items": {"type": "string", "enum": list(ENRICH_FEATURES)}},
        "disable": {"type": "array", "items": {"type": "string", "enum": list(ENRICH_FEATURES)}},
    })),
    ("list_enrichment_tasks", "查看待当前助手自己完成的标签/参考答案任务。不自动启用功能或调用云模型。ids是入库题UUID。", _schema({
        "ids": {"type": "array", "maxItems": 500, "items": {"type": "string"}},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    })),
    ("configure_enrichment_auto", "查看或明确设置新题入库后自动排队标签/参考答案（默认关闭）。不改功能开关、模型、API或旧题；助手继续领任务完成。", _schema({
        "tags": {"type": "boolean"}, "answer": {"type": "boolean"},
    })),
    ("prepare_enrichment", "为指定入库题准备标签或参考答案任务，返回题面、原卷/配图、知识点目录、prompt和fingerprint。当前助手自己看图完成，不调用API。", _schema({
        "publication_id": {"type": "string"},
        "job_id": {"type": "string", "description": "本次待办的精确任务ID；取消或完成后拒绝，不重新创建任务"},
        "kinds": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string", "enum": list(ENRICH_KINDS)}},
        "agent": {"type": "string", "description": "真实助手名称，缺省使用当前MCP客户端名"},
    }, ["publication_id"])),
    ("submit_enrichment", "交回当前助手完成的一种任务。tags从目录选1–3个；答案自己解题复核，仍标未核对。不改原卷答案或审核；过期任务拒绝写入。", _schema({
        "job_id": {"type": "string"}, "fingerprint": {"type": "string"},
        "agent": {"type": "string", "description": "真实助手名称，缺省使用当前MCP客户端名"},
        "tags": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string"}},
        "answer": {"type": "string"}, "analysis": {"type": "string"},
    }, ["job_id", "fingerprint"])),
    ("claim_work", "领一批助手活。每道活带到期时间；助手中途没了，活会自己回到池子里，别的助手能接着领。", _schema({
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "kinds": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string", "enum": list(ENRICH_KINDS)}},
        "ids": {"type": "array", "maxItems": 500, "items": {"type": "string"}},
        "agent": {"type": "string", "description": "真实助手名称，缺省使用当前MCP客户端名"},
    })),
    ("release_work", "把没干完的助手活交回池子，别的助手能接着领。", _schema({
        "ids": {"type": "array", "minItems": 1, "maxItems": 500, "items": {"type": "string"}},
        "agent": {"type": "string", "description": "真实助手名称，缺省使用当前MCP客户端名"},
    }, ["ids"])),
    ("propose_question_change", "交一份单题题面改动提案（仅限修正题干、选项、题型、题源或个别错字）：题面一个字不动，等老师点采纳。严禁用此工具批量灌整本答案书（批量导入标准答案解析请改用 import_solutions）。", _schema({
        "publication_id": {"type": "string", "description": "入库题 UUID"},
        "fields": {
            "type": "object",
            "description": "要改的字段。只写要改的那几项。",
            "properties": {
                "stem": {"type": "string"},
                "options": {"type": "object", "additionalProperties": {"type": "string"}},
                "question_type": {"type": "string", "enum": list(TYPES)},
                "answer": {"type": "string"},
                "analysis": {"type": "string"},
                "origin": {"type": "string"},
            },
        },
        "agent": {"type": "string", "description": "真实助手名称，缺省使用当前MCP客户端名"},
    }, ["publication_id", "fields"])),
    ("import_solutions", "批量将已有标准答案书/官方解析直接写入正式题库解析层（LibrarySolution），不动源题卡、不清通过勾、默认不覆盖已有解析且支持整批撤销。严禁用 propose_question_change 批量灌书本答案解析。建议先设 dry_run=true 预览并核对客观/填空题已填写 answer。", _schema({
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 500,
            "description": "待导入列表：每项含 publication_id（UUID 或 Q-xxxxxxxx 短码）及 answer / analysis（至少填一项）",
            "items": {
                "type": "object",
                "properties": {
                    "publication_id": {"type": "string"},
                    "answer": {"type": "string"},
                    "analysis": {"type": "string"},
                },
                "required": ["publication_id"],
            },
        },
        "overwrite": {"type": "boolean", "description": "是否覆盖已有保存解析（默认 false 保护已有解析）"},
        "dry_run": {"type": "boolean", "description": "是否仅干跑校验和统计预览而不写入（建议首次先设为 true）"},
        "note": {"type": "string", "description": "本次导入批次说明"},
        "agent": {"type": "string", "description": "真实助手名称，缺省使用当前MCP客户端名"},
    }, ["items"])),
    ("list_solution_batches", "查看最近的批量解析导入历史批次（可用于核查或整批撤销）。", _schema({
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    })),
    ("undo_solutions", "整批撤销指定（或最近一次）批量解析导入；若某题后续已被人工另行修改则自动跳过保护。", _schema({
        "batch_id": {"type": "string", "description": "要撤销的批次 UUID（省略则撤销最近一次未撤销批次）"},
    })),
    ("read_workbench", "读助手工作台：待核对真实总数、按助手汇总、谁在干活、老师此刻在哪。", _schema({
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "agent": {"type": "string", "description": "按指定助手名称筛选待核对提案"},
        "scope": {"type": "string", "enum": ["all", "solution_only", "face"], "description": "筛选提案类型"},
    })),
    ("where_is_the_teacher", "问一句老师此刻在哪、你刚提的改动有没有被否决。", _schema({})),
    ("list_folders", "查看题库文件夹树（我的文件夹、试卷夹、教材章节树、智能文件夹、排序方式与标签个人分组）。", _schema({})),
    ("manage_folder", "直接整理我的文件夹、智能文件夹、题目归档与排序方式（支持 undo 撤销）。", _schema({
        "action": {"type": "string", "enum": [
            "create", "rename", "move", "delete",
            "add_questions", "remove_questions", "set_sort",
            "create_smart", "rename_smart", "move_smart", "delete_smart", "undo",
        ]},
        "name": {"type": "string", "description": "文件夹名称（create/rename/create_smart/rename_smart 用）"},
        "folder_id": {"type": "string", "description": "目标文件夹 ID 或名称"},
        "parent_id": {"type": "string", "description": "父级节点 ID（为空表示根目录；也可传 group:papers / group:books / paper:<uuid> / book:<uuid>）"},
        "target_id": {"type": "string", "description": "归档目标（folder_id、userfolder:<id>、paper:<uuid>、book:<uuid>）或排序节点 ID（global 表示全局默认）"},
        "question_ids": {"type": "array", "items": {"type": "string"}, "description": "入库题目 UUID 或 Q-xxxxxxxx 短码列表"},
        "mode": {"type": "string", "enum": list(SORT_MODES), "description": "排序方式（set_sort 用）"},
        "filter": {"type": "object", "description": "智能文件夹筛选条件（create_smart 用）"},
        "operation_id": {"type": "string", "description": "指定要撤销的操作 ID（undo 可选）"},
    }, ["action"])),
    ("set_question_sections", "批量整理已入库教材/教辅题目的章节小节目录（section），直接生效且可通过 manage_folder(action=undo) 撤销。", _schema({
        "section": {"type": "string", "description": "统一设置的章节小节名（配合 question_ids 使用）"},
        "question_ids": {"type": "array", "items": {"type": "string"}, "description": "入库题目 UUID 或 Q-xxxxxxxx 短码列表"},
        "items": {
            "type": "array",
            "description": "逐题映射列表：[{publication_id, section}]（最多 500 项）",
            "items": {
                "type": "object",
                "properties": {
                    "publication_id": {"type": "string"},
                    "section": {"type": "string"},
                },
                "required": ["publication_id", "section"],
            },
        },
    })),
    ("update_paper_folder", "重命名试卷/教材显示名称，或在「试卷(exam) ↔ 教材(book)」之间切换资料类型。", _schema({
        "paper": PAPER,
        "name": {"type": "string", "description": "新的显示名称"},
        "material_type": {"type": "string", "enum": ["exam", "book"], "description": "资料类型：exam 试卷，book 教材教辅"},
    }, ["paper"])),
    ("manage_tag_groups", "查看或整理标签管理里的「个人分组」（建组/改名/删组/增减标签/撤销），不改动知识上下级。", _schema({
        "action": {"type": "string", "enum": [
            "list", "create_group", "update_group", "delete_group",
            "add_tags", "remove_tags", "set_tags", "undo",
        ]},
        "group_id": {"type": "string", "description": "个人分组 UUID 或名称"},
        "name": {"type": "string", "description": "分组名称"},
        "color": {"type": "string", "description": "#RRGGBB 颜色（可选）"},
        "position": {"type": "integer", "minimum": 0, "description": "排序位置（可选）"},
        "tag_ids": {"type": "array", "items": {"type": "string"}, "description": "标签 UUID 或精确标签名列表"},
        "operation_id": {"type": "string", "description": "指定要撤销的个人分组操作 ID"},
    }, ["action"])),
    ("get_paper_pages", "导出指定页的原页高清截图（可选叠加 0–1000 网格），单次最多附加 8 张图片。", _schema({
        "paper": PAPER,
        "pages": {"type": "string", "description": "页码范围（从0起，如 \"0,1\" 或 \"0-3\"）；不传则从第0页起取最多8页"},
        "grid": {"type": "boolean", "description": "是否在图像副本上绘制 0–1000 归一化坐标网格"},
    }, ["paper"])),
    ("get_paper_blocks", "获取资料已扫描的版面块（MinerU/本地文字层文本、表格Markdown、bbox坐标及suggested_scopes结构）。", _schema({
        "paper": PAPER,
        "pages": {"type": "string", "description": "可选，只看某几页（如 \"0,1\" 或 \"0-5\"）"},
    }, ["paper"])),
    ("cut_questions", "助手批量切分题卡，支持双轨：传入 stem 直接跳过OCR录为文字题；不传 stem 存为原图题卡（可后续调 read_cut_questions）。支持安全重切 replace_scope。", _schema({
        "paper": PAPER,
        "questions": {
            "type": "array",
            "description": "1–500 道待建题卡：每项需包含 regions:[{page_idx, bbox}]；可选 figures/confirm_no_figure/number/group_id/group_title/section/source_kind/question_type/stem/options/answer/analysis/origin",
            "items": {"type": "object"},
        },
        "replace_scope": {"type": "string", "enum": ["none", "page", "group", "paper", "question_ids"],
                          "description": "重切范围（默认none）。仅替换未审核、未入库、未编辑的旧草稿，受保护题卡绝不删除"},
        "replace_pages": {"type": "array", "items": {"type": "integer"}, "description": "replace_scope=page 时指定页码"},
        "replace_group_id": {"type": "integer", "description": "replace_scope=group 时指定题组 ID"},
        "replace_question_ids": {"type": "array", "items": {"type": "integer"}, "description": "replace_scope=question_ids 时指定旧题 ID"},
        "layout_revision": {"type": "integer", "description": "可选乐观锁版本号"},
        "agent": {"type": "string"},
    }, ["paper", "questions"])),
    ("edit_question_layout", "调整已切题卡的原卷布局：改范围(regions)、改题号(renumber)、拆题(split)、合题(merge)或撤销(undo)。", _schema({
        "paper": PAPER,
        "action": {"type": "string", "enum": ["regions", "renumber", "split", "merge", "undo"]},
        "card": {"type": "string", "description": "源题号或 #id（regions/renumber/split 用）"},
        "cards": {"type": "array", "items": {"type": "string"}, "description": "待合并的多道题号或 #id（merge 用）"},
        "regions": {"type": "array", "description": "新的 [{page_idx, bbox}]（regions 用）", "items": {"type": "object"}},
        "number": {"type": "integer", "description": "新题号（renumber/merge 可用）"},
        "targets": {"type": "array", "description": "拆题目标列表 [{number, regions, question_type}]（split 用）", "items": {"type": "object"}},
        "operation_id": {"type": "string", "description": "要撤销的操作 UUID（undo 可选）"},
    }, ["paper", "action"])),
    ("read_cut_questions", "把已切好但还是原图状态（source_image）的题卡提交给软件内置看图模型进行识读。", _schema({
        "paper": PAPER,
        "cards": {"type": "array", "items": {"type": "string"}, "description": "指定题号或 #id；不传则识读该资料所有待识读原图题卡"},
        "wait": {"type": "boolean", "description": "是否阻塞等待识读完成"},
        "timeout": {"type": "integer", "minimum": 5, "maximum": 1800},
    }, ["paper"])),
]


def _ns(**values) -> argparse.Namespace:
    defaults = {"json": True, "force": False, "out": None, "no_images": False}
    return argparse.Namespace(**{**defaults, **values})


def mcp_call(client: Client, name: str, arguments: dict) -> tuple[dict | list, list[str]]:
    """Run one tool; return (result, image paths to attach)."""
    a = arguments or {}
    if name == "status":
        return cmd_status(client, _ns()), []
    if name == "start_app":
        return cmd_start(client, _ns(timeout=90)), []
    if name == "configure_reading":
        return cmd_config(client, _ns(reader=a.get("reader"), checker=a.get("checker"),
                                      minimax_plan=a.get("minimax_plan"))), []
    if name == "list_papers":
        return cmd_papers(client, _ns()), []
    if name == "upload_paper":
        return cmd_upload(client, _ns(files=a.get("paths") or [], book=bool(a.get("book")),
                                      parse_mode=a.get("parse_mode"), allow_cloud=a.get("allow_cloud") is True,
                                      cut_only=bool(a.get("cut_only")), scan_only=bool(a.get("scan_only")),
                                      wait=False, timeout=0)), []
    if name == "wait_paper":
        return wait_for(client, find_paper(client, a.get("paper", "")), min(600, int(a.get("timeout") or 300)), quiet=True), []
    if name == "list_cards":
        return cmd_cards(client, _ns(paper=a.get("paper", ""), filter=a.get("filter") or "all")), []
    if name == "show_card":
        result = card_detail(client, a.get("paper", ""), a.get("card", ""))
        images = result.get("images") or {}
        paths = images.get("question_images") or [images.get("candidates") or images.get("crop"), *(images.get("figures") or [])]
        return result, [path for path in paths if path]
    if name == "fix_card":
        options = a.get("options") or {}
        return cmd_fix(client, _ns(
            paper=a.get("paper", ""), card=a.get("card", ""), stem=a.get("stem"), stem_file=None,
            option=[f"{key}={value}" for key, value in options.items() if str(value).strip()],
            clear_option=[key for key, value in options.items() if not str(value).strip()],
            type=a.get("type"), answer=a.get("answer"), analysis=a.get("analysis"),
            origin=a.get("origin"), section=a.get("section"))), []
    if name == "set_figures":
        return cmd_figures(client, _ns(paper=a.get("paper", ""), card=a.get("card", ""), use=a.get("use") or [],
                                       keep=bool(a.get("keep")), none=bool(a.get("none")))), []
    if name == "approve_cards":
        return cmd_approve(client, _ns(paper=a.get("paper", ""), cards=a.get("cards") or [],
                                       green=bool(a.get("green")))), []
    if name == "unapprove_card":
        return cmd_unapprove(client, _ns(paper=a.get("paper", ""), card=a.get("card", ""))), []
    if name == "reread_card":
        return cmd_reread(client, _ns(paper=a.get("paper", ""), card=a.get("card", ""))), []
    if name == "publish_paper":
        return cmd_publish(client, _ns(paper=a.get("paper", ""))), []
    if name == "search_library":
        return cmd_library(client, _ns(keywords=str(a.get("keywords") or "").split(), paper=None, type=None,
                                       review=a.get("review"), answer=a.get("answer"), tag=a.get("tag"),
                                       limit=int(a.get("limit") or 20))), []
    if name == "configure_features":
        return cmd_features(client, _ns(enable=a.get("enable"), disable=a.get("disable"))), []
    if name == "list_enrichment_tasks":
        return cmd_enrich_tasks(client, _ns(ids=a.get("ids"), limit=a.get("limit", 50))), []
    if name == "configure_enrichment_auto":
        return cmd_enrich_auto(client, _ns(tags=a.get("tags"), answer=a.get("answer"))), []
    if name == "prepare_enrichment":
        result = cmd_enrich_prepare(client, _ns(publication_id=a.get("publication_id"),
                                               kinds=a.get("kinds"), agent=a.get("agent"), job_id=a.get("job_id")))
        images = result.get("local_images") or {}
        paths = images.get("question_images") or [images.get("crop"), *(images.get("figures") or [])]
        return result, [path for path in paths if path]
    if name == "submit_enrichment":
        return cmd_enrich_submit(client, _ns(job_id=a.get("job_id"), fingerprint=a.get("fingerprint"),
                                             agent=a.get("agent"), tags=a.get("tags"),
                                             answer=a.get("answer"), analysis=a.get("analysis"))), []
    if name == "claim_work":
        return cmd_claim(client, _ns(limit=a.get("limit", 10), kinds=a.get("kinds"),
                                     ids=a.get("ids"), agent=a.get("agent"))), []
    if name == "release_work":
        return cmd_release(client, _ns(job_id=a.get("ids"), agent=a.get("agent"))), []
    if name == "propose_question_change":
        fields = a.get("fields") or {}
        options = fields.get("options") or {}
        return cmd_propose(client, _ns(
            publication_id=a.get("publication_id"), agent=a.get("agent"),
            stem=fields.get("stem"), stem_file=None,
            option=[f"{key}={value}" for key, value in options.items() if str(value).strip()],
            type=fields.get("question_type"), answer=fields.get("answer"),
            analysis=fields.get("analysis"), origin=fields.get("origin"))), []
    if name == "import_solutions":
        body = {
            "items": list(a.get("items") or []),
            "overwrite": bool(a.get("overwrite", False)),
            "dry_run": bool(a.get("dry_run", False)),
            "agent": a.get("agent") or client.agent,
        }
        if a.get("note") is not None:
            body["note"] = a["note"]
        return client.post("/api/library/solutions/batch", body, timeout=120), []
    if name == "list_solution_batches":
        limit = int(a.get("limit") or 20)
        return client.get(f"/api/library/solutions/batches?limit={limit}"), []
    if name == "undo_solutions":
        body = {}
        if a.get("batch_id"):
            body["batch_id"] = a["batch_id"]
        return client.post("/api/library/solutions/undo", body), []
    if name == "read_workbench":
        return cmd_workbench(client, _ns(
            limit=a.get("limit", 50), from_agent=a.get("agent"), scope=a.get("scope", "all"), clear=False,
        )), []
    if name == "where_is_the_teacher":
        return cmd_whereami(client, _ns()), []
    if name == "list_folders":
        return client.get("/api/library/folders"), []
    if name == "manage_folder":
        action = a.get("action")
        if action == "undo":
            body = {}
            if a.get("operation_id"):
                body["operation_id"] = a["operation_id"]
            return client.post("/api/library/folders/undo", body), []
        return client.post("/api/library/folders", a), []
    if name == "set_question_sections":
        return client.post("/api/library/folders", {
            "action": "set_section",
            "section": a.get("section"),
            "question_ids": a.get("question_ids"),
            "items": a.get("items"),
        }), []
    if name == "update_paper_folder":
        paper = find_paper(client, a.get("paper", ""))
        body = {}
        if a.get("name") is not None:
            body["name"] = a["name"]
        if a.get("material_type") is not None:
            body["material_type"] = a["material_type"]
        if not body:
            raise CliError("请提供 name 或 material_type")
        return client.patch(f"/api/papers/{paper['id']}", body), []
    if name == "manage_tag_groups":
        if a.get("action") == "list":
            return client.get("/api/library/tag-groups"), []
        return client.post("/api/library/tag-groups", a), []
    if name == "get_paper_pages":
        result = cmd_pages(client, _ns(paper=a.get("paper", ""), pages=a.get("pages"),
                                      grid=bool(a.get("grid")), out=None))
        all_paths = list(result.get("image_paths") or [])
        attached = all_paths[:8]
        result["attached_count"] = len(attached)
        result["truncated"] = len(all_paths) > 8
        return result, attached
    if name == "get_paper_blocks":
        return cmd_blocks(client, _ns(paper=a.get("paper", ""), pages=a.get("pages"))), []
    if name == "cut_questions":
        paper = find_paper(client, a.get("paper", ""))
        body = {k: v for k, v in a.items() if k != "paper" and v is not None}
        body.setdefault("agent", client.agent)
        resp = client.post(f"/api/papers/{paper['id']}/agent-cut", body)
        created_set = set(resp.get("created_ids") or [])
        return {
            "paper": paper_summary(resp["paper"]),
            "layout_revision": resp.get("layout_revision"),
            "operation_id": (resp.get("operation") or {}).get("id"),
            "already_applied": bool(resp.get("already_applied")),
            "created_ids": resp.get("created_ids") or [],
            "replaced_ids": resp.get("replaced_ids") or [],
            "skipped_protected": resp.get("skipped_protected") or [],
            "questions": [card_summary(q) for q in (resp.get("questions") or []) if q.get("id") in created_set],
        }, []
    if name == "edit_question_layout":
        action = a.get("action")
        region_strs = [
            f"{r['page_idx']}:{','.join(str(v) for v in r['bbox'])}"
            for r in (a.get("regions") or [])
            if isinstance(r, dict) and "page_idx" in r and isinstance(r.get("bbox"), list)
        ]
        part_strs = []
        if action == "split" and isinstance(a.get("targets"), list):
            for t in a["targets"]:
                if isinstance(t, dict) and isinstance(t.get("regions"), list):
                    segs = [
                        f"{r['page_idx']}:{','.join(str(v) for v in r['bbox'])}"
                        for r in t["regions"]
                        if isinstance(r, dict) and "page_idx" in r and isinstance(r.get("bbox"), list)
                    ]
                    if segs:
                        part_strs.append(";".join(segs))
        return cmd_layout(client, _ns(
            paper=a.get("paper", ""),
            layout_action=action,
            card=a.get("card"),
            cards=a.get("cards") or [],
            region=region_strs,
            part=part_strs,
            number=a.get("number"),
            operation_id=a.get("operation_id"),
            file=None,
        )), []
    if name == "read_cut_questions":
        return cmd_read_cuts(client, _ns(
            paper=a.get("paper", ""),
            cards=a.get("cards") or [],
            wait=bool(a.get("wait")),
            timeout=int(a.get("timeout") or 1800),
        )), []
    raise CliError(f"没有这个工具：{name}")


def mcp_reply(message_id, result=None, error=None) -> dict:
    reply = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        reply["error"] = error
    else:
        reply["result"] = result
    return reply


def mcp_handle(client: Client, message: dict) -> dict | None:
    method = message.get("method")
    message_id = message.get("id")
    if message_id is None:  # notifications need no answer
        return None
    params = message.get("params") or {}
    if method == "initialize":
        client_name = (params.get("clientInfo") or {}).get("name")
        if client_name and not os.environ.get("TIYOUJU_AGENT"):
            client.agent = str(client_name)[:40]
        return mcp_reply(message_id, {
            "protocolVersion": params.get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "tiyouju", "title": "题有据", "version": VERSION},
            "instructions": MCP_INSTRUCTIONS,
        })
    if method == "ping":
        return mcp_reply(message_id, {})
    if method == "tools/list":
        return mcp_reply(message_id, {"tools": [
            {"name": name, "description": description, "inputSchema": schema}
            for name, description, schema in MCP_TOOLS
        ]})
    if method == "tools/call":
        name = params.get("name", "")
        try:
            result, image_paths = mcp_call(client, name, params.get("arguments") or {})
        except CliError as error:
            return mcp_reply(message_id, {"content": [{"type": "text", "text": str(error)}], "isError": True})
        content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=1)}]
        for path in image_paths:
            content.append({"type": "image", "mimeType": "image/png",
                            "data": base64.b64encode(Path(path).read_bytes()).decode("ascii")})
        return mcp_reply(message_id, {"content": content, "isError": False})
    return mcp_reply(message_id, error={"code": -32601, "message": f"Method not found: {method}"})


def run_mcp(client: Client, stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = mcp_reply(None, error={"code": -32700, "message": "Parse error"})
        else:
            if not isinstance(message, dict):
                reply = mcp_reply(None, error={"code": -32600, "message": "Invalid request"})
            else:
                try:
                    reply = mcp_handle(client, message)
                except Exception as error:  # keep the server alive for the next call
                    reply = mcp_reply(message.get("id"), error={"code": -32603, "message": str(error)})
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


# ---------------------------------------------------------------- 入口

WORKFLOW = """\
处理一份试卷的标准步骤（详见 skills/tiyouju/SKILL.md）：
  1. tiyouju status                         看软件开没开、密钥填没填（缺什么、去哪免费申请）
  2. tiyouju upload 卷子.pdf --wait         上传并等它读完
  3. tiyouju cards latest --filter todo     列出需要逐题核对的题
  4. tiyouju show latest 9                  看第 9 题：文字 + 原卷截图（打开图片逐字对照）
  5. tiyouju fix latest 9 --stem-file 9.txt 不对就改；配图用 figures
  6. tiyouju approve latest 9               对了就打勾（记为 AI 通过）
  7. tiyouju approve latest --green         绿卡抽查后一起通过
  8. tiyouju publish latest                 入库
标签/参考答案由当前助手自己完成（不需要豆包API）：按用户需求 features --enable，再 enrich prepare / submit 写回附加内容，仍待核对。
批量的活先 claim 领一批（每道活带到期时间，助手没了活会自己回到池子里），干不完用 release 交回。
已经入库的题不要直接 fix：改法用 propose 交成提案，题面一个字不动，等老师点采纳。
whereami 告诉你老师此刻在哪、你刚提的有没有被否决。
没有看图读题的密钥：征得使用者同意后 tiyouju config --reader assistant，题卡先用 MinerU 的文字，由你逐题核对。
每个命令加 --json 输出 JSON（纯 ASCII，中文写成 \\uXXXX，任何终端都不会乱码）。
退出码：0 成功，1 出错，2 题有据没打开，3 需要使用者处理。"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tiyouju", description="题有据的命令行：让 AI 助手上传、核对、修改、通过、入库试卷。",
        epilog=WORKFLOW, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"tiyouju {VERSION}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="输出 JSON")
    common.add_argument("--agent", default=os.environ.get("TIYOUJU_AGENT") or DEFAULT_AGENT,
                        help="你是谁（显示在“××通过”上），默认“AI 助手”，例如 --agent 豆包")
    sub = parser.add_subparsers(dest="command", metavar="命令")

    sub.add_parser("status", parents=[common], help="看软件是否在运行、密钥是否填好")
    config = sub.add_parser("config", parents=[common], help="看或改读题方式（不碰密钥；改之前先问使用者）")
    config.add_argument("--reader", choices=READERS,
                        help="谁来读题：assistant＝AI 助手读题（只要 MinerU）；或一家看图读题服务")
    config.add_argument("--checker", choices=CHECKERS, help="谁来复核：auto＝自动选另一家")
    config.add_argument("--minimax-plan", choices=PLANS, help="MiniMax 会员档位")
    start = sub.add_parser("start", parents=[common], help="打开题有据")
    start.add_argument("--timeout", type=int, default=90)
    finish = sub.add_parser("assistant-setup", parents=[common],
                            help="核实安装并给出收尾问题；技能和桌面图标只按明确选项修改")
    finish.add_argument("--skill-dir", help="已确认的绝对技能父目录；确认安装后才填写，不猜测助手目录")
    finish.add_argument("--replace-skill", action="store_true", help="明确同意替换不同内容的技能，先保留恢复备份")
    finish.add_argument("--desktop", choices=["show", "hide"], help="使用者选择后显示或隐藏题有据自己的桌面图标")
    sub.add_parser("skill", parents=[common], help="输出与当前安装版一致的 AI Agent 接管指南")
    agents = sub.add_parser("setup-agents", parents=[common], help="自动同步技能到本机主流 AI Agent 并配置用户 PATH")
    agents.add_argument("--home", help="指定用户主目录（默认当前用户目录）")
    agents.add_argument("--no-path", action="store_true", help="只同步与链接技能，不修改用户 PATH")
    agents.add_argument("--replace", action="store_true", help="替换已存在的自定义 tiyouju 技能（保留备份）")
    agents.add_argument("--quiet", action="store_true", help="静默执行，安装器调用时隐藏文本输出")
    sub.add_parser("papers", parents=[common], help="列出试卷")

    upload = sub.add_parser("upload", parents=[common], help="上传 PDF、Word，或几张照片合成一份")
    upload.add_argument("files", nargs="+")
    upload.add_argument("--book", action="store_true", help="是一本书/讲义")
    upload.add_argument("--parse-mode", choices=["auto", "manual", "native", "mineru"], default="auto",
                        help="默认auto本地优先；manual手工；native本地文字PDF；mineru明确云解析")
    upload.add_argument("--allow-cloud", action="store_true", help="同意auto在本地无可靠题卡时尝试已配置的MinerU")
    upload.add_argument("--cut-only", action="store_true", help="只切题不调用看图识读，保留原图题卡")
    upload.add_argument("--scan-only", action="store_true", help="仅保留原页与版面扫描块(blocks)，跳过自动切题与分组停在READY")
    upload.add_argument("--wait", action="store_true", help="上传后等它读完")
    upload.add_argument("--timeout", type=int, default=3600)

    pages_p = sub.add_parser("pages", parents=[common], help="导出原页高清图（可选叠加 0–1000 网格）")
    pages_p.add_argument("paper")
    pages_p.add_argument("--pages", help="页码或范围（从0起，如 0,1 或 0-3）")
    pages_p.add_argument("--grid", action="store_true", help="在返回副本上叠加 0–1000 归一化坐标网格")
    pages_p.add_argument("--out", help="图片存到哪个文件夹（默认临时文件夹）")

    blocks_p = sub.add_parser("blocks", parents=[common], help="查看资料的 MinerU / 本地 PDF 版面扫描块及 bbox")
    blocks_p.add_argument("paper")
    blocks_p.add_argument("--pages", help="可选过滤页码（从0起，如 0,1 或 0-5）")

    cut_p = sub.add_parser("cut", parents=[common], help="由助手自主批量切分题卡（支持直录文字跳过OCR或原图双轨）")
    cut_p.add_argument("paper")
    cut_p.add_argument("--file", help="UTF-8 JSON 路径（- 表示标准输入）：含 questions 列表及 replace_scope 等选项")
    cut_p.add_argument("--region", action="append", help="单题框选区域：页码:x0,y0,x1,y1，可传多次表示跨栏/跨页")
    cut_p.add_argument("--figure", action="append", help="配图框选：[槽位:]页码:x0,y0,x1,y1（槽位默认 stem）")
    cut_p.add_argument("--confirm-no-figure", action="store_true", help="确认本题无印刷配图")
    cut_p.add_argument("--number", type=int, help="题号（不传按题组自动递增）")
    cut_p.add_argument("--section", help="章节小节目录（如 第1章/例题）")
    cut_p.add_argument("--group-id", type=int, help="指定所属 QuestionGroup ID")
    cut_p.add_argument("--group-title", help="按标题匹配或自动创建章节题组")
    cut_p.add_argument("--source-kind", choices=["example", "exercise", "manual", "unknown"])
    cut_p.add_argument("--type", choices=list(TYPES), help="题型")
    cut_p.add_argument("--stem", help="助手直录题干（传入则跳过内置 OCR 队列，写为 text_source=assistant）")
    cut_p.add_argument("--stem-file", help="从 UTF-8 文本文件读题干")
    cut_p.add_argument("--option", action="append", help="选项：A=内容")
    cut_p.add_argument("--answer", help="原卷参考答案")
    cut_p.add_argument("--analysis", help="原卷参考解析")
    cut_p.add_argument("--origin", help="题源")
    cut_p.add_argument("--replace-scope", choices=["none", "page", "group", "paper", "question_ids"], default="none",
                       help="重切旧草稿范围（绝不覆盖已审核、已入库或已编辑题卡）")
    cut_p.add_argument("--replace-pages", help="replace-scope=page 时指定页码（如 0,1）")
    cut_p.add_argument("--replace-group-id", type=int, help="replace-scope=group 时指定题组 ID")
    cut_p.add_argument("--replace-question-ids", help="replace-scope=question-ids 时逗号分隔的题目 ID")
    cut_p.add_argument("--layout-revision", type=int, help="乐观锁布局版本号")

    layout_p = sub.add_parser("layout", parents=[common], help="调整已切题卡的原卷范围、题号、拆题、合题或撤销")
    layout_sub = layout_p.add_subparsers(dest="layout_action", required=True, metavar="布局动作")
    lr = layout_sub.add_parser("regions", parents=[common], help="调整一道题的原卷框选范围")
    lr.add_argument("paper")
    lr.add_argument("card")
    lr.add_argument("--region", action="append", required=True, help="新区域：页码:x0,y0,x1,y1")
    ln = layout_sub.add_parser("renumber", parents=[common], help="修改一道题的题号")
    ln.add_argument("paper")
    ln.add_argument("card")
    ln.add_argument("number", type=int)
    ls = layout_sub.add_parser("split", parents=[common], help="把一道题拆成多道")
    ls.add_argument("paper")
    ls.add_argument("card")
    ls.add_argument("--part", action="append", help="拆出的一道题区域（多片段用 ; 分隔）：p:x0,y0,x1,y1[;p:x0,y0,x1,y1]")
    ls.add_argument("--file", help="自定义 split targets JSON 文件")
    lm = layout_sub.add_parser("merge", parents=[common], help="把同题组多道题合并成一道")
    lm.add_argument("paper")
    lm.add_argument("cards", nargs="+")
    lm.add_argument("--number", type=int, help="合并后的题号（默认取首题）")
    lu = layout_sub.add_parser("undo", parents=[common], help="撤销最近一次（或指定）原卷切题/布局操作")
    lu.add_argument("paper")
    lu.add_argument("--operation-id", help="指定操作 UUID")

    rc_p = sub.add_parser("read-cuts", parents=[common], help="将已切好但还是原图（source_image）的题卡提交给内置看图模型识读")
    rc_p.add_argument("paper")
    rc_p.add_argument("cards", nargs="*", help="指定题号或 #id（不传则识读全部待识读原图题卡）")
    rc_p.add_argument("--wait", action="store_true", help="提交后等待识读完成")
    rc_p.add_argument("--timeout", type=int, default=1800)

    wait = sub.add_parser("wait", parents=[common], help="等一份试卷读完")
    wait.add_argument("paper")
    wait.add_argument("--timeout", type=int, default=3600)

    cards = sub.add_parser("cards", parents=[common], help="列出题卡和疑点")
    cards.add_argument("paper")
    cards.add_argument("--filter", choices=FILTERS, default="all",
                       help="todo 需逐题核对；green 识读完成未通过；approved；ai；human；all")
    cards.add_argument("--summary", action="store_true",
                       help="只输出状态与疑点类型汇总统计，不逐题列出详细题卡")

    show = sub.add_parser("show", parents=[common], help="看一道题：文字、疑点，并把原卷截图和配图存成图片")
    show.add_argument("paper")
    show.add_argument("card")
    show.add_argument("--out", help="图片存到哪个文件夹（默认临时文件夹）")
    show.add_argument("--no-images", action="store_true")

    fix = sub.add_parser("fix", parents=[common], help="改字（公式用 $...$ 的 LaTeX）")
    fix.add_argument("paper")
    fix.add_argument("card")
    fix.add_argument("--stem", help="新题干；写 - 从标准输入读")
    fix.add_argument("--stem-file", help="从 UTF-8 文本文件读题干（中文和公式多时推荐）")
    fix.add_argument("--option", action="append", help="改选项：A=内容，可写多次")
    fix.add_argument("--clear-option", action="append", help="删掉一个选项，例如 E")
    fix.add_argument("--type", choices=list(TYPES), help="只给 --type 时只改题型（题型没读出来的题要先选题型才能通过）")
    fix.add_argument("--origin", help="题源（题干前印的出处，如 2026××中学月考）；写空字符串清掉")
    fix.add_argument("--section", help="章节小节目录（如 第1章 / 1.1 或 第1节 公式法 · 例题）")
    fix.add_argument("--answer")
    fix.add_argument("--analysis")
    fix.add_argument("--force", action="store_true", help="使用者已人工通过的题也改（只在使用者要求时用）")

    figures = sub.add_parser("figures", parents=[common], help="处理配图")
    figures.add_argument("paper")
    figures.add_argument("card")
    group = figures.add_mutually_exclusive_group(required=True)
    group.add_argument("--use", action="append", help="用候选图：1 或 1:stem、2:A（编号见 show 的候选图编号截图）")
    group.add_argument("--keep", action="store_true", help="保留现在的配图")
    group.add_argument("--none", action="store_true", help="确认这道题没有配图")
    figures.add_argument("--force", action="store_true")

    approve = sub.add_parser("approve", parents=[common], help="对照原卷无误后打勾（记为 AI 通过）")
    approve.add_argument("paper")
    approve.add_argument("cards", nargs="*")
    approve.add_argument("--green", action="store_true", help="一起通过已逐题对照原卷核对的绿卡")

    unapprove = sub.add_parser("unapprove", parents=[common], help="撤销 AI 打的勾")
    unapprove.add_argument("paper")
    unapprove.add_argument("card")

    reread = sub.add_parser("reread", parents=[common], help="让读题模型重读一道题")
    reread.add_argument("paper")
    reread.add_argument("card")
    reread.add_argument("--force", action="store_true")

    publish = sub.add_parser("publish", parents=[common], help="把通过的题入库")
    publish.add_argument("paper")

    library = sub.add_parser("library", parents=[common], help="在正式题库里搜题")
    library.add_argument("keywords", nargs="*")
    library.add_argument("--paper")
    library.add_argument("--type", choices=list(TYPES))
    library.add_argument("--review", choices=["human", "ai"])
    library.add_argument("--answer", choices=["yes", "no"], help="yes 只要有答案的，no 只要原卷没答案的")
    library.add_argument("--tag", help="知识点标签（设置里打开“知识点标签”后才有）")
    library.add_argument("--limit", type=int, default=20)
    locate = sub.add_parser("locate", parents=[common], help="通过题目身份证短码（如 Q-xxxxxxxx）、UUID 或定位文本秒查一道题")
    locate.add_argument("ref", nargs="+", help="Q-xxxxxxxx、UUID 或整段复制的定位文本")

    feature_parser = sub.add_parser("features", parents=[common], help="查看或明确启用/关闭标签与参考答案（默认关闭）")
    feature_parser.add_argument("--enable", nargs="+", choices=ENRICH_FEATURES, help="按用户明确生成需求启用")
    feature_parser.add_argument("--disable", nargs="+", choices=ENRICH_FEATURES)
    enrich = sub.add_parser("enrich", help="当前助手自己补标签/参考答案，再写回本机题库；不需要豆包API")
    enrichment = enrich.add_subparsers(dest="enrich_command", required=True, metavar="步骤")
    tasks = enrichment.add_parser("tasks", parents=[common], help="只读查看待助手任务")
    tasks.add_argument("--ids", nargs="+", help="指定完整入库题 UUID（最多500个）")
    tasks.add_argument("--limit", type=int, default=50)
    auto = enrichment.add_parser("auto", parents=[common], help="查看或明确设置新题入库后自动排队（默认关闭）")
    auto.add_argument("--tags", choices=["on", "off"])
    auto.add_argument("--answer", choices=["on", "off"])
    prepare = enrichment.add_parser("prepare", parents=[common], help="领取题面、图片、目录、prompt和任务指纹")
    prepare.add_argument("publication_id", help="入库题 UUID")
    prepare.add_argument("--job-id", help="只领取此明确任务，不替代已取消/完成任务")
    prepare.add_argument("--kinds", nargs="+", choices=ENRICH_KINDS, default=list(ENRICH_KINDS))
    prepare.add_argument("--out", help="原卷截图与配图保存目录（默认新建临时目录）")
    prepare.add_argument("--no-images", action="store_true", help="只返回本机图片URL，不下载图片")
    submit = enrichment.add_parser("submit", parents=[common], help="提交一种已完成的任务，仅写AI附加内容")
    submit.add_argument("job_id", help="prepare 返回的任务 UUID")
    submit.add_argument("--fingerprint", required=True, help="原样使用该任务的 fingerprint")
    result_source = submit.add_mutually_exclusive_group(required=True)
    result_source.add_argument("--result-file", help='UTF-8 JSON：{"tags":["目录标签"]} 或 {"answer":"答案","analysis":"解析"}')
    result_source.add_argument("--tags", nargs="+", help="目录中的1–3个标签，不与答案混交")
    result_source.add_argument("--answer", help="参考答案；有公式时建议使用文件")
    result_source.add_argument("--answer-file", help="UTF-8参考答案文件")
    analysis_source = submit.add_mutually_exclusive_group()
    analysis_source.add_argument("--analysis", help="可选参考解析")
    analysis_source.add_argument("--analysis-file", help="UTF-8参考解析文件")

    sub.add_parser("mcp", parents=[common], help="作为 MCP 服务器运行（stdio），给支持 MCP 的 AI 用")

    claim = sub.add_parser("claim", parents=[common], help="领一批助手活；助手没了活会自己回到池子里")
    claim.add_argument("--limit", type=int, default=10, help="一次领几道（最多 50）")
    claim.add_argument("--kinds", nargs="+", choices=ENRICH_KINDS, default=list(ENRICH_KINDS))
    claim.add_argument("--ids", nargs="+", help="只领这些入库题 UUID（最多500个）")
    release = sub.add_parser("release", parents=[common], help="把没干完的助手活交回池子")
    release.add_argument("--job-id", action="append", help="要交回的任务 UUID（可多次）")
    propose = sub.add_parser("propose", parents=[common], help="交一份单题题面勘误提案；题面不动，等老师采纳（批量导标准答案请用 solutions import）")
    propose.add_argument("publication_id", help="入库题 UUID")
    propose.add_argument("--stem", help="新题干；公式用 $...$ 包住的 LaTeX")
    propose.add_argument("--stem-file", help="从文件读新题干")
    propose.add_argument("--option", action="append", help="要改的选项：A=内容")
    propose.add_argument("--type", choices=list(TYPES), help="要改的题型")
    propose.add_argument("--answer", help="要改的参考答案")
    propose.add_argument("--analysis", help="要改的参考解析")
    propose.add_argument("--origin", help="要改的题源")
    wb = sub.add_parser("workbench", parents=[common], help="读助手工作台真实总数/按助手汇总，或批量清理待核对提案")
    wb.add_argument("--limit", type=int, default=50, help="返回待核对提案条数上限（1–200，不影响真实总数统计）")
    wb.add_argument("--from-agent", help="按来源助手名称过滤（如 海鸥）")
    wb.add_argument("--scope", choices=["all", "solution_only", "face"], default="all", help="筛选类型：all / solution_only / face")
    wb.add_argument("--clear", action="store_true", help="批量拒绝并清空匹配的待核对提案（不改动题面或解析）")
    wb.add_argument("--reason", help="批量清理时记录的拒绝理由")
    sol_p = sub.add_parser("solutions", parents=[common], help="批量导入标准答案解析（直写版本化解析层，保护人工解析且支持整批撤销）")
    sol_sub = sol_p.add_subparsers(dest="solutions_command", metavar="动作")
    sol_imp = sol_sub.add_parser("import", parents=[common], help="从 UTF-8 JSON 文件批量写入标准答案与解析")
    sol_imp.add_argument("--file", required=True, help="UTF-8 JSON 文件路径（支持 {\"items\": [...]}、[...] 或 {题目ID: {answer, analysis}}）")
    sol_imp.add_argument("--overwrite", action="store_true", help="允许覆盖该题已有的保存解析（默认跳过已有解析）")
    sol_imp.add_argument("--dry-run", action="store_true", help="只做校验与预览统计，不写任何数据")
    sol_imp.add_argument("--note", help="本次导入批次说明（最多 200 字）")
    sol_batches = sol_sub.add_parser("batches", parents=[common], help="查看最近的批量解析导入批次")
    sol_batches.add_argument("--limit", type=int, default=20, help="显示最近几批（1–50）")
    sol_undo = sol_sub.add_parser("undo", parents=[common], help="整批撤销一次批量解析导入（保留后续人工编辑）")
    sol_undo.add_argument("--batch-id", help="指定要撤销的批次 UUID（省略则撤销最近一次）")
    sub.add_parser("whereami", parents=[common], help="老师此刻在哪、我刚提的有没有被否决")
    backup = sub.add_parser("backup", parents=[common], help="备份题库（支持在线打包并指定导出目录）")
    backup.add_argument("--dest", metavar="DIR", help="备份包保存到的文件夹（如外部移动硬盘或网盘路径）")
    backup.add_argument("--paper", dest="papers", action="append", metavar="PAPER_ID", help="只打包指定的试卷或书籍 UUID（可多次指定）")
    backup.add_argument("--folder", dest="folders", action="append", metavar="FOLDER", help="只打包指定的目录文件夹 UUID 或名称（可多次指定）")

    folders = sub.add_parser("folders", parents=[common], help="查看并整理软件内的文件夹、教材章节目录与个人标签分组（直接生效且可撤销）")
    folders_sub = folders.add_subparsers(dest="folders_command", metavar="动作")
    folders_sub.add_parser("list", parents=[common], help="查看全部文件夹树（我的文件夹、试卷、教材章节、智能夹、个人分组）")
    folders_sub.add_parser("tree", parents=[common], help="同 list")
    f_create = folders_sub.add_parser("create", parents=[common], help="新建文件夹或子文件夹")
    f_create.add_argument("name", help="文件夹名称")
    f_create.add_argument("--parent", help="父级节点 ID（省略则为根目录；也可传 group:papers / group:books / paper:<id> / book:<id>）")
    f_create.add_argument("--questions", nargs="+", help="顺便放入的入库题目 UUID 或 Q-xxxxxxxx 短码")
    f_rename = folders_sub.add_parser("rename", parents=[common], help="重命名我的文件夹")
    f_rename.add_argument("folder", help="文件夹 ID 或名称")
    f_rename.add_argument("name", help="新名称")
    f_move = folders_sub.add_parser("move", parents=[common], help="移动我的文件夹层级")
    f_move.add_argument("folder", help="文件夹 ID 或名称")
    f_move.add_argument("--parent", help="新父级 ID；省略则移到根目录")
    f_delete = folders_sub.add_parser("delete", parents=[common], help="删除我的文件夹（含子文件夹，不删正式题，可撤销）")
    f_delete.add_argument("folder", help="文件夹 ID 或名称")
    f_add = folders_sub.add_parser("add", parents=[common], help="将入库题目批量放入文件夹")
    f_add.add_argument("target", help="目标文件夹 ID/名称，或 paper:<id> / book:<id>")
    f_add.add_argument("questions", nargs="+", help="入库题目 UUID 或 Q-xxxxxxxx 短码")
    f_remove = folders_sub.add_parser("remove", parents=[common], help="从文件夹批量移出题目")
    f_remove.add_argument("target", help="目标文件夹 ID/名称，或 paper:<id> / book:<id>")
    f_remove.add_argument("questions", nargs="+", help="入库题目 UUID 或 Q-xxxxxxxx 短码")
    f_sort = folders_sub.add_parser("sort", parents=[common], help="设置文件夹排序方式")
    f_sort.add_argument("target", help="节点 ID（如 global、section:folders、group:books、book:<id>、userfolder:<id>）")
    f_sort.add_argument("mode", choices=list(SORT_MODES), help="time_desc / time_asc / name / count_desc / default")
    f_sec = folders_sub.add_parser("set-section", parents=[common], help="批量整理已入库题目的教材章节/小节目录（可撤销）")
    f_sec.add_argument("--section", help="统一的章节小节名")
    f_sec.add_argument("--map-file", help="UTF-8 JSON 映射文件：{题目ID: 章节名} 或 [{publication_id, section}]")
    f_sec.add_argument("questions", nargs="*", help="入库题目 UUID 或 Q-xxxxxxxx 短码")
    f_paper = folders_sub.add_parser("paper", parents=[common], help="重命名试卷/教材或切换「试卷 ↔ 教材」类型")
    f_paper.add_argument("paper", help="试卷/教材编号、latest 或名称片段")
    f_paper.add_argument("--name", help="新的显示名称")
    f_paper.add_argument("--type", choices=["exam", "book"], help="exam 试卷 / book 教材")
    f_tg = folders_sub.add_parser("tag-group", parents=[common], help="整理标签管理里的个人分组")
    tg_sub = f_tg.add_subparsers(dest="tag_group_command", metavar="分组动作")
    tg_sub.add_parser("list", parents=[common], help="列出个人分组及所含标签")
    tg_create = tg_sub.add_parser("create", parents=[common], help="新建个人分组")
    tg_create.add_argument("name", help="分组名称")
    tg_create.add_argument("--color", help="#RRGGBB 颜色")
    tg_create.add_argument("--tags", nargs="+", help="初始加入的标签 UUID 或精确标签名")
    tg_rename = tg_sub.add_parser("rename", parents=[common], help="重命名个人分组")
    tg_rename.add_argument("group", help="分组 ID 或名称")
    tg_rename.add_argument("name", help="新分组名称")
    tg_del = tg_sub.add_parser("delete", parents=[common], help="删除个人分组（可撤销）")
    tg_del.add_argument("group", help="分组 ID 或名称")
    tg_add = tg_sub.add_parser("add-tags", parents=[common], help="往个人分组添加标签")
    tg_add.add_argument("group", help="分组 ID 或名称")
    tg_add.add_argument("tags", nargs="+", help="标签 UUID 或精确标签名")
    tg_rem = tg_sub.add_parser("remove-tags", parents=[common], help="从个人分组移出标签")
    tg_rem.add_argument("group", help="分组 ID 或名称")
    tg_rem.add_argument("tags", nargs="+", help="标签 UUID 或精确标签名")
    tg_set = tg_sub.add_parser("set-tags", parents=[common], help="覆盖设置个人分组所含标签")
    tg_set.add_argument("group", help="分组 ID 或名称")
    tg_set.add_argument("tags", nargs="*", help="标签 UUID 或精确标签名")
    tg_undo = tg_sub.add_parser("undo", parents=[common], help="撤销个人分组操作")
    tg_undo.add_argument("--operation-id", help="指定操作 ID")
    f_undo = folders_sub.add_parser("undo", parents=[common], help="撤销最近一次文件夹或章节整理操作")
    f_undo.add_argument("--operation-id", help="指定操作 ID")
    return parser


COMMANDS = {
    "status": cmd_status, "config": cmd_config, "start": cmd_start, "papers": cmd_papers, "upload": cmd_upload, "wait": cmd_wait,
    "pages": cmd_pages, "blocks": cmd_blocks, "cut": cmd_cut, "layout": cmd_layout, "read-cuts": cmd_read_cuts,
    "cards": cmd_cards, "show": cmd_show, "fix": cmd_fix, "figures": cmd_figures, "approve": cmd_approve,
    "unapprove": cmd_unapprove, "reread": cmd_reread, "publish": cmd_publish, "library": cmd_library,
    "locate": cmd_locate,
    "assistant-setup": cmd_assistant_setup,
    "setup-agents": cmd_setup_agents,
    "skill": cmd_skill,
    "features": cmd_features, "enrich": cmd_enrich,
    "claim": cmd_claim, "release": cmd_release, "propose": cmd_propose,
    "workbench": cmd_workbench, "whereami": cmd_whereami, "solutions": cmd_solutions,
    "backup": cmd_backup,
    "folders": cmd_folders,
}


def configure_streams() -> None:
    # UTF-8 with bare newlines (MCP is newline-delimited JSON).  --json output
    # is pure ASCII anyway, so a console on another code page cannot garble it.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    configure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    # This command inspects the installed files even when the app is stopped.
    # Do not instantiate a client or look up a runtime service for it.
    offline_commands = {"assistant-setup", "setup-agents", "skill"}
    client = None if args.command in offline_commands else Client(
        agent=" ".join(str(args.agent).split())[:40] or DEFAULT_AGENT)
    if args.command == "mcp":
        return run_mcp(client)
    try:
        result = COMMANDS[args.command](client, args)
    except CliError as error:
        if args.json:
            print(json.dumps({"error": str(error), "code": error.code}))
        else:
            print(f"出错了：{error}", file=sys.stderr)
        return error.code
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        shower = setup.format_report if args.command == "assistant-setup" else SHOWERS.get(args.command)
        output = shower(result) if shower else json.dumps(result, ensure_ascii=False, indent=2)
        if output:
            print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
