/*
 * 指出一道题。
 *
 * 同一本教材里题号会从小节重新开始，「第 1 题」在书上有上百处，
 * 光报题号等于没报。所以定位句永远是三样东西：原卷页码 + 章节名 + 题号。
 *
 * 页码是唯一的硬锚点：章节名会重号（「练习」「思考」全书几十处），
 * 题号也会重号，只有「书上第几页」认得出来 —— 所以页码不再只在
 * 「没有章节名时」才报，有章节名也得报。
 *
 * 批量通过的结果面板、题库卡片墙、回收站、找题共用这一套说法，别各写各的。
 */
(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBQuestionLocator = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // 章节名的规矩，跟后端 library.section_label 是同一条（两边拿同一份
  // fixture 断言）。
  //
  // 甲、短标签：短到 12 字、不带冒号逗号。教材的「习题 6.1」「均值不等式」认。
  // 乙、教材的小节名：「5.5.2 简单的三角恒等变换」这种 —— 前面是多级点号编号
  //     （4.5.3），后面是中文标题，标题里没有句读。教材的小节名本来就长，
  //     只按 12 字卡会把「5.5.2 简单的三角恒等变换」整段丢掉（1.26.44 修的）。
  // 丙、一律不显示：试卷的 section 是一整段大题说明（「一、选择题：本大题共8
  //     小题，每小题5分…」）、数字噪音（「1.178 097 245.」）、一整句话
  //     （「1. 观察实际情景，发现和提出问题」）。它们放上去是噪音，进了搜索
  //     还会把不相干的卷子都搜出来。
  const SECTION_LABEL_MAX = 12;
  const SECTION_LABEL_MAX_NUMBERED = 20;
  const SECTION_LABEL_STOP = /[：:，,。;；]/;
  const SECTION_LABEL_TITLE_STOP = /[：:，,。;；、！!？?…—～~]/;
  const SECTION_NUMBERED = /^(\d+(?:\.\d+)+)\s*(.*)$/;
  const SECTION_HAS_HAN = /[\u4e00-\u9fff]/;

  function sectionLabel(section) {
    const text = String(section == null ? "" : section).trim();
    if (!text) return "";
    // 断成两行的不是章节名：印在卡面上会断行，进了搜索也不该当标签。
    if (/[\r\n]/.test(text)) return "";
    if (text.length <= SECTION_LABEL_MAX && !SECTION_LABEL_STOP.test(text)) return text;
    // 教材的小节名：编号 + 中文标题，且没有句读。
    const matched = SECTION_NUMBERED.exec(text);
    if (matched) {
      const title = matched[2].trim();
      if (
        text.length <= SECTION_LABEL_MAX_NUMBERED &&
        title &&
        SECTION_HAS_HAN.test(title) &&
        !SECTION_LABEL_TITLE_STOP.test(title)
      ) {
        return text;
      }
    }
    return "";
  }

  // 真章节名：题组标题里带「章」的才算（「章节01 · 公式法」「第2章 函数」）。
  // 「第 3 组」「第 12 页 · 第 3 组」是位置标签，印出来只会让人以为那是章节。
  function isChapterName(title) {
    return String(title == null ? "" : title).includes("章");
  }

  // 题组名有三种来法：接口给标题字符串、给标题对象、或者放在题库快照里。
  function groupTitleOf(item) {
    if (!item) return "";
    const direct = String(item.group_title == null ? "" : item.group_title).trim();
    if (direct) return direct;
    const group = item.group;
    if (group && typeof group === "object") return String(group.title == null ? "" : group.title).trim();
    if (typeof group === "string") return group.trim();
    const source = item.content && item.content.source_group;
    if (source && typeof source === "object") return String(source.title == null ? "" : source.title).trim();
    return "";
  }

  // 章节名也有两种来法：接口直接给，或放在题库快照的 content.section 里。
  function sectionOf(item) {
    if (!item) return "";
    const direct = String(item.section == null ? "" : item.section).trim();
    if (direct) return direct;
    return String((item.content && item.content.section) || "").trim();
  }

  // 章节名：先认真章节名，其次短章节名，其余一律不算。
  function scopeOf(item) {
    if (!item) return "";
    const given = String(item.scope == null ? "" : item.scope).trim();
    if (given) return given;
    const group = groupTitleOf(item);
    if (isChapterName(group)) return group;
    return sectionLabel(sectionOf(item));
  }

  // 教材里的例题就叫「例 1」，不是「第 1 题」——和题卡顶上写的一样。
  // 没有印刷题号的栏目题给空串，由调用方决定印「栏目题」还是别的。
  function numberText(item) {
    const number = item && item.number;
    if (number == null || number === "") return "";
    return item.source_kind === "example" ? `例 ${number}` : `第 ${number} 题`;
  }

  // 这道题在原卷上的所有页码：去重、从小到大。
  function pagesOf(item) {
    const pages = new Set();
    const collect = (list) => {
      for (const region of list || []) {
        const index = region && typeof region === "object" ? Number(region.page_idx) : NaN;
        if (Number.isFinite(index)) pages.add(index + 1);
      }
    };
    collect(item && item.regions);
    collect(item && item.content && item.content.sources);
    return Array.from(pages).sort((a, b) => a - b);
  }

  // 这道题在原卷的第几页：书上第一次出现的那一页。
  // 接口给了 page 就用它，没给就从原图位置里取最小的那页（和后端同一条规矩）。
  function pageOf(item) {
    const direct = Number(item && item.page);
    if (Number.isFinite(direct) && direct > 0) return direct;
    const pages = pagesOf(item);
    return pages.length ? pages[0] : null;
  }

  // 完整定位句：第 X 页 · 章节名 · 第 N 题。
  function locate(item) {
    const parts = [];
    const page = pageOf(item);
    if (page) parts.push(`第 ${page} 页`);
    const scope = scopeOf(item);
    if (scope) parts.push(scope);
    const number = numberText(item);
    if (number) parts.push(number);
    return parts.join(" · ") || "栏目题";
  }

  // 身份：这道题是哪一题。题库卡片墙的大标题用这个。
  function identityOf(item) {
    return numberText(item) || scopeOf(item) || "栏目题";
  }

  // 位置：这本书的第几页、哪一节。题库卡片墙的小字用这个。
  // 没有题号时章节名就是身份了，不在这里再说一遍。
  function positionOf(item) {
    const parts = [];
    const page = pageOf(item);
    if (page) parts.push(`第 ${page} 页`);
    const scope = scopeOf(item);
    if (scope && numberText(item)) parts.push(scope);
    return parts.join(" · ");
  }

  // 题干开头：认题时扫一眼就能对上，多行文字压成一行。
  function snippet(stem, maxLength) {
    const limit = maxLength || 60;
    const text = String(stem == null ? "" : stem).replace(/\s+/g, " ").trim();
    if (!text) return "";
    return text.length > limit ? `${text.slice(0, limit)}…` : text;
  }

  return {
    locate,
    identityOf,
    positionOf,
    scopeOf,
    numberText,
    pageOf,
    pagesOf,
    sectionLabel,
    snippet,
  };
});
