"use strict";

/*
 * 1.12.7 抽屉导航。几条不能破的规矩：
 * ① 默认永远关着 —— 不占常驻高度，关着时导航在视口外。
 * ② 打开时焦点进抽屉，Esc / 点遮罩 / 点关闭按钮都能收起，焦点还给 ☰。
 * ③ 有原生 <dialog> 开着时 Esc 让给 dialog（它在 top layer，永远盖着抽屉）。
 * ④ 篮里有题才在顶栏给入口；篮的展开/收起登记出去，交给题库页改状态。
 */

const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");

class ClassList {
  constructor(){ this.set = new Set(); }
  add(...names){ names.forEach(name => this.set.add(name)); }
  remove(...names){ names.forEach(name => this.set.delete(name)); }
  contains(name){ return this.set.has(name); }
  toggle(name, value){ value ? this.set.add(name) : this.set.delete(name); return this.set.has(name); }
}
class Element {
  constructor(tag = "div"){ this.tag = tag; this.children = []; this.parentNode = null; this.events = {}; this.attrs = {}; this.classList = new ClassList(); this.ownText = ""; this.innerHTML = ""; this.hidden = false; this.open = false; }
  get className(){ return [...this.classList.set].join(" "); }
  set className(value){ this.classList = new ClassList(); String(value).split(/\s+/).filter(Boolean).forEach(name => this.classList.add(name)); }
  get textContent(){ return this.children.length ? this.children.map(child => child.textContent).join("") : this.ownText; }
  set textContent(value){ this.ownText = String(value); }
  get childElementCount(){ return this.children.length; }
  get firstChild(){ return this.children[0] || null; }
  get isConnected(){ return true; }
  append(...nodes){ nodes.forEach(node => { if (node.parentNode) node.parentNode.children = node.parentNode.children.filter(value => value !== node); this.children.push(node); node.parentNode = this; }); }
  insertBefore(node, before){ const at = this.children.indexOf(before); this.children.splice(at < 0 ? 0 : at, 0, node); node.parentNode = this; }
  setAttribute(key, value){ this.attrs[key] = String(value); }
  getAttribute(key){ return key in this.attrs ? this.attrs[key] : null; }
  hasAttribute(key){ return key in this.attrs; }
  addEventListener(key, callback){ (this.events[key] ||= []).push(callback); }
  fire(key, extra = {}){
    const event = { target: this, defaultPrevented: false, preventDefault(){ event.defaultPrevented = true; }, ...extra };
    for (let node = this; node; node = node.parentNode) (node.events[key] || []).forEach(callback => callback(event));
    return event;
  }
  focus(){ doc.activeElement = this; }
  closest(selector){
    const wantsAnchor = selector === "a[href]";
    for (let node = this; node; node = node.parentNode) if (wantsAnchor && node.tag === "a" && "href" in node.attrs) return node;
    return null;
  }
  contains(node){ return this === node || this.children.includes(node); }
  querySelectorAll(selector){ return this.query(selector); }
  query(selector, pool = []){
    this.children.forEach(child => { pool.push(child); child.query(selector, pool); });
    if (selector === "[data-drawer]") return pool.filter(node => "data-drawer" in node.attrs);
    if (selector === "dialog[open]") return pool.filter(node => node.tag === "dialog" && node.open);
    const attribute = selector.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
    if (attribute) return pool.filter(node => attribute[2] === undefined ? attribute[1] in node.attrs : node.attrs[attribute[1]] === attribute[2]);
    if (selector.startsWith("#")) return pool.filter(node => node.id === selector.slice(1));
    if (selector.startsWith(".")) return pool.filter(node => node.classList.contains(selector.slice(1)));
    return pool.filter(node => node.tag === selector.toUpperCase());
  }
  querySelector(selector){ return this.query(selector)[0] || null; }
}

const body = new Element("body");
const topbar = new Element("header");
const topbarTools = new Element("div");
topbarTools.className = "topbar-tools";
const openDialog = new Element("dialog");
const doc = {
  activeElement: null, body, events: {},
  createElement: tag => new Element(tag),
  querySelector(selector){
    if (selector === ".topbar") return topbar;
    if (selector === ".topbar-tools") return topbarTools;
    return body.query(selector)[0] || null;
  },
  querySelectorAll(selector){ return body.query(selector); },
  addEventListener(key, callback){ this.events[key] = callback; },
  fire(key, extra = {}){
    const event = { key, defaultPrevented: false, preventDefault(){ this.defaultPrevented = true; }, ...extra };
    this.events[key]?.(event);
    return event;
  }
};

const nav = new Element("nav");
nav.className = "topnav";
nav.setAttribute("data-drawer", "nav");
nav.append(new Element("a"));
const pageLink = nav.children[0];
pageLink.setAttribute("href", "/library");
const sourceLink = new Element("a");
sourceLink.setAttribute("data-drawer", "tools");
const basketPanel = new Element("aside");
basketPanel.setAttribute("data-drawer", "basket");
body.append(topbar, topbarTools, nav, sourceLink, basketPanel, openDialog);

const sandbox = { document: doc, module: undefined };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, "site-drawer.js"), "utf8"), sandbox);
const drawer = sandbox.QBSiteDrawer;

// ---- 默认关着：不占常驻高度
assert.equal(drawer.isMounted(), true, "模块启动时应自动挂载");
assert.equal(drawer.isOpen(), false, "抽屉默认必须关着");
const panel = doc.querySelector("#siteDrawer");
const scrim = body.querySelector(".drawer-scrim");
const trigger = topbar.firstChild;
assert.equal(panel.hidden, true, "关闭时抽屉本体不可见");
assert.equal(scrim.hidden, true, "关闭时遮罩不可见");
assert.equal(trigger.getAttribute("aria-expanded"), "false");
assert.ok(trigger.classList.contains("drawer-trigger"), "顶栏最前面是触发器");
assert.ok(trigger.classList.contains("icon-button"));
assert.equal(nav.parentNode.getAttribute("data-slot"), "nav", "导航被搬进抽屉的「页面」组");
assert.equal(sourceLink.parentNode.getAttribute("data-slot"), "tools", "工具按钮被搬进抽屉的「工具」组");
assert.equal(basketPanel.parentNode.getAttribute("data-slot"), "basket", "试题篮被搬进抽屉");

// ---- 打开：焦点进抽屉，背景锁滚
doc.activeElement = trigger;
drawer.open();
assert.equal(drawer.isOpen(), true);
assert.equal(panel.hidden, false);
assert.equal(scrim.hidden, false);
assert.equal(trigger.getAttribute("aria-expanded"), "true");
assert.ok(body.classList.contains("drawer-open"), "打开时锁住背景滚动");
const closeButton = panel.querySelector(".site-drawer-close");
assert.equal(doc.activeElement, closeButton, "焦点要进关闭按钮");
drawer.open();
assert.equal(panel.querySelectorAll(".site-drawer-close").length, 1, "重复打开不重复挂");

// ---- 收起：Esc / 遮罩 / 关闭按钮，焦点还给 ☰
doc.fire("keydown", { key: "Escape" });
assert.equal(drawer.isOpen(), false, "Esc 收起抽屉");
assert.equal(doc.activeElement, trigger, "焦点回到 ☰");
assert.equal(body.classList.contains("drawer-open"), false);

drawer.open();
scrim.fire("click");
assert.equal(drawer.isOpen(), false, "点遮罩收起");
drawer.open();
closeButton.fire("click");
assert.equal(drawer.isOpen(), false, "点关闭按钮收起");

// ---- 原生 dialog 开着时 Esc 让给它（它在 top layer，永远盖着抽屉）
drawer.open();
openDialog.open = true;
doc.fire("keydown", { key: "Escape" });
assert.equal(drawer.isOpen(), true, "dialog[open] 存在时 Esc 不抢");
openDialog.open = false;
doc.fire("keydown", { key: "Escape" });
assert.equal(drawer.isOpen(), false);

// ---- 点导航链接顺带收起（同页的点「设置」不会跳页）
drawer.open();
pageLink.fire("click");
assert.equal(drawer.isOpen(), false, "点导航收起抽屉");

// ---- 触发器点一下开、再点一下关
trigger.fire("click");
assert.equal(drawer.isOpen(), true, "点 ☰ 打开");
trigger.fire("click");
assert.equal(drawer.isOpen(), false, "再点 ☰ 关闭");

// ---- 篮里有题才在顶栏给入口
const topbarBasket = topbarTools.children[0];
assert.equal(topbarBasket.hidden, true, "篮空着时顶栏没有入口");
drawer.setBasketCount(3);
assert.equal(topbarBasket.hidden, false, "篮里有题，顶栏给入口");
assert.equal(topbarBasket.querySelector(".topbar-basket-count").textContent, "3");
const basketGroup = panel.querySelector('[data-group="basket"]');
assert.equal(basketGroup.hidden, false, "抽屉里有「试题篮」这一组");
drawer.setBasketCount(0);
assert.equal(topbarBasket.hidden, true, "篮空了入口又收起来");
drawer.setBasketCount(2);
assert.equal(panel.querySelector(".site-drawer-count").textContent, "2", "抽屉里的篮计数跟着更新");

// ---- 篮不再是折叠项：点标题只登记一次「看一下」，显示由数量决定
const seen = [];
drawer.onBasketChange(action => seen.push(action));
const basketTitle = basketGroup.querySelector(".site-drawer-title");
basketTitle.fire("click");
assert.deepEqual(seen, ["show"], "点篮的标题要通知题库页把篮画出来");
assert.equal(drawer.isOpen(), false, "组内点标题不自动拉开抽屉");
assert.equal(basketGroup.hidden, false, "篮里有题就整组都显示，不需要再展开一次");
topbarBasket.fire("click");
assert.equal(drawer.isOpen(), true, "点顶栏的篮会拉开抽屉");
assert.deepEqual(seen, ["show", "show"], "顶栏入口也是登记一次「看一下」，没有收起这一说");

console.log("Site drawer: closed by default, focus moved in, Esc yields to dialog, basket entry only when stocked: OK");
