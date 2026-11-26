import { test, before, after } from "node:test";
import assert from "node:assert/strict";
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
