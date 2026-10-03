"use strict";
// Keep browser menus out of the app's reading/canvas areas. Text fields keep
// the native copy/paste menu and standard keyboard shortcuts.
(function (root) {
  function keepTextMenu(target) {
    return Boolean(target?.closest?.("input, textarea, [contenteditable]:not([contenteditable='false']), [role='textbox']"));
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { keepTextMenu };
  root.document?.addEventListener("contextmenu", event => {
    if (!keepTextMenu(event.target)) event.preventDefault();
  });
})(typeof window !== "undefined" ? window : globalThis);
