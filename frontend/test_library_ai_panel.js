"use strict";

/*
 * 1.13.4 「标签与答案」面板的两处改动（静态回归）：
 *   1. 模型配置不再每次自动展开 —— 展开 1669px，整个面板 2169px，1000px 的视口放不下。
 *   2. 知识点目录从「显示与导出」页搬到这儿，并给了一份只读预览。
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");

const panelJs = fs.readFileSync(require.resolve("./library-ai-settings.js"), "utf8");
const indexHtml = fs.readFileSync(require.resolve("./index.html"), "utf8");
const appJs = fs.readFileSync(require.resolve("./app.js"), "utf8");

// ---- 1. 模型配置不再自动展开
assert.doesNotMatch(panelJs, /if \(isAPI\(\)\) \$?\("libraryAIAdvanced"\)\.open = true/,
  "不再无条件展开模型配置");
assert.doesNotMatch(panelJs, /libraryAIAdvanced"\)\.open = body\.mode === "api" \|\|/,
  "也不再看 mode 强行展开");
assert.match(panelJs, /function syncAdvanced\(apiReady\)/, "改成按需展开");
assert.match(panelJs, /if \(!apiReady && !state\.advancedTouched && \(!state\.current\?\.model \|\| !state\.current\?\.base_url\)\) \$?\("libraryAIAdvanced"\)\.open = true/,
  "未配好且地址或模型缺失时才替用户展开；未测试但已有配置继续保持简洁");
assert.match(panelJs, /ontoggle = \(event\) => \{ state\.advancedTouched = event\.isTrusted; \}/,
  "用户自己开合过就不再插手，并且要分清是他点的还是代码设的");

// 面板被限高这件事很容易被后来的样式改回「按内容长高」，那就白改了：
// 1366×768 上「保存 API 设置」会掉到 941px 处，用户看不到。
assert.match(panelJs, /\.library-ai-panel\{min-width:0;display:flex;flex-direction:column;height:100%;min-height:0\}/,
  "挂载在设置页里的面板要自己撑满、自己滚");
assert.match(panelJs, /\.library-ai-panel \.library-ai-body\{padding:0;background:none;gap:\d+px;overflow:auto;min-height:0\}/,
  "正文区在挂载模式下也得保留 auto，不能被改回 visible");
assert.match(panelJs, /\.library-ai-form\{display:flex;flex-direction:column;min-height:0;flex:1\}/,
  "表单要占满面板，底栏才有理由钉在原地");
const stylesCss = fs.readFileSync(require.resolve("./styles.css"), "utf8");
assert.match(stylesCss, /\.library-ai-mount \{ display: flex; flex-direction: column; flex: 1; min-height: 0; \}/,
  "挂载点本身要能撑开，否则 height:100% 无处可算");
assert.match(stylesCss, /#credentialAnswerPanel \{ display: flex; flex-direction: column; \}/,
  "答案那一页的外层是 flex 列，面板才有确定高度可依");

// ---- 2. 知识点目录搬进这个面板
assert.ok(!indexHtml.includes('id="knowledgeDetails"'), "「显示与导出」页不再挂知识点目录的入口");
assert.ok(!indexHtml.includes('id="featureNote"'), "那条只剩文件路径的说明一起搬走了");
assert.ok(!appJs.includes("knowledgeDetails"), "app.js 里对应的显隐逻辑一并删干净");
assert.match(panelJs, /id="libraryAIKnowledge"[^>]*aria-label="知识点目录"/, "面板里有知识点目录这一段");
assert.match(panelJs, /id="libraryAIKnowledgeOpen"[^>]*>查看目录</, "有「查看目录」的入口");
assert.match(panelJs, /\/api\/settings\/knowledge"/, "预览读的是目录接口");
assert.match(panelJs, /X-QB-Request": "1"/, "目录接口也要本机页面的请求头");
assert.match(panelJs, /item\.point\.includes\(keyword\) \|\| \(item\.chapter \|\| ""\)\.includes\(keyword\)/,
  "搜章名也要搜得到：椭圆、双曲线、抛物线都不含「圆锥」");
assert.match(panelJs, /catalogue-dialog\[open\]\{display:grid;grid-template-rows:auto minmax\(0,1fr\) auto\}/,
  "目录自己滚，底栏那句说明任何时候都在");
assert.match(panelJs, /if \(id === "libraryAITags"\) \{\s*\$\("libraryAIKnowledge"\)\.hidden = !\$\(id\)\.checked/,
  "关掉标签功能，这一段当场就跟着藏起来");
assert.match(panelJs, /if \(\$\(id\)\.checked && !catalogue\) void loadCatalogue\(\)/,
  "功能关着时服务端不回目录大小（读一次就会替用户把那个文件建出来），打开这一段得自己去问一次 —— 否则用户先看见一句永远不兑现的「正在读取…」");
assert.match(panelJs, /showKnowledgeSummary\(body\.knowledge\);[\s\S]{0,140}\$\("libraryAIKnowledge"\)\.hidden = !body\.features\.knowledge_tags/,
  "藏着的时候也把数字写进去：用户当场打开开关就该看见真实数量，而不是等一次没人发起的读取");

// ---- 3. 生成要花钱，数字得摆在开关旁边
assert.match(panelJs, /每道新题入库会调用 \$\{perQuestion\} 次服务/, "写明每道新题会调用几次服务");
assert.match(panelJs, /题库里还差 \$\{backlog\.tags\} 道有标签/, "标签那一行带着还差几道");
assert.match(panelJs, /题库里还差 \$\{backlog\.answer\} 道有答案/, "答案那一行带着还差几道");
assert.match(panelJs, /const tagsRunning = on\("libraryAITags"\) && on\("libraryAITagsIntake"\)/,
  "功能没开，「入库时生成」就不该算进调用次数");
const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");
assert.match(libraryJs, /由已配置的 API 生成，每道题一次调用，会用到服务额度/,
  "题库页那把批量生成的按钮也写明了一次一题；不写「每道题一次调用」就等于没提示");
assert.doesNotMatch(panelJs, /会用到服务额度/, "面板里旧的那句零信息量的提示已经换成了具体数字");

