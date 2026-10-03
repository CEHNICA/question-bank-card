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

    def render(self, document, *, remote_fonts=False):
        geometry=[]
        original = pdf._CDP.call
        def observe(client, method, params=None):
            if method=="Network.setBlockedURLs" and remote_fonts:
                params={"urls":["https://*","ws://*","wss://*","ftp://*","file://*"]}
            if method=="Page.printToPDF":
                geometry.append(original(client,"Runtime.evaluate",{"expression":_GEOMETRY,"returnByValue":True})["result"]["value"])
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
