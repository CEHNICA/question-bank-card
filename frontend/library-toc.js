"use strict";
// 照着课本目录写一份教材目录（1.26.6）。
//
// 老师的原话：「目录能不能自己根据教材目录来编写，现在根据标签模板生成的目录感觉还是
// 不够细，也不太自由」—— 一个个建标签太慢，所以这里先把课本目录的**文字**解析成
// 一棵树，老师看一眼预览，确认了再一次性建好。
//
// 认得这些写法：
//   第一章 集合 / 第1章 集合 / 第一节 集合   → 一级
//   1.1 集合的含义 / 1.1.2 外集              → 按点号数分级（1.1 是二级，1.1.2 是三级）
//   一、集合的含义 / （一）集合的含义          → 比上一行深一级
//   前面有缩进（空格 / 全角空格 / 制表符）      → 按缩进分级
(function (root) {
  const CHAPTER = /^\s*第\s*[0-9０-９一二三四五六七八九十百千零〇两]+\s*[章講讲编篇部]/;
  const SECTION = /^\s*第\s*[0-9０-９一二三四五六七八九十百千零〇两]+\s*[节節回]/;
  const DOTTED = /^\s*(\d+(?:[.．]\d+)+)/;
  const BRACKET = /^\s*[（(]\s*[0-9０-９一二三四五六七八九十]+\s*[)）]/;
  const CHINESE = /^\s*[一二三四五六七八九十]+\s*[、.．]/;

  function indentWidth(text) {
    let width = 0;
    for (const char of text) {
      if (char === "\t") { width += 4; continue; }
      if (char === " " || char === "　" || char === " ") { width += 1; continue; }
      break;
    }
    return width;
  }

  // 一行属于第几层。认不出编号就沿用上一行的层数（课本目录里的说明行常常没编号）。
  function levelOf(text, previous) {
    if (CHAPTER.test(text) || SECTION.test(text)) return 0;
    const dotted = DOTTED.exec(text);
    if (dotted) return dotted[1].split(/[.．]/).length - 1;
    if (BRACKET.test(text) || CHINESE.test(text)) return previous + 1;
    return previous;
  }

  function parse(text) {
    const lines = String(text || "").split(/\r?\n/);
    const roots = [];
    const stack = [];          // [{node, level}]
    let previous = -1;
    let ignored = 0;
    for (const raw of lines) {
      const line = raw.trim();
      if (!line) continue;
      // 目录页码行、页眉页脚（"…… 12"）这类不要。
      if (/^[\s\d.·—\-—–|/\\]+$/.test(line)) { ignored += 1; continue; }
      const indent = indentWidth(raw);
      let level = levelOf(raw, previous < 0 ? 0 : previous);
      if (indent > 0) level = Math.max(level, 0);
      previous = level;
      const node = { name: line, children: [] };
      while (stack.length && stack[stack.length - 1].level >= level) stack.pop();
      const parent = stack[stack.length - 1];
      if (parent) parent.node.children.push(node);
      else roots.push(node);
      stack.push({ node, level });
    }
    const count = nodes => nodes.reduce((sum, node) => sum + 1 + count(node.children), 0);
    return { roots, count: count(roots), ignored };
  }

  function flatten(nodes, depth, out) {
    const result = out || [];
    nodes.forEach(node => {
      result.push({ name: node.name, depth: depth || 0 });
      flatten(node.children, (depth || 0) + 1, result);
    });
    return result;
  }

  const api = { parse, flatten };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.QBTextbookToc = api;
})(typeof window !== "undefined" ? window : globalThis);
