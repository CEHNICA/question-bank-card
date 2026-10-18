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
assert.match(html, /<svg viewBox="0 0 12 12"/, "顶栏结构没变，这条不用改");
assert.match(js, /sectionTitle\.append\(locateQuestionButton\(item\)\)/,
  "「定位题目」挂在标签区标题栏右侧 —— 和「修改记录」那一条并排");
assert.match(js, /node\("button", "button button-small inspector-locate", "定位题目"\)/,
  "按钮名写清做什么：定位题目，不是「回到」也不是「跳转」");
assert.match(js, /card\.scrollIntoView\(\{ behavior: "smooth", block: "center" \}\)/,
  "点一下把左边列表滚到这道题，并且停在这道题正中间，不要滚到边缘");
assert.match(js, /card\.classList\.add\("locate-flash"\)/,
  "滚过去要让那道题亮一下，否则滑过去的一瞬间看不出停在哪");

// 题还在眼前时，这颗按钮不该占地方。
assert.match(js, /button\.hidden = inView\(document\.getElementById\(`q-\$\{item\.id\}`\)\)/,
  "这道题本来就看得见时，按钮自己藏起来");

// 找不到时要说清下一步去哪，别只弹一句「找不到」。
assert.match(js, /还没加载出来[\s\S]*?加载更多/,
  "题还没加载出来时，写清要滚到底或点「加载更多」，而不是干说找不到");
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
assert.match(css, /\.library-search \{ position: relative; max-width: 560px; \}/,
  "搜索框有上限了 —— 宽屏不再撑满");
assert.match(css, /\.library-search input \{ width: 100%;/,
  "窄屏仍旧靠 width:100% 撑满，那边本来就不够分，不能一起砍掉");
assert.doesNotMatch(css, /\.library-search \{ position: relative; \}/,
  "原来那行没有上限的写法已经换掉了");

console.log("The library page's locate button, back-to-top icon and shorter search box "
  + "are all in place: OK");
