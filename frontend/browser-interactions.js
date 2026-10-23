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

  // The host window has a system title bar, but it is not always within reach:
  // dragged against the screen edge, maximized or in a full-screen reading view,
  // the title bar is out of sight. The page therefore keeps its own
  // minimize/maximize/close controls in the top-right corner. They only appear
  // when a real desktop window is behind the page — in a plain browser tab they
  // would be decoration that does nothing.
  function wireWindowControls() {
    // getElementById is looked up defensively: the unit test drives this file
    // with a document stub that only carries addEventListener.
    const byId = root.document?.getElementById?.bind(root.document);
    const close = byId?.("windowClose");
    const minimize = byId?.("windowMinimize");
    const maximize = byId?.("windowMaximize");
    const group = byId?.("windowControls");
    // 宿主窗口没有系统标题栏，这两个按钮是唯一的窗口控制入口。浏览器没有
    // 「最小化/关闭本窗口」的脚本接口：window.close() 对命令行 --app 打开的
    // 窗口无效，所以请启动器去操作真正的窗口句柄。请求带上前端标记头，
    // 越过跨站请求的默认拦截；普通浏览器标签页里没有这个接口，点了不报错。
    const askHost = action => root.fetch?.(`/api/window/${action}`, {
      method: "POST", headers: { "X-QB-Request": "1" }
    })?.catch?.(() => {});
    const leave = () => { try { root.close(); } catch { /* 标签页关不了自己 */ } };
    let closeRetried = false;
    close?.addEventListener("click", () => {
      // 1.25.34：关窗之前先淡出，不要瞬间消失。
      const body = root.document?.body;
      body?.classList?.add?.("window-exiting");
      const finish = () => { if (!askHost("close")) leave(); };
      if (root.setTimeout) root.setTimeout(finish, 160); else finish();
      // 1.25.51：窗口没关掉时（控制窗口的线程已退出、或请求失败），别把页面永远
      // 留在淡出后的白屏里：1.6 秒后恢复，再补一次关闭请求。
      if (root.setTimeout) {
        root.setTimeout(() => {
          if (root.document?.hidden) return;   // 已经关掉或切走，不用管
          body?.classList?.remove?.("window-exiting");
          if (!closeRetried) { closeRetried = true; finish(); }
        }, 1600);
      }
    });
    minimize?.addEventListener("click", () => { askHost("minimize"); });
    maximize?.addEventListener("click", () => { askHost("maximize"); });
    // 页面右上角这一组按钮，**只在标题栏够不着的时候**才出现：
    //   · 普通浏览器标签页：背后没有可操作的窗口，显示了也是摆设；
    //   · 标题栏还在（普通窗口、最大化）：由标题栏那三个管，页面再画一份就变成
    //     两套控件并排，老师会不知道该点哪一个（1.25.90 删掉它们就是这个理由）；
    //   · 整屏显示、或窗口被拖到屏幕边缘外面：标题栏整个够不着 → 页面这份顶上。
    // 宿主（启动器）守着窗口，窗口一变它就把状态写过来；这里顺便听一下尺寸变化，
    // 整屏/还原不用刷新页面。
    const refresh = () => {
      if (!group || !root.fetch) return;
      root.fetch("/api/window/available", { cache: "no-store" })
        ?.then?.(response => response?.json?.())
        ?.then?.(body => {
          if (!body?.available) { group.hidden = true; return; }
          group.hidden = body.caption !== false;
        })
        ?.catch?.(() => {});
    };
    if (group) {
      refresh();
      root.addEventListener?.("resize", refresh);
    }
  }
  if (root.document?.readyState === "loading") {
    root.document.addEventListener("DOMContentLoaded", wireWindowControls);
  } else {
    wireWindowControls();
  }
})(typeof window !== "undefined" ? window : globalThis);
