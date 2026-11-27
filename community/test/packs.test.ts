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

test("desktop bearer token supports streaming pack upload and revocation", async () => {
  const { env, author } = await setup();
  const issued = await author.json("POST", "/api/desktop-tokens", { label: "测试桌面端" });
  assert.equal(issued.status, 201, JSON.stringify(issued.data));
  const token = issued.data.token as string;
  assert.ok(token.startsWith("tyj_dt_"));

  const listed = await author.json("GET", "/api/desktop-tokens");
  assert.equal(listed.status, 200);
  assert.equal(listed.data.tokens.length, 1);

  const zip = makePackZip({ cover: true });
  const previewRaw = unzipSync(zip)["community-preview.json"];
  const manifestRaw = unzipSync(zip)["题有据备份.json"];
  const desktopClient = new Client(env);
  const authHeaders = { Authorization: `Bearer ${token}` };

  const initRes = await desktopClient.request(
    "POST",
    "/api/packs/init",
    {
      title: "（虚构）桌面端直传题包",
      stage: "高中",
      subject: "数学",
      year: 2026,
      tags: "函数",
      price_yuan: "0",
      originality: "compiled",
      rights_confirmed: true,
      originals_confirmed: false,
      file_size: zip.byteLength,
      manifest: JSON.parse(new TextDecoder().decode(manifestRaw)),
      preview: JSON.parse(new TextDecoder().decode(previewRaw)),
    },
    authHeaders,
  );
  const initData = (await initRes.json()) as Record<string, any>;
  assert.equal(initRes.status, 201, JSON.stringify(initData));

  const putRes = await desktopClient.request("PUT", initData.upload_url, zip, {
    ...authHeaders,
    "Content-Type": "application/zip",
  });
  assert.equal(putRes.status, 200);
  assert.ok(env.PACKS.objects.has(`packs/${initData.id}.zip`));

  const revoked = await author.json("DELETE", `/api/desktop-tokens/${issued.data.id}`, {});
  assert.equal(revoked.status, 200);

  const rejected = await desktopClient.request(
    "POST",
    "/api/packs/init",
    {
      title: "（虚构）吊销后应拒绝",
      stage: "高中",
      rights_confirmed: true,
      file_size: zip.byteLength,
      manifest: JSON.parse(new TextDecoder().decode(manifestRaw)),
      preview: JSON.parse(new TextDecoder().decode(previewRaw)),
    },
    authHeaders,
  );
  assert.equal(rejected.status, 401);
});

test("webdav hybrid mode routes zip blobs to webdav while keeping covers in R2", async () => {
  const davStore = new Map<string, Uint8Array>();
  const methodsSeen: string[] = [];
  const fakeDavFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || "GET").toUpperCase();
    methodsSeen.push(`${method} ${url}`);
    assert.ok(String((init?.headers as Record<string, string> | undefined)?.Authorization || "").startsWith("Basic "));
    if (method === "MKCOL") {
      return new Response("", { status: 201 });
    }
    if (method === "PUT") {
      assert.ok(Number((init?.headers as Record<string, string> | undefined)?.["Content-Length"] || 0) > 0);
      const buf = new Uint8Array(await new Response(init?.body as BodyInit).arrayBuffer());
      davStore.set(url, buf);
      return new Response("", { status: 201 });
    }
    if (method === "GET") {
      const hit = davStore.get(url);
      return hit ? new Response(hit.slice(), { status: 200 }) : new Response("not found", { status: 404 });
    }
    if (method === "DELETE") {
      davStore.delete(url);
      return new Response("", { status: 204 });
    }
    return new Response("", { status: 405 });
  }) as typeof fetch;

  const env = makeEnv({
    PACK_STORAGE_MODE: "webdav",
    WEBDAV_BASE_URL: "https://webdav.123pan.cn/webdav",
    WEBDAV_DIR: "tiyouju-packs",
    WEBDAV_USERNAME: "demo-user",
    WEBDAV_PASSWORD: "demo-password",
    WEBDAV_FETCH: fakeDavFetch,
  });
  const author = new Client(env);
  await author.register("dav_author");
  const admin = new Client(env);
  await admin.register("dav_admin");
  promoteAdmin(env, "dav_admin");

  const zip = makePackZip({ cover: true });
  const up = await author.json("POST", "/api/packs", packForm(zip, { title: "（虚构）网盘混合包" }));
  assert.equal(up.status, 201, JSON.stringify(up.data));
  const id = up.data.id;

  // 封面留 R2，题包主体走 WebDAV
  assert.ok(env.PACKS.objects.has(`covers/${id}.png`));
  assert.equal(env.PACKS.objects.has(`packs/${id}.zip`), false);
  const expectedDavUrl = `https://webdav.123pan.cn/webdav/tiyouju-packs/packs/${id}.zip`;
  assert.ok(davStore.has(expectedDavUrl));

  await admin.json("POST", `/api/admin/packs/${id}/review`, { action: "approve" });
  const dlLink = await author.json("POST", `/api/packs/${id}/download`, {});
  assert.equal(dlLink.status, 200);
  const dlResp = await new Client(env).request("GET", dlLink.data.url);
  assert.equal(dlResp.status, 200);
  const dlBytes = new Uint8Array(await dlResp.arrayBuffer());
  assert.deepEqual(dlBytes, zip);

  const storage = await admin.json("GET", "/api/admin/storage");
  assert.equal(storage.data.storage_mode, "webdav");
  assert.equal(storage.data.r2_used_bytes, 0);
  assert.equal(storage.data.webdav_used_bytes, zip.byteLength);

  const del = await author.json("DELETE", `/api/packs/${id}`, {});
  assert.equal(del.status, 200);
  assert.equal(davStore.has(expectedDavUrl), false);
  assert.equal(env.PACKS.objects.has(`covers/${id}.png`), false);
});

test("ordinary users hit USER_STORAGE_QUOTA_MB while admins bypass per-user and daily limits", async () => {
  const zip = makePackZip();
  const quotaMb = (zip.byteLength * 1.5) / 1024 / 1024;
  const env = makeEnv({
    USER_STORAGE_QUOTA_MB: String(quotaMb),
    MAX_DAILY_UPLOADS_PER_USER: "1",
    MAX_TOTAL_R2_MB: "100",
  });
  const regular = new Client(env);
  await regular.register("regular1");
  const admin = new Client(env);
  await admin.register("owner1");
  promoteAdmin(env, "owner1");

  const first = await regular.json("POST", "/api/packs", packForm(zip, { title: "（虚构）普通首包" }));
  assert.equal(first.status, 201);
  // 绕开每日次数限制，验证个人累计空间上限
  const envQuotaOnly = makeEnv({
    USER_STORAGE_QUOTA_MB: String(quotaMb),
    MAX_DAILY_UPLOADS_PER_USER: "10",
    MAX_TOTAL_R2_MB: "100",
  });
  const reg2 = new Client(envQuotaOnly);
  await reg2.register("reg2");
  const adm2 = new Client(envQuotaOnly);
  await adm2.register("adm2");
  promoteAdmin(envQuotaOnly, "adm2");

  assert.equal((await reg2.json("POST", "/api/packs", packForm(zip, { title: "（虚构）配额内" }))).status, 201);
  const blocked = await reg2.json("POST", "/api/packs", packForm(zip, { title: "（虚构）超个人空间" }));
  assert.equal(blocked.status, 413);
  assert.match(blocked.data.error, /个人云端累计空间已达上限/);

  // 管理员账号不受个人上限与日次数限制
  envQuotaOnly.MAX_DAILY_UPLOADS_PER_USER = "1";
  assert.equal((await adm2.json("POST", "/api/packs", packForm(zip, { title: "（虚构）管理员包1" }))).status, 201);
  assert.equal((await adm2.json("POST", "/api/packs", packForm(zip, { title: "（虚构）管理员包2" }))).status, 201);
});

test("preview retains OneNote badge fields when has_onenote_notes is enabled", async () => {
  const { author, admin } = await setup();
  const baseZip = makePackZip();
  const unpacked = unzipSync(baseZip);
  const preview = JSON.parse(new TextDecoder().decode(unpacked["community-preview.json"]));
  preview.has_onenote_notes = true;
  preview.onenote_count = 3;
  preview.onenote_preview = "data:image/png;base64,iVBORw0KGgo=";
  const manifest = JSON.parse(new TextDecoder().decode(unpacked["题有据备份.json"]));

  const init = await author.json("POST", "/api/packs/init", {
    title: "（虚构）含OneNote笔记包",
    stage: "高中",
    rights_confirmed: true,
    file_size: baseZip.byteLength,
    manifest,
    preview,
  });
  assert.equal(init.status, 201);
  await author.request("PUT", init.data.upload_url, baseZip, { "Content-Type": "application/zip" });
  await admin.json("POST", `/api/admin/packs/${init.data.id}/review`, { action: "approve" });

  const list = await new Client(author.env).json("GET", "/api/packs");
  assert.equal(list.data.packs[0].has_onenote_notes, 1);
  const detail = await new Client(author.env).json("GET", `/api/packs/${init.data.id}`);
  assert.equal(detail.data.preview.has_onenote_notes, true);
  assert.equal(detail.data.preview.onenote_count, 3);
  assert.equal(detail.data.preview.onenote_preview, "data:image/png;base64,iVBORw0KGgo=");
});

test("hybrid mode uploads to R2 hot tier, archives cold packs via HEAD-verified WebDAV, rewarms on read and cleans both", async () => {
  const davStore = new Map<string, Uint8Array>();
  const methodsSeen: string[] = [];
  let forceHeadMismatch = false;
  const fakeDavFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method || "GET").toUpperCase();
    methodsSeen.push(`${method} ${url}`);
    if (method === "MKCOL") {
      assert.ok(url.endsWith("/"), `MKCOL 必须带末尾斜杠: ${url}`);
      return new Response("", { status: 201 });
    }
    if (method === "PROPFIND") {
      return new Response("", { status: 207 });
    }
    if (method === "PUT") {
      const buf = new Uint8Array(await new Response(init?.body as BodyInit).arrayBuffer());
      davStore.set(url, buf);
      return new Response("", { status: 201 });
    }
    if (method === "HEAD") {
      const hit = davStore.get(url);
      if (!hit) return new Response("", { status: 404 });
      const reportedLen = forceHeadMismatch ? hit.byteLength + 17 : hit.byteLength;
      return new Response("", { status: 200, headers: { "Content-Length": String(reportedLen) } });
    }
    if (method === "GET") {
      const hit = davStore.get(url);
      return hit ? new Response(hit.slice(), { status: 200 }) : new Response("not found", { status: 404 });
    }
    if (method === "DELETE") {
      davStore.delete(url);
      return new Response("", { status: 204 });
    }
    return new Response("", { status: 405 });
  }) as typeof fetch;

  const env = makeEnv({
    PACK_STORAGE_MODE: "hybrid",
    WEBDAV_BASE_URL: "https://webdav.123pan.cn/webdav",
    WEBDAV_DIR: "tiyouju-packs",
    WEBDAV_USERNAME: "demo-user",
    WEBDAV_PASSWORD: "demo-password",
    COLD_ARCHIVE_AGE_DAYS: "7",
    R2_HOT_TARGET_MB: "5500",
    ARCHIVE_BATCH_SIZE: "2",
    WEBDAV_FETCH: fakeDavFetch,
  });
  const author = new Client(env);
  await author.register("hyb_author");
  const admin = new Client(env);
  await admin.register("hyb_admin");
  promoteAdmin(env, "hyb_admin");

  const zip = makePackZip({ cover: true });
  const up = await author.json("POST", "/api/packs", packForm(zip, { title: "（虚构）冷热分层包" }));
  assert.equal(up.status, 201);
  const id = up.data.id as string;

  // 前台上传全落 R2 热层，零 WebDAV 请求
  assert.ok(env.PACKS.objects.has(`packs/${id}.zip`));
  assert.equal(methodsSeen.length, 0);
  await admin.json("POST", `/api/admin/packs/${id}/review`, { action: "approve" });

  // 当 HEAD 核验大小不一致时，绝不能删掉 R2 热副本，且要记下 archive_error
  forceHeadMismatch = true;
  const oldIso = new Date(Date.now() - 10 * 86400 * 1000).toISOString();
  env.DB.db.prepare("UPDATE packs SET created_at = ?, last_downloaded_at = NULL WHERE id = ?").run(oldIso, id);
  const failedCron = await runMaintenance(env, Date.now());
  assert.equal(failedCron.cold_archived, 0);
  assert.equal(failedCron.cold_archive_failed, 1);
  assert.ok(env.PACKS.objects.has(`packs/${id}.zip`), "核验失败必须保留 R2 源文件");
  const errRow = env.DB.db.prepare("SELECT r2_cached, archive_error FROM packs WHERE id = ?").get(id) as Record<string, unknown>;
  assert.equal(errRow.r2_cached, 1);
  assert.match(String(errRow.archive_error || ""), /大小不一致/);

  // 恢复正常 HEAD 核验后，管理员触发探活与归档
  forceHeadMismatch = false;
  const syncRes = await admin.json("POST", "/api/admin/storage/sync", { force: false, limit: 3 });
  assert.equal(syncRes.status, 200, JSON.stringify(syncRes.data));
  assert.equal(syncRes.data.probe.ok, true);
  assert.equal(syncRes.data.archival.archived, 1);
  assert.equal(env.PACKS.objects.has(`packs/${id}.zip`), false, "归档核验通过后应释放 R2 热副本");
  const expectedDavUrl = `https://webdav.123pan.cn/webdav/tiyouju-packs/packs/${id}.zip`;
  assert.ok(davStore.has(expectedDavUrl));

  // 冷包首次下载从 WebDAV 拉回并自动回热到 R2
  const dlLink = await author.json("POST", `/api/packs/${id}/download`, {});
  const dlResp = await new Client(env).request("GET", dlLink.data.url);
  assert.equal(dlResp.status, 200);
  const dlBytes = new Uint8Array(await dlResp.arrayBuffer());
  assert.deepEqual(dlBytes, zip);
  await new Promise((resolve) => setTimeout(resolve, 25));
  assert.ok(env.PACKS.objects.has(`packs/${id}.zip`), "冷包下载后应自动回热写回 R2");
  const rewarmRow = env.DB.db.prepare("SELECT r2_cached, last_downloaded_at FROM packs WHERE id = ?").get(id) as Record<string, unknown>;
  assert.equal(rewarmRow.r2_cached, 1);
  assert.ok(rewarmRow.last_downloaded_at);

  // 删除时应同时清理 R2 缓存、123 冷库和封面
  const del = await author.json("DELETE", `/api/packs/${id}`, {});
  assert.equal(del.status, 200);
  assert.equal(env.PACKS.objects.has(`packs/${id}.zip`), false);
  assert.equal(env.PACKS.objects.has(`covers/${id}.png`), false);
  assert.equal(davStore.has(expectedDavUrl), false);
});

test("ordinary users hit MAX_PENDING_PER_USER cap while admins bypass it", async () => {
  const env = makeEnv({ MAX_PENDING_PER_USER: "2", MAX_DAILY_UPLOADS_PER_USER: "10" });
  const regular = new Client(env);
  await regular.register("pending_reg");
  const admin = new Client(env);
  await admin.register("pending_adm");
  promoteAdmin(env, "pending_adm");

  const zip = makePackZip();
  assert.equal((await regular.json("POST", "/api/packs", packForm(zip, { title: "（虚构）待审1" }))).status, 201);
  assert.equal((await regular.json("POST", "/api/packs", packForm(zip, { title: "（虚构）待审2" }))).status, 201);
  const blocked = await regular.json("POST", "/api/packs", packForm(zip, { title: "（虚构）待审3" }));
  assert.equal(blocked.status, 429);
  assert.match(blocked.data.error, /排队审核/);

  for (let i = 1; i <= 3; i += 1) {
    assert.equal((await admin.json("POST", "/api/packs", packForm(zip, { title: `（虚构）管理员待审${i}` }))).status, 201);
  }
});

test("admin streaming upload bypasses MAX_PACK_MB up to ADMIN_MAX_PACK_MB", async () => {
  const env = makeEnv({ MAX_PACK_MB: "0.0001", ADMIN_MAX_PACK_MB: "95" });
  const regular = new Client(env);
  await regular.register("size_reg");
  const admin = new Client(env);
  await admin.register("size_adm");
  promoteAdmin(env, "size_adm");

  const zip = makePackZip();
  const extracted = unzipSync(zip);
  const manifest = JSON.parse(new TextDecoder().decode(extracted["题有据备份.json"]));
  const preview = JSON.parse(new TextDecoder().decode(extracted["community-preview.json"]));

  const regInit = await regular.json("POST", "/api/packs/init", {
    title: "（虚构）普通超限",
    rights_confirmed: true,
    file_size: zip.byteLength,
    manifest,
    preview,
  });
  assert.equal(regInit.status, 413);

  const admInit = await admin.json("POST", "/api/packs/init", {
    title: "（虚构）作者不限包大小",
    rights_confirmed: true,
    file_size: zip.byteLength,
    manifest,
    preview,
  });
  assert.equal(admInit.status, 201, JSON.stringify(admInit.data));
  const admPut = await admin.json("PUT", admInit.data.upload_url, zip);
  assert.equal(admPut.status, 200, JSON.stringify(admPut.data));
  const me = await admin.json("GET", "/api/me");
  assert.equal(me.status, 200);
  assert.equal(me.data.user.is_admin, true);
  assert.equal(me.data.user.unlimited_uploads, true);
});
