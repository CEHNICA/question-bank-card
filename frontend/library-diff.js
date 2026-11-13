"use strict";
// 题有据纯原生字符级/Token 级差异对比引擎（零第三方依赖）
(function (global) {
  function tokenizeText(str) {
    if (!str) return [];
    return String(str).match(/\s+|\\[a-zA-Z]+|[a-zA-Z]+|[0-9]+|[\u4e00-\u9fa5]|./g) || [];
  }

  function computeTokensDiff(beforeStr, afterStr) {
    const a = tokenizeText(beforeStr);
    const b = tokenizeText(afterStr);
    let start = 0;
    while (start < a.length && start < b.length && a[start] === b[start]) {
      start++;
    }
    let endA = a.length - 1;
    let endB = b.length - 1;
    while (endA >= start && endB >= start && a[endA] === b[endB]) {
      endA--;
      endB--;
    }
    const prefix = a.slice(0, start).map((text) => ({ type: "same", text }));
    const suffix = a.slice(endA + 1).map((text) => ({ type: "same", text }));
    const midA = a.slice(start, endA + 1);
    const midB = b.slice(start, endB + 1);
    const n = midA.length;
    const m = midB.length;
    const midDiff = [];
    if (n > 0 && m > 0 && n * m <= 40000) {
      const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
      for (let i = 1; i <= n; i++) {
        for (let j = 1; j <= m; j++) {
          dp[i][j] = midA[i - 1] === midB[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
        }
      }
      let i = n, j = m;
      while (i > 0 || j > 0) {
        if (i > 0 && j > 0 && midA[i - 1] === midB[j - 1]) {
          midDiff.push({ type: "same", text: midA[i - 1] });
          i--; j--;
        } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
          midDiff.push({ type: "add", text: midB[j - 1] });
          j--;
        } else if (i > 0) {
          midDiff.push({ type: "del", text: midA[i - 1] });
          i--;
        }
      }
      midDiff.reverse();
    } else {
      for (let i = 0; i < n; i++) midDiff.push({ type: "del", text: midA[i] });
      for (let j = 0; j < m; j++) midDiff.push({ type: "add", text: midB[j] });
    }
    const all = [...prefix, ...midDiff, ...suffix];
    const merged = [];
    for (const item of all) {
      if (!item.text) continue;
      if (merged.length && merged[merged.length - 1].type === item.type) {
        merged[merged.length - 1].text += item.text;
      } else {
        merged.push({ type: item.type, text: item.text });
      }
    }
    return merged;
  }

  function renderDiffTokens(tokens, targetSide, nodeFactory) {
    const fn = typeof nodeFactory === "function" ? nodeFactory : (tag, cls, text) => {
      const el = document.createElement(tag);
      if (cls) el.className = cls;
      if (text != null) el.textContent = String(text);
      return el;
    };
    const wrap = fn("span", "library-assistant-diff-content");
    for (const token of tokens) {
      if (token.type === "same") {
        wrap.append(fn("span", "", token.text));
      } else if (token.type === "del" && targetSide === "before") {
        wrap.append(fn("del", "diff-del", token.text));
      } else if (token.type === "add" && targetSide === "after") {
        wrap.append(fn("ins", "diff-add", token.text));
      }
    }
    return wrap;
  }

  const api = { tokenizeText, computeTokensDiff, renderDiffTokens };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    global.TiyoujuDiff = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
