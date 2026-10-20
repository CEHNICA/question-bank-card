"use strict";
// 1.25.96：「还有活没干」这块面板被挡住 —— 老师发来截图说「挡住了，让他在右边展示」。
//
// 实测（真实程序，1.25.95）查出来的两个病：
//   ① 位置病：面板原来写 right: 0，锚在「还有活没干」按钮上，而那个按钮住在吸顶的
//      批量条里。窗口一窄（实测 1100 像素宽）按钮被挤到左边，560 像素宽的面板跟着
//      甩出屏幕左缘，落在 x=-141 —— 「标签」栏五颗按钮只剩右半截，「答案」栏却完好。
//      老师截图里正是这个样子。
//   ② 层级病：面板在吸顶条（层级 20）里，「更多筛选」在筛选行（层级 21）里 ——
//      整层压着整层。1442 像素宽、更多筛选一展开，面板就被盖住一部分。
//      1.25.18 修过同样毛病的「更多筛选」面板，这次漏了它。
//
// 这个测试量的是**位置算出来是多少**（纯函数跑一遍），不是规则文本长得像不像。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const js = fs.readFileSync(require.resolve("./library.js"), "utf8");
const css = fs.readFileSync(require.resolve("./library.css"), "utf8");

// ── 一、位置规则：真跑那个纯函数 ──
const helperStart = js.indexOf("  function todoPanelPlacement(");
const helperEnd = js.indexOf("  // 面板改成 fixed 定位", helperStart);
assert.notEqual(helperStart, -1, "todoPanelPlacement was renamed or moved; update this slice");
assert.notEqual(helperEnd, -1, "the comment after todoPanelPlacement is gone; update this slice");
const helperCode = js.slice(helperStart, helperEnd);
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(helperCode, sandbox);
const todoPanelPlacement = sandbox.todoPanelPlacement;
assert.equal(typeof todoPanelPlacement, "function", "位置函数没跑出来");

const MARGIN = 16;

// 1) 宽屏：照旧贴住按钮右缘（面板往左铺），不改变原来的观感。
assert.equal(todoPanelPlacement(687, 560, 1442, MARGIN).left, 127,
  "宽屏时面板仍贴住按钮右缘");
assert.equal(todoPanelPlacement(1069, 560, 1442, MARGIN).left, 509,
  "按钮在别处时，面板跟着按钮走");

// 2) 窄屏：贴按钮右缘会跑出屏幕左缘（实测落在 -141），这时改贴屏幕右缘 ——
//    这就是老师要的「在右边展示」。
assert.equal(todoPanelPlacement(419, 560, 1000, MARGIN).left, 1000 - MARGIN - 560,
  "放不下就贴屏幕右缘");
assert.equal(todoPanelPlacement(35, 560, 900, MARGIN).left, 900 - MARGIN - 560,
  "更窄的窗口也一样贴右边");

// 3) 视口比面板本身还窄：面板按视口收窄，只剩贴左缘这一条路。
const narrow = todoPanelPlacement(380, 560, 400, MARGIN);
assert.equal(narrow.left, MARGIN, "视口比面板还窄时贴屏幕左缘");
assert.equal(narrow.width, 400 - MARGIN * 2, "面板按视口收窄，不顶出屏幕");

// 4) 按钮本身贴着屏幕右缘时，面板也不能顶出右缘。
assert.equal(todoPanelPlacement(1442, 560, 1442, MARGIN).left, 1442 - MARGIN - 560,
  "按钮贴右缘时，面板不顶出屏幕");

// 5) 性质：不管视口多窄、按钮在哪儿，面板整个都得落在屏幕里。
//    这是老师那张截图的根本要求，必须写成性质，不能只挑几个例子。
//    宽度用被测代码给的（place.width），不能自己另算一遍 —— 另算的话，
//    被测代码改了宽度公式，这条断言还在按老公式判，就白测了。
for (let viewport = 320; viewport <= 2000; viewport += 7) {
  for (let anchor = 0; anchor <= viewport; anchor += 23) {
    const place = todoPanelPlacement(anchor, 560, viewport, MARGIN);
    assert.ok(place.left >= MARGIN,
      `面板左缘跑出屏幕了：视口 ${viewport}、按钮右缘 ${anchor}、left ${place.left}`);
    assert.ok(place.left + place.width <= viewport - MARGIN + 0.5,
      `面板右缘跑出屏幕了：视口 ${viewport}、按钮右缘 ${anchor}、left ${place.left}、宽 ${place.width}`);
  }
}

// ── 二、层级：面板得浮在「更多筛选」和详情面板之上 ──
const bodyRule = css.match(/\.library-todo-body \{[^}]*\}/);
assert.ok(bodyRule, "找得到面板那条样式；改版时 update this slice");
const bodyZ = Number(bodyRule[0].match(/z-index:\s*(\d+)/)?.[1]);
assert.ok(Number.isFinite(bodyZ), "面板没写 z-index");
assert.ok(bodyZ > 21, `面板的层级要高于筛选行（21），现在 ${bodyZ}`);
assert.ok(bodyZ > 56, `面板的层级要高于题目详情（56），现在 ${bodyZ}`);
assert.ok(bodyZ < 61, `面板不能压过试题篮和侧栏（61），现在 ${bodyZ}`);

// 面板改成 fixed，自己算位置 —— 不再是 right: 0 锚在按钮上。
assert.match(bodyRule[0], /position:\s*fixed/, "面板改成 fixed 定位，位置自己算");
assert.doesNotMatch(bodyRule[0], /right:\s*0/, "不能再用 right: 0 锚在按钮上：按钮一挪，面板就飞出屏幕");
assert.match(css, /\.library-todo-body\[hidden\] \{ display: none; \}/,
  "收起时要真的藏起来（面板搬出了 details，收起不再自动隐藏）");
// 宽度按「可用宽度」算：fixed 元素的 100% 就是视口宽（不含竖滚动条），
// 而 100vw 把滚动条也算进去 —— 面板右边会多出十几像素，跑出屏幕。
assert.match(bodyRule[0], /width:\s*min\(560px, calc\(100% - 32px\)\)/,
  "面板宽度按可用宽度算");
assert.doesNotMatch(bodyRule[0], /100vw/, "面板宽度不能用 100vw：它把竖滚动条也算进去，比可用宽度宽");

// ── 三、它挂在哪儿 ──
// 挂在 .library-workspace 下：那里 z-index 是 auto，不建层叠上下文，
// 面板的 z-index 才真的算数（在吸顶条里就永远压不过筛选行）。
assert.match(js, /const host = document\.querySelector\("\.library-workspace"\) \|\| document\.body;/,
  "面板挂到 .library-workspace 下");
assert.match(js, /host\.append\(body\);/, "面板真的挂到那个宿主上");
assert.doesNotMatch(js, /menu\.append\(body\);/,
  "面板不能再挂在 details 里：它一挂进去就跟着吸顶条走，层级永远比筛选行低");

// 收起时自己藏；打开时自己摆位置。
// 两处都要有：重画时按当前开合状态摆一次，用户点开/点收时再摆一次。
// 只钉一句会漏 —— 改掉另一处，测试照样绿。
const toggleBlock = js.slice(js.indexOf('menu.addEventListener("toggle"'), js.indexOf("box.append(menu);"));
assert.notEqual(toggleBlock.length, 0, "找得到 toggle 那段；改版时 update this slice");
assert.match(toggleBlock, /body\.hidden = !menu\.open;/, "用户收起时面板要真的藏起来");
// 结束锚点必须是 toggle 那行本身：再往后切就会把 toggle 里的 body.hidden 也圈进来，
// 删掉重画那一句反而照样绿。
const renderBlock = js.slice(js.indexOf("host.append(body);"), js.indexOf('menu.addEventListener("toggle"'));
assert.match(renderBlock, /body\.hidden = !menu\.open;/, "重画后面板也要跟着当前的开合状态");
assert.match(js, /if \(menu\.open\) placeTodoPanel\(\);/, "打开时要摆位置");

// 窗口大小变了、页面滚了，按钮位置跟着变，面板得跟着重摆。
assert.match(js, /window\.addEventListener\("resize", syncTodoPanel\)/, "窗口一变就要重摆");
assert.match(js, /window\.addEventListener\("scroll", syncTodoPanel, \{ passive: true \}\)/,
  "页面一滚就要重摆");

// 面板重画时旧的那块要收掉，不能在页面上攒一叠看不见的空壳。
assert.match(js, /for \(const stale of document\.querySelectorAll\("\.library-todo-body"\)\) stale\.remove\(\);/,
  "重画时把上一块面板收掉");

// 面板搬到 .library-workspace 以后，找它里面东西的选择器不能再带 #libraryTodos 前缀
// —— 带着前缀就一个都圈不到（按钮锁不住、排队文案不刷新）。
assert.doesNotMatch(js, /querySelectorAll\("#libraryTodos /,
  "不能再用 #libraryTodos 前缀找面板里的东西");
assert.doesNotMatch(js, /querySelector\("#libraryTodos /,
  "不能再用 #libraryTodos 前缀找面板里的东西");
assert.match(js, /querySelectorAll\("\.library-tool-actions button"\)/, "AI 动作那组按钮要圈得到");
assert.match(js, /querySelectorAll\("\.library-todo-look button"\)/, "筛选那组按钮要圈得到");
assert.match(js, /querySelector\("\.library-batch-state"\)/, "排队那一行要找到");

// 面板里的按钮样式原来挂在 .library-todos 后面，跟着搬过来。
assert.match(css, /\.library-todo-body \.library-tool-action \{/, "按钮样式跟着面板搬了");
assert.doesNotMatch(css, /\.library-todos \.library-tool-action \{/, "旧的那条已经没人认领了");
