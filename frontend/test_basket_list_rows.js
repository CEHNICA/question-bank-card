"use strict";

// Walking the basket with real questions in it turned up two things on the same
// line. The question number was printed twice — once in the bold row ("第 4 题 ·
// 单选题") and again at the end of the filename below it — and because the filename
// line could break anywhere, a long paper name left "· 第" on the first line and
// pushed "4 题" onto a second one. The three rows then measured 57 / 40 / 40 px.
//
// These checks pin the shape: the second line says which paper the question came
// from and nothing else, it stays on one line, and the full name is still reachable
// by hover and by a screen reader.
const assert = require("node:assert/strict");
const fs = require("node:fs");

const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");
const libraryCss = fs.readFileSync(require.resolve("./library.css"), "utf8");

// ---- the second line does not repeat the number printed above it
assert.match(libraryJs, /const locator = item \? sourceLocator\(item\) : `选题 \$\{index \+ 1\}`;/,
  "the row works out where the question sits once, and uses it for both lines");
// "Compute it once" has to be enforced, not just intended: today substituting
// sourceLocator(item) back into the bold row happens to print the same string,
// but the two lines would then be free to drift apart later.
assert.match(libraryJs, /open\.append\(node\("strong", "", item \? `\$\{locator\} · /,
  "the bold line uses that same value rather than working it out again");
assert.doesNotMatch(libraryJs, /`\$\{sourceLocator\(item\)\} · \$\{QB\.TYPE_NAMES/,
  "the bold line must not recompute the locator; two calls can drift apart");
assert.match(libraryJs, /const line = node\("span", "basket-item-source", item \? item\.source_filename : locator\);/,
  "the second line carries the paper name, not the name plus the number again");
assert.doesNotMatch(libraryJs, /node\("span", "", `\$\{item\.source_filename\} · /,
  "the old line appended the locator a second time");

// The name used for hover and for assistive tech still says which question it is,
// because a list of rows all saying only "移出" tells a screen reader nothing.
assert.match(libraryJs, /const name = item \? `\$\{item\.source_filename\} · \$\{locator\}` : locator;/,
  "the full name still carries the question number");
assert.match(libraryJs, /line\.title = name;/, "and it is reachable by hover");
assert.match(libraryJs, /remove\.setAttribute\("aria-label", `移出\$\{name\}`\)/,
  "the remove button still names the exact question it removes");

// ---- the filename stays on one line, whatever its length
assert.match(libraryCss, /\.basket-item-open \.basket-item-source \{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; \}/,
  "a long paper name is clipped to one line instead of pushing the row taller");
assert.match(libraryCss, /\.basket-item-open strong \{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; \}/,
  "the bold line gets the same treatment so a long location cannot wrap either");
// The error line still has to wrap: it explains what happened to a withdrawn
// question and there is no hover for it.
assert.match(libraryCss, /\.basket-item-open span \{[^}]*overflow-wrap: anywhere; \}/,
  "the general rule still allows wrapping, so a warning line is not clipped");

console.log("basket row shape: OK");
