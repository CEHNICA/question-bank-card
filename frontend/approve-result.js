/*
 * 批量通过的结果面板（1.26.41）。
 *
 * 一卷几百道题，逐条平铺会把老师淹没。这里改成：先给总账，再把过不去的题
 * 按原因归类，每一类折叠起来，展开才列出是哪几道。
 *
 * 定位交给 QBQuestionLocator —— 同一本教材里题号会重号，只写「第 1 题」
 * 等于没写。
 */
(function (root, factory) {
  const value = factory(root);
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBApproveResult = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  const locator = root.QBQuestionLocator
    || (typeof require === "function" ? require("./question-locator.js") : null);

  function makeEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  // 原因常常本身是一整句（带句号）。句尾的标点只留一个，
  // 不然「卡在同一件事上：……。。」这种双句号会直接印在面板上。
  function trimEnd(text) {
    return String(text == null ? "" : text).replace(/[\s\u3002\uff01\uff1f\uff1b\uff0c\u3001,.;!?]+$/u, "");
  }

  // 归类看服务端给的类别；没有就退回第一条提醒，再没有就用原因本身。
  function categoryOf(item) {
    const category = String((item && item.category) || "").trim();
    if (category) return category;
    const flags = Array.isArray(item && item.flags) ? item.flags.map(String).filter(Boolean) : [];
    if (flags.length) return flags[0];
    return String((item && item.reason) || "").trim() || "其它原因";
  }

  // 按原因分组，多的排前面 —— 老师先看最大的那一类。
  function groupByReason(skipped) {
    const groups = new Map();
    (skipped || []).forEach((item) => {
      const reason = categoryOf(item);
      if (!groups.has(reason)) groups.set(reason, { reason, items: [] });
      groups.get(reason).items.push(item);
    });
    return Array.from(groups.values()).sort((a, b) => b.items.length - a.items.length);
  }

  function render(container, payload) {
    const approved = Number(payload && payload.approved) || 0;
    const problems = (payload && payload.problems) || [];
    const skipped = (payload && payload.skipped) || [];
    const groups = groupByReason(skipped);
    container.replaceChildren();

    const head = makeEl("p", "approve-result-head");
    head.append(makeEl("strong", "", `已入库 ${approved} 道`));
    if (problems.length) {
      head.append(makeEl("span", "approve-result-sub", `另有 ${problems.length} 道保存未完成，请重试`));
    }
    container.append(head);

    if (!skipped.length && !problems.length) {
      container.append(makeEl("p", "approve-result-ok", "这一卷剩下的题都已经入库了。"));
      return { groups, approved, skipped: 0 };
    }

    if (skipped.length) {
      container.append(
        makeEl(
          "p",
          "approve-result-summary",
          `还有 ${skipped.length} 道没能入库，分成 ${groups.length} 类原因。每类可以展开，看清是哪几道。`
        )
      );
      if (approved === 0 && groups.length) {
        const top = groups[0];
        container.append(
          makeEl(
            "p",
            "approve-result-lead",
            `这一卷暂时没有题能直接入库。最多的一类是 ${top.items.length} 道，卡在同一件事上：${trimEnd(top.reason)}。`
          )
        );
      }
    }

    groups.forEach((group) => {
      const details = makeEl("details", "approve-result-group");
      details.append(makeEl("summary", "approve-result-group-head", `${trimEnd(group.reason)}（${group.items.length} 道）`));
      const list = makeEl("ul", "approve-result-items");
      group.items.forEach((item) => {
        const row = makeEl("li", "approve-result-item");
        row.append(makeEl("span", "approve-result-where", locator.locateWithPage(item)));
        const text = locator.snippet(item.stem, 60);
        if (text) row.append(makeEl("span", "approve-result-stem", text));
        // 分组标题已经说了卡在哪一类，这里只补这道题自己的那一条。
        const detail = String((item && item.detail) || "").trim();
        if (detail) row.append(makeEl("span", "approve-result-detail", detail));
        list.append(row);
      });
      details.append(list);
      container.append(details);
    });

    if (problems.length) {
      const list = makeEl("ul", "approve-result-problems");
      problems.forEach((text) => list.append(makeEl("li", "approve-result-problem", String(text))));
      container.append(list);
    }

    return { groups, approved, skipped: skipped.length };
  }

  return { render, groupByReason, categoryOf };
});
