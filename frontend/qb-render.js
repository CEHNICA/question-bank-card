/*
 * 题库公共排版与比对模块（审核页和正式题库页共用）。
 *
 * 三条原则：
 * 1. 只改变显示，不改变任何存储的文字。排版视图由原文推导，随时可切回逐字原文。
 * 2. 同一题面用同一套规则：已标记的 $…$ 与未标记的数学片段一起排版，避免半边排版半边原文。
 * 3. 比对分三级：仅空白/公式标记/全半角（排版差异）、符号写法不同（如 // 与 ∥）、实质差异。
 *    只有实质差异按字标色；任何等级都不等于已核对原卷。
 */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.QBRender = api;
})(typeof window !== "undefined" ? window : globalThis, (root) => {
  "use strict";

  const OPTION_KEYS = ["A", "B", "C", "D", "E"];
  // A–D are always shown for a choice question (an empty one says so); E only
  // when the paper prints it.
  function shownOptionKeys(options, figures = []) {
    const hasE = String(options?.E ?? "").trim() || figures.some((figure) => figure.slot === "E");
    return hasE ? OPTION_KEYS : OPTION_KEYS.slice(0, 4);
  }
  const EXPLICIT_MATH = /\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]/g;
  const BLANK = /(?:\\_){2,}|_{3,}|（[ \u3000]*）|\([ \u3000]+\)/g;
  const CJK = /[⺀-⿿　-〿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]/;

  // ---------------------------------------------------------------- 字符规范化

  function halfWidth(character) {
    const code = character.codePointAt(0);
    if ((code >= 0xff10 && code <= 0xff19) || (code >= 0xff21 && code <= 0xff3a) || (code >= 0xff41 && code <= 0xff5a)) {
      return String.fromCodePoint(code - 0xfee0);
    }
    return character;
  }

  // 纯排版：全角/半角、中英文标点与 Unicode 减号等。
  const FORMAT_CHARS = {
    "（": "(", "）": ")", "﹙": "(", "﹚": ")", "［": "[", "］": "]", "｛": "{", "｝": "}",
    "，": ",", "．": ".", "。": ".", "：": ":", "；": ";", "？": "?", "！": "!", "｜": "|",
    "−": "-", "－": "-", "＝": "=", "＋": "+", "＜": "<", "＞": ">", "％": "%", "／": "/",
    "²": "^2", "³": "^3", "¹": "^1", "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3", "₄": "_4",
    "ℝ": "ℝ", "ℕ": "ℕ", "ℤ": "ℤ", "ℚ": "ℚ", "ℂ": "ℂ", "≌": "≅", " ": " "
  };

  // 同一数学含义的不同写法。只用于“符号写法不同”这一级。
  const SYMBOL_VARIANTS = {
    "//": "∥", "⩽": "≤", "≦": "≤", "⩾": "≥", "≧": "≥", "丄": "⊥",
    "′": "'", "’": "'", "‘": "'", "″": "''", "⋅": "·", "•": "·", "∙": "·", "…": "⋯",
    "∽": "∼", "～": "∼", "〜": "∼"
  };

  // LaTeX 命令 → 显示字符（比对用）。
  const COMMAND_SYMBOLS = {
    angle: "∠", parallel: "∥", perp: "⊥", bot: "⊥", triangle: "△", bigtriangleup: "△", times: "×", div: "÷",
    cdot: "·", pm: "±", mp: "∓", le: "≤", leq: "≤", ge: "≥", geq: "≥", leqslant: "⩽", geqslant: "⩾",
    leqq: "≦", geqq: "≧", ne: "≠", neq: "≠", approx: "≈", equiv: "≡", cong: "≅", sim: "∼", backsim: "∽",
    pi: "π", infty: "∞", in: "∈", notin: "∉", subset: "⊂", subseteq: "⊆", subsetneqq: "⊊", supset: "⊃",
    supseteq: "⊇", cup: "∪", cap: "∩", emptyset: "∅", varnothing: "∅", forall: "∀", exists: "∃",
    neg: "¬", because: "∵", therefore: "∴", circ: "°", degree: "°", prime: "'", cdots: "⋯", ldots: "⋯",
    dots: "⋯", mid: "|", vert: "|", lvert: "|", rvert: "|", complement: "∁", rightarrow: "→", to: "→",
    Rightarrow: "⇒", Leftrightarrow: "⇔", leftrightarrow: "↔", square: "□", Box: "□", lbrace: "{", rbrace: "}",
    alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", theta: "θ", lambda: "λ",
    mu: "μ", rho: "ρ", sigma: "σ", tau: "τ", phi: "φ", varphi: "φ", omega: "ω", Delta: "Δ", Omega: "Ω",
    sin: "sin", cos: "cos", tan: "tan", cot: "cot", log: "log", ln: "ln", lg: "lg", max: "max", min: "min"
  };
  const SKIPPED_COMMANDS = new Set([
    "left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "quad", "qquad",
    "displaystyle", "textstyle", "text", "mathrm", "mathit", "mathbf", "rm", "bf", "it", "operatorname",
    "underline", "limits", "nolimits", "mathord", "mathop", "boldsymbol"
  ]);
  const DOUBLE_STRUCK = { R: "ℝ", N: "ℕ", Z: "ℤ", Q: "ℚ", C: "ℂ" };

  function isGeometryParallel(source, index) {
    return source.startsWith("//", index) &&
      /[A-Za-z]{1,2}\s*$/.test(source.slice(Math.max(0, index - 6), index)) &&
      /^\s*[A-Za-z]{1,2}(?![a-z])/.test(source.slice(index + 2, index + 8));
  }

  /*
   * 把一段文字切成可比较的单位。每个单位带两把钥匙：
   *   fmt —— 忽略空白、$、\( \)、括号/标点全半角、LaTeX 命令与等价字符后的写法；
   *   sym —— 在 fmt 基础上再把同义符号写法统一（// 与 ∥、≦ 与 ≤ ……）。
   */
  function comparisonUnits(value) {
    const source = String(value ?? "");
    const units = [];
    const mathRanges = Array.from(source.matchAll(EXPLICIT_MATH), (m) => [m.index, m.index + m[0].length]);
    const inMath = (index) => mathRanges.some(([start, end]) => index > start && index < end);
    const push = (fmt, start, end) => {
      const sym = SYMBOL_VARIANTS[fmt] || fmt;
      units.push({ fmt, sym, start, end });
    };
    for (let index = 0; index < source.length;) {
      const rest = source.slice(index);
      if (rest.startsWith("\\(") || rest.startsWith("\\)") || rest.startsWith("\\[") || rest.startsWith("\\]")) {
        index += 2; continue;
      }
      const escaped = /^\\([{}|%_&#$ ,;:!])/.exec(rest);
      if (escaped) {
        if ("{}|%_&#".includes(escaped[1])) push(escaped[1] === "_" ? "_" : escaped[1], index, index + 2);
        index += 2; continue;
      }
      const command = /^\\([A-Za-z]+)\s*/.exec(rest);
      if (command) {
        const name = command[1];
        const end = index + command[0].length;
        if (name === "mathbb" || name === "Bbb") {
          const letter = /^\{?\s*([A-Z])\s*\}?/.exec(source.slice(end));
          if (letter) {
            push(DOUBLE_STRUCK[letter[1]] || letter[1], index, end + letter[0].length);
            index = end + letter[0].length; continue;
          }
        }
        if (name === "sqrt") { push("√", index, end); index = end; continue; }
        if (SKIPPED_COMMANDS.has(name)) { index = end; continue; }
        const symbol = COMMAND_SYMBOLS[name];
        if (symbol) {
          if (symbol.length > 1 && /^[a-z]+$/.test(symbol)) {
            for (const letter of symbol) push(letter, index, end);
          } else push(symbol, index, end);
        } else push(`\\${name}`, index, end);
        index = end; continue;
      }
      if (source.startsWith("//", index) && isGeometryParallel(source, index)) {
        push("//", index, index + 2); index += 2; continue;
      }
      if (rest.startsWith("^") && /^\^\s*\{?\s*\\circ/.test(rest)) { index += 1; continue; }
      const character = String.fromCodePoint(source.codePointAt(index));
      const end = index + character.length;
      if (/[\s​⁠$]/u.test(character)) { index = end; continue; }
      if ((character === "{" || character === "}") && inMath(index)) { index = end; continue; }
      const mapped = FORMAT_CHARS[character] ?? halfWidth(character);
      if (mapped.length === 2 && /^[\^_]/.test(mapped)) {
        push(mapped[0], index, end); push(mapped[1], index, end);
      } else if (mapped.trim()) push(mapped, index, end);
      index = end;
    }
    return units;
  }

  function stripQuestionNumber(value, number) {
    const source = String(value ?? "");
    if (!Number.isInteger(Number(number))) return { text: source, offset: 0 };
    const match = new RegExp(`^\\s*${Number(number)}\\s*[.．、,，]\\s*`).exec(source);
    return match ? { text: source.slice(match[0].length), offset: match[0].length } : { text: source, offset: 0 };
  }

  function lcsPairs(left, right, key) {
    // 返回匹配对 [i, j]；超长时只做首尾对齐，避免卡住页面。
    const pairs = [];
    let prefix = 0;
    while (prefix < left.length && prefix < right.length && left[prefix][key] === right[prefix][key]) {
      pairs.push([prefix, prefix]); prefix += 1;
    }
    let leftEnd = left.length;
    let rightEnd = right.length;
    const tail = [];
    while (leftEnd > prefix && rightEnd > prefix && left[leftEnd - 1][key] === right[rightEnd - 1][key]) {
      leftEnd -= 1; rightEnd -= 1; tail.unshift([leftEnd, rightEnd]);
    }
    const n = leftEnd - prefix;
    const m = rightEnd - prefix;
    if (n && m && n * m <= 250000) {
      const table = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
      for (let i = n - 1; i >= 0; i -= 1) {
        for (let j = m - 1; j >= 0; j -= 1) {
          table[i][j] = left[prefix + i][key] === right[prefix + j][key]
            ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
        }
      }
      let i = 0;
      let j = 0;
      while (i < n && j < m) {
        if (left[prefix + i][key] === right[prefix + j][key]) { pairs.push([prefix + i, prefix + j]); i += 1; j += 1; }
        else if (table[i + 1][j] >= table[i][j + 1]) i += 1;
        else j += 1;
      }
    }
    return pairs.concat(tail);
  }

  function rangesOf(units, flags, offset = 0) {
    return units.reduce((result, unit, index) => {
      if (!flags[index]) return result;
      const previous = result[result.length - 1];
      const start = unit.start + offset;
      const end = unit.end + offset;
      if (previous && start <= previous.end) previous.end = Math.max(previous.end, end);
      else result.push({ start, end });
      return result;
    }, []);
  }

  /*
   * 比较当前稿与 AI 重读。返回：
   *   level: same | format | symbol | content
   *   current/observed: 实质差异的字符区间（只在 content 级有）
   *   symbolCurrent/symbolObserved: 写法不同的区间（虚线提示，不标色）
   *   symbolPairs: ["// → ∥", …]
   *   ignoredNumber: AI 文字开头的题号前缀已忽略
   */
  function compareTexts(current, observed, { number = null } = {}) {
    const left = String(current ?? "");
    const rightRaw = String(observed ?? "");
    const stripped = number !== null ? stripQuestionNumber(rightRaw, number) : { text: rightRaw, offset: 0 };
    const leftHasNumber = number !== null && stripQuestionNumber(left, number).offset > 0;
    const right = leftHasNumber ? rightRaw : stripped.text;
    const offset = leftHasNumber ? 0 : stripped.offset;
    const result = {
      level: "same", current: [], observed: [], symbolCurrent: [], symbolObserved: [], symbolPairs: [],
      ignoredNumber: !leftHasNumber && stripped.offset > 0
    };
    if (left === rightRaw) return result;
    const a = comparisonUnits(left);
    const b = comparisonUnits(right);
    const fmtEqual = a.length === b.length && a.every((unit, index) => unit.fmt === b[index].fmt);
    if (fmtEqual) { result.level = "format"; return result; }
    const pairs = lcsPairs(a, b, "sym");
    const changedA = a.map(() => true);
    const changedB = b.map(() => true);
    const symbolA = a.map(() => false);
    const symbolB = b.map(() => false);
    const seen = new Set();
    pairs.forEach(([i, j]) => {
      changedA[i] = false; changedB[j] = false;
      if (a[i].fmt !== b[j].fmt) {
        symbolA[i] = true; symbolB[j] = true;
        const label = `${left.slice(a[i].start, a[i].end)} → ${right.slice(b[j].start, b[j].end)}`;
        if (!seen.has(label)) { seen.add(label); result.symbolPairs.push(label); }
      }
    });
    const substantive = changedA.some(Boolean) || changedB.some(Boolean);
    result.level = substantive ? "content" : "symbol";
    result.symbolCurrent = rangesOf(a, symbolA);
    result.symbolObserved = rangesOf(b, symbolB, offset);
    if (substantive) {
      result.current = rangesOf(a, changedA);
      result.observed = rangesOf(b, changedB, offset);
    }
    return result;
  }

  /*
   * 逐处对照需要成对的位置，而 compareTexts.current/observed 只是各自的标色范围。
   * 一侧独有的文字用另一侧的零长度位置表示，便于明确说出“未读出/多读出”。
   */
  function comparisonHunks(current, observed, { number = null } = {}) {
    const left = String(current ?? "");
    const rightRaw = String(observed ?? "");
    const stripped = number !== null ? stripQuestionNumber(rightRaw, number) : { text: rightRaw, offset: 0 };
    const leftHasNumber = number !== null && stripQuestionNumber(left, number).offset > 0;
    const right = leftHasNumber ? rightRaw : stripped.text;
    const offset = leftHasNumber ? 0 : stripped.offset;
    const a = comparisonUnits(left);
    const b = comparisonUnits(right);
    const matched = [[-1, -1], ...lcsPairs(a, b, "sym"), [a.length, b.length]];
    const raw = [];
    const span = (units, start, end, length, shift = 0) => {
      if (start < end) return { start: units[start].start + shift, end: units[end - 1].end + shift };
      const point = (start < units.length ? units[start].start : length) + shift;
      return { start: point, end: point };
    };
    for (let index = 1; index < matched.length; index += 1) {
      const [previousA, previousB] = matched[index - 1];
      const [nextA, nextB] = matched[index];
      const startA = previousA + 1;
      const startB = previousB + 1;
      if (startA === nextA && startB === nextB) continue;
      raw.push({
        current: span(a, startA, nextA, left.length),
        observed: span(b, startB, nextB, right.length, offset)
      });
    }
    const boundary = /[\r\n。！？；;，,]|[（(]\s*\d+\s*[）)]/u;
    const hunks = [];
    for (const item of raw) {
      const previous = hunks[hunks.length - 1];
      if (previous) {
        const currentGap = left.slice(previous.current.end, item.current.start);
        const observedGap = rightRaw.slice(previous.observed.end, item.observed.start);
        if (currentGap.length <= 12 && observedGap.length <= 12 &&
          !boundary.test(currentGap) && !boundary.test(observedGap) &&
          comparisonUnits(currentGap).length <= 3 && comparisonUnits(observedGap).length <= 3) {
          previous.current.end = item.current.end;
          previous.observed.end = item.observed.end;
          continue;
        }
      }
      hunks.push(item);
    }
    return hunks;
  }

  const LEVEL_TEXT = {
    same: "两份文字完全一致",
    format: "仅空白、公式标记或全半角不同",
    symbol: "符号写法不同，含义相同",
    content: "有实质差异"
  };

  // ---------------------------------------------------------------- 排版（显示层）

  const RUN_SYMBOLS = "=+-<>|^_'!%:/·×÷±∓≤≥≦≧⩽⩾≠≈≡≅≌∼∽∠∥⊥丄△▱□°′″√π∞∈∉⊂⊆⊊⊃⊇∪∩∅∀∃∵∴→⇒⇔↔⋯…ℝℕℤℚℂ²³¹₀₁₂₃₄αβγδεθλμρστφωΔΩ＝＋－−＜＞";
  const OPERATOR_SET = new Set(Array.from("=+-<>|^_·×÷±∓≤≥≦≧⩽⩾≠≈≡≅≌∼∽∠∥⊥丄△▱□°′″√π∞∈∉⊂⊆⊊⊃⊇∪∩∅∀∃∵∴→⇒⇔↔²³ℝℕℤℚℂ＝＋－−＜＞/:'%!"));
  const TO_LATEX = {
    "＝": "=", "＋": "+", "－": "-", "−": "-", "＜": "<", "＞": ">", "×": "\\times ", "÷": "\\div ",
    "·": "\\cdot ", "⋅": "\\cdot ", "±": "\\pm ", "∓": "\\mp ", "≤": "\\le ", "≥": "\\ge ", "≦": "\\leqq ",
    "≧": "\\geqq ", "⩽": "\\leqslant ", "⩾": "\\geqslant ", "≠": "\\ne ", "≈": "\\approx ", "≡": "\\equiv ",
    "≅": "\\cong ", "≌": "\\cong ", "∼": "\\sim ", "∽": "\\backsim ", "∠": "\\angle ", "∥": "\\parallel ",
    "⊥": "\\perp ", "丄": "\\perp ", "△": "\\triangle ", "▱": "▱", "□": "\\square ", "°": "^{\\circ}",
    "′": "'", "″": "''", "π": "\\pi ", "∞": "\\infty ", "∈": "\\in ", "∉": "\\notin ", "⊂": "\\subset ",
    "⊆": "\\subseteq ", "⊊": "\\subsetneqq ", "⊃": "\\supset ", "⊇": "\\supseteq ", "∪": "\\cup ",
    "∩": "\\cap ", "∅": "\\varnothing ", "∀": "\\forall ", "∃": "\\exists ", "∵": "\\because ",
    "∴": "\\therefore ", "→": "\\to ", "⇒": "\\Rightarrow ", "⇔": "\\Leftrightarrow ", "↔": "\\leftrightarrow ",
    "⋯": "\\cdots ", "…": "\\cdots ", "%": "\\%", "ℝ": "\\mathbb{R}", "ℕ": "\\mathbb{N}", "ℤ": "\\mathbb{Z}",
    "ℚ": "\\mathbb{Q}", "ℂ": "\\mathbb{C}", "²": "^{2}", "³": "^{3}", "¹": "^{1}", "₀": "_{0}", "₁": "_{1}",
    "₂": "_{2}", "₃": "_{3}", "₄": "_{4}", "α": "\\alpha ", "β": "\\beta ", "γ": "\\gamma ", "δ": "\\delta ",
    "ε": "\\varepsilon ", "θ": "\\theta ", "λ": "\\lambda ", "μ": "\\mu ", "ρ": "\\rho ", "σ": "\\sigma ",
    "τ": "\\tau ", "φ": "\\varphi ", "ω": "\\omega ", "Δ": "\\Delta ", "Ω": "\\Omega ", "（": "(", "）": ")",
    "，": ",\\,", "：": ":", "｜": "|", "{": "\\{", "}": "\\}"
  };
  const FUNCTIONS = new Set(["sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "lg", "max", "min", "lim", "exp"]);
  const UNITS = /^(?:cm|mm|km|dm|m|kg|mg|g|ml|mL|L|s|h|min)$/;

  function isRunChar(character) {
    return /[A-Za-z0-9Ａ-Ｚａ-ｚ０-９()[\]{}]/.test(character) || RUN_SYMBOLS.includes(character);
  }

  function qualifiesAsMath(segment) {
    const qualifies = segment && (/[A-Za-zＡ-Ｚａ-ｚα-ωΔΩ]/.test(segment) ||
      Array.from(segment).some((c) => OPERATOR_SET.has(c) && c !== "/" && c !== ":" && c !== "!" && c !== "%" && c !== "'"));
    const plainWord = /^[a-z]{3,}$/.test(segment) && !FUNCTIONS.has(segment);
    const subLabel = /^\(\d+\)$/.test(segment);
    return Boolean(qualifies && !plainWord && !subLabel);
  }

  // 英文句子里的单词（The、graph、temperature，以及 at、of、if 这些常用短词）。
  // sin、cm 这类函数名和单位、ABC 这类点名不算。
  const SHORT_WORDS = new Set(["at", "of", "if", "is", "in", "on", "to", "be", "by", "or", "as", "an", "it", "we",
    "so", "do", "no", "up", "am"]);
  function isProseWord(token) {
    const word = token.replace(/[.,;:?!]+$/, "");
    if (SHORT_WORDS.has(word.toLowerCase()) && /^[A-Za-z][a-z]?$/.test(word)) return true;
    return /^[A-Za-z][a-z]{2,}$/.test(word) && !FUNCTIONS.has(word.toLowerCase()) && !UNITS.test(word);
  }

  function proseWordCount(segment) {
    return segment.split(/\s+/).filter(isProseWord).length;
  }

  // 一句英文：只把里面的数学（18°C、x+1=3、AB）排成公式，单词照常显示，空格保留。
  function proseRuns(segment, offset) {
    const runs = [];
    const tokens = Array.from(segment.matchAll(/\S+/g));
    let group = null;
    const flush = () => {
      if (group && qualifiesAsMath(group.text)) runs.push(group);
      group = null;
    };
    tokens.forEach((match, position) => {
      const token = match[0];
      const next = tokens[position + 1]?.[0] || "";
      // 冠词 a / A 后面跟着名词时是英文（a number），不是字母变量（Let a be …）。
      const article = /^(?:a|A)$/.test(token) && /^[A-Za-z][a-z]{2,}/.test(next) && isProseWord(next)
        && !/^(?:and|are|was|has|had|the)$/i.test(next.replace(/[.,;:?!]+$/, ""));
      if (isProseWord(token) || article) { flush(); return; }
      const start = offset + match.index;
      const end = start + token.length;
      if (group) group = { start: group.start, end, text: segment.slice(group.start - offset, end - offset) };
      else group = { start, end, text: token };
    });
    flush();
    return runs;
  }

  // 在未标记的文字中找出数学片段。只识别，不改写存储。
  function detectRuns(text) {
    const runs = [];
    let index = 0;
    while (index < text.length) {
      const character = text[index];
      if (!isRunChar(character) || /[)\]}]/.test(character)) { index += 1; continue; }
      let end = index;
      let depth = 0;
      while (end < text.length) {
        const current = text[end];
        if (isRunChar(current)) {
          if (current === "(" || current === "[" || current === "{") depth += 1;
          if (current === ")" || current === "]" || current === "}") { if (depth === 0) break; depth -= 1; }
          end += 1; continue;
        }
        if (current === "." && /\d/.test(text[end - 1] || "") && /\d/.test(text[end + 1] || "")) { end += 1; continue; }
        if (current === "," && depth > 0) { end += 1; continue; }
        if (current === " ") {
          let next = end;
          while (text[next] === " ") next += 1;
          const following = text[next];
          if (next < text.length && isRunChar(following) && (!/[)\]}]/.test(following) || depth > 0)) {
            end = next; continue;
          }
        }
        break;
      }
      let segment = text.slice(index, end).replace(/\s+$/, "");
      // 去掉不成对的开括号前缀，例如“(x”。
      while (segment.startsWith("(") && (segment.match(/\(/g) || []).length > (segment.match(/\)/g) || []).length) {
        segment = segment.slice(1); index += 1;
      }
      const label = /^\(\d+\)\s+/.exec(segment);
      if (label && segment.length > label[0].length) { segment = segment.slice(label[0].length); index += label[0].length; }
      if (proseWordCount(segment) >= 1 && /\s/.test(segment)) runs.push(...proseRuns(segment, index));
      else if (qualifiesAsMath(segment)) runs.push({ start: index, end: index + segment.length, text: segment });
      index = Math.max(end, index + 1);
    }
    return runs;
  }

  function runToLatex(text) {
    let out = "";
    for (let index = 0; index < text.length;) {
      const rest = text.slice(index);
      if (rest.startsWith("//") && isGeometryParallel(text, index)) { out += "\\parallel "; index += 2; continue; }
      const sqrt = /^√\s*(\d+(?:\.\d+)?|[A-Za-z]|\([^()]*\))/.exec(rest);
      if (sqrt) { out += `\\sqrt{${runToLatex(sqrt[1])}}`; index += sqrt[0].length; continue; }
      const word = /^[A-Za-z]+/.exec(rest);
      if (word) {
        const value = word[0];
        const previous = text.slice(0, index).trimEnd();
        if (FUNCTIONS.has(value)) out += `\\${value === "lg" ? "lg" : value} `;
        else if (UNITS.test(value) && /\d$/.test(previous)) out += `\\,\\mathrm{${value}}`;
        else if (value === "Rt") out += "\\mathrm{Rt}";
        // 18°C、32°F：温度单位用正体。
        else if (/^[CF]$/.test(value) && /°\s*$/.test(previous)) out += `\\mathrm{${value}}`;
        else out += value;
        index += value.length; continue;
      }
      const character = String.fromCodePoint(text.codePointAt(index));
      const ascii = halfWidth(character);
      out += TO_LATEX[ascii] ?? (ascii === "\\" ? "\\backslash " : ascii);
      index += character.length;
    }
    return out;
  }

  // 已标记公式里偶尔混有 Unicode 符号（例如 AI 预览的 $∠BAD＝∠BCD$）；只在显示时换成 KaTeX 能排的写法。
  function explicitToLatex(inner) {
    let out = "";
    for (let index = 0; index < inner.length;) {
      if (inner.startsWith("//", index) && isGeometryParallel(inner, index)) { out += "\\parallel "; index += 2; continue; }
      const command = /^\\[A-Za-z]+|^\\./.exec(inner.slice(index));
      if (command) { out += command[0]; index += command[0].length; continue; }
      const character = String.fromCodePoint(inner.codePointAt(index));
      const mapped = character === "{" || character === "}" ? character : TO_LATEX[character];
      out += mapped ?? halfWidth(character);
      index += character.length;
    }
    return out;
  }

  /* 把文字切成排版片段：text / math / blank / break。 */
  function typesetSegments(value) {
    const source = String(value ?? "");
    const segments = [];
    let cursor = 0;
    const pushParallelogramRun = (text, absoluteStart, { auto, display = false } = {}) => {
      let local = 0;
      // 显式公式中模型有时会写成 \text{▱} / \mathrm{▱} / \mathbf{▱}。把整个命令视作
      // 一个平行四边形符号，避免拆出残缺的大括号再交给 KaTeX。
      const marker = auto ? /▱/g : /\\(?:text|mathrm|mathbf)\s*\{\s*▱\s*\}|▱/g;
      // 展示公式拆成多个子片段时，子片段都按行内公式渲染，再由同一个
      // displayGroup 容器居中；否则每个子片段都会成为独立块而竖向散开。
      const displayGroup = display ? `display:${absoluteStart}:${text.length}` : "";
      const partDisplay = display && !displayGroup;
      for (const match of text.matchAll(marker)) {
        if (match.index > local) {
          const before = text.slice(local, match.index);
          segments.push({ type: "math", start: absoluteStart + local, end: absoluteStart + match.index,
            latex: auto ? runToLatex(before) : explicitToLatex(before), display: partDisplay, displayGroup, auto });
        }
        segments.push({ type: "parallelogram", start: absoluteStart + match.index,
          end: absoluteStart + match.index + match[0].length, display: partDisplay, displayGroup, auto });
        local = match.index + match[0].length;
      }
      if (local < text.length) {
        const after = text.slice(local);
        segments.push({ type: "math", start: absoluteStart + local, end: absoluteStart + text.length,
          latex: auto ? runToLatex(after) : explicitToLatex(after), display: partDisplay, displayGroup, auto });
      }
    };
    const pushPlain = (start, end) => {
      if (end <= start) return;
      const plain = source.slice(start, end);
      const pushPiece = (from, to) => {
        if (to <= from) return;
        const piece = plain.slice(from, to);
        let local = 0;
        for (const run of detectRuns(piece)) {
          if (run.start > local) segments.push({ type: "text", start: start + from + local, end: start + from + run.start });
          const absoluteStart = start + from + run.start;
          if (run.text.includes("▱")) pushParallelogramRun(run.text, absoluteStart, { auto: true });
          else segments.push({ type: "math", start: absoluteStart, end: start + from + run.end, latex: runToLatex(run.text), auto: true });
          local = run.end;
        }
        if (local < piece.length) segments.push({ type: "text", start: start + from + local, end: start + to });
      };
      let cursorInPlain = 0;
      for (const blank of plain.matchAll(BLANK)) {
        pushPiece(cursorInPlain, blank.index);
        const bracket = /^[（(]/.test(blank[0]);
        segments.push({ type: bracket ? "bracket" : "blank", start: start + blank.index, end: start + blank.index + blank[0].length });
        cursorInPlain = blank.index + blank[0].length;
      }
      pushPiece(cursorInPlain, plain.length);
    };
    for (const match of source.matchAll(EXPLICIT_MATH)) {
      pushPlain(cursor, match.index);
      const raw = match[0];
      const display = raw.startsWith("$$") || raw.startsWith("\\[");
      const inner = raw.startsWith("$$") ? raw.slice(2, -2) : raw.startsWith("$") ? raw.slice(1, -1) : raw.slice(2, -2);
      const delimiter = raw.startsWith("$$") || raw.startsWith("\\[") ? 2 : 1;
      if (inner.includes("▱")) pushParallelogramRun(inner, match.index + delimiter, { auto: false, display });
      else segments.push({ type: "math", start: match.index, end: match.index + raw.length, latex: explicitToLatex(inner), display, auto: false });
      cursor = match.index + raw.length;
    }
    pushPlain(cursor, source.length);
    return segments;
  }

  // 国内教材与试卷把平行号印成斜的“//”；排版视图照此显示，比对仍按 ∥ 处理。
  const KATEX_MACROS = { "\\parallel": "\\mathrel{/\\mkern-6mu/}", "\\nparallel": "\\mathrel{/\\mkern-6mu/\\mkern-11mu\\backslash}" };

  function katexReady() {
    return typeof root.katex?.render === "function";
  }

  function renderLatex(node, latex, display) {
    if (!katexReady()) return false;
    try {
      root.katex.render(latex, node, {
        displayMode: Boolean(display), throwOnError: true, strict: "ignore", trust: false, maxSize: 10, maxExpand: 1000,
        macros: KATEX_MACROS
      });
      return true;
    } catch {
      return false;
    }
  }

  // 中文排版里，汉字与公式之间的空格由间距处理；换行只在确有分段意义时保留。
  // before/after 是相邻片段的首尾字符（公式记作 x），让段首段尾的换行也能按上下文判断。
  function tidyText(text, before = "", after = "") {
    let value = `${before}${text}${after}`;
    value = value.replace(/[ \t\u00a0]+/g, " ");
    value = value.replace(/ ?\n ?/g, "\n");
    // A blank line before a sub-question “(1)” / “（2）” is just its own line:
    // the paper prints the parts one under another, not a paragraph apart.
    value = value.replace(/\n{2,}(?=[（(]\d{1,2}[)）]|[①②③④⑤⑥⑦⑧⑨⑩])/g, "\n");
    value = value.replace(/([^\n])\n(?=([^\n]))/g, (all, a, b, offset) => {
      const rest = value.slice(offset + 2);
      const hard = /^[（(]?\d+[)）.．、]/.test(rest) || /^[（(][一二三四五六七八九十]/.test(rest) || /^[①②③④⑤⑥⑦⑧⑨⑩]/.test(rest);
      if (hard) return `${a}\n`;
      return CJK.test(a) || CJK.test(b) ? a : `${a} `;
    });
    value = value.replace(/ (?=[\u3000-\u303f\uff00-\uffef\u4e00-\u9fff])|(?<=[\u3000-\u303f\uff00-\uffef\u4e00-\u9fff]) /g, "");
    value = value.slice(before.length, value.length - after.length);
    if (before === "x") value = value.replace(/^ /, "");
    if (after === "x") value = value.replace(/ $/, "");
    return value;
  }

  function appendMarked(parent, text, start, marks, markClass) {
    // marks: [{start,end,kind}] 绝对位置；把 text（起点 start）切片包进 <mark>。
    let cursor = 0;
    const relevant = marks.filter((mark) => mark.end > start && mark.start < start + text.length)
      .sort((x, y) => x.start - y.start);
    for (const mark of relevant) {
      const from = Math.max(0, mark.start - start);
      const to = Math.min(text.length, mark.end - start);
      if (from > cursor) parent.append(document.createTextNode(text.slice(cursor, from)));
      if (to > Math.max(from, cursor)) {
        const element = document.createElement("mark");
        element.className = `${markClass} ${mark.kind || ""}`.trim();
        element.textContent = text.slice(Math.max(from, cursor), to);
        parent.append(element);
      }
      cursor = Math.max(cursor, to);
    }
    if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
  }

  /*
   * tidyText 只动空白（合并空格、去掉汉字旁的空格和软换行），字符本身不变。
   * 把 raw 上的标记（绝对位置，raw 起点 start）按非空白字符对到 text 上；
   * 对不上，或某个标记只盖住空白时返回 null，由调用方整段标出。
   */
  function tidiedMarks(raw, text, start, marks) {
    const at = [];
    let j = 0;
    for (let i = 0; i < raw.length; i += 1) {
      if (/\s/.test(raw[i])) continue;
      while (j < text.length && /\s/.test(text[j])) j += 1;
      if (j >= text.length || text[j] !== raw[i]) return null;
      at[i] = j;
      j += 1;
    }
    const mapped = [];
    for (const mark of marks) {
      let first = -1;
      let last = -1;
      for (let i = Math.max(0, mark.start - start); i < Math.min(raw.length, mark.end - start); i += 1) {
        if (at[i] === undefined) continue;
        if (first < 0) first = at[i];
        last = at[i];
      }
      if (first < 0) return null;
      mapped.push({ ...mark, start: first, end: last + 1 });
    }
    return mapped;
  }

  // ---------------------------------------------------------------- 表格
  // 格子里只有文字和数字的表格是题目文字：Markdown 管道表（| a | b |，第一行后
  // 可有 |---| 表头分隔行），有合并单元格时用只含 tr/td/th 与 rowspan/colspan 的 HTML。

  const TABLE_SEPARATOR = /^\s*:?-{3,}:?\s*$/;
  const HTML_TABLE = /<table\b[^>]*>[\s\S]*?<\/table\s*>/gi;

  function isTableLine(line) {
    const text = line.trim();
    return text.startsWith("|") && (text.match(/\|/g) || []).length >= 2;
  }

  // 一行拆成格子，并记下每格在原文里的位置；$…$ 里的 |（如 |x|）和 \| 不算分隔。
  function tableRowCells(line, lineStart) {
    const cells = [];
    let index = 0;
    const leading = line.length - line.trimStart().length;
    index = leading;
    if (line[index] === "|") index += 1;
    let end = line.trimEnd().length;
    if (end > index && line[end - 1] === "|" && line[end - 2] !== "\\") end -= 1;
    let cellStart = index;
    let inMath = false;
    const push = (from, to) => {
      let a = from;
      let b = to;
      while (a < b && /\s/.test(line[a])) a += 1;
      while (b > a && /\s/.test(line[b - 1])) b -= 1;
      cells.push({ start: lineStart + a, end: lineStart + b, text: line.slice(a, b) });
    };
    for (; index < end; index += 1) {
      const character = line[index];
      if (character === "\\" && line[index + 1] === "|" && !inMath) { index += 1; continue; }
      if (character === "$") inMath = !inMath;
      if (character === "|" && !inMath) { push(cellStart, index); cellStart = index + 1; }
    }
    push(cellStart, end);
    return cells;
  }

  function decodeEntities(value) {
    return value.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (all, code) => {
      const lower = code.toLowerCase();
      if (lower === "amp") return "&";
      if (lower === "lt") return "<";
      if (lower === "gt") return ">";
      if (lower === "quot") return '"';
      if (lower === "apos") return "'";
      if (lower === "nbsp") return " ";
      const number = lower.startsWith("#x") ? parseInt(lower.slice(2), 16) : parseInt(lower.slice(1), 10);
      return Number.isFinite(number) && number > 0 && number < 0x110000 ? String.fromCodePoint(number) : all;
    });
  }

  function htmlTableRows(raw, offset) {
    const rows = [];
    for (const row of raw.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr\s*>/gi)) {
      const rowInner = offset + row.index + row[0].indexOf(">") + 1;
      const cells = [];
      for (const cell of row[1].matchAll(/<(td|th)\b([^>]*)>([\s\S]*?)<\/\1\s*>/gi)) {
        const start = rowInner + cell.index + cell[0].indexOf(">") + 1;
        const span = (name) => {
          const match = new RegExp(`\\b${name}\\s*=\\s*["']?(\\d{1,2})`, "i").exec(cell[2]);
          return match ? Math.max(1, Math.min(20, Number(match[1]))) : 1;
        };
        const text = decodeEntities(cell[3].replace(/<br\s*\/?>/gi, " ").replace(/<[^>]+>/g, ""))
          .replace(/\s+/g, " ").trim();
        cells.push({ start, end: start + cell[3].length, text, rowspan: span("rowspan"), colspan: span("colspan"),
          header: cell[1].toLowerCase() === "th", decoded: true });
      }
      if (cells.length) rows.push(cells.slice(0, 20));
      if (rows.length >= 60) break;
    }
    return rows;
  }

  // 文本里的全部表格：{start, end, rows: [[{start, end, text, …}]], header}
  function findTables(source) {
    const found = [];
    for (const match of source.matchAll(HTML_TABLE)) {
      const rows = htmlTableRows(match[0], match.index);
      if (rows.length) found.push({ start: match.index, end: match.index + match[0].length, rows, header: false });
    }
    const lines = source.split("\n");
    let offset = 0;
    const starts = lines.map((line) => { const start = offset; offset += line.length + 1; return start; });
    for (let index = 0; index < lines.length;) {
      if (!isTableLine(lines[index]) || found.some((table) => table.start <= starts[index] && starts[index] < table.end)) {
        index += 1;
        continue;
      }
      let last = index;
      while (last + 1 < lines.length && isTableLine(lines[last + 1])) last += 1;
      if (last > index) {
        let rows = lines.slice(index, last + 1).map((line, row) => tableRowCells(line, starts[index + row]));
        const header = rows.length > 1 && rows[1].some((cell) => cell.text) && rows[1].every((cell) => !cell.text || TABLE_SEPARATOR.test(cell.text));
        if (header) rows = [rows[0], ...rows.slice(2)];
        found.push({ start: starts[index], end: starts[last] + lines[last].length, rows, header });
      }
      index = last + 1;
    }
    return found.sort((a, b) => a.start - b.start);
  }

  function renderTable(doc, table, marks) {
    const wrap = doc.createElement("span");
    wrap.className = "qb-table-wrap";
    const element = doc.createElement("table");
    element.className = "qb-table";
    const width = Math.max(...table.rows.map((row) => row.reduce((sum, cell) => sum + (cell.colspan || 1), 0)));
    table.rows.forEach((row, rowIndex) => {
      const tr = doc.createElement("tr");
      let used = 0;
      row.forEach((cell) => {
        const td = doc.createElement(cell.header || (table.header && rowIndex === 0) ? "th" : "td");
        if (cell.rowspan > 1) td.rowSpan = cell.rowspan;
        if (cell.colspan > 1) td.colSpan = cell.colspan;
        used += cell.colspan || 1;
        const hits = marks.filter((mark) => mark.end > cell.start && mark.start < cell.end);
        if (cell.decoded) {
          renderTypesetText(td, cell.text, { empty: "" });
          if (hits.length) td.classList.add("qb-cell-marked");
        } else {
          const text = cell.text.replace(/\\\|/g, "|");
          const shifted = text === cell.text ? hits.map((mark) => ({ ...mark, start: mark.start - cell.start, end: mark.end - cell.start })) : [];
          renderTypesetText(td, text, { marks: shifted, empty: "" });
          if (hits.length && !shifted.length) td.classList.add("qb-cell-marked");
        }
        td.classList.remove("qb-typeset", "is-empty");
        tr.append(td);
      });
      // 少写了格子的行补成完整的一行，表格线才对得齐。
      if (!table.rows.some((other) => other.some((cell) => (cell.rowspan || 1) > 1))) {
        for (; used < width; used += 1) tr.append(doc.createElement(table.header && rowIndex === 0 ? "th" : "td"));
      }
      element.append(tr);
    });
    wrap.append(element);
    return wrap;
  }

  /*
   * 排版视图。题目文字里的表格排成真正的表格，其余文字照常排版。
   */
  function renderTypeset(node, value, options = {}) {
    const source = String(value ?? "");
    const tables = source.includes("|") || /<table/i.test(source) ? findTables(source) : [];
    if (!tables.length) return renderTypesetText(node, source, options);
    const doc = node.ownerDocument || document;
    const marks = options.marks || [];
    node.replaceChildren();
    node.classList.add("qb-typeset", "has-table");
    node.classList.remove("is-empty");
    const text = (from, to) => {
      let start = from;
      let end = to;
      while (start < end && source[start] === "\n") start += 1;
      while (end > start && source[end - 1] === "\n") end -= 1;
      if (!source.slice(start, end).trim()) return;
      const run = doc.createElement("span");
      run.className = "qb-text-run";
      renderTypesetText(run, source.slice(start, end), {
        empty: "",
        marks: marks.filter((mark) => mark.end > start && mark.start < end)
          .map((mark) => ({ ...mark, start: mark.start - start, end: mark.end - start }))
      });
      node.append(run);
    };
    let cursor = 0;
    tables.forEach((table) => {
      text(cursor, table.start);
      node.append(renderTable(doc, table, marks));
      cursor = table.end;
    });
    text(cursor, source.length);
    return node;
  }

  const SPOT_OPEN = "\uE000";
  const SPOT_CLOSE = "\uE001";

  /*
   * A formula with the characters of one mark coloured (a disputed exponent in
   * “x^{3}”).  Only for a whole $…$ / $$…$$ / \(…\) / \[…\] formula, never
   * splitting a command or a brace group; null otherwise, and the caller falls
   * back to boxing the whole formula.
   */
  function colourLatex(source, segment, mark) {
    if (segment.auto) return null;
    const raw = source.slice(segment.start, segment.end);
    const delimiter = raw.startsWith("$$") || raw.startsWith("\\[") || raw.startsWith("\\(") ? 2 : raw.startsWith("$") ? 1 : 0;
    if (!delimiter) return null;
    const inner = raw.slice(delimiter, raw.length - delimiter);
    const from = mark.start - segment.start - delimiter;
    const to = mark.end - segment.start - delimiter;
    if (from < 0 || to > inner.length || from >= to) return null;
    const piece = inner.slice(from, to);
    if (/[{}\\$]/.test(piece)) return null;
    // Not inside a command name (“\\frac”, “\\infty”).
    const before = inner.slice(0, from);
    if (/\\[A-Za-z]*$/.test(before) && /^[A-Za-z]/.test(piece)) return null;
    const latex = explicitToLatex(`${before}${SPOT_OPEN}${piece}${SPOT_CLOSE}${inner.slice(to)}`);
    return latex.replace(SPOT_OPEN, "{\\textcolor{#c2410c}{").replace(SPOT_CLOSE, "}}");
  }

  /*
   * 排版一段文字。marks 中与公式重叠的，会把整段公式包进同色框（公式无法逐字标色）。
   */
  function renderTypesetText(node, value, { marks = [], empty = "（空）" } = {}) {
    const source = String(value ?? "");
    node.replaceChildren();
    node.classList.add("qb-typeset");
    if (!source.trim()) { node.append(document.createTextNode(empty)); node.classList.add("is-empty"); return node; }
    const segments = typesetSegments(source);
    let displayGroup = "";
    let displayGroupNode = null;
    segments.forEach((segment, index) => {
      let target = node;
      if (segment.displayGroup) {
        if (displayGroup !== segment.displayGroup) {
          displayGroup = segment.displayGroup;
          displayGroupNode = document.createElement("span");
          displayGroupNode.className = "qb-math-display-group";
          node.append(displayGroupNode);
        }
        target = displayGroupNode;
      } else {
        displayGroup = "";
        displayGroupNode = null;
      }
      const overlapping = marks.filter((mark) => mark.end > segment.start && mark.start < segment.end);
      if (segment.type === "parallelogram") {
        // KaTeX 把 U+25B1 当成无字形字符；映射到 \\square 又会把平行四边形
        // 错画成正方形。这个语义符号由浏览器的 Unicode 符号字体直接绘制，
        // 相邻字母仍交给 KaTeX 排版，且真正的 □ / \\square 保持原样。
        const symbol = document.createElement("span");
        symbol.className = `qb-parallelogram-symbol${segment.display ? " is-display" : ""}`;
        symbol.textContent = "▱";
        symbol.title = "平行四边形符号";
        if (overlapping.length) {
          const mark = document.createElement("mark");
          mark.className = `qb-mark ${overlapping[0].kind || ""}`.trim();
          mark.append(symbol);
          target.append(mark);
        } else target.append(symbol);
        return;
      }
      if (segment.type === "math") {
        const span = document.createElement("span");
        span.className = `qb-math${segment.auto ? " is-auto" : ""}${segment.display ? " is-display" : ""}`;
        const raw = source.slice(segment.start, segment.end);
        span.dataset.raw = raw;
        const exact = overlapping.find((mark) => mark.exact);
        const coloured = exact ? colourLatex(source, segment, exact) : null;
        if (coloured && renderLatex(span, coloured, segment.display)) {
          // The disputed character itself is coloured inside the formula.
        } else if (!renderLatex(span, segment.latex, segment.display)) {
          span.className = "qb-math-fallback";
          span.textContent = raw.replace(/^\$+|\$+$/g, "");
        }
        if (overlapping.length) {
          const mark = document.createElement("mark");
          mark.className = `qb-mark qb-mark-math ${overlapping[0].kind || ""}`.trim();
          mark.append(span);
          target.append(mark);
        } else target.append(span);
        return;
      }
      if (segment.type === "bracket") {
        const bracket = document.createElement("span");
        bracket.className = "qb-bracket";
        bracket.textContent = "（\u3000\u3000）";
        if (overlapping.length) {
          const mark = document.createElement("mark");
          mark.className = `qb-mark ${overlapping[0].kind || ""}`.trim();
          mark.append(bracket);
          node.append(mark);
        } else node.append(bracket);
        return;
      }
      if (segment.type === "blank") {
        const blank = document.createElement("span");
        blank.className = "qb-blank";
        blank.setAttribute("aria-label", "填空");
        node.append(blank);
        return;
      }
      const neighbour = (other, last) => {
        if (!other) return "";
        if (other.type !== "text") return "x";
        const value = source.slice(other.start, other.end).replace(/\s+/g, "");
        return last ? value.slice(-1) : value.slice(0, 1);
      };
      const raw = source.slice(segment.start, segment.end);
      const text = tidyText(raw, neighbour(segments[index - 1], true), neighbour(segments[index + 1], false));
      if (!overlapping.length) node.append(document.createTextNode(text));
      else if (text === raw) appendMarked(node, raw, segment.start, overlapping, "qb-mark");
      else {
        // Spaces and soft line breaks were tidied: mark the same characters in the tidied text.
        const mapped = tidiedMarks(raw, text, segment.start, overlapping);
        if (mapped) appendMarked(node, text, 0, mapped, "qb-mark");
        else {
          const mark = document.createElement("mark");
          mark.className = `qb-mark ${overlapping[0].kind || ""}`.trim();
          mark.textContent = text;
          node.append(mark);
        }
      }
    });
    // 公式后紧跟的中文标点不应单独折到下一行行首。
    node.querySelectorAll(".qb-math, .qb-mark-math").forEach((math) => {
      const next = math.nextSibling;
      if (!next || next.nodeType !== 3) return;
      const punctuation = /^[，。、；：？！）》」』】,.;:?!)]+/.exec(next.nodeValue);
      if (!punctuation) return;
      const holder = document.createElement("span");
      holder.className = "qb-nobr";
      math.replaceWith(holder);
      holder.append(math, document.createTextNode(punctuation[0]));
      next.nodeValue = next.nodeValue.slice(punctuation[0].length);
    });
    return node;
  }

  /* 逐字原文视图：保留每个字符；$ 与 LaTeX 命令淡色显示；marks 精确到字。 */
  function renderLiteral(node, value, { marks = [], empty = "（空）" } = {}) {
    const source = String(value ?? "");
    node.replaceChildren();
    node.classList.add("qb-literal");
    if (!source) { node.append(document.createTextNode(empty)); node.classList.add("is-empty"); return node; }
    const quiet = [];
    for (const match of source.matchAll(/\$|\\[A-Za-z]+|\\[()[\]{}]/g)) quiet.push({ start: match.index, end: match.index + match[0].length, kind: "qb-syntax" });
    const all = [...marks, ...quiet.filter((item) => !marks.some((mark) => mark.start < item.end && item.start < mark.end))];
    appendMarked(node, source, 0, all, "qb-mark");
    node.querySelectorAll("mark.qb-syntax").forEach((element) => { element.className = "qb-syntax"; });
    return node;
  }

  // ---------------------------------------------------------------- 题面

  function displayWidth(value) {
    const text = String(value ?? "").replace(/\$|\\(?:left|right|,|;|quad)/g, "").replace(/\\[A-Za-z]+/g, "x").replace(/[{}^_]/g, "");
    let width = 0;
    for (const character of text) width += CJK.test(character) ? 2 : character.trim() ? 1 : 0.5;
    return width;
  }

  // 仿照纸质试卷：选项短就一行排完（四个或五个），中等两个，长则一行一个。
  function optionColumns(options, figures = []) {
    const keys = shownOptionKeys(options, figures);
    const values = keys.map((key) => options?.[key] ?? "");
    const optionFigures = figures.filter((figure) => OPTION_KEYS.includes(figure.slot));
    if (optionFigures.length >= 3) return keys.length;
    const widest = Math.max(0, ...values.map(displayWidth));
    if (widest <= (keys.length === 5 ? 8 : 10)) return keys.length;
    if (widest <= 26) return 2;
    return 1;
  }

  /*
   * 选项列数还要看实际可用宽度：卡片右栏比试卷窄，按纸面规则排两列时
   * “∠ABD=∠CBD” 这类选项会被硬折行。渲染后按容器宽度逐级降为 2 列或 1 列。
   */
  function fitOptions(root) {
    const lists = root?.querySelectorAll ? [...root.querySelectorAll(".qb-options[data-widest]")] : [];
    // Measure every list first, then set the classes: measuring after each change
    // made the browser lay out the whole page again for every list (slow on a 600-card book).
    const sizes = lists.map((list) => [list.clientWidth, parseFloat(getComputedStyle(list).fontSize) || 16]);
    lists.forEach((list, index) => {
      const [width, fontSize] = sizes[index];
      if (!width) return;
      const preferred = Number(list.dataset.cols || 1);
      const widest = Number(list.dataset.widest || 0);
      const need = (cols) => cols * (widest * fontSize * 0.5 + fontSize * 2.4) + (cols - 1) * fontSize;
      const fitted = [5, 4, 2, 1].find((cols) => cols <= preferred && (cols === 1 || need(cols) <= width)) || 1;
      const set = ["cols-1", "cols-2", "cols-4", "cols-5"].filter((name) => list.classList.contains(name));
      if (set.length === 1 && set[0] === `cols-${fitted}`) return;  // already so: no style change
      list.classList.remove("cols-1", "cols-2", "cols-4", "cols-5");
      list.classList.add(`cols-${fitted}`);
    });
  }

  function figureElement(figure, resolveUrl, action) {
    const frame = document.createElement("figure");
    frame.className = "qb-figure";
    const image = document.createElement("img");
    image.loading = "lazy";
    image.alt = figure.slot === "stem" ? "题干配图" : `选项 ${figure.slot} 配图`;
    image.src = resolveUrl ? resolveUrl(figure) : figure.url;
    frame.append(image);
    // The review page can offer an action under a figure (turn a table crop into text).
    const extra = typeof action === "function" ? action(figure) : null;
    if (extra) {
      const caption = document.createElement("figcaption");
      caption.className = "qb-figure-action";
      caption.append(extra);
      frame.append(caption);
    }
    return frame;
  }

  const TYPE_NAMES = {
    single_choice: "单选题", multiple_choice: "多选题", fill_blank: "填空题", true_false: "判断题", free_response: "解答题",
    short_answer: "解答题", unknown: "题型待核对"
  };

  /*
   * 渲染完整题面。content: {number, question_type, stem, options, answer, analysis, figures}
   * opts: {showNumber, number, resolveUrl, showAnswer: "none"|"collapsed"|"open", literal}
   */
  function renderQuestion(container, content, opts = {}) {
    const doc = container.ownerDocument || document;
    const make = (tag, className, text) => {
      const element = doc.createElement(tag);
      if (className) element.className = className;
      if (text !== undefined) element.textContent = text;
      return element;
    };
    const view = opts.literal ? renderLiteral : renderTypeset;
    container.replaceChildren();
    container.classList.add("qb-question");
    const figures = Array.isArray(content.figures) ? content.figures : [];
    const stem = make("div", "qb-stem");
    if (opts.showNumber !== false) stem.append(make("span", "qb-number", `${opts.number ?? content.number ?? "?"}.`));
    const stemBody = make("span", "qb-stem-body");
    const marks = opts.marks || {};
    view(stemBody, content.stem, { empty: "（题干为空）", marks: marks.stem || [] });
    stem.append(stemBody);
    container.append(stem);
    const stemFigures = figures.filter((figure) => figure.slot === "stem");
    if (stemFigures.length) {
      const row = make("div", "qb-figures");
      stemFigures.forEach((figure) => row.append(figureElement(figure, opts.resolveUrl, opts.figureAction)));
      container.append(row);
    }
    const options = content.options && typeof content.options === "object" ? content.options : {};
    const hasOptions = OPTION_KEYS.some((key) => String(options[key] ?? "").trim() || figures.some((figure) => figure.slot === key));
    if (hasOptions) {
      const keys = shownOptionKeys(options, figures);
      const columns = optionColumns(options, figures);
      const list = make("ol", `qb-options cols-${columns}`);
      if (!figures.some((figure) => OPTION_KEYS.includes(figure.slot))) {
        list.dataset.cols = String(columns);
        list.dataset.widest = String(Math.max(0, ...keys.map((key) => displayWidth(options[key] ?? ""))));
      }
      keys.forEach((key) => {
        const item = make("li", "qb-option");
        item.append(make("span", "qb-option-label", `${key}.`));
        const body = make("span", "qb-option-body");
        view(body, options[key], { empty: figures.some((figure) => figure.slot === key) ? "" : "（空）", marks: marks[key] || [] });
        item.append(body);
        figures.filter((figure) => figure.slot === key).forEach((figure) => item.append(figureElement(figure, opts.resolveUrl, opts.figureAction)));
        list.append(item);
      });
      container.append(list);
    }
    const mode = opts.showAnswer || "collapsed";
    if (mode !== "none" && (String(content.answer ?? "").trim() || String(content.analysis ?? "").trim() || opts.showEmptyAnswer)) {
      const box = make("details", "qb-answer");
      if (mode === "open") box.open = true;
      box.append(make("summary", "", "答案与解析"));
      const answer = make("p", "qb-answer-row");
      answer.append(make("strong", "", "答案"));
      const answerBody = make("span");
      // 选择题答案“B”“ACD”是选项标号，按正体显示，不当作数学变量排成斜体。
      if (!opts.literal && /^\s*[A-E]{1,5}\s*$/.test(String(content.answer ?? ""))) {
        answerBody.className = "qb-choice-answer";
        answerBody.textContent = String(content.answer).trim();
      } else view(answerBody, content.answer, { empty: "原卷未提供" });
      answer.append(answerBody);
      const analysis = make("div", "qb-answer-row");
      analysis.append(make("strong", "", "解析"));
      const analysisBody = make("div", "qb-analysis");
      view(analysisBody, content.analysis, { empty: "原卷未提供" });
      analysis.append(analysisBody);
      box.append(answer, analysis);
      container.append(box);
    }
    return container;
  }

  // 在给定边界内寻找最大的可用缩放。measure(scale) 可以测量由多段截图、
  // 跨页连接条和标注层共同组成的真实 DOM，因此不假设内容会严格等比缩放。
  function fitScale(measure, availableWidth, availableHeight, opts = {}) {
    const minimum = Math.max(0.01, Number(opts.min ?? 0.04));
    const maximum = Math.max(minimum, Number(opts.max ?? 1));
    const steps = Math.max(1, Number(opts.steps ?? 10));
    if (typeof measure !== "function" || !(availableWidth > 0) || !(availableHeight > 0)) return maximum;
    const fits = (scale) => {
      const size = measure(scale) || {};
      return Number(size.width) <= availableWidth + 0.5 && Number(size.height) <= availableHeight + 0.5;
    };
    if (fits(maximum)) return maximum;
    if (!fits(minimum)) return minimum;
    let low = minimum;
    let high = maximum;
    for (let index = 0; index < steps; index += 1) {
      const middle = (low + high) / 2;
      if (fits(middle)) low = middle;
      else high = middle;
    }
    return low;
  }

  return {
    OPTION_KEYS, shownOptionKeys, LEVEL_TEXT, TYPE_NAMES, KATEX_MACROS,
    comparisonUnits, compareTexts, comparisonHunks, stripQuestionNumber,
    detectRuns, runToLatex, explicitToLatex, typesetSegments, colourLatex,
    renderTypeset, renderLiteral, renderQuestion, optionColumns, displayWidth, fitScale, fitOptions, findTables, tidyText,
    tidiedMarks
  };
});
