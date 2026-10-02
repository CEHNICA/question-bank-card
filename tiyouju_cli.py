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
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
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
    "waiting": "等待识读", "reading": "识读中", "green": "识读一致", "yellow": "需核对", "red": "识读失败",
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
    }


def first_line(text: str, limit: int = 60) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[:limit - 1] + "…"


def candidate_key(item: dict) -> str | None:
    """The page/bbox identity the app uses for a figure candidate (figure_policy.candidate_key)."""
    bbox = item.get("bbox") if isinstance(item, dict) else None
    if not isinstance(item.get("page_idx") if isinstance(item, dict) else None, int) \
            or not isinstance(bbox, list) or len(bbox) != 4:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
           for value in bbox):
        return None
    x0, y0, x1, y1 = (round(float(value), 1) for value in bbox)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(1000, x1), min(1000, y1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None

    def shown(number: float) -> str:
        return f"{number:.1f}".rstrip("0").rstrip(".")

    return f"{item['page_idx']}:" + ",".join(shown(value) for value in (x0, y0, x1, y1))


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
        missing.append({"what": "mineru", "label": "MinerU Token（免费，每天 1000 页；14 天过期一次）",
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
        f"可以上传新卷子：{'是' if result['upload_enabled'] else '否——请使用者在软件“设置 → 常用 → 填写或更换密钥”里填好上面缺的密钥'}",
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


def paper_summary(item: dict) -> dict:
    counts = item.get("counts") or {}
    return {
        "id": item["id"], "name": item.get("name"), "status": item.get("status"),
        "status_label": item.get("status_label"), "material_type": item.get("material_type"),
        "demo": bool(item.get("demo")), "created_at": item.get("created_at"),
        "total": counts.get("total", item.get("total", 0)), "approved": counts.get("approved", 0),
        "published": counts.get("published", 0), "yellow": counts.get("yellow", 0), "red": counts.get("red", 0),
        "error": item.get("error") or "",
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
    result = client.upload(files, {"material_type": "book" if args.book else "exam"})
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
    return (f"{short(paper['id'])} 读完了：{result['cards']} 道题，需逐题核对 {result['todo']} 道，"
            f"识读一致 {result['green']} 道。下一步：`tiyouju cards {short(paper['id'])} --filter todo`。")


def cmd_cards(client: Client, args) -> dict:
    paper = find_paper(client, args.paper)
    detail = paper_detail(client, paper)
    cards = [card_summary(question) for question in detail.get("questions", [])
             if matches_filter(question, args.filter)]
    return {"paper": paper_summary(detail["paper"]), "filter": args.filter, "cards": cards}


def approval_label(card: dict) -> str:
    if card["approved_by"] == "human":
        return "人工通过"
    if card["approved_by"] == "ai":
        return f"{card['approval_agent'] or 'AI'} 通过"
    return ""


def show_cards(result: dict) -> str:
    paper = result["paper"]
    lines = [f"{short(paper['id'])} {paper['name']}：{len(result['cards'])} 道（筛选：{result['filter']}）"]
    for card in result["cards"]:
        state = approval_label(card) or STATES.get(card["state"], card["state"])
        group = f"[{card['group']}] " if card["group"] else ""
        lines.append(f"  第 {card['number']} 题 {group}#{card['id']}  {state}  {first_line(card['stem'], 46)}")
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
    return result


def cmd_show(client: Client, args) -> dict:
    return card_detail(client, args.paper, args.card, args.out, images=not args.no_images)


def show_card(result: dict) -> str:
    lines = [f"第 {result['number']} 题 #{result['id']}  {TYPES.get(result['type'], result['type'])}  "
             f"{approval_label(result) or STATES.get(result['state'], result['state'])}"]
    if result.get("origin"):
        lines.append(f"题源：{result['origin']}（题干前印的出处，单独存放）")
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
    text_given = any(value is not None for value in (stem, args.answer, args.analysis, origin)) \
        or bool(args.option or args.clear_option)
    if args.type and not text_given:
        # Only the type: keep the text, its reading record and the other reminders.
        if args.type == "unknown":
            raise CliError("请选一个题型：" + "、".join(DECIDED_TYPES))
        saved = client.post(f"/api/questions/{question['id']}/type",
                            {"question_type": args.type, "by": "ai", "agent": client.agent})["question"]
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
        raise CliError("请写要通过的题号（可以写好几个），或用 --green 一起通过识读一致的绿卡。")
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
    saved = client.post(f"/api/questions/{question['id']}/reread", {"by": "ai", "agent": client.agent})["question"]
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
        "id": item["id"], "source": item.get("source_filename"), "number": item.get("number"),
        "type": item.get("question_type"), "version": item.get("version"),
        "review": (item.get("review") or {}).get("source", "human"),
        "origin": item.get("origin") or "",
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
        lines.append(f"  {item['source']} 第 {item['number']} 题{review}{origin}：{first_line(item['stem'], 50)}")
        if item.get("tags"):
            lines.append(f"      知识点：{'、'.join(item['tags'])}")
    return "\n".join(lines)


SHOWERS = {
    "status": show_status, "config": show_config, "papers": show_papers, "upload": show_upload, "wait": show_wait, "cards": show_cards,
    "show": show_card, "fix": show_fix, "figures": show_figures, "approve": show_approve, "publish": show_publish,
    "unapprove": show_unapprove, "reread": show_reread,
    "library": show_library,
}


# ---------------------------------------------------------------- MCP（给支持 MCP 的 AI 用）

MCP_INSTRUCTIONS = (
    "题有据：把扫描/拍照的数学试卷变成核对过的题库。处理一份试卷：upload_paper → wait_paper → "
    "list_cards(filter=todo) → 对每道 show_card（会给你原卷截图），逐字对照；不对就 fix_card / set_figures，"
    "对了就 approve_cards；绿卡也要抽查后再 approve_cards(green=true)；最后 publish_paper。"
    "你打的勾记成“AI 通过”，使用者会再核对。不要碰密钥；缺密钥请使用者在软件“设置 → 常用”里填写"
    "（MinerU 和魔搭都免费，status 会给出申请网址）。题卡写着“MinerU 初稿”时（AI 助手读题），"
    "题面还没人看图核对过：每一道都要对照截图逐字核对。"
    "题型没读出来（type_blocked）的题不能通过：先用 fix_card 只传 type 选好题型。"
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
    }, ["paths"])),
    ("wait_paper", "等一份试卷读完（最多等 timeout 秒，没读完可以再调一次）。", _schema({
        "paper": PAPER, "timeout": {"type": "integer", "minimum": 5, "maximum": 600},
    }, ["paper"])),
    ("list_cards", "列出一份试卷的题卡和疑点。filter：todo 需逐题核对、green 识读一致未通过、approved、ai、human、all。",
     _schema({"paper": PAPER, "filter": {"type": "string", "enum": list(FILTERS)}}, ["paper"])),
    ("show_card", "看一道题：读出的文字、疑点、两次识读，以及原卷截图（有候选图时另给一张标了 图1、图2… 的截图）和配图。",
     _schema({"paper": PAPER, "card": CARD}, ["paper", "card"])),
    ("fix_card", "改一道题的文字。只传要改的字段；公式用 $...$ 包住的 LaTeX；表格用 Markdown。", _schema({
        "paper": PAPER, "card": CARD, "stem": {"type": "string"},
        "options": {"type": "object", "description": "要改的选项，例如 {\"B\": \"-3\"}；值为空字符串表示删掉该选项",
                    "additionalProperties": {"type": "string"}},
        "type": {"type": "string", "enum": list(TYPES), "description": "只传 type 时只改题型"},
        "answer": {"type": "string"}, "analysis": {"type": "string"},
        "origin": {"type": "string", "description": "题源：题干前印的出处，如“2026××中学月考”"},
    }, ["paper", "card"])),
    ("set_figures", "处理一道题的配图：use 选候选图（编号见 show_card，如 [\"1\", \"2:A\"]）、keep 保留现有配图、none 确认无图。",
     _schema({"paper": PAPER, "card": CARD, "use": {"type": "array", "items": {"type": "string"}},
              "keep": {"type": "boolean"}, "none": {"type": "boolean"}}, ["paper", "card"])),
    ("approve_cards", "对照原卷无误后打勾（记为 AI 通过）。cards 写题号；green=true 一起通过识读一致的绿卡（先抽查）。",
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
        return cmd_upload(client, _ns(files=a.get("paths") or [], book=bool(a.get("book")), wait=False, timeout=0)), []
    if name == "wait_paper":
        return wait_for(client, find_paper(client, a.get("paper", "")), min(600, int(a.get("timeout") or 300)), quiet=True), []
    if name == "list_cards":
        return cmd_cards(client, _ns(paper=a.get("paper", ""), filter=a.get("filter") or "all")), []
    if name == "show_card":
        result = card_detail(client, a.get("paper", ""), a.get("card", ""))
        images = result.get("images") or {}
        paths = [images.get("candidates") or images.get("crop"), *(images.get("figures") or [])]
        return result, [path for path in paths if path]
    if name == "fix_card":
        options = a.get("options") or {}
        return cmd_fix(client, _ns(
            paper=a.get("paper", ""), card=a.get("card", ""), stem=a.get("stem"), stem_file=None,
            option=[f"{key}={value}" for key, value in options.items() if str(value).strip()],
            clear_option=[key for key, value in options.items() if not str(value).strip()],
            type=a.get("type"), answer=a.get("answer"), analysis=a.get("analysis"), origin=a.get("origin"))), []
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
    sub.add_parser("papers", parents=[common], help="列出试卷")

    upload = sub.add_parser("upload", parents=[common], help="上传 PDF、Word，或几张照片合成一份")
    upload.add_argument("files", nargs="+")
    upload.add_argument("--book", action="store_true", help="是一本书/讲义")
    upload.add_argument("--wait", action="store_true", help="上传后等它读完")
    upload.add_argument("--timeout", type=int, default=3600)

    wait = sub.add_parser("wait", parents=[common], help="等一份试卷读完")
    wait.add_argument("paper")
    wait.add_argument("--timeout", type=int, default=3600)

    cards = sub.add_parser("cards", parents=[common], help="列出题卡和疑点")
    cards.add_argument("paper")
    cards.add_argument("--filter", choices=FILTERS, default="all",
                       help="todo 需逐题核对；green 识读一致未通过；approved；ai；human；all")

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
    approve.add_argument("--green", action="store_true", help="一起通过识读一致的绿卡（先抽查）")

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

    sub.add_parser("mcp", parents=[common], help="作为 MCP 服务器运行（stdio），给支持 MCP 的 AI 用")
    return parser


COMMANDS = {
    "status": cmd_status, "config": cmd_config, "start": cmd_start, "papers": cmd_papers, "upload": cmd_upload, "wait": cmd_wait,
    "cards": cmd_cards, "show": cmd_show, "fix": cmd_fix, "figures": cmd_figures, "approve": cmd_approve,
    "unapprove": cmd_unapprove, "reread": cmd_reread, "publish": cmd_publish, "library": cmd_library,
    "assistant-setup": cmd_assistant_setup,
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
    client = None if args.command == "assistant-setup" else Client(
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
        print(shower(result) if shower else json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
