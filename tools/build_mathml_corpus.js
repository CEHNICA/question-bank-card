"use strict";
// 常见高中数学公式的 LaTeX。KaTeX 渲染成 MathML 后存成固件，
// 让 Python 侧的 Word 转换测试对着「生产环境真正的形状」跑，而不是手写的理想形状。
// 用法：node tools\build_mathml_corpus.js
const fs = require("node:fs");
const path = require("node:path");

const katex = require(path.join(__dirname, "..", "frontend", "vendor", "katex", "katex.min.js"));

const LATEX = [
  // 重音 / 标记
  String.raw`\overline{z}+z`,
  String.raw`\overline{AB}`,
  String.raw`\overline{AB}=\overrightarrow{AB}`,
  String.raw`\acute{a}`,
  String.raw`\grave{a}`,
  String.raw`\vec{a}`,
  String.raw`\hat{y}`,
  String.raw`\bar{x}`,
  String.raw`\dot{x}`,
  String.raw`\tilde{x}`,
  String.raw`\overrightarrow{AB}\cdot\overrightarrow{CD}`,
  String.raw`\hat{y}=\ln x`,
  // 根式 / 分式
  String.raw`\sqrt{x+1}`,
  String.raw`\frac{a}{b}`,
  String.raw`\frac{-b\pm\sqrt{b^2-4ac}}{2a}`,
  String.raw`\frac{\pi}{3}`,
  String.raw`\sqrt[3]{27}`,
  // 上下标
  String.raw`x^2+y^2`,
  String.raw`a_1+a_2+\cdots+a_n`,
  String.raw`x^{2}+y^{2}\leqslant r^{2}`,
  String.raw`e^{x}`,
  String.raw`a^{n}`,
  // 上下限
  String.raw`\lim_{x\to 0}\frac{\sin x}{x}`,
  String.raw`\sum_{n=1}^{\infty}\frac{1}{n^{2}}`,
  String.raw`\int_{0}^{1}x^{2}\,dx`,
  String.raw`\int_{a}^{b}f(x)\,dx`,
  // 定界符 / 集合
  String.raw`\left(\frac{a}{b}\right)`,
  String.raw`\left|x-1\right|`,
  String.raw`[a,b]`,
  String.raw`\{x\mid x>0\}`,
  String.raw`A\cap B`,
  // 关系 / 符号
  String.raw`a\neq b`,
  String.raw`a\approx b`,
  String.raw`a\equiv b`,
  String.raw`x\propto y`,
  String.raw`\alpha+\beta+\gamma=\pi`,
  String.raw`40^\circ`,
  String.raw`\mathrm{i}`,
  String.raw`\log_{2}8`,
  // 函数 / 三角
  String.raw`\sin\alpha+\cos\beta`,
  String.raw`\ln x`,
  String.raw`f(x)=\frac{1}{x}`,
  String.raw`\binom{n}{k}`,
];

const OPTIONS = { output: "mathml", throwOnError: true, strict: "ignore", maxSize: 10, maxExpand: 1000 };
const corpus = [];
for (const latex of LATEX) {
  const html = katex.renderToString(latex, OPTIONS);
  const match = html.match(/<math\b[\s\S]*?<\/math>/);
  if (!match) throw new Error(`no <math> for ${latex}`);
  corpus.push({ latex, mathml: match[0] });
}

const target = path.join(__dirname, "..", "backend", "core", "test_mathml_corpus.json");
fs.writeFileSync(target, JSON.stringify(corpus, null, 1) + "\n", "utf8");
console.log(`${corpus.length} formulas -> ${target}`);
