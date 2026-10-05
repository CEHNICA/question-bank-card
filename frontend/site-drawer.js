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
  const ICON_BASKET = '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 9h16l-1.6 9.2a2 2 0 0 1-2 1.8H7.6a2 2 0 0 1-2-1.8L4 9Z"/><path d="m8.5 9 3.5-5 3.5 5"/></svg>';

  // 试题篮是一整块内容，不是需要再点一下的折叠项：同一个抽屉里「页面 / 题库 /
  // 工具」点一下就出内容，唯独它要点两下，用户会以为坏了。
  const GROUPS = [
    { name: "nav", title: "页面" },
    { name: "library", title: "题库" },
    { name: "basket", title: "试题篮" },
    { name: "tools", title: "工具" },
    { name: "hint", title: "操作说明" }
  ];

  let ui = null;
  const basketHandlers = new Set();

  function element(doc, tag, className, text) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // 组里没有内容就别占位置（录入终审页没有题库组）。试题篮看**篮里的数量**，
  // 不是看 slot 有没有子节点 —— 篮面板本身就是 slot 的子节点，拿子节点判空
  // 永远为真，篮空着也会显示一个「试题篮 0」。
  function syncGroups() {
    for (const group of GROUPS) {
      const entry = ui.groups[group.name];
      const empty = group.name === "basket" ? ui.basketCount <= 0
        : (!entry.slot.childElementCount && !entry.slot.textContent);
      entry.section.hidden = empty;
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

  // 试题篮里的面板是登记过的整块内容，展开与否由题库页自己的 state 决定；
  // 这里只把「有人点了篮的入口」这件事转出去，不自己改数据。
  function onBasketChange(handler) { basketHandlers.add(handler); }

  function runBasket(action) {
    basketHandlers.forEach((handler) => { handler(action); });
  }

  function setBasketCount(count) {
    if (!ui) return;
    const value = Math.max(0, Number(count) || 0);
    ui.basketCount = value;
    ui.groups.basket.count.textContent = String(value);
    ui.topbarBasketCount.textContent = String(value);
    ui.topbarBasket.hidden = !value;
    syncGroups();
  }

  // 篮不再折叠，这一格跟着数量走：篮里有题就显示、就展开，篮空就整组消失。
  function setBasketVisible() {
    if (!ui) return;
    syncGroups();
  }

  function showBasket() {
    open();
    runBasket("show");
  }

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
      let title;
      if (group.name === "basket") {
        // 和「组卷草稿」同级的普通动作行：点它 = 打开抽屉，篮的内容就在下面。
        // 以前它是折叠项，长得和菜单行一样却要点两下，看起来就像坏了。
        title = element(document, "h2", "site-drawer-title");
        const count = element(document, "span", "site-drawer-count", "0");
        groups[group.name] = { section, slot, title, count };
        title.append(element(document, "span", "", group.title), count);
      } else {
        title = element(document, "h2", "site-drawer-title", group.title);
        groups[group.name] = { section, slot, title };
      }
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

    // 篮里有题时顶栏才给一个入口，篮空着就让它消失。
    const topbarBasket = element(document, "button", "button button-quiet small topbar-basket");
    topbarBasket.type = "button";
    topbarBasket.hidden = true;
    const basketIcon = element(document, "span", "topbar-basket-icon");
    basketIcon.innerHTML = ICON_BASKET;
    const topbarBasketCount = element(document, "span", "topbar-basket-count", "0");
    topbarBasket.append(basketIcon, element(document, "span", "topbar-basket-label", "试题篮"), topbarBasketCount);
    document.querySelector(".topbar-tools")?.append(topbarBasket);

    document.body.append(scrim, panel);
    topbar.insertBefore(trigger, topbar.firstChild);

    ui = {
      doc: document, topbar, scrim, panel, body, groups, trigger, close: closeButton,
      topbarBasket, topbarBasketCount, open: false, returnFocus: null, basketCount: 0
    };

    // HTML 上的 [data-drawer] 标记决定谁进哪个组。
    for (const node of Array.from(document.querySelectorAll?.("[data-drawer]") || [])) {
      const entry = groups[node.getAttribute("data-drawer")];
      if (entry && !entry.slot.contains?.(node)) entry.slot.append(node);
    }

    trigger.addEventListener("click", toggle);
    closeButton.addEventListener("click", close);
    scrim.addEventListener("click", close);
    topbarBasket.addEventListener("click", showBasket);
    // 点抽屉里的「试题篮 N」= 打开抽屉看篮（内容就在标题下面，不用再点一次）。
    groups.basket.title.addEventListener("click", () => runBasket("show"));
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
    setBasketCount, setBasketVisible, onBasketChange,
    slot: (name) => ui?.groups?.[name]?.slot || null,
    isMounted: () => Boolean(ui)
  };

  if (root.document?.querySelector) mount();
  return api;
});
