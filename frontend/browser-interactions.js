"use strict";
// Keep browser menus out of the app's reading/canvas areas. Text fields keep
// the native copy/paste menu and standard keyboard shortcuts.
//
// 1.26.3：页面不再自绘窗口按钮（最小化/最大化/关闭）。
// 宿主窗口是普通的浏览器窗口，标题栏本身就带那三个按钮 —— 页面上再画一套
// 就是两套并排，老师会不知道该点哪一个。1.26.0 到 1.26.2 加过又撤掉，
// 记在这里，免得下回又有人加。
(function (root) {
  function keepTextMenu(target) {
    return Boolean(target?.closest?.("input, textarea, [contenteditable]:not([contenteditable='false']), [role='textbox']"));
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { keepTextMenu };
  root.document?.addEventListener("contextmenu", event => {
    if (!keepTextMenu(event.target)) event.preventDefault();
  });
})(typeof window !== "undefined" ? window : globalThis);
