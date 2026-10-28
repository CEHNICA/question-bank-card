"use strict";
// 1.26.32：导出失败要说人话。
//
// 老师转述的用户反馈是「Word 未能导出，请稍后重试」。查下来这句不是后台给的
// 结论，是网页上的兜底话：后台回了非 2xx、正文又不是带 error 的 JSON 时，
// 前端只能这么说。
//
// 这一版量的是真跑出来的行为：
//   1. 后台给了原因 → 原话照传到屏幕上（诊断号也就跟着出来了）；
//   2. 后台没给原因 → 兜底话必须说清「后台没给出原因」，而且不能再是
//      「请稍后重试」—— 原因若是永久性的，重试一万次也没用。
const assert = require("node:assert/strict");
global.QBRender = require("./qb-render.js");
global.katex = require("./vendor/katex/katex.min.js");
const Export = require("./exam-export.js");

const item = {
  id: "publication-1",
  number: 3,
  origin: "",
  content: { stem: "已知 $x>0$，求 $x^2$ 的最小值。", options: {}, answer: "1", analysis: "原卷解析" },
  solution_revision: "origin",
};

async function withFetch(fake, action) {
  const original = globalThis.fetch;
  globalThis.fetch = fake;
  try {
    return await action();
  } finally {
    globalThis.fetch = original;
  }
}

function response(status, body, mime = "text/html") {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => (name === "Content-Type" ? mime : null) },
    json: async () => JSON.parse(body),
    blob: async () => { throw new Error("不该走到下载这一步"); },
  };
}

(async function main() {
  // ---- 一、后台给了原因 → 原话照传（诊断号也就跟着到屏幕上）--------------
  {
    const reason = "Word导出没完成（诊断号 EX-1A2B3C4D5E6F）：你的题和原卷都没动。可以再试一次；还是这样就把这个号发给开发者。";
    await withFetch(async (url) => {
      if (String(url).includes("export-preferences")) return response(200, "{}", "application/json");
      return response(500, JSON.stringify({ error: reason }), "application/json");
    }, async () => {
      await assert.rejects(
        () => Export.download([item], { title: "测试", format: "docx" }),
        (error) => {
          assert.equal(error.message, reason, "后台给的话要原样传到屏幕上");
          assert.match(error.message, /EX-1A2B3C4D5E6F/, "诊断号要出现在屏幕上，老师才能转述");
          return true;
        },
      );
    });
  }

  // ---- 二、后台没给原因 → 兜底话要说清，且不能再叫「稍后重试」--------------
  {
    await withFetch(async (url) => {
      if (String(url).includes("export-preferences")) return response(200, "{}", "application/json");
      return response(500, "<html><body>Server Error</body></html>", "text/html");
    }, async () => {
      await assert.rejects(
        () => Export.download([item], { title: "测试", format: "docx" }),
        (error) => {
          assert.match(error.message, /后台没给出原因/, "要说明白是后台没给原因");
          assert.doesNotMatch(error.message, /请稍后重试/, "不能再说「请稍后重试」——那只会让人一直重试");
          assert.match(error.message, /再试一次/, "还是要给一条能走的路");
          return true;
        },
      );
    });
  }

  // ---- 三、PDF 那条也一样 -------------------------------------------------
  {
    await withFetch(async (url) => {
      if (String(url).includes("export-preferences")) return response(200, "{}", "application/json");
      return response(500, "<html>Server Error</html>", "text/html");
    }, async () => {
      await assert.rejects(
        () => Export.download([item], { title: "测试", format: "pdf" }),
        (error) => {
          assert.match(error.message, /^PDF/, "要说得清是 PDF");
          assert.doesNotMatch(error.message, /请稍后重试/);
          return true;
        },
      );
    });
  }

  console.log("An export failure says what happened: OK");
})().catch((error) => { console.error(error); process.exit(1); });
