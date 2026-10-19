"use strict";
// 题库页三处界面改动（1.25.37）。
//
// 老师的原话：
//   1「应该加上定位题目的按钮」
//   2「这个回到上方的按钮，可以和审核界面的回到上方的按钮一致，
//       并最好设计成，你鼠标移动过去，他才会显示出来」
//   3「搜索框太长」
//
// 每一处都单独立一组断言：这三样都是「看一眼就知道对不对」的东西，
// 一旦改回去（比如又把搜索框撑满、或把图标换回文字）要立刻报出来。
const assert = require("node:assert/strict");
const fs = require("node:fs");

const js = fs.readFileSync(require.resolve("./library.js"), "utf8");
const html = fs.readFileSync(require.resolve("./library.html"), "utf8");
const css = fs.readFileSync(require.resolve("./library.css"), "utf8");
const styles = fs.readFileSync(require.resolve("./styles.css"), "utf8");
const indexHtml = fs.readFileSync(require.resolve("./index.html"), "utf8");

// ── 一、面板上的「定位题目」 ──
// 1.25.19 把「定位题卡」当废话删了 —— 那时卡片就在眼前，伸手能点。
// 现在这面板是用来核对 AI 标签的，干完活眼睛已经在面板上，
// 左边那道题往往已经滚出屏幕。所以加回来，但换个位置和说法。
//
// 位置踩过一次坑：第一版建在「标签」区标题栏里，而那一区**只有题打过标签才渲染** ——
// 实机一验，没标签的题上这颗按钮根本不出现（静态断言查不出来）。
// 所以它必须是面板头部那颗常驻按钮，跟「收起」并排。
assert.match(html, /<button id="locateQuestion"[^>]*class="[^"]*inspector-locate[^"]*"[^>]*>定位题目<\/button>/,
  "「定位题目」在面板头部、跟「收起」并排 —— 每道题都在，不挑题");
assert.match(html, /<div><h2>当前题目<\/h2><\/div>[\s\S]{0,400}?<button id="locateQuestion"/,
  "它紧跟在「当前题目」标题后面（收起按钮之前），不是藏在下面某个分区里");
assert.doesNotMatch(js, /sectionTitle\.append\(locateQuestionButton/,
  "不能再挂到「标签」区 —— 那一区没标签就不渲染，按钮等于不存在");
assert.match(js, /initLocateQuestion\(\);/, "按钮是常驻元素，事件在启动时挂一次");
// 上面两条只证明「代码里有 initLocateQuestion() 这个调用」，证不了它真的被执行了。
// 它在文件末尾那一大串事件绑定里 —— 万一被包在某个永远不进的分支里（或者后面那句
// 渲染成注释），测试照样全绿。所以要把它所在的那一段切出来单独看。
const bootStart = js.indexOf('  $("closeInspector").addEventListener("click"');
const bootSrc = js.slice(bootStart, js.indexOf('  $("inspectorScrim").addEventListener("click"', bootStart));
assert.notEqual(bootSrc.length, 0, "没切到面板按钮的启动那段；写法改了就把这里一并更新");
assert.match(bootSrc, /initLocateQuestion\(\);/,
  "它就挂在「收起」按钮旁边，必须真在启动时执行 —— 只是字符串存在不算数");
assert.match(js, /function initLocateQuestion\(\) \{[\s\S]*?addEventListener\("click"/,
  "点击确实挂上了");

// 题还在眼前时，这颗按钮不该占地方；滚远了要出来。
assert.match(js, /function syncLocateButton\(item\) \{[\s\S]*?button\.hidden = !item \|\| inView\(/,
  "没选题时藏起来；这道题就在屏上时也藏起来；滚出屏幕才出现");
// inView 第一版是 selectInspectorQuestion 里的局部常量，syncLocateButton 够不到它，
// 运行时抛 ReferenceError，按钮就一直藏着 —— 而静态断言只查得到那句调用存在。
// 所以要断言它在模块层（有独立的顶层定义），不是被圈在别的函数里。
const inViewDefs = (js.match(/const inView = element =>/g) || []).length;
assert.equal(inViewDefs, 1,
  "inView 只能在模块层定义一次；被圈在别的函数里，syncLocateButton 就够不到它");
const inspectStart = js.indexOf("  function selectInspectorQuestion(");
const inspectSrc = js.slice(inspectStart, js.indexOf("\n  const inView = element =>", inspectStart));
assert.doesNotMatch(inspectSrc, /const inView = element =>/,
  "旧的局部定义已经拿掉了，留着两份会让顺序决定用哪份");
assert.match(js, /\n {2}const inView = element => \{/,
  "inView 提到模块层了（两格缩进，和别的顶层辅助函数一致）");
// 这两条调用必须真的在执行路径上 —— 同样是「字符串存在」不等于「会被调用」。
//
// 切片的坑（踩了两次）：结束锚点 inspectorFact 定义在 renderInspector **之前**
// （1301 行 vs 1355 行），所以 indexOf(end) 必须从 start 往后找，不能全文件找 ——
// 否则切出来是空串，断言失败的原因和真正的问题毫无关系。
const renderInsStart = js.indexOf("  function renderInspector(item)");
const renderInsSrc = js.slice(renderInsStart, js.indexOf("  function ", renderInsStart + 10));
assert.notEqual(renderInsSrc.length, 0, "没切到 renderInspector；写法改了就把这里一并更新");
assert.match(renderInsSrc, /syncLocateButton\(item\)/,
  "每渲染一次面板就要同步一次按钮 —— 换一道题，它得跟着换");
// 滚动时要跟着更新 —— 题滚出屏口，这颗按钮才有意义。
// 起点按它真实的写法找：syncBulkBar 是 `const … = () => {` 的箭头函数，
// 不是 `function` 声明。第一版按 `function syncBulkBar()` 切，indexOf 返回 -1，
// 拿到空串 —— 断言失败的原因和真正的问题毫无关系。
const scrollSyncSrc = js.slice(js.indexOf("  const syncBulkBar = () => {"),
  js.indexOf('window.addEventListener("scroll", syncBulkBar'));
assert.notEqual(scrollSyncSrc.length, 0, "syncBulkBar 那一段没切到；函数写法改了就把这里一并更新");
assert.match(scrollSyncSrc, /syncLocateButton\(/,
  "滚动时要跟着更新 —— 题滚出屏口，这颗按钮才有意义（否则它会一直显示/一直藏着）");

// 找不到时要说清下一步去哪，别只弹一句「找不到」。
assert.match(js, /还没加载出来[\s\S]*?加载更多/,
  "题还没加载出来时，写清要滚到底或点「加载更多」，而不是干说找不到");
assert.match(js, /card\.scrollIntoView\(\{ behavior: "smooth", block: "center" \}\)/,
  "点一下把左边列表滚到这道题正中间，不要滚到边缘");
assert.match(js, /card\.classList\.add\("locate-flash"\)/,
  "滚过去要让那道题亮一下，否则滑过去的一瞬间看不出停在哪");
assert.match(css, /\.library-card\.locate-flash \{ animation: library-locate-flash/,
  "亮一下的效果得有样式，否则那行 class 加了等于没加");

// ── 二、右下角「回到顶部」改成审核界面那颗一样的图标钮 ──
// 两边必须一致：同一个符号、同样的大小、同样的圆形。
assert.match(html, /<button id="backToTop"[^>]*>\s*<svg class="icon" aria-hidden="true">\s*<use href="#i-up"><\/use>\s*<\/svg>\s*<\/button>/,
  "这颗改成图标钮了，不再是写着「回到顶部」四个字的胶囊");
assert.match(html, /<symbol id="i-up" viewBox="0 0 24 24"><path d="M12 19V5m0 0-6 6m6-6 6 6"\/><\/symbol>/,
  "向上箭头搬过来了 —— 不搬就是个空壳按钮");
assert.match(indexHtml, /<symbol id="i-up" viewBox="0 0 24 24"><path d="M12 19V5m0 0-6 6m6-6 6 6"\/><\/symbol>/,
  "题库页这颗和审核页那颗用的是同一个图形，不是另画的");
assert.match(html, /id="backToTop"[^>]*aria-label="回到题目列表最上面"/,
  "只剩图标了，悬停和读屏都得说清这颗是干什么的");

// 两个坑：① 注释里也出现过 ".back-to-top {"，按它 indexOf 会切到注释为止，拿到空串；
// ② ".basket-panel" 在文件前面就有一处，end 必须取 start 之后的那个，否则切片是反的。
const backTopStart = css.indexOf("\n.back-to-top { position: fixed");
const backTopEnd = css.indexOf(".basket-panel", backTopStart);
const backTopCss = css.slice(backTopStart, backTopEnd);
assert.notEqual(backTopStart, -1, "没找到 .back-to-top 那条声明；样式改了就把这里一并更新");
// 尺寸和圆角写在两条声明里，中间隔着换行 —— 断言要能跨行，别只在一行里找。
// 圆角这条要写成「有 50% 且没有 999px」：只查前一半的话，
// 有人把 50% 改成 999px、前面那行 50% 还在，测试照样全绿（这正是漏过的那一条）。
assert.match(backTopCss, /width: 42px;/, "和审核界面那颗一样是 42×42");
assert.match(backTopCss, /border-radius: 50%/, "圆形 —— 和审核界面的 .to-top 一致");
assert.doesNotMatch(backTopCss, /border-radius: 999px/, "胶囊形状不能回来");
assert.match(styles, /\.to-top \{[^}]*width: 42px; height: 42px; border-radius: 50%/s,
  "审核界面那颗还是那样；两边要一样，就不能只改一边");

// 鼠标移过去才显示 —— 这是老师的原话，必须钉住。
assert.match(backTopCss, /opacity: \.3/, "默认淡着，不是完全藏起来（完全藏住键盘和触屏就找不着了）");
assert.match(backTopCss, /\.back-to-top:hover, \.back-to-top:focus-visible \{ opacity: 1/,
  "鼠标移上去、以及键盘 Tab 过来都变实 —— 只认鼠标的话键盘用不了");

// ── 三、搜索框别再独吞整条顶栏 ──
// 量过：1520 宽的屏上它一个人占 1077 像素，顶栏里只剩一颗标签管理按钮。
//
// 这里是这一版踩过的坑：上限先写在 library.css 里，测试照样全绿，实机一量还是 1077。
// 因为 library-reading.css 在 library.css **之后**加载，里面有一条更具体的
// `.library-top-navigation .library-search { max-width: none }` 把封顶抹了。
//
// 教训：静态断言只能证明「我写了 max-width」，证不了「它生效了」。
// 真正决定渲染的是**级联结果**，所以断言必须锚在最后生效的那条规则上。
const readingCss = fs.readFileSync(require.resolve("./library-reading.css"), "utf8");
// 注意不能整份文件扫 `max-width: none` —— 窄屏那条（搜索框独占一行）是合法的，
// 一起判掉就是误报。只盯顶栏那条：它后面必须跟 560，不是 none。
assert.match(readingCss,
  /\.library-top-navigation \.library-search \{ flex: 0 1 560px; min-width: 140px; max-width: 560px; \}/,
  "真正生效的那条规则上必须有上限（library-reading.css 后加载，它说了算）");

// 窄屏那条是搜索框独占一行，撑满是对的，不能一起砍掉。
assert.match(readingCss, /\.library-top-navigation \.library-search \{ min-width: 90px; max-width: none; \}/,
  "窄屏那条保留撑满 —— 那一行只有搜索框，本来就不够分");
assert.match(readingCss, /\.library-top-navigation \{ display: flex;[^}]*justify-content: flex-start/,
  "标签管理贴着搜索框右边排，中间不再留一大片空白");
assert.match(css, /\.library-search input \{ width: 100%;/, "输入框跟着容器走");

console.log("The library page's locate button, back-to-top icon and shorter search box "
  + "are all in place: OK");

// 1.25.43：核对页「在题库中看」带过来的 focus，可能不在这 40 道里。
assert.match(js, /ids=\$\{encodeURIComponent\(state\.focus\)\}/, "题库会按编号单独取回要定位的那一道");
assert.match(js, /state\.items = \[item, \.\.\.state\.items\]/, "取回来的题放在最前面，随列表一起显示");
