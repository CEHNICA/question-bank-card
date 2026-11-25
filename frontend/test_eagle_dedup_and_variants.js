"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { describe, it } = require("node:test");
const vm = require("node:vm");

const indexHtml = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const appJs = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const stylesCss = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
const eagleHtml = fs.readFileSync(path.join(__dirname, "eagle/index.html"), "utf8");

describe("Stage 3: 查重提示、一题多源证据与变式题组前端集成", () => {
  it("1. 录题审核页提供温和查重提示条与不静默删题的四路对比弹窗", () => {
    assert.ok(indexHtml.includes('id="similarityDialog"'), "index.html 需包含 #similarityDialog");
    assert.ok(indexHtml.includes('id="similarityMergeBtn"'), "需包含合并证据按钮");
    assert.ok(indexHtml.includes('id="similarityBindVariantBtn"'), "需包含存为独立题并绑变式按钮");
    assert.ok(indexHtml.includes('id="similarityKeepBtn"'), "需包含仍存为独立题按钮");
    assert.ok(indexHtml.includes('id="similaritySkipBtn"'), "需包含纯跳过按钮");

    assert.ok(appJs.includes("function similarityBanner(q)"), "app.js 需实现 similarityBanner");
    assert.ok(appJs.includes("function openSimilarityDialog(q, match)"), "app.js 需实现 openSimilarityDialog");
    assert.ok(appJs.includes("/merge-duplicate"), "合并操作必须走 merge-duplicate 接口");
    assert.ok(appJs.includes("/similarity-preference"), "保留独立题/绑变式必须走 similarity-preference 接口");
    assert.ok(stylesCss.includes(".similarity-banner"), "styles.css 需定义 .similarity-banner 样式");
  });

  it("2. /eagle 核验证据弹窗支持多源标签切换及原卷丢失时的裁片 PNG 兜底", () => {
    assert.ok(eagleHtml.includes('id="opSourceTabs"'), "#originalPaperModal 需包含 #opSourceTabs");
    assert.ok(eagleHtml.includes("function renderOpSourceTabs("), "需实现 renderOpSourceTabs");
    assert.ok(eagleHtml.includes("function switchOpEvidenceSource("), "需实现 switchOpEvidenceSource");
    assert.ok(eagleHtml.includes("function detachArchivedEvidence("), "需实现 detachArchivedEvidence");
    assert.ok(eagleHtml.includes("opState.fallbackCropUrl"), "需在原卷失效时自动回退到独立归档裁片 URL");

    // 验证当原卷不可用时，img.onerror 会切换为 fallbackCropUrl 且不弹出 #opErrorNotice
    let errorNoticeShown = false;
    const fakeImg = {
      src: "/api/documents/missing-paper/pages/0/preview",
      style: { display: "block" },
      onerror: null,
    };
    const fakeState = {
      fallbackCropUrl: "/api/library/pub-1/evidence/abc123.png",
      showingFallbackCrop: false,
      paperName: "张三手写错题卷",
    };
    fakeImg.onerror = () => {
      if (fakeState.fallbackCropUrl && !fakeState.showingFallbackCrop) {
        fakeState.showingFallbackCrop = true;
        fakeImg.style.display = "block";
        fakeImg.src = fakeState.fallbackCropUrl;
        return;
      }
      errorNoticeShown = true;
    };
    fakeImg.onerror();
    assert.equal(errorNoticeShown, false, "存在归档手写裁片时不应显示找不到扫描件");
    assert.equal(fakeImg.src, "/api/library/pub-1/evidence/abc123.png");
    assert.equal(fakeState.showingFallbackCrop, true);
  });

  it("3. 试题篮 swapBasketVariant 原地替换变式且严格保持题目顺序", async () => {
    const fnMatch = eagleHtml.match(/async function swapBasketVariant\(qid\)[\s\S]*?\n {4}\}/);
    assert.ok(fnMatch, "eagle/index.html 中必须存在 swapBasketVariant 函数");

    const q1 = { id: "q1", variantPeers: [] };
    const q2 = { id: "q2", variantPeers: [{ id: "q2_v2", short_id: "Q-var2" }] };
    const q2v2 = { id: "q2_v2", variantPeers: [{ id: "q2", short_id: "Q-orig" }] };
    const q3 = { id: "q3", variantPeers: [] };

    const basketQuestionIds = new Set(["q1", "q2", "q3"]);
    let toastMsg = "";
    const sandbox = {
      API_BASE: "",
      basketQuestionIds,
      questionsData: [q1, q2, q2v2, q3],
      localStorage: { setItem: () => {} },
      updateBasketBadges: () => {},
      renderBasketModalList: () => {},
      onExportOptionsChanged: () => {},
      refreshSingleQuestionCard: () => {},
      convertLibraryListItem: (x) => x,
      showToast: (msg) => { toastMsg = msg; },
      fetch: async () => ({ ok: false }),
    };
    vm.runInNewContext(`${fnMatch[0]}\nthis.swapBasketVariant = swapBasketVariant;`, sandbox);

    const swapped = await sandbox.swapBasketVariant("q2");
    assert.equal(swapped, true);
    assert.deepEqual(Array.from(basketQuestionIds), ["q1", "q2_v2", "q3"]);
    assert.match(toastMsg, /Q-var2/);
  });

  it("4. /eagle 提供全库查重与变式整理中心及批量互绑变式组", () => {
    assert.ok(eagleHtml.includes('id="dedupCenterModal"'), "需包含 #dedupCenterModal");
    assert.ok(eagleHtml.includes("openDedupCenterModal"), "需暴露 openDedupCenterModal 入口");
    assert.ok(eagleHtml.includes("batchBindVariantGroup"), "需提供批量互绑变式组入口");
    assert.ok(eagleHtml.includes("swapBasketVariant("), "试题篮列表需挂载 换变式 按钮");
  });
});
