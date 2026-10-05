/*
 * 抽屉导航。顶栏只留 ☰ 和品牌：三个页面导航、工具按钮、试题篮和操作说明
 * 都收在这里，要用才拉开。默认永远关着，不占任何常驻高度 —— 页面上一行的
 * 高度全部让给题目。
 *
 * 抽屉里的元素是「搬」进去的，不是复制：`[data-drawer="nav"]` 这类标记写在
 * HTML 里，模块启动时把它们 appendChild 到对应分组。搬过去的节点 id 和类名
 * 都不变，所以别处按 id / `.topnav` 找它们的代码照旧能用。
 */
(function (root, factory) {
  "use strict";
  const value = factory(root);
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBSiteDrawer = value;
})(typeof window === "object" ? window : globalThis, function (root) {
  "use strict";

  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';
  const ICON_MENU = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 7h16M4 12h16M4 17h16"/></svg>';
  const ICON_CLOSE = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6 6 18"/></svg>';

  // 1.12.7：篮不住在这个抽屉里了。它是题库页右边缘一条常驻把手 + 一个抽屉面板 ——
  // 干活的面，不是菜单项。这个抽屉只管导航和工具。
  const GROUPS = [
    { name: "nav", title: "页面" },
    { name: "library", title: "题库" },
    { name: "tools", title: "工具" },
    { name: "hint", title: "操作说明" }
  ];

  let ui = null;

  function element(doc, tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // 组里没有内容就别占位置（录入终审页没有题库组）。
  function syncGroups() {
    for (const group of GROUPS) {
      const entry = ui.groups[group.name];
      entry.section.hidden = !entry.slot.childElementCount && !entry.slot.textContent;
    }
  }

  function focusables() {
    const list = ui.panel.querySelectorAll?.(FOCUSABLE) || [];
    return Array.from(list).filter((node) => !node.closest?.("[hidden]"));
  }

  function open() {
    if (!ui || ui.open) return;
    ui.returnFocus = ui.doc.activeElement;
    ui.open = true;
    ui.scrim.hidden = false;
    ui.panel.hidden = false;
    ui.doc.body.classList.add("drawer-open");
    ui.trigger.setAttribute("aria-expanded", "true");
    ui.close.focus?.({ preventScroll: true });
  }

  function close() {
    if (!ui || !ui.open) return;
    ui.open = false;
    ui.scrim.hidden = true;
    ui.panel.hidden = true;
    ui.doc.body.classList.remove("drawer-open");
    ui.trigger.setAttribute("aria-expanded", "false");
    const back = ui.returnFocus?.isConnected === false ? null : ui.returnFocus;
    ui.returnFocus = null;
    (back || ui.trigger).focus?.({ preventScroll: true });
  }

  function toggle() { (ui?.open ? close : open)(); }

  function onKeyDown(event) {
    if (!ui?.open || event.defaultPrevented) return;
    // 原生 <dialog> 在 top layer，永远盖住抽屉：Esc 先交给它。
    if (event.key === "Escape" && ui.doc.querySelector?.("dialog[open]")) return;
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
    if (event.key !== "Tab") return;
    const list = focusables();
    if (!list.length) return;
    const first = list[0], last = list[list.length - 1];
    const active = ui.doc.activeElement;
    if (event.shiftKey && (active === first || !ui.panel.contains?.(active))) {
      event.preventDefault(); last.focus?.({ preventScroll: true });
    } else if (!event.shiftKey && (active === last || !ui.panel.contains?.(active))) {
      event.preventDefault(); first.focus?.({ preventScroll: true });
    }
  }

  function mount(doc) {
    const document = doc || root.document;
    if (ui || !document?.body) return api;
    const topbar = document.querySelector(".topbar");
    if (!topbar) return api;

    const scrim = element(document, "div", "drawer-scrim");
    scrim.hidden = true;

    const panel = element(document, "aside", "site-drawer");
    panel.id = "siteDrawer";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "导航与工具");
    panel.hidden = true;

    const head = element(document, "div", "site-drawer-head");
    head.append(element(document, "span", "site-drawer-brand", "题有据"));
    const closeButton = element(document, "button", "icon-button site-drawer-close");
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "关闭菜单");
    closeButton.innerHTML = ICON_CLOSE;
    head.append(closeButton);

    const body = element(document, "div", "site-drawer-body");
    const groups = {};
    for (const group of GROUPS) {
      const section = element(document, "section", "site-drawer-group");
      section.setAttribute("data-group", group.name);
      section.hidden = true;
      const slot = element(document, "div", "site-drawer-slot");
      slot.setAttribute("data-slot", group.name);
      const title = element(document, "h2", "site-drawer-title", group.title);
      groups[group.name] = { section, slot, title };
      section.append(title, slot);
      body.append(section);
    }
    panel.append(head, body);

    const trigger = element(document, "button", "icon-button drawer-trigger");
    trigger.type = "button";
    trigger.id = "drawerTrigger";
    trigger.setAttribute("aria-label", "打开菜单");
    trigger.setAttribute("aria-controls", "siteDrawer");
    trigger.setAttribute("aria-expanded", "false");
    trigger.innerHTML = ICON_MENU;

    document.body.append(scrim, panel);
    topbar.insertBefore(trigger, topbar.firstChild);

    ui = { doc: document, topbar, scrim, panel, body, groups, trigger, close: closeButton,
      open: false, returnFocus: null };

    // HTML 上的 [data-drawer] 标记决定谁进哪个组。
    for (const node of Array.from(document.querySelectorAll?.("[data-drawer]") || [])) {
      const entry = groups[node.getAttribute("data-drawer")];
      if (entry && !entry.slot.contains?.(node)) entry.slot.append(node);
    }

    trigger.addEventListener("click", toggle);
    closeButton.addEventListener("click", close);
    scrim.addEventListener("click", close);
    // 导航点完就收起：跳页本身是整页加载，但同页的「已设置」这类点击不会。
    panel.addEventListener("click", (event) => {
      const link = event.target.closest?.("a[href]");
      if (link && !link.hasAttribute("download")) close();
    });
    document.addEventListener("keydown", onKeyDown);

    syncGroups();
    return api;
  }

  const api = {
    mount, open, close, toggle, isOpen: () => Boolean(ui?.open),
    slot: (name) => ui?.groups?.[name]?.slot || null,
    isMounted: () => Boolean(ui)
  };

  if (root.document?.querySelector) mount();
  return api;
});
