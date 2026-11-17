"use strict";
// 删资料进回收站必须按资料整批下架，不能只撤 questionsData 里已加载的那几道。
// 首屏分页没载完时，旧写法会漏题：资料进了回收站，题还挂在正式题库。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

function bodyOf(source, signature) {
  const start = source.indexOf(signature);
  if (start < 0) return "";
  let i = start + signature.length;
  let depth = 0;
  let begun = false;
  for (; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === "{") {
      depth += 1;
      begun = true;
    } else if (ch === "}") {
      depth -= 1;
      if (begun && depth === 0) return source.slice(start, i + 1);
    }
  }
  return "";
}

test("删资料进回收站按资料整批下架，不靠本页已加载题列表", async (t) => {
  await t.test("1. movePaperToTrash 必须传 document_id", () => {
    const fn = bodyOf(html, "async function movePaperToTrash");
    assert.ok(fn, "应能找到 movePaperToTrash");
    assert.match(fn, /document_id\s*:\s*paperId/, "必须把 document_id 交给 withdraw-batch");
    assert.ok(
      !/questionsData\.filter\(\s*q\s*=>\s*q\.documentId\s*===\s*paperId\s*\)/.test(fn),
      "不许再从 questionsData 里筛 id 去下架"
    );
  });

  await t.test("2. 确认弹窗用资料已入库数，不看内存题列表长度", () => {
    const fn = bodyOf(html, "function confirmDeletePaper");
    assert.ok(fn, "应能找到 confirmDeletePaper");
    assert.match(fn, /p\.published/, "确认文案必须用 p.published");
    assert.ok(
      !/questionsData\.filter\(\s*q\s*=>\s*q\.documentId\s*===\s*paperId\s*\)/.test(fn),
      "确认文案不许用 questionsData 长度冒充全量"
    );
  });

  await t.test("3. 恢复资料时把该卷在回收站里的下架题一并放回", () => {
    const fn = bodyOf(html, "async function restoreTrashPaper");
    assert.ok(fn, "应能找到 restoreTrashPaper");
    assert.match(fn, /documentId\s*\|\|\s*q\.document_id/, "恢复必须按资料从回收站总账对题");
    assert.match(fn, /recordedIds/, "仍可合并本地记录的伴随下架 id");
  });
});
