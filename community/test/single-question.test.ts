import { test } from "node:test";
import assert from "node:assert/strict";
import { cleanPreview, previewForViewer } from "../worker/src/packs.ts";
// @ts-expect-error 前端是原生 JS 模块
import { singleQuestionHtml } from "../web/src/single-question.js";
import { Client, makeEnv, makePackZip, packForm, promoteAdmin, setPayment, stubTurnstile } from "./helpers.ts";

const image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j0X8AAAAASUVORK5CYII=";
function sample() {
  return {
    counts: { papers: 1, questions: 1, tags: 0 },
    samples: [{ number: 7, question_type: "single_choice", stem: "（虚构）求 $x^2-4x+5$ 的最小值。", options: [{ key: "A", text: "$1$" }], figures: 1 }],
    papers: [{ title: "（虚构）分享示例卷", questions: 1 }],
    has_onenote_notes: true,
    onenote_count: 1,
    single_question: { question_images: [], figures: [image], onenote_pages: [image, image], images_omitted: 0 },
  };
}

test("single question retains ordered figures and note pages, rejects URLs and unknown fields", () => {
  const raw = sample();
  const data = { ...raw, single_question: { ...raw.single_question, figures: [image, "javascript:alert(1)", "https://example.com/private.png", "data:image/svg+xml;base64,AAAA"], private_path: "C:/Users/private" } };
  const result = cleanPreview(data);
  assert.deepEqual(result.single_question?.figures, [image]);
  assert.deepEqual(result.single_question?.onenote_pages, [image, image]);
  assert.equal(JSON.stringify(result).includes("private_path"), false);
  assert.equal(cleanPreview({ ...raw, counts: { questions: 2 } }).single_question, undefined);
  assert.deepEqual(cleanPreview({ ...raw, has_onenote_notes: false }).single_question?.onenote_pages, []);
});

test("single-question image previews have per-image and aggregate size limits", () => {
  const raw = sample();
  const large = "data:image/png;base64," + "A".repeat(350_000);
  const result = cleanPreview({ ...raw, single_question: { figures: Array(12).fill(large), onenote_pages: [large] } });
  const items = [...result.single_question!.figures, ...result.single_question!.onenote_pages];
  assert.ok(items.reduce((total, item) => total + item.length, 0) <= 1_200_000);
  assert.deepEqual(cleanPreview({ ...raw, single_question: { figures: [large + "A".repeat(20_000)] } }).single_question?.figures, []);
});

test("paid preview hides full notes without mutating stored data", () => {
  const original = cleanPreview(sample());
  const locked = previewForViewer(original, false);
  assert.deepEqual(locked?.single_question?.onenote_pages, []);
  assert.equal(locked?.single_question?.notes_locked, true);
  assert.equal(original.single_question?.onenote_pages.length, 2);
  assert.equal(previewForViewer(original, true)?.single_question?.onenote_pages.length, 2);
});

test("reading card renders math, the original question number and all notes without raw HTML", () => {
  const data = cleanPreview(sample());
  data.samples[0].stem += '<img src=x onerror="alert(1)">';
  const html = singleQuestionHtml(data);
  assert.ok(html.includes('class="katex"'));
  assert.ok(html.includes("原题第 7 题"));
  assert.equal((html.match(/class="single-note-page"/g) || []).length, 2);
  assert.ok(html.includes("复制分享链接"));
  assert.ok(!html.includes('<img src=x'));
  const lockedHtml = singleQuestionHtml(previewForViewer(data, false));
  assert.ok(lockedHtml.includes("获取这道题后"));
  assert.ok(!lockedHtml.includes('class="single-note-page"'));
});

test("community API shows all shared notes for free and entitled viewers, protects paid notes", async () => {
  const restore = stubTurnstile();
  try {
    const env = makeEnv();
    const author = new Client(env);
    const admin = new Client(env);
    const visitor = new Client(env);
    await author.register("shareauthor");
    await admin.register("shareadmin");
    promoteAdmin(env, "shareadmin");
    await visitor.register("sharereader");
    await setPayment(author);
    for (const paid of [false, true]) {
      if (paid) promoteAdmin(env, "shareauthor");
      const upload = await author.json("POST", "/api/packs", packForm(makePackZip({ preview: sample() }), paid ? { originality: "original", price_yuan: "2" } : {}));
      assert.equal(upload.status, 201, JSON.stringify(upload.data));
      const id = upload.data.id;
      await admin.json("POST", `/api/admin/packs/${id}/review`, { action: "approve" });
      const publicView = await visitor.json("GET", `/api/packs/${id}`);
      assert.equal(publicView.data.preview.single_question.onenote_pages.length, paid ? 0 : 2);
      const ownerView = await author.json("GET", `/api/packs/${id}`);
      assert.equal(ownerView.data.preview.single_question.onenote_pages.length, 2);
    }
  } finally {
    restore();
  }
});
