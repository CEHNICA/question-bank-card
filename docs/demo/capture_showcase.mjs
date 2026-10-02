#!/usr/bin/env node
/**
 * Capture four original-demo screenshots from a running local server.
 *
 * The script is intentionally read-only with respect to the question-bank API.
 * It expects at least one ready paper and at least one published question. The
 * trial basket exists only in this browser context (localStorage), so repeated
 * runs do not change the application data.
 *
 * Usage:
 *   node docs/demo/capture_showcase.mjs
 *   node docs/demo/capture_showcase.mjs --base-url http://127.0.0.1:8768
 *   node docs/demo/capture_showcase.mjs --paper-id <演示任务 ID>
 *
 * Dependencies: playwright and sharp. In the Codex desktop runtime, set
 * NODE_PATH to the bundled node_modules directory before running this file.
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
let chromium;
let sharp;
try {
  ({ chromium } = require("playwright"));
  sharp = require("sharp");
} catch (error) {
  throw new Error(
    "缺少截图依赖。请安装 playwright 与 sharp，或把包含它们的目录设为 NODE_PATH。\n" + error.message,
  );
}

const here = path.dirname(fileURLToPath(import.meta.url));
const defaultOutput = path.resolve(here, "../assets/screenshots/demo");

function option(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const baseUrl = option("--base-url", process.env.QB_SHOWCASE_URL || "http://127.0.0.1:8768").replace(/\/$/, "");
const outputDir = path.resolve(option("--out-dir", defaultOutput));
const requestedPaperId = option("--paper-id", process.env.QB_SHOWCASE_PAPER_ID || "");
const edgeCandidates = [
  process.env.QB_SHOWCASE_BROWSER,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].filter(Boolean);
const executablePath = edgeCandidates.find((candidate) => fs.existsSync(candidate));

async function json(route) {
  const response = await fetch(`${baseUrl}${route}`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${route} 返回 HTTP ${response.status}`);
  return response.json();
}

async function waitForStablePage(page, selector) {
  const root = page.locator(selector).first();
  await root.waitFor({ state: "visible", timeout: 20_000 });
  await page.evaluate(async () => {
    if (document.fonts?.ready) await document.fonts.ready;
  });
  await page.waitForTimeout(350);
  await root.evaluate(async (root) => {
    const images = [...root.querySelectorAll("img")];
    await Promise.all(images.map((image) => image.complete
      ? Promise.resolve()
      : new Promise((resolve) => {
          image.addEventListener("load", resolve, { once: true });
          image.addEventListener("error", resolve, { once: true });
        })));
  });
  await page.waitForTimeout(150);
}

async function saveWebp(page, name) {
  const png = await page.screenshot({ type: "png", animations: "disabled", caret: "hide" });
  const target = path.join(outputDir, name);
  await sharp(png).webp({ quality: 90, smartSubsample: true }).toFile(target);
  const metadata = await sharp(target).metadata();
  return { name, width: metadata.width, height: metadata.height, bytes: fs.statSync(target).size };
}

async function main() {
  fs.mkdirSync(outputDir, { recursive: true });
  const papers = await json("/api/papers");
  const candidates = papers.papers || [];
  const paper = requestedPaperId
    ? candidates.find((item) => String(item.id) === String(requestedPaperId))
    : candidates.find((item) => item.status === "ready" && item.name === "题有据功能演示卷");
  if (!paper) {
    throw new Error(
      requestedPaperId
        ? `没有找到 ID 为 ${requestedPaperId} 的演示任务。`
        : "没有找到名为“题有据功能演示卷”的已完成任务。请先导入并重命名演示卷，或显式传入 --paper-id。",
    );
  }

  const detail = await json(`/api/papers/${encodeURIComponent(paper.id)}`);
  const questions = detail.questions || [];
  if (!questions.length) throw new Error("当前任务还没有生成题卡。");
  const showcaseQuestion = questions.find((item) => Number(item.number) === 11)
    || questions.find((item) => Number(item.number) === 9)
    || questions.find((item) => Number(item.number) === 5)
    || questions.find((item) => !item.approved)
    || questions[0];

  const library = await json(`/api/library?document=${encodeURIComponent(paper.id)}&limit=40&offset=0`);
  if (!library.items?.length) throw new Error("当前演示任务的正式题库为空。请先把已人工核对的演示题入库。");
  const basketIds = library.items.slice(0, Math.min(3, library.items.length)).map((item) => item.id);

  const browser = await chromium.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
  });
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 960 },
      deviceScaleFactor: 1,
      locale: "zh-CN",
      colorScheme: "light",
      reducedMotion: "reduce",
    });
    await context.addInitScript((ids) => {
      window.localStorage.setItem("qb-basket", JSON.stringify(ids));
      window.localStorage.setItem("qb-lens", "0");
    }, basketIds);

    const page = await context.newPage();
    page.on("console", (message) => {
      if (message.type() === "error") process.stderr.write(`[browser] ${message.text()}\n`);
    });
    const results = [];

    // Review workspace: a real pending card, original crop and recognized text.
    await page.goto(`${baseUrl}/?paper=${encodeURIComponent(paper.id)}`, { waitUntil: "networkidle" });
    const card = page.locator(`.card[data-id="${showcaseQuestion.id}"]`);
    await waitForStablePage(page, ".paper-head");
    await card.scrollIntoViewIfNeeded();
    await card.click({ position: { x: 10, y: 10 } });
    await page.evaluate((id) => {
      const target = document.querySelector(`.card[data-id="${id}"]`);
      if (!target) return;
      const top = (document.querySelector(".topbar")?.offsetHeight || 56) + 16;
      window.scrollTo({ top: window.scrollY + target.getBoundingClientRect().top - top, behavior: "instant" });
    }, showcaseQuestion.id);
    await waitForStablePage(page, `.card[data-id="${showcaseQuestion.id}"]`);
    results.push(await saveWebp(page, "review-workspace.webp"));

    // Enlarged comparison: fit the whole source question with the typeset text.
    await card.locator(".crop.zoomable").click();
    await waitForStablePage(page, "#viewerDialog[open]");
    await page.locator("#zoomFit").click();
    await page.waitForTimeout(350);
    results.push(await saveWebp(page, "compare-view.webp"));
    await page.locator('#viewerDialog [data-close]').click();

    // Formal library: published cards plus the local-only basket affordance.
    await page.goto(`${baseUrl}/library?document=${encodeURIComponent(paper.id)}`, { waitUntil: "networkidle" });
    await waitForStablePage(page, ".library-card");
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
    results.push(await saveWebp(page, "library-and-basket.webp"));

    // Paper preview: the same real published questions assembled as a worksheet.
    await page.locator("#basketButton").click();
    await waitForStablePage(page, "#printSheet:not([hidden])");
    await page.locator("#printTitle").fill("题有据 · 数学练习");
    await page.waitForTimeout(200);
    results.push(await saveWebp(page, "paper-preview.webp"));

    process.stdout.write(`${JSON.stringify({ baseUrl, paper: paper.name, question: showcaseQuestion.number, outputDir, results }, null, 2)}\n`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
