"use strict";
// 1.26.50：软件打开后，试卷列表要等后台把几千道题的判定算完才出来。
// 这段时间里左边「试卷」那一栏是空的 —— 老师看见的是一片空白，
// 分不清是软件坏了还是还在准备。
//
// 这里做一件事：在列表还没出来的时候，在那一栏里说清楚「正在准备」，
// 列表一出来就把这句话收走。
//
// 两个细节：
//   1. 热启动时列表 0.3 秒就回来了，提示一闪而过反而吓人 —— 所以先等 1.2 秒，
//      还没出来才显示。
//   2. 提示写在列表这一栏里（就在老师看的那儿），不是在屏幕中央弹一条，
//      免得挡住别的东西。
(function () {
  const LIST_ID = "paperList";
  const SHOW_AFTER_MS = 1200;
  const GIVE_UP_AFTER_MS = 60000;
  const WAITING_TEXT = "正在准备试卷列表…";
  const SLOW_TEXT = "试卷列表还没准备好，程序会自己再试一次。";

  function listElement() {
    return document.getElementById(LIST_ID);
  }

  function isReady(list) {
    if (!list) return true;
    for (const child of list.children) {
      const names = String(child.className || "").split(/\s+/);
      if (names.includes("startup-hint")) continue;
      return true;
    }
    return false;
  }

  function start() {
    const list = listElement();
    if (!list) return;

    let hint = null;
    const show = (text) => {
      if (hint && hint.isConnected) {
        hint.textContent = text;
        return;
      }
      hint = document.createElement("li");
      hint.className = "side-empty startup-hint";
      hint.setAttribute("role", "status");
      hint.textContent = text;
      list.append(hint);
    };
    const hide = () => {
      if (hint && hint.isConnected) hint.remove();
    };

    // 列表里出现别的东西（真列表，或者「还没有试卷」）就说明好了。
    const observer = new MutationObserver(() => {
      if (!isReady(list)) return;
      hide();
      observer.disconnect();
    });
    observer.observe(list, { childList: true });

    window.setTimeout(() => {
      if (!isReady(list)) show(WAITING_TEXT);
    }, SHOW_AFTER_MS);

    window.setTimeout(() => {
      if (!isReady(list)) show(SLOW_TEXT);
    }, GIVE_UP_AFTER_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
