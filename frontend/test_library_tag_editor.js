"use strict";

/*
 * 1.13.4 知识点标签可编辑（静态回归）。
 * 真活由 tools/check_tag_editing.py 在浏览器里点，这里只钉住那些「坏了也不报错、
 * 界面却看着正常」的接缝：改的入口跟着标签一起有、请求带得上前端校验头、
 * 客户端也守着「最多三个」这条线。
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");

const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");
const libraryCss = fs.readFileSync(require.resolve("./library.css"), "utf8");
const urls = fs.readFileSync(require.resolve("../backend/qb_server/urls.py"), "utf8");
const libraryTags = fs.readFileSync(require.resolve("../backend/core/library_tags.py"), "utf8");

// 「改」跟着标签一起有：没有标签就没有这一行（清空之后），也就没有「改」。
assert.match(libraryJs, /if \(\(item\.tags \|\| \[\]\)\.length\) \{[\s\S]{0,600}library-tags-edit/,
  "标签行里带一个「改」");
assert.match(libraryJs, /node\("button", "library-tags-edit", "改"\)/, "「改」用的是约定的类名");
assert.match(libraryCss, /\.library-tags-edit \{/, "「改」有样式，不是裸按钮");

// 请求要带上前端校验头。缺了这个头后端直接 403，界面只弹一句「没成功」，
// 标签看着还在——这是这一类改动最常见的「绿着坏掉」。
assert.match(libraryJs, /"X-QB-Request": "1"/, "保存标签的请求带上 X-QB-Request");
assert.match(libraryJs, /`\/api\/library\/\$\{encodeURIComponent\(item\.id\)\}\/tags`/, "走的是标签专用接口");
assert.match(urls, /path\("api\/library\/<uuid:publication_id>\/tags", library_tags\.tags_view\)/,
  "路由和前端用的路径一致");

// 目录里没登记的词不能写进去，人工选也不例外。
assert.match(libraryTags, /if value not in allowed:/, "后端按知识点目录校验每个标签");
assert.match(libraryTags, /raise TagError\(f"一道题最多 \{knowledge\.MAX_TAGS\} 个知识点"\)/, "后端守着三个的上限");
assert.match(libraryJs, /tagPicked\.length >= Number\(tagDialog\.dataset\.max \|\| 3\)/, "前端也守着三个的上限");
assert.match(libraryJs, /一道题最多 \$\{tagDialog\.dataset\.max\} 个知识点/, "超限时说清楚为什么");

// 标签是附加分类，不该让题面产生新版本。
assert.match(libraryTags, /library\.save_extras\(publication, extras\)/, "只写 extras，不新建版本");
assert.doesNotMatch(libraryTags, /library\.publish\(/, "这里绝不碰 publish");
