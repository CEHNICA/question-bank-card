/* 1.26.39：控件话术集中在一处。
 *
 * 两条规矩：
 *   1) 同一个操作只留一套说法。右侧详情面板里那颗按钮和顶栏那颗开关做的是同一件事，
 *      以前一个只写「收起」、一个写「收起右侧详情」，读者得自己猜它们是一回事。
 *   2) 看不懂的控件给一句悬停说明，写清按了会对谁做什么；灰掉的按钮还要写清为什么灰、
 *      下一步去哪（按钮禁用时浏览器不发鼠标事件，悬停气泡根本不弹，光写 title 等于没说，
 *      所以灰掉时把话说写进按钮本身）。
 *
 * 页面只从这里取话术，不再各自写一套。
 */
(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBControlWording = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // 右侧「当前题目」详情面板。面板里那颗按钮和顶栏那颗开关共用这三套说法。
  const INSPECTOR = {
    expanded: {
      label: "收起详情",
      hint: "收起右边的题目详情；想再看，点左边的题卡就能打开。"
    },
    collapsed: {
      label: "展开详情",
      hint: "打开右边的题目详情，看当前题目的标签和待核对建议。"
    },
    disabled: {
      label: "先选中一道题",
      hint: "先在左边点一道题，这里才有它的标签和待核对建议。"
    }
  };

  // 教材目录面板。
  const DIRECTORY = {
    label: "收起目录",
    hint: "收起左边的教材目录；要再打开，点筛选栏里的「目录」按钮。"
  };

  // 答案解析里的裁剪工具条。
  const CROP = {
    label: "收起裁剪条",
    hint: "收起这一条裁剪工具；要再裁剪，重新点「从原卷裁图」。"
  };

  // 原卷那三颗显示方式按钮。
  const SOURCE = {
    question: { label: "只看本题", hint: "只看这一道题，原卷上别的题先藏起来。" },
    wholePage: { label: "整页位置", hint: "显示整页原卷，看这道题在页面里的位置。" },
    fit: { label: "适合窗口", hint: "把整页原卷缩到刚好放进窗口，看全页的样子。" }
  };

  // 「更多」里到底有哪些项，按菜单里实际有的生成 —— 不写菜单里没有的东西。
  function menuSummaryTitle(labels) {
    const names = [];
    (labels || []).forEach((label) => {
      const text = String(label == null ? "" : label).trim();
      if (text && !names.includes(text)) names.push(text);
    });
    return names.length ? `更多操作：${names.join("、")}` : "更多操作";
  }

  return { INSPECTOR, DIRECTORY, CROP, SOURCE, menuSummaryTitle };
});
