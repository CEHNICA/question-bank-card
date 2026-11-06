/* Resegment-preview wording. Kept apart so the sentences can be checked
   without booting the whole review page. */
/* 重新切题预演弹窗里那颗会灰的按钮，要说清的话。
   规矩跟别处的灰控件一样：说清「为什么灰」和「下一步去哪」。
   1.26.38：这次全软件扫了一遍，发现就剩这颗按钮灰着什么都不说。
   两版话术都在这：长版给按钮的悬停说明，短版给按钮旁边那行常驻小字
   （按钮禁用时浏览器不发鼠标事件，悬停气泡根本不弹，光写 title 等于没说）。 */
(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBResegmentWording = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  // 长版：鼠标停上去看到的那句。
  function applyHint(state) {
    const s = state || {};
    if (s.applying) return "正在应用这次重新切题，等它跑完。";
    if (s.loading) return "正在按最新规则生成只读预演，等它算完就能确认应用。";
    if (s.blocked) return "这次预演没跑成功，暂时不能应用。先看上面的原因，解决后重新点「重新切题…」。";
    if (!s.report) return "还没有可应用的预演结果。点一下「重新切题…」先生成预演。";
    if (!s.readOnly) return "服务端没有确认这是一份只读预演，已经禁止应用。重新点「重新切题…」再预演一次。";
    if (!s.acknowledged) return "先勾上上面的确认框，看完这次影响才能应用重新切题。";
    return "按预演结果应用这次重新切题。";
  }

  // 短版：按钮旁边那行常驻小字用的。
  // 不从长句里截半句 —— 那样会变成半句话，更看不懂。
  // 一一对应长版的六种情况，一种都不能少。
  const BRIEF = {
    applying: "正在应用，请稍等",
    loading: "正在算预演，算完就能应用",
    blocked: "预演没跑成功，看上面的原因",
    noReport: "还没有预演结果",
    notReadOnly: "服务端不认这次预演，不能应用",
    notAcknowledged: "先勾上确认框",
    ready: "",
  };

  function applyHintBrief(state) {
    const s = state || {};
    if (s.applying) return BRIEF.applying;
    if (s.loading) return BRIEF.loading;
    if (s.blocked) return BRIEF.blocked;
    if (!s.report) return BRIEF.noReport;
    if (!s.readOnly) return BRIEF.notReadOnly;
    if (!s.acknowledged) return BRIEF.notAcknowledged;
    return BRIEF.ready;
  }

  return { applyHint, applyHintBrief, BRIEF };
});
