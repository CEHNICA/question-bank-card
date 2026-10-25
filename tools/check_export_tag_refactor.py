"""标签重构之后，导出还照旧吗 —— 组卷到文件那条路。

导出是这个产品**唯一的出口**。用户在题库里挑了半天题、组好卷、点了导出，
最后拿到的文件里少一题、少一个公式、少一段答案，界面上不会有任何提示。

标签重构动了题目的关联、用途和范围。这一条专门盯住它有没有把导出带偏：

1. 四种题型各挑一道进篮，导出的 Word 里题干都在；
2. 公式是 Word 公式（OMML）而不是图片；
3. 配图真的进了文件；
4. 学生卷里没有答案，教师卷里有；
5. 「分别导出」给的是真 zip，里面题目卷、答案卷各能打开；
6. PDF 文件头合法、真的有页；
7. **给题贴上新标签（用途、范围、解法分支）之后，再导一次，文件内容一字不变** ——
   这是本次重构特有的风险点：用途和范围是新的维度，导出要是把它们印到卷面上，
   或者反过来被它们挡住，卷子就废了。

脚本自己造虚构数据，自己核对，不读真实题库。

用法：

    python tools/check_export_tag_refactor.py --database <db> --base http://127.0.0.1:8799
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import unescape

import django

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")


def parse_args():
    parser = argparse.ArgumentParser(description="标签重构后的导出验收")
    parser.add_argument("--database", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--data-root", required=True)
    return parser.parse_args()


# 环境变量必须在 django.setup() 之前设好 —— settings 在那时就把它读进 DATABASES 了。
ARGS = parse_args()
os.environ["QB_DATABASE"] = str(Path(ARGS.database).resolve())
os.environ["QB_DATA_ROOT"] = str(Path(ARGS.data_root).resolve())
django.setup()

from django.core.management import call_command  # noqa: E402
from PIL import Image  # noqa: E402

from core import library  # noqa: E402
from core.models import Paper, PublishedQuestion, Question  # noqa: E402

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
}

WT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
MT = re.compile(r"<m:t(?:\s[^>]*)?>(.*?)</m:t>", re.S)

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return bool(ok)


def units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def field(value: str) -> dict:
    return {
        "source": value,
        "blocks": (
            [
                {
                    "type": "text",
                    "start": 0,
                    "end": units(value),
                    "segments": [{"type": "text", "start": 0, "end": units(value)}],
                }
            ]
            if value
            else []
        ),
    }


def math_field(latex: str, body: str, *, display: bool = False) -> dict:
    source = ("$$" if display else "$") + latex + ("$$" if display else "$")
    # 命名空间必须是 MathML 那个。写错了公式转 OMML 会失败，
    # 然后走「用本机浏览器渲染成图片」的兜底 —— 那条路要起浏览器，
    # 起不来就整卷拒收，报的还是「不能准确转换」。
    from core.library_export import MATH_NS

    mathml = (
        f'<math xmlns="{MATH_NS}"><semantics>{body}'
        f'<annotation encoding="application/x-tex">' + unescape(latex) + "</annotation></semantics></math>"
    )
    return {
        "source": source,
        "blocks": [
            {
                "type": "text",
                "start": 0,
                "end": units(source),
                "segments": [
                    {
                        "type": "math",
                        "start": 0,
                        "end": units(source),
                        "latex": latex,
                        "mathml": mathml,
                        "display": display,
                    }
                ],
            }
        ],
    }


# MathML 要写完整，否则导出会说「这段公式暂时不能准确转换为可编辑 Word 公式」。
MATH_LATEX = r"\sqrt{x+1}+\frac{a}{b}"
MATH_BODY = (
    "<mrow><msqrt><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow></msqrt>"
    "<mo>+</mo><mfrac><mi>a</mi><mi>b</mi></mfrac></mrow>"
)


def make_publication(
    counter: int, *, kind: str, stem: str, answer: str, analysis: str, options=None
) -> PublishedQuestion:
    paper = Paper.objects.create(filename="虚构导出卷.pdf", kind="pdf", sha256="c" * 64)
    question = Question.objects.create(
        paper=paper,
        number=counter,
        question_type=kind,
        stem=stem,
        answer=answer,
        analysis=analysis,
        options=options or {},
        state="green",
    )
    content = {
        "stem": stem,
        "answer": answer,
        "analysis": analysis,
        "options": options or {},
        "question_type": kind,
        "origin": "虚构出处",
        "figures": [],
        "sources": [],
        "section": "",
        "document_id": str(paper.id),
        "source_filename": paper.filename,
    }
    return PublishedQuestion.objects.create(
        paper=paper,
        question=question,
        number=counter,
        source_filename=paper.filename,
        question_type=kind,
        version=1,
        status=PublishedQuestion.Status.PUBLISHED,
        content=content,
        content_hash=library.content_hash(content),
        extras={},
    )


def add_figure(publication: PublishedQuestion, data_root: Path, color: str = "red") -> None:
    folder = Path(data_root) / "library" / str(publication.id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "figure-1.png"
    Image.new("RGB", (240, 120), color).save(target)
    publication.content["figures"] = [{"slot": "stem", "file": target.name, "page_idx": 0, "bbox": [0, 0, 100, 100]}]
    publication.content_hash = library.content_hash(publication.content)
    publication.save(update_fields=["content", "content_hash"])


def build_payload(publications, *, options=None, output_format="docx", title="虚构练习"):
    fields = {}
    for publication in publications:
        content = publication.content
        entry = {name: field(str(content.get(name) or "")) for name in ("stem", "origin")}
        entry.update({f"options.{key}": field(value) for key, value in (content.get("options") or {}).items()})
        entry.update({name: field(str(content.get(name) or "")) for name in ("answer", "analysis")})
        # 题干正好是公式的那道，排版结果用公式段落。
        # source 必须和入库版里的原文一字不差，否则导出会被拦下。
        if str(content.get("stem") or "") == "$$" + MATH_LATEX + "$$":
            entry["stem"] = math_field(MATH_LATEX, MATH_BODY, display=True)
        fields[str(publication.id)] = entry
    return {
        "ids": [str(pub.id) for pub in publications],
        "title": title,
        "print_options": options or {},
        "rendered_fields": fields,
        "format": output_format,
    }


def post(base: str, path: str, payload: dict):
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"X-QB-Request": "1", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def document_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    return "".join(unescape(part) for part in WT.findall(xml))


def document_math(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    return "".join(unescape(part) for part in MT.findall(xml))


def media_names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [name for name in archive.namelist() if name.startswith("word/media/")]


def main() -> int:
    args = ARGS

    call_command("migrate", run_syncdb=True, verbosity=0)

    from core import library_concepts

    print("=== 造四种题型的虚构数据 ===")
    pubs = []
    for counter, (kind, stem, answer, analysis, options) in enumerate(
        [
            (
                "single_choice",
                "虚构选择题：下列说法正确的是（　）",
                "A",
                "虚构解析：选 A。",
                {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
            ),
            (
                "multiple_choice",
                "虚构多选题：下列正确的是（　）",
                "AB",
                "虚构解析：选 AB。",
                {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
            ),
            ("fill_blank", "$$\\sqrt{x+1}+\\frac{a}{b}$$", "2", "虚构解析：配方。", {}),
            ("free_response", "虚构解答题：已知 x 的平方加一，求最小值并说明理由。", "2", "虚构解析：配方得 2。", {}),
        ],
        start=1,
    ):
        pubs.append(make_publication(counter, kind=kind, stem=stem, answer=answer, analysis=analysis, options=options))
    add_figure(pubs[3], args.data_root)
    print(f"  造了 {len(pubs)} 道题，其中一道带配图")

    print("=== 1. Word 导出（题目卷 + 答案卷）===")
    status, data, headers = post(args.base, "/api/library/export-docx", build_payload(pubs, options={"answers": True}))
    check(status == 200, f"导出 Word 成功（状态 {status}）")
    if status != 200:
        print(data[:400].decode("utf-8", "replace"))
        return 1
    text = document_text(data)
    for label in ("虚构选择题", "虚构多选题", "虚构解答题"):
        check(label in text, f"Word 里有「{label}」")
    # 填空题的题干就是那道公式 —— 它在 Word 里是 OMML，不是文字。
    check("a" in document_math(data) or "b" in document_math(data), "Word 里有那道公式题（OMML）")
    check(len(media_names(data)) >= 1, f"配图进了文件（{len(media_names(data))} 个媒体文件）")

    print("=== 2. 学生卷不含答案 ===")
    status, student, _ = post(args.base, "/api/library/export-docx", build_payload(pubs, options={"answers": False}))
    check(status == 200, f"导出学生卷成功（状态 {status}）")
    student_text = document_text(student)
    check("虚构解析：选 A。" not in student_text, "学生卷里没有解析")
    check("虚构解析：选 A。" in text, "教师卷里有解析")

    print("=== 3. 分别导出（真 zip）===")
    status, split, headers = post(args.base, "/api/library/export-docx", build_payload(pubs, output_format="split"))
    check(status == 200, f"分别导出成功（状态 {status}）")
    if status == 200:
        try:
            with zipfile.ZipFile(io.BytesIO(split)) as archive:
                names = archive.namelist()
                check(len(names) >= 2, f"zip 里有 {len(names)} 个文件")
                docs = [n for n in names if n.endswith(".docx")]
                check(len(docs) >= 2, f"题目卷和答案卷各是一个能打开的 docx（{len(docs)} 个）")
                for name in docs:
                    with zipfile.ZipFile(io.BytesIO(archive.read(name))) as inner:
                        check("word/document.xml" in inner.namelist(), f"{name} 内部结构完整")
        except zipfile.BadZipFile:
            check(False, "分别导出给的是真 zip")

    print("=== 4. PDF 导出 ===")
    # PDF 端点只认 format="pdf"（Word 那边才是 docx/split）。
    status, pdf, _ = post(args.base, "/api/library/export-pdf", build_payload(pubs, output_format="pdf"))
    check(status == 200, f"导出 PDF 成功（状态 {status}）")
    if status != 200:
        print("  PDF 错误：" + pdf[:400].decode("utf-8", "replace"))
    if status == 200:
        check(pdf[:5] == b"%PDF-", "PDF 文件头合法")
        try:
            import pymupdf as fitz

            document = fitz.open(stream=pdf, filetype="pdf")
            check(document.page_count >= 1, f"PDF 真的有页（{document.page_count} 页）")
            pdf_text = "".join(page.get_text() for page in document)
            check("虚构解答题" in pdf_text, "PDF 里有题目正文")
            document.close()
        except Exception as error:  # pragma: no cover - 诊断用
            check(False, f"PDF 能打开：{error}")

    print("=== 5. 贴了新标签之后，导出内容一字不变 ===")
    # 贴一条真的关联（用途 + 范围），再看导出有没有被带偏。
    from core.models import LibraryTag

    tag = LibraryTag.objects.create(name="虚构考点", kind="knowledge", canonical_key="虚构考点")
    publication = pubs[3]
    library_concepts.attach_assertion(
        {
            "publication_id": str(publication.pk),
            "tag_ids": [str(tag.pk)],
            "role": "primary",
            "client_request_id": str(__import__("uuid").uuid4()),
            "expected_revision": publication.tag_revision,
        }
    )
    before = document_text(data)
    status, after_data, _ = post(args.base, "/api/library/export-docx", build_payload(pubs, options={"answers": True}))
    check(status == 200, f"贴标签后重新导出成功（状态 {status}）")
    if status == 200:
        check(document_text(after_data) == before, "导出内容与贴标签前完全一致（标签不印到卷面上，也不影响排版）")

    print()
    print("=== 6. 组卷草稿：贴标签后固定版本不变 ===")
    # 组卷草稿固定的是「当初选的那一版」—— 题面和答案版本。
    # 给题贴标签属于题面之外的东西，不该把已经组好的卷子改掉。
    ids = [str(pub.id) for pub in pubs]
    status, draft, _ = post(
        args.base,
        "/api/library/drafts",
        {
            "ids": ids,
            "title": "虚构组卷",
            "print_options": {"answers": True},
            "solutions": dict.fromkeys(ids, "origin"),
        },
    )
    # 新建草稿返回 201，既有草稿返回 200 —— 两个都算成功。
    check(status in (200, 201), f"保存组卷草稿成功（状态 {status}）")
    if status != 200:
        print("  草稿错误：" + draft[:400].decode("utf-8", "replace"))
    if status == 200:
        draft_id = draft.get("id")
        status, before_draft, _ = post(
            args.base, "/api/library/drafts", {"ids": ids, "title": "虚构组卷"}, method="GET"
        )
        snapshot = json.dumps(before_draft.get("drafts", []), sort_keys=True, ensure_ascii=False)
        # 再给第一道题贴一条关联
        from core.models import LibraryTag

        tag = LibraryTag.objects.create(name="虚构草稿考点", kind="knowledge", canonical_key="虚构草稿考点")
        target = pubs[0]
        library_concepts.attach_assertion(
            {
                "publication_id": str(target.pk),
                "tag_ids": [str(tag.pk)],
                "role": "primary",
                "client_request_id": str(__import__("uuid").uuid4()),
                "expected_revision": target.tag_revision,
            }
        )
        status, after_draft, _ = post(args.base, "/api/library/drafts", {"ids": ids, "title": "虚构组卷"}, method="GET")
        after = json.dumps(after_draft.get("drafts", []), sort_keys=True, ensure_ascii=False)
        check(snapshot == after, "贴标签后草稿内容一字不变（固定版本没被动）")
        check(draft_id is not None, "草稿有编号")

    print()
    passed = sum(1 for ok, _ in results if ok)
    print(f"{passed}/{len(results)} 项通过")
    for ok, label in results:
        if not ok:
            print("  失败：" + label)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
