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

  // 1.26.47：「AI 识读这题」灰掉时说的话。
  // 后端对原图题有三条硬保护（已审核、已入库、已有人工文字），页面照着做 ——
  // 事前就把「为什么灰、下一步去哪」写出来，不用等点了之后弹一条红条说不行。
  // label 写进按钮本身（按钮禁用时浏览器不发鼠标事件，悬停气泡根本不弹）；
  // title 是悬停说明；brief 是按钮旁边那行常驻可见的小字。
  const CUT_READING = {
    approved: {
      label: "已审核，不再识读",
      title: "这道题已经审核过，AI 识读不会改动它；要重新识读请先撤销通过，要改字请对照原卷用「改字」。",
      brief: "已审核过，AI 识读不会改动它；要重新识读请先撤销通过。"
    },
    published: {
      label: "已入库，不再识读",
      title: "这道题现在在题库里，AI 识读不会改动它；要改请到题库里改，或对照原卷用「改字」。",
      brief: "这道题现在在题库里，AI 识读不会改动它；要改请到题库里改。"
    },
    manual_text: {
      label: "已有人工文字，不再识读",
      title: "这道题已经有你或助手改过的文字，AI 识读会盖掉它；请对照原卷用「改字」修改。",
      brief: "已有你或助手改过的文字，AI 识读会盖掉它；请对照原卷用「改字」修改。"
    },
    unknown: {
      label: "不再识读",
      title: "这道题已经有审核、入库或人工文字，AI 识读不会改动它；要改请对照原卷用「改字」。",
      brief: "已有审核、入库或人工文字，AI 识读不会改动它；要改请对照原卷用「改字」。"
    }
  };

  function cutReadingGuardText(reason) {
    return CUT_READING[reason] || CUT_READING.unknown;
  }

  return { INSPECTOR, DIRECTORY, CROP, SOURCE, menuSummaryTitle, CUT_READING, cutReadingGuardText };
});
