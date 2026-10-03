"use strict";

// Minimal synthetic DOM. Never opens a browser, credentials or the network.
function node(id = "", tag = "div", text = "") {
  const classes = new Set();
  return {
    id, tag, value: "", textContent: text, hidden: false, disabled: false, open: false,
    children: [], events: {}, attributes: {}, isConnected: true,
    classList: {
      add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
      contains(name) { return classes.has(name); },
      toggle(name, force) { const add = force ?? !classes.has(name); if (add) classes.add(name); else classes.delete(name); }
    },
    addEventListener(name, handler) { (this.events[name] ||= []).push(handler); },
    append(...items) { this.children.push(...items); },
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
