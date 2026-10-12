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

  // The host window has no system title bar, so the page supplies its own
  // minimize/close controls. In a plain browser tab these simply do nothing,
  // which keeps the same markup usable during development.
  function wireWindowControls() {
    const close = root.document?.getElementById("windowClose");
    const minimize = root.document?.getElementById("windowMinimize");
    // window.close() only works on a window opened by script. The application
    // window qualifies; an ordinary tab does not, and stays untouched.
    close?.addEventListener("click", () => {
      try { root.close(); } catch { /* a normal tab cannot close itself */ }
    });
    // There is no script API to minimize, so ask the host through the launcher.
    minimize?.addEventListener("click", () => {
      root.fetch?.("/api/window/minimize", { method: "POST" }).catch?.(() => {});
    });
  }
  if (root.document?.readyState === "loading") {
    root.document.addEventListener("DOMContentLoaded", wireWindowControls);
  } else {
    wireWindowControls();
  }
})(typeof window !== "undefined" ? window : globalThis);
