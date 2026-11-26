import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { strToU8, unzipSync } from "fflate";
import { runMaintenance } from "../worker/src/index.ts";
import { Client, makeEnv, makePackZip, packForm, promoteAdmin, setPayment, stubTurnstile } from "./helpers.ts";

let restore: () => void;
before(() => {
  restore = stubTurnstile();
});
after(() => restore());

async function setup() {
  const env = makeEnv();
  const author = new Client(env);
  await author.register("author1");
  const admin = new Client(env);
  await admin.register("admin1");
  promoteAdmin(env, "admin1");
  const visitor = new Client(env);
  await visitor.register("visitor1");
  return { env, author, admin, visitor, anonymous: new Client(env) };
}

test("upload lands in the review queue, hidden until approved", async () => {
  const { env, author, admin, visitor, anonymous } = await setup();
  const up = await author.json("POST", "/api/packs", packForm(makePackZip({ cover: true })));
  assert.equal(up.status, 201, JSON.stringify(up.data));
  const id = up.data.id;
  assert.equal(up.data.status, "pending");
  assert.ok(env.PACKS.objects.has(`packs/${id}.zip`));
  assert.ok(env.PACKS.objects.has(`covers/${id}.png`));

  assert.equal((await anonymous.json("GET", "/api/packs")).data.packs.length, 0);
  assert.equal((await visitor.json("GET", `/api/packs/${id}`)).status, 404);
  assert.equal((await author.json("GET", `/api/packs/${id}`)).status, 200, "作者能看自己待审的题包");
  assert.equal((await admin.json("GET", "/api/admin/packs")).data.packs.length, 1);

  assert.equal((await admin.json("POST", `/api/admin/packs/${id}/review`, { action: "reject" })).status, 400, "驳回要写理由");
  const ok = await admin.json("POST", `/api/admin/packs/${id}/review`, { action: "approve" });
  assert.equal(ok.status, 200);

  const list = await anonymous.json("GET", "/api/packs");
  assert.equal(list.data.packs.length, 1);
  assert.equal(list.data.packs[0].author_username, "author1");
  const detail = await anonymous.json("GET", `/api/packs/${id}`);
  assert.equal(detail.status, 200);
  assert.equal(detail.data.pack.r2_key, undefined, "不暴露存储键");
  assert.equal(detail.data.pack.has_cover, true);
  const cover = await anonymous.request("GET", `/api/packs/${id}/cover`);
  assert.equal(cover.status, 200);
  assert.equal(cover.headers.get("Content-Type"), "image/png");
});

test("preview is cleaned: no answers, no unknown fields", async () => {
  const { author, admin } = await setup();
  const { data } = await author.json("POST", "/api/packs", packForm(makePackZip()));
  await admin.json("POST", `/api/admin/packs/${data.id}/review`, { action: "approve" });
  const detail = await new Client(author.env).json("GET", `/api/packs/${data.id}`);
  const text = JSON.stringify(detail.data.preview);
  assert.equal(text.includes("不该出现的答案"), false);
  assert.equal(text.includes("secret_field"), false);
  assert.equal(detail.data.preview.samples[0].stem, "（虚构）已知 $f(x)=x^2$，求 $f(2)$。");
  assert.deepEqual(detail.data.preview.question_types, { single_choice: 2 });
});

test("rejects non-community bundles, missing rights and unconfirmed originals", async () => {
  const { author } = await setup();
  const plain = await author.json("POST", "/api/packs", packForm(makePackZip({ community: false })));
  assert.equal(plain.status, 400);
  assert.match(plain.data.error, /导出为社区题包/);
  const junk = await author.json("POST", "/api/packs", packForm(new Uint8Array([1, 2, 3])));
  assert.equal(junk.status, 400);
  const noRights = await author.json("POST", "/api/packs", packForm(makePackZip(), { rights_confirmed: "" }));
  assert.equal(noRights.status, 400);
  const originals = await author.json("POST", "/api/packs", packForm(makePackZip({ includeOriginals: true })));
  assert.equal(originals.status, 400);
  const confirmed = await author.json(
    "POST",
    "/api/packs",
    packForm(makePackZip({ includeOriginals: true }), { originals_confirmed: "1" }),
  );
  assert.equal(confirmed.status, 201);
  assert.equal((await new Client(author.env).json("POST", "/api/packs", packForm(makePackZip()))).status, 401);
});

test("search and filters only return approved matches", async () => {
  const { author, admin, anonymous } = await setup();
  await setPayment(author);
  const ids: string[] = [];
  for (const [title, stage, price] of [
    ["（虚构）高一函数", "高中", "0"],
    ["（虚构）初二几何", "初中", "30"],
  ]) {
    const { data } = await author.json(
      "POST",
      "/api/packs",
      packForm(makePackZip(), { title, stage, price_yuan: price, originality: "original" }),
    );
    ids.push(data.id);
    await admin.json("POST", `/api/admin/packs/${data.id}/review`, { action: "approve" });
  }
  await author.json("POST", "/api/packs", packForm(makePackZip(), { title: "（虚构）待审函数" }));
  const found = await anonymous.json("GET", "/api/packs?q=高一");
  assert.deepEqual(
    found.data.packs.map((pack: { id: string }) => pack.id),
    [ids[0]],
  );
  assert.equal((await anonymous.json("GET", "/api/packs?stage=初中")).data.packs[0].id, ids[1]);
  assert.equal((await anonymous.json("GET", "/api/packs?price=free")).data.packs[0].id, ids[0]);
  assert.equal((await anonymous.json("GET", "/api/packs?price=paid")).data.packs[0].id, ids[1]);
  assert.equal((await anonymous.json("GET", "/api/packs?tag=期中")).data.packs.length, 2);
});

test("free download issues a short-lived signed link that streams the zip", async () => {
  const { env, author, admin, visitor, anonymous } = await setup();
  const zip = makePackZip();
  const { data } = await author.json("POST", "/api/packs", packForm(zip));
  await admin.json("POST", `/api/admin/packs/${data.id}/review`, { action: "approve" });
  assert.equal((await anonymous.json("POST", `/api/packs/${data.id}/download`, {})).status, 401);
  const link = await visitor.json("POST", `/api/packs/${data.id}/download`, {});
  assert.equal(link.status, 200);
  assert.match(link.data.url, /^\/api\/dl\//);
  const file = await anonymous.request("GET", link.data.url);
  assert.equal(file.status, 200);
  assert.deepEqual(new Uint8Array(await file.arrayBuffer()), zip);
  const tampered = await anonymous.request("GET", `${link.data.url.slice(0, -2)}xx`);
  assert.equal(tampered.status, 403);
  const row = env.DB.db.prepare("SELECT downloads FROM packs WHERE id = ?").get(data.id) as { downloads: number };
  assert.equal(row.downloads, 1);
});

test("reports reach the admin and takedown hides the pack", async () => {
  const { author, admin, visitor, anonymous } = await setup();
  const { data } = await author.json("POST", "/api/packs", packForm(makePackZip()));
  await admin.json("POST", `/api/admin/packs/${data.id}/review`, { action: "approve" });
  assert.equal((await visitor.json("POST", `/api/packs/${data.id}/report`, { reason: "短" })).status, 400);
  assert.equal((await visitor.json("POST", `/api/packs/${data.id}/report`, { reason: "（虚构）疑似盗版教辅" })).status, 201);
  const reports = await admin.json("GET", "/api/admin/reports");
  assert.equal(reports.data.reports.length, 1);
  assert.equal(reports.data.reports[0].target_title, "（虚构）高一期中题包");
  const resolved = await admin.json("POST", `/api/admin/reports/${reports.data.reports[0].id}/resolve`, {
    action: "takedown",
    note: "（虚构）版权投诉",
  });
  assert.equal(resolved.status, 200);
  assert.equal((await anonymous.json("GET", `/api/packs/${data.id}`)).status, 404);
  assert.equal((await anonymous.json("GET", "/api/packs")).data.packs.length, 0);
  const mine = await author.json("GET", "/api/me/packs");
  assert.equal(mine.data.packs[0].status, "taken_down");
  assert.equal(mine.data.packs[0].review_note, "（虚构）版权投诉");
});

test("streaming two-step upload stores zip in R2 without server unzip", async () => {
  const { env, author, admin, visitor } = await setup();
  const zip = makePackZip({ cover: true });
  const extracted = unzipSync(zip);
  const manifest = JSON.parse(new TextDecoder().decode(extracted["题有据备份.json"]));
  const preview = JSON.parse(new TextDecoder().decode(extracted["community-preview.json"]));
  const coverBytes = extracted["community-cover.png"];
  let binary = "";
  for (const b of coverBytes) binary += String.fromCharCode(b);

  const init = await author.json("POST", "/api/packs/init", {
    title: "（虚构）流式上传题包",
    stage: "高中",
    subject: "数学",
    year: 2026,
    tags: "导数,流式",
    price_yuan: "0",
    originality: "original",
    description: "（虚构）流式上传测试",
    rights_confirmed: true,
    file_size: zip.byteLength,
    manifest,
    preview,
    cover_base64: btoa(binary),
  });
  assert.equal(init.status, 201, JSON.stringify(init.data));
  assert.equal(init.data.status, "uploading");
  assert.equal((await admin.json("GET", "/api/admin/packs")).data.packs.length, 0, "uploading 阶段不进入待审队列");

  const putRes = await author.json("PUT", init.data.upload_url, zip);
  assert.equal(putRes.status, 200, JSON.stringify(putRes.data));
  assert.equal(putRes.data.status, "pending");
  assert.equal((await admin.json("GET", "/api/admin/packs")).data.packs.length, 1);

  await admin.json("POST", `/api/admin/packs/${init.data.id}/review`, { action: "approve" });
  const link = await visitor.json("POST", `/api/packs/${init.data.id}/download`, {});
  const file = await visitor.request("GET", link.data.url);
  assert.equal(file.status, 200);
  assert.deepEqual(new Uint8Array(await file.arrayBuffer()), zip);
});

test("streaming upload rejects non-zip stream and enforces storage quota & daily upload limit", async () => {
  const zip = makePackZip();
  const extracted = unzipSync(zip);
  const manifest = JSON.parse(new TextDecoder().decode(extracted["题有据备份.json"]));
  const preview = JSON.parse(new TextDecoder().decode(extracted["community-preview.json"]));

  const env = makeEnv({ MAX_TOTAL_R2_MB: String((zip.byteLength * 1.5) / 1024 / 1024), MAX_DAILY_UPLOADS_PER_USER: "2" });
  const author = new Client(env);
  await author.register("author_quota");

  const badInit = await author.json("POST", "/api/packs/init", {
    title: "（虚构）坏文件流",
    rights_confirmed: true,
    file_size: zip.byteLength,
    manifest,
    preview,
  });
  assert.equal(badInit.status, 201);
  const badBytes = new Uint8Array(zip.byteLength);
  badBytes.set(strToU8("NOT_A_ZIP"));
  const badPut = await author.json("PUT", badInit.data.upload_url, badBytes);
  assert.equal(badPut.status, 400);
  assert.equal(env.PACKS.objects.has(`packs/${badInit.data.id}.zip`), false);

  // Clean up uploading placeholder so storage sum reflects only real packs
  env.DB.db.prepare("DELETE FROM packs WHERE id = ?").run(badInit.data.id);

  const ok1 = await author.json("POST", "/api/packs", packForm(zip, { title: "（虚构）配额包1" }));
  assert.equal(ok1.status, 201);
  const overQuota = await author.json("POST", "/api/packs", packForm(zip, { title: "（虚构）超配额包" }));
  assert.equal(overQuota.status, 507);

  // Raise quota and verify per-user daily upload limit (1 already uploaded, 1 more succeeds, 3rd hits 429)
  env.MAX_TOTAL_R2_MB = "100";
  const ok2 = await author.json("POST", "/api/packs", packForm(zip, { title: "（虚构）配额包2" }));
  assert.equal(ok2.status, 201);
  const limited = await author.json("POST", "/api/packs", packForm(zip, { title: "（虚构）超次数包" }));
  assert.equal(limited.status, 429);
});

test("pack purge frees R2 storage when unpurchased and cron cleans stale records", async () => {
  const { env, author, admin, visitor } = await setup();
  const up1 = await author.json("POST", "/api/packs", packForm(makePackZip({ cover: true }), { title: "（虚构）可清理" }));
  assert.equal(up1.status, 201);
  const id1 = up1.data.id;

  const del1 = await author.json("DELETE", `/api/packs/${id1}`, {});
  assert.equal(del1.status, 200);
  assert.equal(env.PACKS.objects.has(`packs/${id1}.zip`), false);
  assert.equal(env.PACKS.objects.has(`covers/${id1}.png`), false);

  // Pack with entitlement cannot be deleted by author
  await setPayment(author);
  const up2 = await author.json(
    "POST",
    "/api/packs",
    packForm(makePackZip(), { title: "（虚构）已售不可删", originality: "original", price_yuan: "10" }),
  );
  const id2 = up2.data.id;
  await admin.json("POST", `/api/admin/packs/${id2}/review`, { action: "approve" });
  const { data: codeRes } = await author.json("POST", `/api/packs/${id2}/codes`, { count: 1 });
  await visitor.json("POST", "/api/redeem", { code: codeRes.codes[0] });
  const blockedDel = await author.json("DELETE", `/api/packs/${id2}`, {});
  assert.equal(blockedDel.status, 409);

  // Rejected pack + abandoned uploading + expired session cleanup via maintenance cron
  const up3 = await author.json("POST", "/api/packs", packForm(makePackZip(), { title: "（虚构）待审驳回" }));
  const id3 = up3.data.id;
  await admin.json("POST", `/api/admin/packs/${id3}/review`, { action: "reject", note: "内容不完整" });
  const oldIso = new Date(Date.now() - 8 * 86400 * 1000).toISOString();
  env.DB.db.prepare("UPDATE packs SET updated_at = ? WHERE id = ?").run(oldIso, id3);

  const storageBefore = await admin.json("GET", "/api/admin/storage");
  assert.equal(storageBefore.status, 200);
  assert.equal(storageBefore.data.recoverable_packs, 1);

  const summary = await runMaintenance(env, Date.now());
  assert.equal(summary.purged_rejected, 1);
  assert.equal(env.PACKS.objects.has(`packs/${id3}.zip`), false);
});
