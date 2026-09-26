"""Create and verify a consistent local backup of the question bank data."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
DATABASE = BACKEND / "db.sqlite3"
DATA = BACKEND / "data"
BACKUPS = BACKEND / "backups"
INSTANCE_FILE = BACKEND / "runtime" / "instance.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _service_is_running() -> bool:
    ports = [8768]
    try:
        record = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
        port = record.get("port") if isinstance(record, dict) else None
        if isinstance(port, int) and 1 <= port <= 65535 and port not in ports:
            ports.insert(0, port)
    except (OSError, ValueError):
        pass
    for port in ports:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                payload = json.loads(response.read(2048))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("app") == "question-bank-card":
            return True
    return False


def _database_check(path: Path) -> None:
    with contextlib.closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("数据库 quick_check 未通过")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("数据库存在失效的外键引用")


def _manifest(root: Path) -> dict:
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return {"format": 1, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "files": rows}


def verify(root: Path) -> None:
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("找不到有效的 manifest.json") from exc
    rows = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("备份清单为空")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise RuntimeError("备份清单格式不正确")
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("备份清单含不安全路径")
        path = root / relative
        if not path.is_file() or path.stat().st_size != row.get("size") or _sha256(path) != row.get("sha256"):
            raise RuntimeError(f"文件校验失败：{relative.as_posix()}")
    _database_check(root / "db.sqlite3")


def create_backup() -> Path:
    if _service_is_running():
        raise RuntimeError("题库仍在运行。请先关闭启动窗口，确认服务停止后再备份。")
    if not DATABASE.is_file() or not DATA.is_dir():
        raise RuntimeError("没有找到完整的 backend\\db.sqlite3 与 backend\\data")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    target = BACKUPS / f"manual-{time.strftime('%Y%m%d-%H%M%S')}"
    if target.exists():
        raise RuntimeError("同名备份已存在，请稍后再试")
    target.mkdir()
    try:
        with contextlib.closing(sqlite3.connect(f"file:{DATABASE.as_posix()}?mode=ro", uri=True)) as source, \
                contextlib.closing(sqlite3.connect(target / "db.sqlite3")) as output:
            source.backup(output)
        shutil.copytree(DATA, target / "data", copy_function=shutil.copy2)
        manifest = _manifest(target)
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        verify(target)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="题库题卡版备份与校验")
    parser.add_argument("--verify", metavar="BACKUP_DIR", type=Path, help="只校验指定备份")
    args = parser.parse_args()
    try:
        if args.verify:
            verify(args.verify.resolve())
            print(f"备份校验通过：{args.verify.resolve()}")
        else:
            target = create_backup()
            print(f"备份完成并校验通过：{target}")
            print("备份含原卷和可能的手写内容，请按私密资料保管。")
        return 0
    except Exception as exc:
        print(f"备份未完成：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
