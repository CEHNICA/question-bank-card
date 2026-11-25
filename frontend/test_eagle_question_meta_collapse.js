"use strict";
// 考题属性应对齐 Eagle「基本信息」：整块可折叠，并记住本机偏好。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { describe, it } = require("node:test");
const vm = require("node:vm");

const eagleHtml = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

describe("Eagle 考题属性折叠（对齐基本信息）", () => {
  it("1. 标记与可点击标题齐全", () => {
    assert.ok(eagleHtml.includes('id="questionMetaCard"'), "需有 #questionMetaCard");
    assert.ok(eagleHtml.includes('id="questionMetaCardBody"'), "内容需包在 #questionMetaCardBody");
    assert.ok(eagleHtml.includes('id="btnToggleQuestionMetaCard"'), "需有折叠按钮");
    assert.ok(eagleHtml.includes("eagle-inspector-section-toggle"), "需有区块标题样式钩子");
    assert.ok(eagleHtml.includes("function toggleQuestionMetaCard"), "需实现 toggleQuestionMetaCard");
    assert.ok(eagleHtml.includes("function syncQuestionMetaCardUI"), "需实现 syncQuestionMetaCardUI");
    assert.match(
      eagleHtml,
      /#questionMetaCard\.is-collapsed\s+#questionMetaCardBody\s*\{[^}]*display:\s*none/s,
      "折叠态要隐藏内容区"
    );
  });

  it("2. 点击切换并写入 localStorage", () => {
    const store = { eagle_question_meta_open: "1" };
    const card = {
      classList: {
        _set: new Set(),
        toggle(name, on) {
          if (on) this._set.add(name);
          else this._set.delete(name);
        },
        contains(name) {
          return this._set.has(name);
        },
      },
    };
    const btn = {
      attrs: {},
      title: "",
      setAttribute(k, v) {
        this.attrs[k] = v;
      },
      removeAttribute(k) {
        delete this.attrs[k];
        if (k === "title") this.title = "";
      },
    };
    const body = {};
    const sandbox = {
      localStorage: {
        getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
        setItem: (k, v) => {
          store[k] = String(v);
        },
      },
      setEagleTip(el, title, body) {
        if (!el) return;
        if (title) el.setAttribute("data-tip-title", title);
        if (body) el.setAttribute("data-tip-body", body);
        el.removeAttribute("title");
      },
      document: {
        getElementById(id) {
          if (id === "questionMetaCard") return card;
          if (id === "btnToggleQuestionMetaCard") return btn;
          if (id === "questionMetaCardBody") return body;
          return null;
        },
      },
    };

    const block = eagleHtml.match(
      /\/\/ 考题属性折叠：[\s\S]*?function toggleQuestionMetaCard\(event\) \{[\s\S]*?\n {4}\}/
    );
    assert.ok(block, "应能抽出折叠逻辑");
    vm.runInNewContext(
      `${block[0]}\nthis.toggleQuestionMetaCard = toggleQuestionMetaCard;\nthis.syncQuestionMetaCardUI = syncQuestionMetaCardUI;\nthis.questionMetaCardOpen = questionMetaCardOpen;`,
      sandbox
    );

    assert.equal(sandbox.questionMetaCardOpen, true);
    sandbox.syncQuestionMetaCardUI();
    assert.equal(card.classList.contains("is-collapsed"), false);
    assert.equal(btn.attrs["aria-expanded"], "true");

    sandbox.toggleQuestionMetaCard();
    assert.equal(store.eagle_question_meta_open, "0");
    assert.equal(card.classList.contains("is-collapsed"), true);
    assert.equal(btn.attrs["aria-expanded"], "false");

    sandbox.toggleQuestionMetaCard();
    assert.equal(store.eagle_question_meta_open, "1");
    assert.equal(card.classList.contains("is-collapsed"), false);
  });
});
