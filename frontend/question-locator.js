/*
 * 指出一道题。
 *
 * 同一本教材里题号会从小节重新开始，「第 1 题」在书上有上百处，
 * 光报题号等于没报。这里把「章节/小节 + 题号」凑成一句能对回原书的话；
 * 找不到章节名时，用原卷页码顶上。
 *
 * 批量通过的结果面板、回收站、找题共用这一套说法，别各写各的。
 */
(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBQuestionLocator = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // 章节名有三种来法：接口给标题对象、给字符串、或者根本没给。
  function scopeOf(item) {
    if (!item) return "";
    const direct = String(item.group_title || "").trim();
    if (direct) return direct;
    const group = item.group;
    if (group && typeof group === "object") return String(group.title || "").trim();
    if (typeof group === "string") return group.trim();
    return String(item.section || "").trim();
  }

  // 教材里的例题就叫「例 1」，不是「第 1 题」——和题卡顶上写的一样。
  function numberText(item) {
    const number = item && item.number;
    if (number == null || number === "") return "这道题";
    return item.source_kind === "example" ? `例 ${number}` : `第 ${number} 题`;
  }

  // 这道题在原卷上的页码。
  function pagesOf(item) {
    const regions = (item && item.regions) || [];
    const pages = new Set();
    for (const region of regions) {
      const index = region && typeof region === "object" ? Number(region.page_idx) : NaN;
      if (Number.isFinite(index)) pages.add(index + 1);
    }
    return Array.from(pages).sort((a, b) => a - b);
  }

  // 主定位：章节/小节 + 题号，和题卡顶上写的对得上，老师一看就知道是哪张卡。
  function locate(item) {
    const scope = scopeOf(item);
    const number = numberText(item);
    return scope ? `${scope} · ${number}` : number;
  }

  // 没有章节名时题号会重号，只能靠原卷页码认。
  function locateWithPage(item) {
    if (scopeOf(item)) return locate(item);
    const page = Number(item && item.page);
    if (Number.isFinite(page) && page > 0) return `第 ${page} 页 · ${numberText(item)}`;
    return locate(item);
  }

  // 题干开头：认题时扫一眼就能对上，多行文字压成一行。
  function snippet(stem, maxLength) {
    const limit = maxLength || 60;
    const text = String(stem == null ? "" : stem).replace(/\s+/g, " ").trim();
    if (!text) return "";
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
  }

  return { locate, locateWithPage, scopeOf, numberText, pagesOf, snippet };
});
