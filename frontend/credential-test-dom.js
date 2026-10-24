"use strict";

// Minimal synthetic DOM. Never opens a browser, credentials or the network.
function node(id = "", tag = "div", text = "") {
  const classes = new Set();
  return {
    id, tag, value: "", textContent: text, hidden: false, disabled: false, open: false,
    children: [], events: {}, attributes: {}, isConnected: true, parentElement: null,
    classList: {
      add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
      toggle(name, force) { const add = force ?? !classes.has(name); if (add) classes.add(name); else classes.delete(name); }
    },
    addEventListener(name, handler) { (this.events[name] ||= []).push(handler); },
    // 1.26.9：配置块会在"设置页"和"弹窗"之间搬来搬去 —— append 一次就是**移动**
    // （同一个节点换爹，旧爹不再留着它），假 DOM 得记住这件事，否则测不出"只有一份"。
    append(...items) {
      for (const item of items) {
        if (item && typeof item === "object" && item.parentElement && item.parentElement !== this) {
          const previous = item.parentElement;
          if (Array.isArray(previous.children)) previous.children = previous.children.filter((child) => child !== item);
        }
        this.children.push(item);
        if (item && typeof item === "object") item.parentElement = this;
      }
    },
    replaceChildren(...items) { this.children.forEach((child) => { child.isConnected = false; }); this.children = items; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
    getAttribute(name) { return this.attributes[name] ?? null; },
    querySelectorAll() { return []; }, focus() {}
  };
}
const el = (tag, className, text) => { const item = node("", tag, text); item.className = className || ""; return item; };
const icon = () => node("", "svg");
module.exports = { node, el, icon };
