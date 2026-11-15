"""Real offline browser/PDF pagination checks with synthesized papers only."""

import base64
import io
import os
import re
import threading
import time
import uuid
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from . import library_pdf as pdf

_GEOMETRY = r"""(()=>({pages:[...document.querySelectorAll('#printPaper .exam-page')].map(page=>{
 const z=parseFloat(getComputedStyle(page).transform.match(/^matrix\(([^,]+)/)?.[1])||1;
 const base=page.querySelector('.exam-page-body').getBoundingClientRect();
 const rect=n=>{const r=n.getBoundingClientRect();return [r.x-base.x,r.y-base.y,r.width,r.height].map(x=>Math.round(x/z*1000)/1000);};
 return {questions:[...page.querySelectorAll('.print-question')].map(q=>({id:q.dataset.questionId,rect:rect(q),text:q.textContent,
 spaces:[...q.querySelectorAll('.print-answer-space')].map(n=>({rect:rect(n),mode:n.dataset.answerSpace})),
 images:[...q.querySelectorAll('img')].map(n=>rect(n)),math:[...q.querySelectorAll('.katex')].map(n=>rect(n))})),
 answers:[...page.querySelectorAll('.print-answer-row')].map(n=>({text:n.textContent,rect:rect(n)}))};
})}))()"""

# 表格有没有长出滚动条：overflow-x 一旦是 auto，overflow-y 按 CSS 规范也算 auto，
# 单元格里的 \dfrac 字体形盒比行框高两三个像素，就被算成纵向溢出，Chromium 于是给它
# 画一条 15px 的纵向滚动条，而 printToPDF 把灰条和上箭头一起印到了纸上。
# offsetWidth-clientWidth / offsetHeight-clientHeight 就是这条滚动条占掉的那 15px；
# 再顺带看一眼计算样式，是为了让「换成别的实现方式」也照样抓得到。
_TABLES = r"""(()=>[...document.querySelectorAll('#printPaper .qb-table-wrap')].map(node=>{
 const row=node.querySelector('tr'),style=getComputedStyle(node);
 return {columns:row?row.children.length:0,rows:node.querySelectorAll('tr').length,
  overflow_x:node.offsetWidth-node.clientWidth,overflow_y:node.offsetHeight-node.clientHeight,
  overflow:style.overflowX+'/'+style.overflowY,text:node.textContent.replace(/\s+/g,'').length};
}))()"""


@skipUnless(os.name == "nt", "Windows local PDF browser integration")
class SharedPdfPaginationTests(SimpleTestCase):
    def setUp(self):
        try:
            pdf._browser_path()
        except pdf.word.ExportError:
            self.skipTest("No installed local PDF browser")
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (363, 349), "white")
        draw = ImageDraw.Draw(image)
        draw.line((25, 320, 25, 25, 330, 320, 25, 320), fill="black", width=3)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        self.image_bytes = stream.getvalue()
        specs = [
            (
                "single_choice",
                r"命题“$\forall x\in\mathbb R$，都有 $x^2+x\ge0$”的否定为（ ）",
                {
                    "A": r"$\exists x\in\mathbb R$，使得 $x^2+x<0$",
                    "B": r"$\exists x\in\mathbb R$，使得 $x^2+x\ge0$",
                    "C": r"$\forall x\in\mathbb R$，都有 $x^2+x\le0$",
                    "D": r"$\forall x\in\mathbb R$，都有 $x^2+x<0$",
                },
            ),
            (
                "single_choice",
                r"设 $U=\{x|-2<x<6,x\in\mathbb N\}$，$A=\{0,2\}$，求 $\complement_U A$（ ）",
                {"A": "{0,1,3,4}", "B": "{1,3,4}", "C": "{-1,1,3,4}", "D": "{-1,0,1,3,4}"},
            ),
            (
                "multiple_choice",
                r"定义 $f(x,y)=x+y+xy$，则下列说法正确的是（ ）",
                {
                    "A": r"$f(1,5)=f(5,1)$",
                    "B": r"$x>0,y>0,f(x,y)f(\frac1x,\frac1y)\ge4$",
                    "C": r"对一切 $x\in\mathbb R$，$(2x+1)(x-a)+a+2\ge0$，则 $a\in[-\frac32,\frac52]$",
                    "D": r"对一切 $x>2$，$(2x+1)(x-a)\ge-a-2$，则 $a\in[3,+\infty)$",
                },
            ),
            (
                "fill_blank",
                r"已知 $f(x)=\begin{cases}x^2-1,&x\le1\\\frac1{x-1},&x>1\end{cases}$，求 $f(f(-2))=$____。",
                {},
            ),
            (
                "free_response",
                r"设 $m\in\mathbb R$，$A=\{x|-2\le x<4\}$，$B=\{x|m\le x\le m+2\}$。"
                + "\n"
                + r"（1）若 $m=3$，求 $A\cup B$ 与 $\complement_\mathbb R(A\cup B)$；"
                + "\n"
                + r"（2）若 $A\cap B=\varnothing$，求 $m$ 的取值范围。",
                {},
            ),
            (
                "free_response",
                r"已知 $f(x)=x^2-bx+c$。"
                + "\n"
                + r"（1）若 $f(x)\le0$ 的解集为 $\{x|1\le x\le2\}$，求 $b,c$；"
                + "\n"
                + r"（2）若 $b=c$，求 $f(x)>x$ 的解集；"
                + "\n"
                + r"（3）若 $\exists a\in[1,2]$，对 $\forall x\in[1,2]$，$f(x)\le(1-a)x^2$，求 $2c-3b$ 的最大值。",
                {},
            ),
            (
                "free_response",
                r"某商品进价为10元，每天的销售量 $y$ 与价格 $x$ 的关系如图。"
                + "\n"
                + "(1)为使日利润最大，应如何确定销售价格？"
                + "\n"
                + r"(2)为使日利润不低于售价15元时的日利润，求 $x$ 的取值范围。",
                {},
            ),
        ]
        self.items = [
            {
                "id": str(uuid.UUID(int=index)),
                "type": kind,
                "content": {"stem": stem, "options": choices},
                "selected": {},
                "ai": False,
                "solution_images": [],
                "images": [{"slot": "stem", "bytes": self.image_bytes, "size": (363, 349)}] if index == 7 else [],
            }
            for index, (kind, stem, choices) in enumerate(specs, 1)
        ]
        self.options = {
            "document": "questions",
            "answers": False,
            "ai_answers": False,
            "origin": False,
            "font_size": 12,
            "student_info": True,
            "pagination": "compact",
            "option_layout": "auto",
            "answer_space": "large",
            "answer_layout": "appendix",
            "option_overrides": {},
            "answer_space_overrides": {},
            "question_breaks": [],
        }

    def render(self, document, *, remote_fonts=False, probe=_GEOMETRY):
        geometry = []
        original = pdf._CDP.call

        def observe(client, method, params=None):
            if method == "Network.setBlockedURLs" and remote_fonts:
                params = {"urls": ["https://*", "ws://*", "wss://*", "ftp://*", "file://*"]}
            if method == "Page.printToPDF":
                geometry.append(
                    original(client, "Runtime.evaluate", {"expression": probe, "returnByValue": True})["result"][
                        "value"
                    ]
                )
            return original(client, method, params)

        original_popen = pdf.subprocess.Popen

        def start(args, **kwargs):
            args = [
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"
                if remote_fonts and x.startswith("--host-resolver-rules=")
                else x
                for x in args
            ]
            return original_popen(args, **kwargs)

        with mock.patch.object(pdf._CDP, "call", observe), mock.patch.object(pdf.subprocess, "Popen", start):
            data, pages = pdf._render(document)
        return data, pages, geometry[0]

    def assert_geometry(self, left, right):
        if isinstance(left, dict):
            self.assertEqual(left.keys(), right.keys())
            for key in left:
                self.assert_geometry(left[key], right[key])
        elif isinstance(left, list):
            self.assertEqual(len(left), len(right))
            for a, b in zip(left, right):
                self.assert_geometry(a, b)
        elif isinstance(left, (float, int)):
            self.assertAlmostEqual(left, right, delta=0.15)
        else:
            self.assertEqual(left, right)


    # 九上教材「下面是某同学的画法」那张列表：13 列 + 单元格里的 \dfrac，
    # 正是它在真卷上把表格盒撑出了滚动条。合成自检不能换成 \frac —— 那个不溢出。
    WIDE_TABLE = (
        "下面是某同学的列表：\n"
        "| $x$ | $-8$ | $-4$ | $-3$ | $-2$ | $-1$ | $-\\dfrac12$ | $\\dfrac12$ | $1$ | $2$ | $3$ | $4$ | $8$ |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| $y=\\dfrac4x$ | $-\\dfrac12$ | $-1$ | $-\\dfrac43$ | $-2$ | $-4$ | $-8$ | $8$ | $4$ | $2$ | $\\dfrac43$ | $1$ | $\\dfrac12$ |"
    )

    def test_a_wide_table_prints_without_a_browser_scrollbar(self):
        """宽表格在卷面上长出一条浏览器滚动条。

        .qb-table-wrap 的 overflow-x: auto 会按 CSS 规范把 overflow-y 也算成 auto，
        单元格里的 \\dfrac 字体形盒比行框高几个像素，就被算成纵向溢出，Chromium
        于是给它画一条 15px 的纵向滚动条；printToPDF 把灰条和上箭头一起画到纸上，
        老师印出来就是表格右边一条竖杠。这里量的是几何（滚动条占掉的那 15px），
        所以换成别的实现方式也照样抓得到。
        """
        before = deepcopy(self.items)
        items = deepcopy(self.items)
        items[0]["content"]["stem"] = self.WIDE_TABLE
        exported = pdf._html_document(items, "宽表格卷", self.options)
        _, pages, tables = self.render(exported, probe=_TABLES)
        self.assertGreaterEqual(pages, 1)
        self.assertTrue(tables, "卷面上必须真的排出这张表格，否则这道测试是空跑")
        self.assertEqual([(table["columns"], table["rows"]) for table in tables], [(13, 2)])
        for table in tables:
            self.assertEqual(
                (table["overflow_x"], table["overflow_y"]),
                (0, 0),
                "卷面上这张表格长出了滚动条，PDF 会把它印到纸上：" + repr(table),
            )
            self.assertEqual(
                table["overflow"], "visible/visible", "卷面上这张表格是滚动容器，PDF 会把它印到纸上：" + repr(table)
            )
        self.assertEqual(self.items, before)
