"use strict";
// 照着课本目录写一份教材目录（1.26.6）。
//
// 老师的原话：「目录能不能自己根据教材目录来编写，现在根据标签模板生成的目录感觉还是
// 不够细，也不太自由」—— 一个个建标签太慢，所以这里先把课本目录的**文字**解析成
// 一棵树，老师看一眼预览，确认了再一次性建好。
//
// 认得这些写法：
//   第一章 集合 / 第1章 集合 / 第一节 集合   → 章（一级）
//   1.1 集合的含义 / 1.1.2 外集              → 按点号数分级（1.1 是二级，1.1.2 是三级）
//   一、集合的含义 / （一）集合的含义          → 「（一）」「（二）」是兄弟，各深一级
(function (root) {
  const CHAPTER = /^\s*第\s*[0-9０-９一二三四五六七八九十百千零〇两]+\s*[章講讲编篇部]/;
  const SECTION = /^\s*第\s*[0-9０-９一二三四五六七八九十百千零〇两]+\s*[节節回]/;
  const DOTTED = /^\s*(\d+(?:[.．]\d+)+)/;
  const BRACKET = /^\s*[（(]\s*[0-9０-９一二三四五六七八九十]+\s*[)）]/;
  const CHINESE = /^\s*[一二三四五六七八九十]+\s*[、.．]/;

  // 一行属于第几层。认不出编号就沿用上一行的层数（课本目录里的说明行常常没编号）。
  //
  // 「（一）」「（二）」是同一族 —— 兄弟，不是父子：括号编号沿用上一行的层数；
  // 「一、」和「（一）」不是一族，括号要更深一层。
  function levelOf(text, previous, family) {
    if (CHAPTER.test(text)) return { level: 0, family: "chapter" };
    if (SECTION.test(text)) return { level: 1, family: "chapter" };
    const dotted = DOTTED.exec(text);
    if (dotted) return { level: dotted[1].split(/[.．]/).length - 1, family: "dotted" };
    if (BRACKET.test(text)) return { level: family === "bracket" ? previous : previous + 1, family: "bracket" };
    if (CHINESE.test(text)) return { level: family === "chinese" ? previous : previous + 1, family: "chinese" };
    return { level: previous, family };
  }

  function parse(text) {
    const lines = String(text || "").split(/\r?\n/);
    const roots = [];
    const stack = [];          // [{node, level}]
    let previous = -1;
    let family = "";
    let ignored = 0;
    for (const raw of lines) {
      const line = raw.trim();
      if (!line) continue;
      // 目录页码行、页眉页脚（"…… 12"）这类不要。
      if (/^[\s\d.·—\-–|/\\]+$/.test(line)) { ignored += 1; continue; }
      const found = levelOf(raw, previous < 0 ? 0 : previous, family);
      const level = Math.max(found.level, 0);
      previous = level;
      family = found.family;
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
