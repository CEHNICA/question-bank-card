import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { Client, makeEnv, makePackZip, packForm, promoteAdmin, setPayment, stubTurnstile } from "./helpers.ts";
import { generateCode, normalizeCode } from "../worker/src/redeem.ts";

let restore: () => void;
before(() => {
  restore = stubTurnstile();
});
after(() => restore());

async function setup(price = "30") {
  const env = makeEnv();
  const author = new Client(env);
  await author.register("author1");
  const admin = new Client(env);
  await admin.register("admin1");
  promoteAdmin(env, "admin1");
  const buyer = new Client(env);
  await buyer.register("buyer1");
  const other = new Client(env);
  await other.register("other1");
  await setPayment(author);
  const { data } = await author.json(
    "POST",
    "/api/packs",
    packForm(makePackZip(), { price_yuan: price, originality: "original" }),
  );
  await admin.json("POST", `/api/admin/packs/${data.id}/review`, { action: "approve" });
  return { env, author, admin, buyer, other, packId: data.id as string };
}

test("codes look right and normalize loosely", () => {
  const code = generateCode();
  assert.match(code, /^[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){3}$/);
  assert.equal(normalizeCode(` ${code.toLowerCase().replace(/-/g, " ")} `), code.replace(/-/g, ""));
});

test("paid pack: download blocked until a code is redeemed, codes stored only as hashes", async () => {
  const { env, author, buyer, other, packId } = await setup();
  const blocked = await buyer.json("POST", `/api/packs/${packId}/download`, {});
  assert.equal(blocked.status, 402);
  const detail = await buyer.json("GET", `/api/packs/${packId}`);
  assert.equal(detail.data.viewer.can_download, false);

  assert.equal((await buyer.json("POST", `/api/packs/${packId}/codes`, { count: 3 })).status, 404, "只有作者能生成");
  const batch = await author.json("POST", `/api/packs/${packId}/codes`, { count: 3, note: "（虚构）第一批" });
  assert.equal(batch.status, 201);
  assert.equal(batch.data.codes.length, 3);
  const rows = env.DB.db.prepare("SELECT code_hash, hint FROM redeem_codes").all() as { code_hash: string; hint: string }[];
  const dump = JSON.stringify(rows);
  for (const code of batch.data.codes) {
    assert.equal(dump.includes(code), false, "库里不能有明文兑换码");
    assert.equal(dump.includes(code.replace(/-/g, "")), false);
  }

  const redeemed = await buyer.json("POST", "/api/redeem", { code: batch.data.codes[0].toLowerCase() });
  assert.equal(redeemed.status, 200);
  assert.equal(redeemed.data.already_owned, false);
  assert.equal((await buyer.json("POST", `/api/packs/${packId}/download`, {})).status, 200);
  assert.equal((await buyer.json("GET", "/api/me/packs")).data.owned.length, 1);

  const again = await buyer.json("POST", "/api/redeem", { code: batch.data.codes[1] });
  assert.equal(again.data.already_owned, true, "已经拥有时不浪费第二个码");
  const reused = await other.json("POST", "/api/redeem", { code: batch.data.codes[0] });
  assert.equal(reused.status, 400);
  assert.match(reused.data.error, /用过/);

  const listing = await author.json("GET", `/api/packs/${packId}/codes`);
  assert.deepEqual(listing.data.summary, { total: 3, used: 1, revoked: 0, available: 2 });
  assert.equal(listing.data.codes.find((row: { used_at: string }) => row.used_at).used_by_username, "buyer1");
  assert.equal(JSON.stringify(listing.data).includes(batch.data.codes[1]), false, "列表里不再出现明文");
});

test("revoked codes stop working and wrong guesses are rate limited", async () => {
  const { author, buyer, other, packId } = await setup();
  const batch = await author.json("POST", `/api/packs/${packId}/codes`, { count: 2 });
  const revoke = await author.json("POST", `/api/packs/${packId}/codes/revoke`, { batch_id: batch.data.batch_id });
  assert.equal(revoke.data.revoked, 2);
  assert.equal((await buyer.json("POST", "/api/redeem", { code: batch.data.codes[0] })).status, 400);

  for (let i = 0; i < 10; i++) {
    await other.json("POST", "/api/redeem", { code: "AAAA-BBBB-CCCC-DDDD" });
  }
  const locked = await other.json("POST", "/api/redeem", { code: "AAAA-BBBB-CCCC-DDDD" });
  assert.equal(locked.status, 429);
});

test("free packs refuse codes and taken-down packs refuse redemption", async () => {
  const free = await setup("0");
  assert.equal((await free.author.json("POST", `/api/packs/${free.packId}/codes`, { count: 1 })).status, 400);

  const paid = await setup();
  const batch = await paid.author.json("POST", `/api/packs/${paid.packId}/codes`, { count: 1 });
  await paid.admin.json("POST", `/api/admin/packs/${paid.packId}/review`, { action: "takedown", note: "（虚构）下架" });
  const res = await paid.buyer.json("POST", "/api/redeem", { code: batch.data.codes[0] });
  assert.equal(res.status, 400);
  assert.match(res.data.error, /下架/);
});

test("concurrent redemption of one code grants exactly one entitlement", async () => {
  const { env, author, buyer, other, packId } = await setup();
  const batch = await author.json("POST", `/api/packs/${packId}/codes`, { count: 1 });
  const results = await Promise.all([
    buyer.json("POST", "/api/redeem", { code: batch.data.codes[0] }),
    other.json("POST", "/api/redeem", { code: batch.data.codes[0] }),
  ]);
  const ok = results.filter((r) => r.status === 200);
  assert.equal(ok.length, 1);
  const count = env.DB.db.prepare("SELECT COUNT(*) AS n FROM entitlements").get() as { n: number };
  assert.equal(count.n, 1);
});
