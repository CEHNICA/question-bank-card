"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

test("Eagle workbench wires Stage 5 on-demand streaming and FTS5 server search", () => {
  const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

  assert.match(html, /const LIBRARY_EAGER_PRELOAD_CAP = 2000;/);
  assert.match(html, /async function loadNextOnDemandBatch\(/);
  assert.match(html, /async function runServerLibrarySearch\(/);
  assert.match(html, /async function loadMoreServerSearchResults\(/);
  assert.match(html, /async function selectAllMatchingFromServer\(/);
  assert.match(html, /function buildServerLibraryFilterParams\(/);
  assert.match(html, /libraryOnDemandMode = offset < totalCount;/);
  assert.match(html, /api\/library\?ids_only=1/);

  const bulkUrls = html.match(/api\/library\?bulk=1[^`'"\s]*/g) || [];
  assert.ok(bulkUrls.length >= 4, "Should have first batch, background batch, on-demand batch, and FTS search");
  for (const url of bulkUrls) {
    assert.match(url, /slim=1/, `Bulk library fetch must stay slim: ${url}`);
    assert.match(url, /sort=display/, `Bulk library fetch must align with display order: ${url}`);
  }

  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
  let inlineCount = 0;
  for (const m of scripts) {
    if (/\bsrc\s*=/.test(m[1])) continue;
    new vm.Script(m[2], { filename: `stage5_inline_${inlineCount++}.js` });
  }
  assert.ok(inlineCount >= 1);
});
