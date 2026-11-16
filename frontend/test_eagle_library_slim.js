"use strict";
// 题库列表瘦身：首屏拉全库，但别把详情页的胖字段一起拖回来。
//
// 真库实测（8768，810 道题）：
//   GET /api/library?bulk=1&limit=1000&offset=0
//     明文 1.90 MB / gzip 338 KB / 稳态 320~376 ms
// 逐字段拆开（810 道合计 1.44 MB）：
//   content.review      372,577 B  26%   每题的配图核验快照 —— 列表一个字节都不读
//   ai_answer           207,412 B  14%
//   tag_assignments     134,956 B   9%   标签对象带 description/aliases/levels…
//                                       外加 AI 的 rationale/evidence
//   content.source_group 36,863 B   3%   资料组信息 —— 列表从不读
//
// 后端 ?slim=1 把这些丢掉。列表的标签芯片只要 id/name/kind/color/source/
// needs_review，展开一道题时按 publication_id 另拉详情补齐。
//
// 要求：Eagle 页的列表取数一律带 slim=1；单题详情接口不许带（详情要全字段）。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

test("Eagle 列表取数要瘦身，详情取数不许瘦身", async (t) => {
  await t.test("1. 首屏拉全库的那次请求必须带 slim=1", () => {
    assert.ok(
      /api\/library\?bulk=1&limit=1000&offset=0&slim=1/.test(html),
      "首屏整库请求必须带 ?slim=1"
    );
  });

  await t.test("2. 续拉下一页的那次请求也必须带 slim=1", () => {
    assert.ok(
      /api\/library\?bulk=1&limit=1000&offset=\$\{offset\}&slim=1/.test(html),
      "续拉请求必须带 ?slim=1"
    );
  });

  await t.test("3. 按资料补拉的那次请求也必须带 slim=1", () => {
    assert.ok(
      /api\/library\?document=[^`]*?&slim=1/.test(html),
      "按资料补拉必须带 ?slim=1"
    );
  });

  await t.test("4. 页面上不许残留不带 slim 的 bulk 请求", () => {
    const leftovers = html.match(/api\/library\?bulk=1[^`'"\s]*/g) || [];
    for (const url of leftovers) {
      assert.ok(
        /slim=1/.test(url),
        `列表请求必须带 slim=1，发现漏网的：${url}`
      );
    }
    assert.ok(leftovers.length >= 2, "至少要有首屏和续拉两次 bulk 请求");
  });

  await t.test("5. 单题详情接口不许带 slim", () => {
    const detail = html.match(/api\/library\/\$\{[^}]+\}`/g) || [];
    assert.ok(detail.length > 0, "应能找到单题详情请求");
    for (const url of detail) {
      assert.ok(
        !/slim=1/.test(url),
        `详情接口要走完整载荷，不许瘦身：${url}`
      );
    }
  });

  await t.test("6. 前端不许去读被丢掉的那些字段", () => {
    // 列表读 content.review / content.source_group 就说明瘦错了。
    assert.ok(
      !/c\.review|content\.review|content\.source_group/.test(html),
      "列表代码不许读 content.review / content.source_group（它们已被丢掉）"
    );
  });
});
