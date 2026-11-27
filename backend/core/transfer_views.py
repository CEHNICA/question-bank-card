"""备份与合并的入口：设置 → 数据备份与迁移。

给老师看到的每一句都是人话：做了什么、动了几样、接下来点哪里。
"""

from __future__ import annotations

import base64
import ipaddress
import json
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import credential_settings, library_transfer
from .library_transfer import TransferError

MAX_UPLOAD_BYTES = library_transfer.MAX_BUNDLE_BYTES
DEFAULT_COMMUNITY_SITE = "https://community.tiyouju.com"
_STAGED_LOCK = threading.Lock()
_STAGED_PACKS: dict[str, dict[str, object]] = {}
_STAGED_TTL_SECONDS = 1800.0
_MAX_STAGED_ITEMS = 8


def _guard(request, *, json_body=True):
    from .views import _guard as guard

    return guard(request, json_body=json_body)


def _error(message: str, status: int = 400) -> JsonResponse:
    response = JsonResponse({"error": message}, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _body(request, limit: int = 100_000) -> dict | None:
    from .views import _body as body

    return body(request, limit=limit)


@csrf_exempt
def papers_view(request):
    """列出本机可单独打包分享的卷子/书与目录文件夹。"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("题库资料列表只能在本机题库中查看", 403)
    response = JsonResponse(
        {"papers": library_transfer.list_exportable_papers(), "folders": library_transfer.list_exportable_folders()}
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def export_view(request):
    """把整库或选定资料打包成一个 zip，放到老师选的文件夹里。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request, limit=100_000)
    if payload is None:
        return _error("请求格式不正确。")
    directory = str(payload.get("directory") or "").strip()
    if not directory:
        return _error("先选一个文件夹，备份会放在那里。")
    raw_paper_ids = payload.get("paper_ids")
    raw_tag_ids = payload.get("tag_ids")
    if raw_paper_ids is not None and raw_tag_ids is not None:
        return _error("一次只能按卷子或按目录文件夹其中一种方式导出。")
    paper_ids: list[str] | None = None
    tag_ids: list[str] | None = None
    if raw_paper_ids is not None:
        if not isinstance(raw_paper_ids, list) or not all(isinstance(item, str) for item in raw_paper_ids):
            return _error("选中的卷子列表格式不正确。")
        paper_ids = raw_paper_ids
    if raw_tag_ids is not None:
        if not isinstance(raw_tag_ids, list) or not all(isinstance(item, str) for item in raw_tag_ids):
            return _error("选中的目录文件夹格式不正确。")
        tag_ids = raw_tag_ids
    try:
        if tag_ids is not None:
            result = library_transfer.export_bundle(Path(directory), tag_ids=tag_ids)
        elif paper_ids is not None:
            result = library_transfer.export_bundle(Path(directory), paper_ids=paper_ids)
        else:
            result = library_transfer.export_bundle(Path(directory))
    except TransferError as error:
        return _error(str(error))
    except OSError:
        return _error("备份没写成，检查一下那个文件夹能不能写入。", status=500)
    counts = result.get("counts") or {}
    response = JsonResponse(
        {
            "file": result["file"],
            "name": result["name"],
            "size": result["size"],
            "papers": counts.get("papers", 0),
            "questions": counts.get("questions", 0),
            "tags": counts.get("tags", 0),
            "publications": counts.get("publications", 0),
            "paper_names": result.get("paper_names") or [],
            "folder_names": result.get("folder_names") or [],
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def community_export_view(request):
    """把选定的卷子或目录文件夹打成社区题包；默认不带原卷，不能整库。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request, limit=100_000)
    if payload is None:
        return _error("请求格式不正确。")
    directory = str(payload.get("directory") or "").strip()
    if not directory:
        return _error("先选一个文件夹，社区题包会放在那里。")
    raw_paper_ids = payload.get("paper_ids")
    raw_tag_ids = payload.get("tag_ids")
    if (raw_paper_ids is None) == (raw_tag_ids is None):
        return _error("社区题包要先选卷子或目录文件夹其中一种，不能把整个题库发出去。")
    selected = raw_paper_ids if raw_paper_ids is not None else raw_tag_ids
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        return _error("选中的卷子或目录文件夹格式不正确。")
    include_originals = payload.get("include_originals", False)
    if not isinstance(include_originals, bool):
        return _error("是否附带原卷的选项格式不正确。")
    try:
        result = library_transfer.export_community_pack(
            Path(directory),
            paper_ids=selected if raw_paper_ids is not None else None,
            tag_ids=selected if raw_tag_ids is not None else None,
            include_originals=include_originals,
        )
    except TransferError as error:
        return _error(str(error))
    except OSError:
        return _error("社区题包没写成，检查一下那个文件夹能不能写入。", status=500)
    counts = result.get("counts") or {}
    community = result.get("community") or {}
    response = JsonResponse(
        {
            "file": result["file"],
            "name": result["name"],
            "size": result["size"],
            "papers": counts.get("papers", 0),
            "questions": community.get("questions", counts.get("publications", 0)),
            "include_originals": bool(community.get("include_originals")),
            "cover": community.get("cover"),
            "paper_names": result.get("paper_names") or [],
            "folder_names": result.get("folder_names") or [],
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def choose_directory_view(request):
    """只在这台电脑上打开系统文件夹选择窗口；取消什么也不改。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    from .export_preferences import desktop_capable

    if not desktop_capable():
        return _error("只有 Windows 桌面版能选文件夹；网页版请用浏览器的下载。", status=409)
    from .native_folder_picker import FolderPickerError, choose_directory

    try:
        chosen = choose_directory("", "选择备份要存放的文件夹")
    except FolderPickerError as error:
        return _error(str(error), status=409)
    if chosen is None:
        return JsonResponse({"selected": False, "cancelled": True})
    return JsonResponse({"selected": True, "directory": str(chosen)})


def _uploaded_bundle(request) -> Path:
    uploads = request.FILES.getlist("file")
    if not uploads:
        raise TransferError("先选一个备份包。")
    if len(uploads) > 1:
        raise TransferError("一次只并一个备份包。")
    upload = uploads[0]
    if upload.size > MAX_UPLOAD_BYTES:
        raise TransferError("这个备份包太大了，请确认选的是题有据导出的备份包。")
    work = Path(tempfile.mkdtemp(prefix="qb-bundle-"))
    target = work / (Path(upload.name).name or "bundle.zip")
    with open(target, "wb") as writer:
        for chunk in upload.chunks():
            writer.write(chunk)
    return target


def _inspect_response(result: dict) -> JsonResponse:
    """整理备份预览结果，补充包含的资料名与目录文件夹清单。"""
    manifest = result["manifest"]
    counts = manifest.get("counts") or {}
    paper_items = [
        {
            "title": str(item.get("title") or item.get("filename") or "").strip(),
            "filename": str(item.get("filename") or "").strip(),
            "questions": int(item.get("questions") or 0),
        }
        for item in (manifest.get("papers") or [])
        if isinstance(item, dict) and (item.get("title") or item.get("filename"))
    ]
    folder_items = [
        {"name": str(item.get("name") or "").strip(), "kind": str(item.get("kind") or "").strip()}
        for item in (manifest.get("folders") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    response = JsonResponse(
        {
            "created_at": manifest.get("created_at"),
            "machine": manifest.get("machine"),
            "app_version": manifest.get("app_version"),
            "papers": counts.get("papers", 0),
            "questions": counts.get("questions", 0),
            "tags": counts.get("tags", 0),
            "publications": counts.get("publications", 0),
            "paper_items": paper_items,
            "folder_items": folder_items,
            "report": result["report"],
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def inspect_view(request):
    """合并前先看一眼：这份备份会带来什么。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    try:
        path = _uploaded_bundle(request)
    except TransferError as error:
        return _error(str(error))
    try:
        result = library_transfer.plan_merge(path)
    except TransferError as error:
        return _error(str(error))
    except (OSError, ValueError):
        return _error("这个备份包读不出来，没法看。请确认它是题有据导出的备份包。", status=400)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
    return _inspect_response(result)


@csrf_exempt
def import_view(request):
    """把一份备份并进本机：只增不减，本机已有的题一道不删。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    try:
        path = _uploaded_bundle(request)
    except TransferError as error:
        return _error(str(error))
    try:
        result = library_transfer.merge_bundle(path)
    except TransferError as error:
        return _error(str(error))
    except (OSError, ValueError):
        return _error("合并没能完成，本机题库还是原样。可以再试一次。", status=500)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
    response = JsonResponse({"report": result["report"], "record": result["record"]})
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def records_view(request):
    """合并过哪些次、能不能撤销。"""
    if request.method == "GET":
        if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
            return _error("合并记录只能在本机题库中查看", 403)
        response = JsonResponse({"records": library_transfer.list_records()})
        response["Cache-Control"] = "no-store"
        return response
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    try:
        removed = library_transfer.undo_last_merge()
    except TransferError as error:
        return _error(str(error))
    response = JsonResponse({"removed": removed})
    response["Cache-Control"] = "no-store"
    return response


def _cleanup_expired_staged_locked(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    expired = [k for k, v in _STAGED_PACKS.items() if (current - float(v.get("created_at") or 0)) > _STAGED_TTL_SECONDS]
    for key in expired:
        entry = _STAGED_PACKS.pop(key, None)
        if entry and isinstance(entry.get("dir"), Path):
            shutil.rmtree(entry["dir"], ignore_errors=True)
    while len(_STAGED_PACKS) >= _MAX_STAGED_ITEMS:
        oldest_key = min(_STAGED_PACKS, key=lambda k: float(_STAGED_PACKS[k].get("created_at") or 0))
        entry = _STAGED_PACKS.pop(oldest_key, None)
        if entry and isinstance(entry.get("dir"), Path):
            shutil.rmtree(entry["dir"], ignore_errors=True)


def _validate_pack_download_url(raw_url: str) -> str:
    url = str(raw_url or "").strip()
    if not url or len(url) > 2048:
        raise TransferError("下载链接无效。")
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if not host or parsed.username or parsed.password:
        raise TransferError("下载链接格式不受支持。")
    scheme = parsed.scheme.lower()
    is_local_dev = host in {"127.0.0.1", "localhost"}
    if scheme == "http" and is_local_dev:
        pass
    elif scheme == "https":
        try:
            addr = ipaddress.ip_address(host)
        except ValueError:
            addr = None
        if addr is not None and (
            addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved
        ):
            raise TransferError("不允许从内网或保留地址下载题包。")
    else:
        raise TransferError("只允许从安全的 HTTPS 社区地址下载题包。")
    if "/api/dl/" not in (parsed.path or ""):
        raise TransferError("只支持题有据社区签发的题包下载链接。")
    return url


def _normalize_site_url(raw_site: str | None) -> str:
    candidate = str(raw_site or "").strip().rstrip("/") or DEFAULT_COMMUNITY_SITE
    parsed = urllib.parse.urlparse(candidate)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return DEFAULT_COMMUNITY_SITE
    if parsed.scheme.lower() == "https" or (parsed.scheme.lower() == "http" and host in {"127.0.0.1", "localhost"}):
        return f"{parsed.scheme.lower()}://{parsed.netloc}"
    return DEFAULT_COMMUNITY_SITE


@csrf_exempt
def community_session_view(request):
    """GET /api/community/session: 返回本机是否已授权连接社区账号（绝不返回 token）。"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("只能在本机查看社区授权状态", 403)
    store = credential_settings._load_store()
    info = store.community_session_public()
    response = JsonResponse(info)
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def community_disconnect_view(request):
    """POST /api/community/disconnect: 清除本机保存的社区桌面授权令牌。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    store = credential_settings._load_store()
    store.clear_community_session()
    response = JsonResponse({"ok": True})
    response["Cache-Control"] = "no-store"
    return response


def _read_community_zip_metadata(zip_path: Path) -> tuple[dict, dict, str]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        manifest = json.loads(zf.read(library_transfer.MANIFEST_NAME).decode("utf-8"))
        preview = json.loads(zf.read(library_transfer.COMMUNITY_PREVIEW_NAME).decode("utf-8"))
        cover_b64 = ""
        for name in zf.namelist():
            if name.startswith("community-cover.") and name.rsplit(".", 1)[-1].lower() in {
                "png",
                "jpg",
                "jpeg",
                "webp",
            }:
                raw_cover = zf.read(name)
                if len(raw_cover) <= library_transfer.COMMUNITY_COVER_MAX_BYTES:
                    cover_b64 = base64.b64encode(raw_cover).decode("ascii")
                break
    return manifest, preview, cover_b64


def _community_http_json(
    url: str, *, token: str, method: str = "POST", payload: dict | None = None
) -> tuple[int, dict]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {token}", "X-TYJ": "1", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
            return int(getattr(resp, "status", 200)), body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {"error": raw or f"HTTP {err.code}"}
        return int(err.code), body if isinstance(body, dict) else {"error": f"HTTP {err.code}"}


def _community_http_put_file(url: str, *, token: str, file_path: Path) -> tuple[int, dict]:
    raw_bytes = file_path.read_bytes()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-TYJ": "1",
        "Content-Type": "application/zip",
        "Content-Length": str(len(raw_bytes)),
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, data=raw_bytes, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
            return int(getattr(resp, "status", 200)), body if isinstance(body, dict) else {}
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {"error": raw or f"HTTP {err.code}"}
        return int(err.code), body if isinstance(body, dict) else {"error": f"HTTP {err.code}"}


@csrf_exempt
def community_upload_view(request):
    """POST /api/backup/community-upload: 支持选定试卷/教材、普通文件夹或单/多道题目，在临时目录压缩打包并上传到社区。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request, limit=200_000)
    if payload is None:
        return _error("请求格式不正确。")

    store = credential_settings._load_store()
    session = store.load_community_session()
    if not session or not session.get("token"):
        resp = JsonResponse({"error": "请先在浏览器登录并授权连接题有据社区账号。", "needs_auth": True}, status=401)
        resp["Cache-Control"] = "no-store"
        return resp

    raw_paper_ids = payload.get("paper_ids")
    raw_publication_ids = payload.get("publication_ids")
    paper_ids = (
        [x.strip() for x in raw_paper_ids]
        if isinstance(raw_paper_ids, list)
        and raw_paper_ids
        and all(isinstance(x, str) and x.strip() for x in raw_paper_ids)
        else None
    )
    publication_ids = (
        [x.strip() for x in raw_publication_ids]
        if isinstance(raw_publication_ids, list)
        and raw_publication_ids
        and all(isinstance(x, str) and x.strip() for x in raw_publication_ids)
        else None
    )
    if bool(paper_ids) == bool(publication_ids):
        return _error("请指定要上传的试卷/教材，或选定要上传的题目与文件夹。")

    include_originals = bool(payload.get("include_originals", False)) if paper_ids else False
    include_onenote = bool(payload.get("include_onenote", False))
    rights_confirmed = payload.get("rights_confirmed") is True
    originals_confirmed = payload.get("originals_confirmed") is True
    if not rights_confirmed:
        return _error("请先确认你有权分发这份题包。")
    if include_originals and not originals_confirmed:
        return _error("附带原卷文件时，请同时确认原卷版权归属。")

    title = str(payload.get("title") or "").strip()
    if len(title) < 2:
        return _error("请填写题包标题（至少 2 个字）。")

    site_url = _normalize_site_url(str(payload.get("site_url") or session.get("site") or ""))
    token = session["token"]

    with tempfile.TemporaryDirectory(prefix="tyj-comm-up-") as work:
        try:
            exported = library_transfer.export_community_pack(
                Path(work),
                paper_ids=paper_ids,
                publication_ids=publication_ids,
                include_originals=include_originals,
                include_onenote=include_onenote,
                custom_title=title,
            )
            zip_path = Path(exported["file"])
            zip_size = int(exported.get("size") or zip_path.stat().st_size)
            manifest, preview, cover_b64 = _read_community_zip_metadata(zip_path)
        except TransferError as error:
            return _error(str(error))
        except (OSError, ValueError, zipfile.BadZipFile):
            return _error("题包打包失败，请稍后重试。", status=500)

        init_body = {
            "title": title,
            "stage": str(payload.get("stage") or "高中").strip(),
            "subject": str(payload.get("subject") or "数学").strip(),
            "year": payload.get("year") or "",
            "tags": str(payload.get("tags") or "").strip(),
            "price_yuan": str(payload.get("price_yuan") if payload.get("price_yuan") is not None else "0"),
            "originality": "original" if payload.get("originality") == "original" else "compiled",
            "description": str(payload.get("description") or "").strip(),
            "rights_confirmed": True,
            "originals_confirmed": originals_confirmed,
            "file_size": zip_size,
            "manifest": manifest,
            "preview": preview,
            "cover_base64": cover_b64 or None,
        }
        try:
            init_status, init_data = _community_http_json(
                f"{site_url}/api/packs/init", token=token, method="POST", payload=init_body
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _error(f"连不上社区服务器：{exc}", status=502)

        if init_status == 401:
            store.clear_community_session()
            resp = JsonResponse({"error": "社区授权已过期或被吊销，请重新授权连接。", "needs_auth": True}, status=401)
            resp["Cache-Control"] = "no-store"
            return resp
        if init_status >= 400:
            return _error(str(init_data.get("error") or "社区登记题包失败。"), status=init_status)

        pack_id = str(init_data.get("id") or "")
        upload_path = str(init_data.get("upload_url") or "")
        if not pack_id or not upload_path:
            return _error("社区没有返回上传地址。", status=502)

        put_url = urllib.parse.urljoin(site_url + "/", upload_path.lstrip("/"))
        try:
            put_status, put_data = _community_http_put_file(put_url, token=token, file_path=zip_path)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return _error(f"上传题包文件时网络中断：{exc}", status=502)
        if put_status >= 400:
            return _error(str(put_data.get("error") or "题包文件上传失败。"), status=put_status)

    community_meta = exported.get("community") or {}
    response = JsonResponse(
        {
            "ok": True,
            "id": pack_id,
            "status": str(put_data.get("status") or "pending"),
            "url": f"{site_url}/#/pack/{pack_id}",
            "questions": int(community_meta.get("questions") or 0),
            "size": zip_size,
            "onenote_count": int(community_meta.get("onenote_count") or 0),
            "onenote_skipped": int(community_meta.get("onenote_skipped") or 0),
        },
        status=201,
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def fetch_and_inspect_view(request):
    """POST /api/backup/fetch-and-inspect: 拉取社区签名下载链接到暂存区并返回合并预览。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request, limit=20_000)
    if payload is None:
        return _error("请求格式不正确。")
    try:
        dl_url = _validate_pack_download_url(str(payload.get("url") or ""))
    except TransferError as error:
        return _error(str(error))

    stage_dir = Path(tempfile.mkdtemp(prefix="tyj-stage-"))
    target = stage_dir / "community-pack.zip"
    try:
        req = urllib.request.Request(dl_url, headers={"Accept": "application/zip, */*"}, method="GET")
        total = 0
        checked_magic = False
        with urllib.request.urlopen(req, timeout=90) as resp, open(target, "wb") as writer:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                if not checked_magic:
                    if len(chunk) < 2 or chunk[:2] != b"PK":
                        raise TransferError("下载到的文件不是有效的 zip 题包。")
                    checked_magic = True
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise TransferError("题包太大了，超过本地导入上限。")
                writer.write(chunk)
        if not checked_magic or total == 0:
            raise TransferError("下载到的题包是空的，请重试。")
        result = library_transfer.plan_merge(target)
    except TransferError as error:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return _error(str(error))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        shutil.rmtree(stage_dir, ignore_errors=True)
        return _error(f"下载题包失败（链接可能已过期，请在网页重新点击下载）：{exc}", status=502)

    staging_id = uuid.uuid4().hex
    with _STAGED_LOCK:
        _cleanup_expired_staged_locked()
        _STAGED_PACKS[staging_id] = {
            "dir": stage_dir,
            "path": target,
            "created_at": time.monotonic(),
            "title": str(payload.get("title") or "").strip(),
            "pack_id": str(payload.get("pack_id") or "").strip(),
        }

    resp = _inspect_response(result)
    data = json.loads(resp.content.decode("utf-8"))
    data["staging_id"] = staging_id
    data["title"] = str(payload.get("title") or "").strip()
    data["pack_id"] = str(payload.get("pack_id") or "").strip()
    out = JsonResponse(data)
    out["Cache-Control"] = "no-store"
    return out


@csrf_exempt
def import_staged_view(request):
    """POST /api/backup/import-staged: 确认并合并暂存区的社区题包进本机题库。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request, limit=10_000)
    if payload is None:
        return _error("请求格式不正确。")
    staging_id = str(payload.get("staging_id") or "").strip()
    if not staging_id:
        return _error("缺少暂存题包编号。")
    with _STAGED_LOCK:
        entry = _STAGED_PACKS.pop(staging_id, None)
    if not entry:
        return _error("暂存题包已过期或不存在，请在社区页面重新点一次下载。", status=404)
    stage_dir = entry["dir"]
    path = entry["path"]
    cloud_folder = None
    try:
        result = library_transfer.merge_bundle(path)
        manifest = result.get("manifest") or {}
        pub_ids = list(result.get("publication_ids") or [])
        if manifest.get("community") and pub_ids:
            from . import library_folders

            try:
                cloud_folder = library_folders.assign_publications_to_cloud_folder(pub_ids)
            except Exception:
                cloud_folder = None
    except TransferError as error:
        return _error(str(error))
    except (OSError, ValueError):
        return _error("合并没能完成，本机题库还是原样。可以再试一次。", status=500)
    finally:
        if isinstance(stage_dir, Path):
            shutil.rmtree(stage_dir, ignore_errors=True)
    response = JsonResponse(
        {
            "report": result["report"],
            "record": result["record"],
            "publication_ids": result.get("publication_ids") or [],
            "cloud_folder": cloud_folder,
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def discard_staged_view(request):
    """POST /api/backup/discard-staged: 取消导入并清理暂存题包。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request, limit=10_000) or {}
    staging_id = str(payload.get("staging_id") or "").strip()
    if staging_id:
        with _STAGED_LOCK:
            entry = _STAGED_PACKS.pop(staging_id, None)
        if entry and isinstance(entry.get("dir"), Path):
            shutil.rmtree(entry["dir"], ignore_errors=True)
    response = JsonResponse({"ok": True})
    response["Cache-Control"] = "no-store"
    return response
