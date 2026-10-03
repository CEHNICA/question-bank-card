"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Upload = require("./app.js");

(async () => {
  // An unconfigured cloud cannot ask for permission or block local import.
  let prompts = 0;
  const prompt = async () => { prompts += 1; return true; };
  const noCloud = await Upload.resolveUploadPolicy({ cloudReady: false, acknowledged: true }, prompt);
  assert.deepEqual(noCloud, { parseMode: "auto", allowCloud: false });
  assert.equal(prompts, 0);

  // Declining cloud still produces an upload, with explicit local permission.
  const declined = await Upload.resolveUploadPolicy({ cloudReady: true }, async () => false);
  const declinedForm = new FormData();
  Upload.appendUploadPolicy(declinedForm, declined);
  assert.equal(declinedForm.get("parse_mode"), "auto");
  assert.equal(declinedForm.get("allow_cloud"), "0");

  // Permission is captured for the batch and photo preview. Later configuration
  // changes cannot silently upgrade a local-only upload into a cloud upload.
  const source = { cloudReady: false, acknowledged: false };
  const captured = await Upload.resolveUploadPolicy(source, prompt);
  source.cloudReady = true;
  source.acknowledged = true;
  assert.equal(Object.isFrozen(captured), true);
  const photoForm = new FormData();
  Upload.appendUploadPolicy(photoForm, captured);
  assert.equal(photoForm.get("allow_cloud"), "0");

  const granted = await Upload.resolveUploadPolicy({ cloudReady: true }, prompt);
  assert.equal(prompts, 1);
  const cloudForm = new FormData();
  Upload.appendUploadPolicy(cloudForm, granted);
  assert.equal(cloudForm.get("parse_mode"), "auto");
  assert.equal(cloudForm.get("allow_cloud"), "1");
  const remembered = await Upload.resolveUploadPolicy({ cloudReady: true, acknowledged: true }, prompt);
  assert.equal(remembered.allowCloud, true);
  assert.equal(prompts, 1);
  const missingPrompt = await Upload.resolveUploadPolicy({ cloudReady: true });
  assert.equal(missingPrompt.allowCloud, false);

  const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
  const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
  const library = fs.readFileSync(path.join(__dirname, "library.js"), "utf8");
  assert.doesNotMatch(html, /id="parseMode"/);
  assert.doesNotMatch(js, /\$\("parseMode"\)|selectedParseMode|renderIntakeMode/);
  assert.doesNotMatch(js, /automatic_parse_ready\s*\?\?\s*state\.status\?\.upload_enabled/);
  assert.match(js, /QBUpload\.appendUploadPolicy\(form, photoUpload\.policy\)/);
  assert.match(library, /upload\.href = "\/#dropZone"/);
  assert.match(js, /window\.location\.hash === "#dropZone"/);
  assert.match(js, /button\("继续整理", "small primary", \(\) => switchToManual\(\)\)/);
  assert.match(js, /crop-missing", "[^"\n]*点“调整范围”[^"\n]*保存范围不会自动识读/);
  assert.doesNotMatch(js, /框出来，AI 会自动读题/);
  console.log("automatic intake permission checks: OK");
})().catch((error) => { console.error(error); process.exitCode = 1; });
