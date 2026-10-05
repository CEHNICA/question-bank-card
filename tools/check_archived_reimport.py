"""1.12.6 第 3 条：归档过的原件再传一次，不能静悄悄多出一份同名任务。

在真库副本上找一个已归档的卷的原件（sha256 相同的那个），
直接打上传端点，看它认不认得「已经录过」。
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def post_multipart(url, fields, filename, content):
    boundary = "----qbcheck1.12.6"
    parts = []
    for key, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                 "Content-Type: application/octet-stream\r\n\r\n".encode())
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "X-QB-Request": "1"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8803")
    args = parser.parse_args()
    url = args.url.rstrip("/")
    db = Path(os.environ["QB_DATABASE"])
    data_root = Path(os.environ["QB_DATA_ROOT"])
    connection = sqlite3.connect(db)
    # 找一个已归档的卷，它有未归档的同名同源副本最能说明问题；
    # 找不到就找任何一个已归档的。
    # 照片卷的 sha256 是「同一组照片的组合哈希」，不是单个文件的内容哈希，
    # 拿原文件重算对不上。能用单文件复现的只有 PDF / Word 这类整文件入库的。
    rows = connection.execute(
        "select id, sha256, material_type, source_path, filename, archived from core_paper "
        "where archived=1 and kind in ('pdf', 'docx') order by created_at desc").fetchall()
    assert rows, "副本里没有已归档的卷"
    paper_id, digest, material_type, source_path, filename, _ = rows[0]
    source = Path(source_path)
    if not source.is_absolute():
        source = data_root / source_path
    print("archived paper  :", paper_id, filename, "sha", digest[:12])
    print("source readable :", source.is_file(), source)
    if not source.is_file():
        print("原卷文件不在，无法实测上传；跳过")
        return
    content = source.read_bytes()
    assert hashlib.sha256(content).hexdigest() == digest, "原卷文件与记录的 sha256 对不上"

    before = connection.execute("select count(*) from core_paper").fetchone()[0]
    status, body = post_multipart(f"{url}/api/papers", {
        "parse_mode": "auto", "allow_cloud": "0", "material_type": material_type}, filename, content)
    after = sqlite3.connect(db).execute("select count(*) from core_paper").fetchone()[0]
    print("upload status   :", status, "duplicate =", body.get("duplicate"), "archived =", body.get("archived"))
    print("papers before/after:", before, "/", after)
    assert status == 200, body
    assert body.get("duplicate") is True, "归档过的原件再传一次没有被认出来"
    assert body.get("archived") is True, "响应没告诉前端那份已归档，前端会照旧直接打开"
    # DB 里 UUID 存成 32 位无连字符，API 返回带连字符，比之前先规范化。
    assert body["paper"]["id"].replace("-", "") == str(paper_id).replace("-", ""), \
        f"回的应该是原来那份归档卷，不是新建的：{body['paper']['id']} vs {paper_id}"
    assert after == before, f"不该新建任务：{before} -> {after}"
    print("Archived re-import: recognised, no second task, response says it is archived: OK")


if __name__ == "__main__":
    main()
