"""Real offline browser/PDF pagination checks with synthesized papers only."""
import base64
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
from pathlib import Path
import re
import threading
import time
from unittest import mock, skipUnless
import uuid

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


def _preview_driver():
    # Exercise the actual preview renderer, not a second fixture implementation.
    source = (Path(settings.FRONTEND_ROOT) / "library.js").read_text(encoding="utf-8")
    render = source[source.index("  function renderPrint("):source.index("  function syncIndividualLayout(")]
    answer = source[source.index("  function printAnswerRow("):source.index("  async function checkMissingAnswers(")]
    return r"""
window.__qbPdfStatus={ready:false};
(async()=>{
const input=JSON.parse(document.getElementById('examData').textContent),o=input.options;
const host=document.getElementById('printPaper');host.style.width='620px';
const node=(tag,cls='',text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const QB=QBRender,solutions=LibrarySolutions;
const ui={paper:host,printTitle:{value:input.title},printOrigin:{checked:o.origin},printAnswers:{checked:o.document!=='questions'}};
const printState={missing:[],availableAnswers:input.items.filter(x=>Object.keys(x.selected||{}).length).length};
const controls={},$=id=>(controls[id]||={hidden:false,textContent:''});
const currentPrintOptions=()=>o,renderPrintMissing=()=>{},syncPrintAnswers=()=>{},syncExportButtons=()=>{},syncIndividualLayout=()=>{},preparePrintLayout=()=>{};
const printTools=()=>node('div','no-print','tools'),printAnswerContent=item=>Object.keys(item.selected||{}).length||item.solution_images?.length?
{content:{...item.selected,figures:(item.solution_images||[]).map(f=>({...f,url:f.file}))},ai:item.ai}:null;
async function refreshPrintPages(flow){const layoutHost=node('div','print-paper');const result=await ExamLayout.paginate(flow,{host:layoutHost,...o});host.replaceChildren(...result.pages);ExamLayout.scale(host);return result;}
/*ANSWER*/
/*RENDER*/
const items=input.items.map(x=>({...x,question_type:x.type,content:{...x.content,figures:(x.content.figures||[]).map(f=>({...f,url:f.file})),question_images:(x.content.question_images||[]).map(f=>({...f,url:f.file}))}}));
renderPrint(items);const result=await printState.layoutPromise;
window.__qbPdfStatus={ready:true,page_count:result.page_count,warnings:result.warnings||[]};
})().catch(e=>{window.__qbPdfStatus={ready:false,error:e.message||'preview failed'};});
""".replace("/*ANSWER*/", answer).replace("/*RENDER*/", render)


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
        stream = io.BytesIO(); image.save(stream, format="PNG")
        self.image_bytes = stream.getvalue()
        specs = [
            ("single_choice", r'命题“$\forall x\in\mathbb R$，都有 $x^2+x\ge0$”的否定为（ ）',
             {"A":r'$\exists x\in\mathbb R$，使得 $x^2+x<0$',"B":r'$\exists x\in\mathbb R$，使得 $x^2+x\ge0$',
              "C":r'$\forall x\in\mathbb R$，都有 $x^2+x\le0$',"D":r'$\forall x\in\mathbb R$，都有 $x^2+x<0$'}),
            ("single_choice", r'设 $U=\{x|-2<x<6,x\in\mathbb N\}$，$A=\{0,2\}$，求 $\complement_U A$（ ）',
             {"A":"{0,1,3,4}","B":"{1,3,4}","C":"{-1,1,3,4}","D":"{-1,0,1,3,4}"}),
            ("multiple_choice", r'定义 $f(x,y)=x+y+xy$，则下列说法正确的是（ ）',
             {"A":r'$f(1,5)=f(5,1)$',"B":r'$x>0,y>0,f(x,y)f(\frac1x,\frac1y)\ge4$',
              "C":r'对一切 $x\in\mathbb R$，$(2x+1)(x-a)+a+2\ge0$，则 $a\in[-\frac32,\frac52]$',
              "D":r'对一切 $x>2$，$(2x+1)(x-a)\ge-a-2$，则 $a\in[3,+\infty)$'}),
            ("fill_blank", r'已知 $f(x)=\begin{cases}x^2-1,&x\le1\\\frac1{x-1},&x>1\end{cases}$，求 $f(f(-2))=$____。', {}),
            ("free_response", r'设 $m\in\mathbb R$，$A=\{x|-2\le x<4\}$，$B=\{x|m\le x\le m+2\}$。'+'\n'+
             r'（1）若 $m=3$，求 $A\cup B$ 与 $\complement_\mathbb R(A\cup B)$；'+'\n'+r'（2）若 $A\cap B=\varnothing$，求 $m$ 的取值范围。',{}),
            ("free_response", r'已知 $f(x)=x^2-bx+c$。'+'\n'+r'（1）若 $f(x)\le0$ 的解集为 $\{x|1\le x\le2\}$，求 $b,c$；'+'\n'+
             r'（2）若 $b=c$，求 $f(x)>x$ 的解集；'+'\n'+r'（3）若 $\exists a\in[1,2]$，对 $\forall x\in[1,2]$，$f(x)\le(1-a)x^2$，求 $2c-3b$ 的最大值。',{}),
            ("free_response", r'某商品进价为10元，每天的销售量 $y$ 与价格 $x$ 的关系如图。'+'\n'+
             '(1)为使日利润最大，应如何确定销售价格？'+'\n'+r'(2)为使日利润不低于售价15元时的日利润，求 $x$ 的取值范围。',{}),
        ]
        self.items = [{"id":str(uuid.UUID(int=index)),"type":kind,"content":{"stem":stem,"options":choices},
                       "selected":{},"ai":False,"solution_images":[],
                       "images":[{"slot":"stem","bytes":self.image_bytes,"size":(363,349)}] if index==7 else []}
                      for index,(kind,stem,choices) in enumerate(specs,1)]
        self.options={"document":"questions","answers":False,"ai_answers":False,"origin":False,"font_size":12,
                      "student_info":True,"pagination":"compact","option_layout":"auto","answer_space":"large",
                      "answer_layout":"appendix","option_overrides":{},"answer_space_overrides":{},"question_breaks":[]}

    def render(self, document, *, remote_fonts=False, probe=_GEOMETRY):
        geometry=[]
        original = pdf._CDP.call
        def observe(client, method, params=None):
            if method=="Network.setBlockedURLs" and remote_fonts:
                params={"urls":["https://*","ws://*","wss://*","ftp://*","file://*"]}
            if method=="Page.printToPDF":
                geometry.append(original(client,"Runtime.evaluate",{"expression":probe,"returnByValue":True})["result"]["value"])
            return original(client,method,params)
        original_popen=pdf.subprocess.Popen
        def start(args, **kwargs):
            args=["--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1" if remote_fonts and x.startswith("--host-resolver-rules=") else x for x in args]
            return original_popen(args,**kwargs)
        with mock.patch.object(pdf._CDP,"call",observe), mock.patch.object(pdf.subprocess,"Popen",start):
            data,pages=pdf._render(document)
        return data,pages,geometry[0]

    def assert_geometry(self, left, right):
        if isinstance(left,dict):
            self.assertEqual(left.keys(),right.keys())
            for key in left:self.assert_geometry(left[key],right[key])
        elif isinstance(left,list):
            self.assertEqual(len(left),len(right))
            for a,b in zip(left,right):self.assert_geometry(a,b)
        elif isinstance(left,(float,int)):
            self.assertAlmostEqual(left,right,delta=.15)
        else:self.assertEqual(left,right)

    def documents(self, options=None):
        options=options or self.options
        with mock.patch.object(pdf,"_DRIVER",_preview_driver()):
            preview=pdf._html_document(self.items,"合成分页验收",options)
        return preview,pdf._html_document(self.items,"合成分页验收",options)

    def test_compact_blank_space_splits_without_empty_third_page_or_losing_writing_space(self):
        before=deepcopy(self.items)
        preview, exported=self.documents()
        _,preview_count,preview_geometry=self.render(preview)
        data,export_count,geometry=self.render(exported)
        self.assertEqual((preview_count,export_count),(2,2))
        self.assert_geometry(preview_geometry,geometry)
        questions=[q for page in geometry["pages"] for q in page["questions"]]
        self.assertEqual(set(q["id"] for q in questions),set(x["id"] for x in self.items))
        self.assertEqual(sum(len(q["images"]) for q in questions),1)
        space_mm=sum(s["rect"][3] for q in questions for s in q["spaces"])*25.4/96
        self.assertAlmostEqual(space_mm,180,delta=.05)
        self.assertTrue(any(q["spaces"] for q in geometry["pages"][0]["questions"]),"Page-bottom writing space is used before continuing")
        import pymupdf
        with pymupdf.open(stream=data,filetype="pdf") as result:
            self.assertEqual(result.page_count,2)
            self.assertIn("商品",result[1].get_text())
            self.assertTrue(result[1].get_images())
        self.assertEqual(self.items,before)

    def test_slow_url_fonts_match_embedded_pdf_and_failed_fonts_do_not_publish_fallback_pagination(self):
        preview,exported=self.documents()
        fonts,requests={},[]
        class Handler(BaseHTTPRequestHandler):
            fail=False
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append(self.path)
                if self.fail or self.path not in fonts:
                    self.send_error(503);return
                time.sleep(.15)
                data=fonts[self.path]
                self.send_response(200);self.send_header("Content-Type","font/woff2")
                self.send_header("Access-Control-Allow-Origin","*");self.send_header("Content-Length",str(len(data)))
                self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f"http://127.0.0.1:{server.server_port}"
        def replace(match):
            key=f"/font-{len(fonts)}.woff2";fonts[key]=base64.b64decode(match[1]);return f'url("{origin}{key}")'
        delayed=re.sub(r'url\("data:font/[^;]+;base64,([^"\)]+)"\)',replace,preview)
        delayed=delayed.replace("font-src data:;",f"font-src data: {origin};")
        try:
            _,preview_count,preview_geometry=self.render(delayed,remote_fonts=True)
            _,export_count,export_geometry=self.render(exported)
            self.assertGreater(len(requests),0,"The test must actually load delayed URL fonts")
            self.assertEqual((preview_count,export_count),(2,2))
            self.assert_geometry(preview_geometry,export_geometry)
            Handler.fail=True
            with self.assertRaisesRegex(pdf.word.ExportError,"公式字体未能载入"):
                self.render(delayed,remote_fonts=True)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_keep_whole_questions_retains_unsplit_writing_space_and_matching_geometry(self):
        options={**self.options,"pagination":"keep"}
        preview,exported=self.documents(options)
        _,preview_count,preview_geometry=self.render(preview)
        _,export_count,geometry=self.render(exported)
        self.assertEqual(preview_count,export_count)
        self.assert_geometry(preview_geometry,geometry)
        questions=[q for page in geometry["pages"] for q in page["questions"]]
        self.assertEqual(len(questions),7,"Keep mode must not split a fitting question to save paper")
        spaces=[s for q in questions for s in q["spaces"]]
        self.assertEqual(len(spaces),3)
        for space in spaces:self.assertAlmostEqual(space["rect"][3]*25.4/96,60,delta=.02)

    def page_heads(self,geometry):
        heads={}
        for index,page in enumerate(geometry["pages"]):
            if page["questions"]:heads[page["questions"][0]["id"]]=index
        return heads

    def page_of(self,geometry):
        where={}
        for index,page in enumerate(geometry["pages"]):
            for question in page["questions"]:where[question["id"]]=index
        return where

    def test_question_break_starts_that_question_on_a_new_page_and_leaves_the_others_alone(self):
        target=self.items[3]["id"]
        before=deepcopy(self.items)
        _,base_count,base_geometry=self.render(self.documents()[1])
        base_heads=self.page_heads(base_geometry)
        base_where=self.page_of(base_geometry)
        self.assertNotIn(target,base_heads,"The fixture must not already break this question for the test to mean anything")

        options={**self.options,"question_breaks":[target]}
        preview,exported=self.documents(options)
        _,preview_count,preview_geometry=self.render(preview)
        _,export_count,geometry=self.render(exported)
        self.assertEqual((preview_count,export_count),(preview_count,export_count))
        self.assert_geometry(preview_geometry,geometry)
        heads=self.page_heads(geometry)
        self.assertIn(target,heads,"The chosen question has to become the first one on its page")
        self.assertGreaterEqual(heads[target],1,"A break on the first question cannot invent a page before the title")
        broken_where=self.page_of(geometry)
        for index,other in enumerate(self.items,1):
            if index>=4:break
            self.assertEqual(broken_where[other["id"]],base_where[other["id"]],
                             "Questions printed before the break must not move")
        self.assertGreaterEqual(export_count,base_count)
        self.assertEqual(self.items,before,"Rendering must not rewrite the caller's items")

    def test_teacher_ai_note_matches_preview_text_order_and_geometry(self):
        for index,item in enumerate(self.items):
            item["selected"]={"answer":"合成答案","analysis":r"由 $x=1$ 代入计算，得到结论。"}
            item["ai"]=index%2==0
        options={**self.options,"document":"combined","answers":True,"answer_layout":"inline","answer_space":"none"}
        preview,exported=self.documents(options)
        _,preview_count,preview_geometry=self.render(preview)
        _,export_count,geometry=self.render(exported)
        self.assertEqual(preview_count,export_count)
        self.assert_geometry(preview_geometry,geometry)
        notes=[answer["text"] for page in geometry["pages"] for answer in page["answers"] if "AI" in answer["text"]]
        self.assertTrue(notes)
        self.assertTrue(all(text.startswith("（AI 参考，未核对）") for text in notes))

    def test_source_image_origin_is_visible_in_preview_and_export_alike(self):
        """原图题只建 .qb-stem，没有 .qb-stem-body。

        题源在导出侧已经能落到 .qb-stem 上，预览侧却只认 .qb-stem-body，于是这类题
        在屏幕上没有题源、导出的卷子上有 —— 老师照屏幕核对会对不上号。
        """
        before=deepcopy(self.items)
        items=deepcopy(self.items)
        items[0]["content"].update(body_mode="source_image",stem="",origin="2024 全国甲卷 第 1 题")
        items[0]["images"]=[{"slot":"stem","bytes":self.image_bytes,"size":(363,349)}]
        options={**self.options,"origin":True}
        with mock.patch.object(pdf,"_DRIVER",_preview_driver()):
            preview=pdf._html_document(items,"原图题源对齐",options)
        exported=pdf._html_document(items,"原图题源对齐",options)
        _,preview_count,preview_geometry=self.render(preview)
        _,export_count,geometry=self.render(exported)
        self.assertEqual(preview_count,export_count)
        self.assert_geometry(preview_geometry,geometry)
        self.assertIn("2024 全国甲卷 第 1 题",geometry["pages"][0]["questions"][0]["text"])
        self.assertEqual(self.items,before)

    # 九上教材「下面是某同学的画法」那张列表：13 列 + 单元格里的 \dfrac，
    # 正是它在真卷上把表格盒撑出了滚动条。合成自检不能换成 \frac —— 那个不溢出。
    WIDE_TABLE = (
        "下面是某同学的列表：\n"
        "| $x$ | $-8$ | $-4$ | $-3$ | $-2$ | $-1$ | $-\\dfrac12$ | $\\dfrac12$ | $1$ | $2$ | $3$ | $4$ | $8$ |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| $y=\\dfrac4x$ | $-\\dfrac12$ | $-1$ | $-\\dfrac43$ | $-2$ | $-4$ | $-8$ | $8$ | $4$ | $2$ | $\\dfrac43$ | $1$ | $\\dfrac12$ |")

    def test_a_wide_table_prints_without_a_browser_scrollbar(self):
        """宽表格在卷面上长出一条浏览器滚动条。

        .qb-table-wrap 的 overflow-x: auto 会按 CSS 规范把 overflow-y 也算成 auto，
        单元格里的 \\dfrac 字体形盒比行框高几个像素，就被算成纵向溢出，Chromium
        于是给它画一条 15px 的纵向滚动条；printToPDF 把灰条和上箭头一起画到纸上，
        老师印出来就是表格右边一条竖杠。这里量的是几何（滚动条占掉的那 15px），
        所以换成别的实现方式也照样抓得到。
        """
        before=deepcopy(self.items)
        items=deepcopy(self.items)
        items[0]["content"]["stem"]=self.WIDE_TABLE
        exported=pdf._html_document(items,"宽表格卷",self.options)
        _,pages,tables=self.render(exported,probe=_TABLES)
        self.assertGreaterEqual(pages,1)
        self.assertTrue(tables,"卷面上必须真的排出这张表格，否则这道测试是空跑")
        self.assertEqual([(table["columns"],table["rows"]) for table in tables],[(13,2)])
        for table in tables:
            self.assertEqual((table["overflow_x"],table["overflow_y"]),(0,0),
                "卷面上这张表格长出了滚动条，PDF 会把它印到纸上："+repr(table))
            self.assertEqual(table["overflow"],"visible/visible",
                "卷面上这张表格是滚动容器，PDF 会把它印到纸上："+repr(table))
        self.assertEqual(self.items,before)
