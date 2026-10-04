/* The current task's keys first; the complete reference stays on demand. */
(function (root, factory) {
  "use strict";
  const value = factory(root);
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBShortcutHelp = value;
})(typeof window === "object" ? window : globalThis, function (root) {
  "use strict";
  const TITLES = { review: "录入审核", crop: "切题与配图", library: "题库看题", answers: "答案编辑", print: "组卷导出" };
  const row = (label, ...keys) => ({ label, keys });
  function reference(scene = "review", options = {}) {
    if (!Object.hasOwn(TITLES, scene)) scene = "review";
    const note = "输入时不触发导航和画框快捷键；当前编辑器的保存组合键仍可使用。";
    if (scene === "review" && options.comparison) return { scene, title: "放大对照", note,
      primary: [row("上一题 / 下一题", "←", "→"), row("通过并继续", "Enter"), row("返回题卡", "Esc")],
      more: [row("下一题 / 上一题", "N", "J", "K"), row("撤销当前题通过", "U"), row("改字", "E"), row("调整范围 / 配图", "R", "F"),
        row("返回题卡", "Space"), row("全题 / 适宽", "0", "W"), row("缩小 / 放大原卷", "−", "+"), row("鼠标所在位置缩放", "Ctrl+滚轮"), row("打开快捷键说明", "?")],
      extra: "Enter 不撤销已通过的题；继续看下一题。撤销使用 U 或返回题卡取消勾选。" };
    if (scene === "review") return { scene, title: TITLES[scene], note,
      primary: [row("下一题", "N"), row("通过并继续", "Enter"), row("上一题", "K")],
      more: [row("下一张 / 上一张题卡", "J", "K"), row("放大对照原卷 / 返回", "Space"),
        row("撤销当前题通过", "U"), row("改字", "E"), row("审核改字中保存", "Ctrl+Enter"), row("取消改字", "Esc"),
        row("调整范围 / 配图", "R", "F"), row("展开 / 收起已通过题", "O"), row("已通过题全部展开 / 收起", "Shift+O"),
        row("题卡放大镜开 / 关", "L"), row("专注开 / 关", "Z"), row("全屏审核开 / 关", "Q"), row("退出全屏审核 / 退出选择", "Esc"),
        row("全部 / 需要核查 / 已通过", "1", "2", "3"), ...(options.aiFilter ? [row("AI 通过筛选", "4")] : []),
        row("批量选择：增减单题 / 连续范围", "Ctrl+点击", "Shift+点击"), row("已选题移到回收站", "Delete"),
        row("对照原卷：全题 / 适宽", "0", "W"), row("对照原卷：缩小 / 放大", "−", "+"),
        row("鼠标所在位置缩放", "Ctrl+滚轮"), row("对照窗口上一题 / 下一题", "←", "→"), row("打开快捷键说明", "?")],
      extra: "N 按当前筛选顺序逐题前进。Enter 不撤销已通过的题；撤销使用 U 或取消勾选。通过后自动入库。" };
    if (scene === "crop") {
      const mode = options.mode || "new", editing = mode !== "view";
      const primary = mode === "new" ? [row("保存下一题", "S"), row("完成切题", "Ctrl+S"), row("取消新框 / 返回", "Esc")]
        : editing ? [...(!options.practiceRead ? [row(mode === "read" ? "识读这一块" : "保存当前修改", "Ctrl+Enter")] : []), row("移动选中框", "方向键"), row("取消新框 / 返回", "Esc")]
          : [row("上一页 / 下一页", "PageUp", "PageDown"), row("适页 / 适宽", "0", "W"), row("返回", "Esc")];
      return { scene, title: mode === "figures" ? "配图" : mode === "regions" ? "调整题目范围" : mode === "read" ? "框选识读" : mode === "view" ? "查看原卷" : "手工切题", note: "输入题号、页码或文字时，切题快捷键暂不生效；完成切题组合键在工具栏和教学提示中也可使用。",
        primary, more: [...(mode === "new" ? [row("保存下一题（兼容）", "Enter"), row("完成切题（兼容）", "Ctrl+Enter")] : []),
          ...(editing ? [row("删除选中框", "Delete", "Backspace"), row("先选中框，再移动 / 大步移动", "方向键", "Shift+方向键")] : []),
          ...(["new", "regions"].includes(mode) ? [row("撤销范围改动 / 重做", "Ctrl+Z", "Ctrl+Shift+Z")] : []),
          row("上一页 / 下一页", "PageUp", "PageDown"), row("适页 / 适宽", "0", "W"), row("缩小 / 放大", "−", "+"), row("画布缩放", "Ctrl+滚轮"),
          ...(editing ? [row("平移画布", "Space+拖动", "中键拖动")] : [row("平移画布", "左键拖动")]),
          ...(mode === "figures" ? [row("归属菜单：题干 / 选项 / 无关 / 接上一图", "S", "A–E", "X", "J")] : [])],
        extra: mode === "new" ? "S 保存下一题需先点原卷画布；Ctrl+S 完成切题可在窗口工具栏和教学提示中使用。保存下一题只保存范围，完成切题后识读已保存题目。" : options.practiceRead ? "示例仅练习画框，不提交识读。" : mode === "view" ? "查看原卷不会改动题目。" : "先点选要调整的框；保存时会保留未保存提醒和核查要求。" };
    }
    if (scene === "library") return { scene, title: TITLES[scene], note,
      primary: options.fullScreen ? [row("上一题 / 下一题", "←", "→"), row("返回题库", "Esc"), row("看题缩放", "Ctrl+滚轮")]
        : [row("搜索题目", "/"), row("全屏上一题 / 下一题", "←", "→"), row("全屏返回题库", "Esc")],
      more: options.fullScreen ? [] : [row("全屏看题缩放", "Ctrl+滚轮"), row("打开快捷键说明", "?")],
      extra: "左右键仅在全屏看题中切题，按打开时的筛选或试题篮顺序浏览。宽公式正在横向滚动时，左右键保留给公式。" };
    if (scene === "answers") return { scene, title: TITLES[scene], note,
      primary: [row("保存答案解析", "Ctrl+S"), row("返回 / 先取消未完成的框", "Esc")],
      more: [row("粘贴解析图片", "Ctrl+V"), row("文字框中换行", "Enter")],
      extra: "保存组合键在答案输入框内生效。退出有改动的答案前，会提醒保存或丢弃。" };
    return { scene, title: TITLES[scene], note,
      primary: [row("返回题库", "Esc")], more: [row("在设置、题目调整和导出按钮间移动", "Tab", "Shift+Tab"), row("执行当前按钮", "Enter")],
      extra: "导出使用窗口中的导出按钮；Esc 返回时会保留试题篮，草稿需单独保存。" };
  }
  function isEditingTarget(target) {
    return Boolean(target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]') || target?.isContentEditable);
  }
  function ordinaryKeyBlocked(event, { allowShift = false } = {}) {
    return Boolean(event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey
      || (!allowShift && event.shiftKey) || isEditingTarget(event.target));
  }
  function element(doc, tag, cls, text) {
    const value = doc.createElement(tag); if (cls) value.className = cls; if (text !== undefined) value.textContent = text; return value;
  }

  // Optional operating guidance is a preference of its own. It deliberately
  // does not share a key with the crop banner or the review onboarding, so
  // turning one of those off can never silently switch off another, and
  // restoring the guidance can never look like a settings change.
  const DISMISSED_PREF = "qb-hint-dismissed";
  const CATEGORY_LABELS = {
    review: "录入审核快捷键", "review-compare": "放大对照快捷键", crop: "切题与配图快捷键",
    library: "题库与全屏看题提示", answers: "答案编辑快捷键", print: "组卷导出快捷键",
    "reading-overflow": "公式横向查看提示", "preview-note": "题卡全屏看题提示",
    "editor-position": "改字预览位置说明", "editor-shortcut": "改字保存快捷键说明"
  };
  function store() { try { return root.localStorage || null; } catch { return null; } }
  // Not every host that loads this file has a full event target (the test
  // harnesses do not), so announcing a change is always best effort.
  function fire(name, detail) {
    try {
      if (typeof root.dispatchEvent === "function" && typeof root.CustomEvent === "function") {
        root.dispatchEvent(new root.CustomEvent(name, { detail }));
      }
    } catch { /* no event target here */ }
  }
  function readDismissed() {
    const box = store(); if (!box) return [];
    try {
      const parsed = JSON.parse(box.getItem(DISMISSED_PREF) || "[]");
      return Array.isArray(parsed) ? parsed.filter(name => typeof name === "string") : [];
    } catch { return []; }
  }
  function writeDismissed(names) {
    const box = store(); if (!box) return false;
    try {
      const unique = [...new Set(names)].filter(name => Object.hasOwn(CATEGORY_LABELS, name));
      if (unique.length) box.setItem(DISMISSED_PREF, JSON.stringify(unique));
      else box.removeItem(DISMISSED_PREF);
      return true;
    } catch { return false; }
  }
  // Storage can be unavailable or full. A dismissal that cannot be persisted
  // still has to work for this session, so keep an in-memory copy as well.
  let sessionOnly = [];
  function dismissedNames() { return [...new Set([...readDismissed(), ...sessionOnly])]; }
  function hintDismissed(category) { return dismissedNames().includes(category); }
  function dismissHint(category) {
    if (!writeDismissed([...dismissedNames(), category]) && !sessionOnly.includes(category)) sessionOnly.push(category);
  }
  function restoreHints(category) {
    if (category) {
      const kept = dismissedNames().filter(name => name !== category);
      sessionOnly = sessionOnly.filter(name => name !== category);
      writeDismissed(kept);
    } else {
      sessionOnly = []; writeDismissed([]);
    }
  }
  function dismissedCategories() { return dismissedNames().filter(name => Object.hasOwn(CATEGORY_LABELS, name)); }
  function categoryFor(scene, options = {}) {
    if (!Object.hasOwn(TITLES, scene)) scene = "review";
    if (scene === "review") return options.comparison ? "review-compare" : "review";
    return scene;
  }

  function keyRow(doc, item) {
    const value = element(doc, "div", "key-row"), keys = element(doc, "span");
    value.append(element(doc, "span", "", item.label), keys);
    item.keys.forEach(key => keys.append(element(doc, "kbd", "", key)));
    return value;
  }
  function detailed(doc, info) {
    const list = element(doc, "div", "shortcut-full-list");
    [...info.primary, ...info.more].forEach(item => list.append(keyRow(doc, item)));
    list.append(element(doc, "p", "shortcut-note", info.extra), element(doc, "p", "shortcut-note", info.note));
    return list;
  }
  function referenceButton(doc, scene, options, label) {
    const view = element(doc, "button", "button quiet small", label || "查看完整说明");
    view.type = "button";
    view.addEventListener("click", () => open(scene, options));
    return view;
  }
  // Dismissed guidance must not come back on the next render, and it must not
  // leave a blank strip behind either. Reading the preference here -- inside
  // the mount that every surface re-runs -- is what makes a question switch, a
  // page change or a new window honour it.
  function mountHint(host, scene, options = {}) {
    if (!host?.ownerDocument) return null;
    host.removeAttribute("aria-hidden");
    host.hidden = false;
    const doc = host.ownerDocument, info = reference(scene, options);
    const category = categoryFor(scene, options);
    const details = element(doc, "details", "shortcut-hint");
    if (hintDismissed(category)) {
      details.classList.add("shortcut-hint-off");
      const summary = element(doc, "summary", "shortcut-hint-summary");
      summary.setAttribute("aria-label", `${info.title}操作说明；快捷键提示已永久关闭`);
      summary.append(element(doc, "span", "shortcut-expand-label", "操作说明"));
      const panel = element(doc, "div", "shortcut-hint-panel");
      panel.append(element(doc, "p", "shortcut-note", "这一处的操作提示已永久关闭，快捷键仍然可用。"),
        referenceButton(doc, scene, options, "查看完整说明"));
      details.append(summary, panel);
      host.replaceChildren(details);
      return details;
    }
    const summary = element(doc, "summary", "shortcut-hint-summary");
    summary.setAttribute("aria-label", `${info.title}快捷键，展开完整说明`);
    info.primary.forEach(item => { const part = element(doc, "span", "shortcut-quick-key"); item.keys.forEach(key => part.append(element(doc, "kbd", "", key))); part.append(doc.createTextNode(` ${item.label}`)); summary.append(part); });
    summary.append(element(doc, "span", "shortcut-expand-label", "完整说明"));
    const panel = element(doc, "div", "shortcut-hint-panel"); panel.append(detailed(doc, info));
    const all = element(doc, "button", "button quiet small", "查看其他场景"); all.type = "button"; all.addEventListener("click", () => open(scene, options)); panel.append(all);
    panel.append(dismissButton(doc, category, host));
    details.append(summary, panel); host.replaceChildren(details); return details;
  }
  function dismissButton(doc, category, host) {
    const off = element(doc, "button", "button quiet small", "以后不再提示");
    off.type = "button";
    off.title = `不再显示${CATEGORY_LABELS[category] || "这一处提示"}；快捷键仍然有效，可在帮助中恢复`;
    off.addEventListener("click", () => {
      dismissHint(category);
      // Collapse the host so the strip leaves no gap, and let every other
      // surface re-read the preference now rather than on its next render.
      if (host) { host.replaceChildren(); host.hidden = true; }
      const view = root.document?.getElementById("keysDialog");
      if (view?.open) view.close();
      // A dismissal is a preference, not a settings edit: it must never make
      // the API configuration look like it is waiting to be saved.
      root.document?.querySelectorAll("[data-model-save-result]").forEach(node => { node.textContent = ""; });
      fire("qb:hint-dismissed", { category });
    });
    return off;
  }
  function open(scene = "review", options = {}) {
    const doc = root.document; if (!doc) return false;
    let dialog = doc.getElementById("keysDialog"), body;
    if (!dialog) {
      dialog = element(doc, "dialog", "keys-dialog"); dialog.id = "keysDialog"; dialog.setAttribute("aria-labelledby", "keysTitle");
      const head = element(doc, "div", "dialog-head"), heading = element(doc, "div");
      const title = element(doc, "h3", "", "快捷键"); title.id = "keysTitle"; heading.append(title);
      const close = element(doc, "button", "button", "关闭"); close.type = "button"; close.dataset.shortcutHelpClose = "";
      head.append(heading, close); body = element(doc, "div", "shortcut-help-body"); body.id = "shortcutHelpBody"; dialog.append(head, body); doc.body.append(dialog);
    } else body = doc.getElementById("shortcutHelpBody");
    if (!body) return false;
    if (!dialog.dataset.shortcutReady) {
      dialog.dataset.shortcutReady = "1";
      dialog.querySelector("[data-shortcut-help-close]")?.addEventListener("click", () => dialog.close());
      dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
    }
    const choose = element(doc, "label", "shortcut-scene-label", "使用场景"), picker = element(doc, "select", "shortcut-scene"); picker.setAttribute("aria-label", "快捷键使用场景");
    Object.entries(TITLES).forEach(([key, title]) => { const option = element(doc, "option", "", title); option.value = key; picker.append(option); });
    const content = element(doc, "section", "shortcut-current-scene");
    const render = () => {
      const info = reference(picker.value, picker.value === scene ? options : {});
      const heading = element(doc, "h4", "", info.title), quick = element(doc, "div", "shortcut-main");
      info.primary.forEach(item => quick.append(keyRow(doc, item)));
      const more = element(doc, "details", "shortcut-details"); more.append(element(doc, "summary", "", "展开完整说明"), detailed(doc, info));
      content.replaceChildren(heading, quick, more);
    };
    picker.value = Object.hasOwn(TITLES, scene) ? scene : "review"; picker.addEventListener("change", render); choose.append(picker);
    // Restoring belongs here: this dialog is the one place every surface can
    // still reach, so a permanently hidden hint is never a one-way door.
    const restoreRow = element(doc, "div", "shortcut-restore-row");
    const renderRestore = () => {
      const off = dismissedCategories();
      const note = element(doc, "p", "shortcut-note",
        off.length ? `已关闭：${off.map(name => CATEGORY_LABELS[name]).join("、")}`
          : "当前没有被永久关闭的操作提示。");
      const back = element(doc, "button", "button quiet small", "恢复全部操作提示");
      back.type = "button"; back.disabled = !off.length;
      back.addEventListener("click", () => {
        restoreHints();
        fire("qb:hints-restored");
        renderRestore();
      });
      restoreRow.replaceChildren(note, back);
    };
    renderRestore();
    body.replaceChildren(choose, content, restoreRow); render();
    if (!dialog.open) dialog.showModal(); return true;
  }
  return { reference, mountHint, open, isEditingTarget, ordinaryKeyBlocked,
    DISMISSED_PREF, CATEGORY_LABELS, categoryFor, hintDismissed, dismissHint,
    restoreHints, dismissedCategories };
});
