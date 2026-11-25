"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const VirtualList = require("./eagle-virtual-list.js");

function createFakeDom(viewportHeight = 720, heightFn = () => 188) {
  function makeEl(tag, ownerDoc) {
    const el = {
      tagName: String(tag || "DIV").toUpperCase(),
      id: "",
      className: "",
      dataset: {},
      attributes: {},
      style: { cssText: "", height: "0px", marginTop: "", marginBottom: "" },
      children: [],
      parentNode: null,
      ownerDocument: ownerDoc,
      scrollTop: 0,
      clientHeight: viewportHeight,
      _innerHTML: "",
      setAttribute(k, v) { this.attributes[k] = String(v); },
      get isConnected() {
        let cur = this;
        while (cur.parentNode) cur = cur.parentNode;
        return cur === container;
      },
      get firstElementChild() { return this.children[0] || null; },
      get nextElementSibling() {
        if (!this.parentNode) return null;
        const idx = this.parentNode.children.indexOf(this);
        return idx >= 0 && idx + 1 < this.parentNode.children.length ? this.parentNode.children[idx + 1] : null;
      },
      get offsetHeight() {
        return heightFn(this);
      },
      getBoundingClientRect() {
        return { top: 0, height: heightFn(this) };
      },
      appendChild(child) {
        if (child.parentNode) child.parentNode.removeChild(child);
        child.parentNode = this;
        this.children.push(child);
        return child;
      },
      insertBefore(child, ref) {
        if (!ref) return this.appendChild(child);
        if (child.parentNode) child.parentNode.removeChild(child);
        const idx = this.children.indexOf(ref);
        child.parentNode = this;
        if (idx < 0) this.children.push(child);
        else this.children.splice(idx, 0, child);
        return child;
      },
      removeChild(child) {
        const idx = this.children.indexOf(child);
        if (idx >= 0) {
          this.children.splice(idx, 1);
          child.parentNode = null;
        }
        return child;
      },
      replaceChild(nextEl, oldEl) {
        const idx = this.children.indexOf(oldEl);
        if (idx >= 0) {
          if (nextEl.parentNode) nextEl.parentNode.removeChild(nextEl);
          oldEl.parentNode = null;
          nextEl.parentNode = this;
          this.children[idx] = nextEl;
        }
        return oldEl;
      },
      querySelector(sel) {
        const targets = sel.split(",").map((s) => s.trim());
        const match = (node) => {
          for (const t of targets) {
            if (t.startsWith("#") && node.id === t.slice(1)) return true;
            if (t.startsWith(".") && String(node.className || "").split(/\s+/).includes(t.slice(1))) return true;
          }
          return false;
        };
        const dfs = (root) => {
          for (const ch of root.children) {
            if (match(ch)) return ch;
            const found = dfs(ch);
            if (found) return found;
          }
          return null;
        };
        return dfs(this);
      }
    };
    Object.defineProperty(el, "innerHTML", {
      get() { return this._innerHTML; },
      set(val) {
        this._innerHTML = String(val || "");
        this.children = [];
        // Parse lightweight card wrapper `<div id="qcard_X" ...><div class="eagle-q-stem">...</div><div id="analysis_box_X">...</div></div>`
        const cardMatch = this._innerHTML.match(/^<div\s+id="(qcard_[^"]+)"[^>]*>([\s\S]*)<\/div>$/);
        if (cardMatch) {
          const card = makeEl("div", ownerDoc);
          card.id = cardMatch[1];
          const stem = makeEl("div", ownerDoc);
          stem.className = "eagle-q-stem";
          const stemM = cardMatch[2].match(/<div class="eagle-q-stem">([\s\S]*?)<\/div>/);
          stem._innerHTML = stemM ? stemM[1] : "";
          const ana = makeEl("div", ownerDoc);
          ana.id = card.id.replace("qcard_", "analysis_box_");
          const anaM = cardMatch[2].match(/<div id="analysis_box_[^"]+">([\s\S]*?)<\/div>/);
          ana._innerHTML = anaM ? anaM[1] : "";
          card.appendChild(stem);
          card.appendChild(ana);
          this.appendChild(card);
        }
      }
    });
    return el;
  }

  const doc = { createElement: (tag) => makeEl(tag, doc) };
  const container = makeEl("div", doc);
  container.id = "questionListContainer";
  const sentinel = makeEl("div", doc);
  sentinel.id = "chunkSentinel";
  container.appendChild(sentinel);
  return { doc, container, sentinel };
}

function makeQuestions(count) {
  const list = [];
  for (let i = 1; i <= count; i++) {
    list.push({
      id: `pub-${i}`,
      version: 1,
      questionNumber: i,
      rawStem: `第 ${i} 题：$f(x)=x^{${i}}$`,
      content: `<p>第 ${i} 题：$f(x)=x^{${i}}$</p>`,
      rawAnswer: `${i}`,
      rawAnalysis: `解析 $x=${i}$`
    });
  }
  return list;
}

test("Eagle 万题连续虚拟滚动与公式缓存引擎", async (t) => {
  await t.test("1. 万题规模下 DOM 挂载数始终有界，滚动复用保留重叠节点且二次滚回零重复 KaTeX 排版", () => {
    const { container } = createFakeDom(720, () => 188);
    const items = makeQuestions(10000);
    let katexCalls = 0;
    const positions = [];

    const ctrl = VirtualList.createVirtualListController({
      getContainer: () => container,
      isAnalysisExpanded: () => false,
      renderItemHtml: (q) =>
        `<div id="qcard_${q.id}"><div class="eagle-q-stem">${q.content}</div><div id="analysis_box_${q.id}">${q.rawAnalysis}</div></div>`,
      triggerKaTeX: (cardEl) => {
        katexCalls += 1;
        const stem = cardEl.querySelector(".eagle-q-stem");
        if (stem) stem._innerHTML = `<span class="katex">${stem._innerHTML}</span>`;
      },
      onPositionChange: (cur, total) => positions.push([cur, total])
    });

    ctrl.setItems(items, "card", { resetScroll: true, forceRemount: true });
    assert.ok(ctrl.mountedCount > 0 && ctrl.mountedCount <= 22, `首屏挂载数应控制在二十张以内，实际: ${ctrl.mountedCount}`);
    assert.equal(ctrl.currentOneBased, 1);
    const firstScreenCalls = katexCalls;
    assert.equal(firstScreenCalls, ctrl.mountedCount, "首次出现的卡片各执行一次 KaTeX");

    // 轻微滚动 200px：仍在窗口内的节点实例必须原样保留（不重建）
    const host = container.querySelector("#virtualCardsHost");
    const keptCardRef = host.querySelector("#qcard_pub-3");
    assert.ok(keptCardRef);
    container.scrollTop = 200;
    ctrl.syncWindow(false);
    assert.equal(host.querySelector("#qcard_pub-3"), keptCardRef, "可视重叠区内的 DOM 节点应原样保留不重建");

    // 拖滚动条秒跳到第 6789 题：顶部旧卡全部卸载回收，挂载数仍然有界
    const jumped = ctrl.scrollToOneBasedIndex(6789);
    assert.equal(jumped.id, "pub-6789");
    assert.equal(ctrl.currentOneBased, 6789);
    assert.equal(host.querySelector("#qcard_pub-1"), null, "滚远的顶部卡片应从 DOM 回收");
    assert.ok(host.querySelector("#qcard_pub-6789"), "目标第 6789 题应立即挂载");
    assert.ok(ctrl.mountedCount <= 24, `深处挂载数仍应有界，实际: ${ctrl.mountedCount}`);

    // 滚回第 1 题：全部命中公式 HTML 缓存，triggerKaTeX 零新增调用！
    const callsBeforeReturn = katexCalls;
    ctrl.scrollToOneBasedIndex(1);
    assert.equal(katexCalls, callsBeforeReturn, "滚回已看过的题目应直接命中公式 HTML 缓存，零重复 KaTeX 排版");
    assert.match(host.querySelector("#qcard_pub-1").querySelector(".eagle-q-stem").innerHTML, /class="katex"/);

    // 单题改字后精准失效该题缓存，重刷单卡只重排这 1 道题
    items[0].version = 2;
    items[0].content = "<p>改版后：$g(x)=2x$</p>";
    assert.equal(ctrl.refreshSingleQuestion("pub-1"), true);
    assert.equal(katexCalls, callsBeforeReturn + 1, "单题刷新仅重排被修改的那一道题");
  });

  await t.test("2. 向上滚入未实测高卡片时自动锚定补偿 scrollTop，防视口跳动", () => {
    let dynamicTall = false;
    const { container } = createFakeDom(600, (el) => {
      if (dynamicTall && (el.id === "qcard_pub-1" || el.id === "qcard_pub-2")) return 340;
      return 188;
    });
    const items = makeQuestions(100);
    const ctrl = VirtualList.createVirtualListController({
      getContainer: () => container,
      isAnalysisExpanded: () => false,
      renderItemHtml: (q) =>
        `<div id="qcard_${q.id}"><div class="eagle-q-stem">${q.content}</div><div id="analysis_box_${q.id}"></div></div>`,
      triggerKaTeX: () => {}
    });

    ctrl.setItems(items, "card", { resetScroll: true, forceRemount: true });
    ctrl.scrollToOneBasedIndex(30);
    // 模拟回滚到第 4 题附近时，上方的第 1、2 题图片加载后实测高度从 188 涨到 340（共 +304px）
    dynamicTall = true;
    ctrl.scrollToOneBasedIndex(4);
    assert.ok(container.scrollTop >= 340 * 2 + 188, `上方卡片实测变高时应自动补偿 scrollTop，实际: ${container.scrollTop}`);
  });

  await t.test("3. /eagle 页面集成契约：全量全选；顶栏不再常驻题序/总数", () => {
    const eagleHtml = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");
    assert.match(eagleHtml, /<script src="\/eagle-virtual-list\.js"><\/script>/);
    assert.match(eagleHtml, /id="matchedQuestionCount"/);
    assert.doesNotMatch(eagleHtml, /id="eaglePositionIndicator"/);
    assert.doesNotMatch(eagleHtml, /id="btnMatchedCountJump"/);
    assert.doesNotMatch(eagleHtml, /当前第/);
    assert.match(eagleHtml, /function jumpToQuestionOrder\(/);
    assert.match(
      eagleHtml,
      /if \(checked\) filteredQuestions\.forEach\(q => selectedQuestionIds\.add\(q\.id\)\);/,
      "全选必须覆盖当前筛选出的全部题目，不限于视口内挂载的十几道"
    );
  });
});
