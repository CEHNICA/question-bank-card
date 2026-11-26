import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { Client, makeEnv, makePackZip, packForm, promoteAdmin, setPayment, stubTurnstile } from "./helpers.ts";
import { parsePriceYuan } from "../worker/src/sales.ts";

let restore: () => void;
before(() => {
  restore = stubTurnstile();
});
after(() => restore());

const PNG = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13]);

async function people() {
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
  return { env, author, admin, buyer, other, anonymous: new Client(env) };
}

async function paidPack(price = "30") {
  const ctx = await people();
  await setPayment(ctx.author);
  const { data, status } = await ctx.author.json(
    "POST",
    "/api/packs",
    packForm(makePackZip(), { price_yuan: price, originality: "original" }),
  );
  assert.equal(status, 201, JSON.stringify(data));
  await ctx.admin.json("POST", `/api/admin/packs/${data.id}/review`, { action: "approve" });
  return { ...ctx, packId: data.id as string };
}

function qrForm(bytes: Uint8Array, name = "qr.png") {
  const form = new FormData();
  form.append("file", new File([bytes], name, { type: "image/png" }));
  return form;
}

test("prices are entered in yuan and stored in cents", () => {
  assert.equal(parsePriceYuan("0"), 0);
  assert.equal(parsePriceYuan(""), 0);
  assert.equal(parsePriceYuan("12"), 1200);
  assert.equal(parsePriceYuan("9.9"), 990);
  assert.equal(parsePriceYuan("9.99"), 999);
  assert.equal(parsePriceYuan(undefined, 500), 500);
  for (const bad of ["0.5", "1000", "-1", "1.234", "abc"]) {
    assert.throws(() => parsePriceYuan(bad), undefined, bad);
  }
});

test("only original packs without originals can be sold, and only with a payment method", async () => {
  const { author } = await people();
  const noPayment = await author.json(
    "POST",
    "/api/packs",
    packForm(makePackZip(), { price_yuan: "10", originality: "original" }),
  );
  assert.equal(noPayment.status, 400);
  assert.match(noPayment.data.error, /收款方式/);

  await setPayment(author);
  const compiled = await author.json("POST", "/api/packs", packForm(makePackZip(), { price_yuan: "10" }));
  assert.equal(compiled.status, 400);
  assert.match(compiled.data.error, /原创/);

  const withOriginals = await author.json(
    "POST",
    "/api/packs",
    packForm(makePackZip({ includeOriginals: true }), {
      price_yuan: "10",
      originality: "original",
      originals_confirmed: "1",
    }),
  );
  assert.equal(withOriginals.status, 400);
  assert.match(withOriginals.data.error, /原卷/);

  const free = await author.json("POST", "/api/packs", packForm(makePackZip()));
  assert.equal(free.status, 201, "整理汇编的免费题包照常可以发");
  const toPaid = await author.json("PATCH", `/api/packs/${free.data.id}`, { title: "（虚构）改价", price_yuan: "5" });
  assert.equal(toPaid.status, 400, "整理汇编的题包事后也不能改成付费");

  const ok = await author.json("POST", "/api/packs", packForm(makePackZip(), { price_yuan: "9.9", originality: "original" }));
  assert.equal(ok.status, 201);
  const detail = await author.json("GET", `/api/packs/${ok.data.id}`);
  assert.equal(detail.data.pack.price_cents, 990);
  assert.equal(detail.data.pack.originality, "original");

  const clear = await author.json("POST", "/api/me/payment", { note: "" });
  assert.equal(clear.status, 400, "还有付费题包在售时不能清空收款方式");
});

test("buyer notifies payment, author grants, buyer downloads and author sees the estimate", async () => {
  const { env, author, buyer, other, anonymous, packId } = await paidPack("30");

  assert.equal((await anonymous.json("GET", `/api/packs/${packId}`)).data.payment, null, "未登录看不到收款方式");
  const view = await buyer.json("GET", `/api/packs/${packId}`);
  assert.match(view.data.payment.note, /微信/);
  assert.equal(view.data.viewer.can_download, false);

  assert.equal((await author.json("POST", `/api/packs/${packId}/purchase`, { note: "（虚构）自己买" })).status, 400);
  assert.equal((await buyer.json("POST", `/api/packs/${packId}/purchase`, { note: "" })).status, 400);
  const sent = await buyer.json("POST", `/api/packs/${packId}/purchase`, { note: "（虚构）微信转账 14:05" });
  assert.equal(sent.status, 201);
  assert.equal((await buyer.json("POST", `/api/packs/${packId}/purchase`, { note: "（虚构）再来" })).status, 409);
  assert.equal((await buyer.json("GET", `/api/packs/${packId}`)).data.my_request.status, "pending");
  assert.equal((await author.json("GET", "/api/me")).data.user.pending_sales, 1);

  const inbox = await author.json("GET", "/api/me/purchase-requests");
  assert.equal(inbox.data.incoming.length, 1);
  assert.equal(inbox.data.incoming[0].buyer_username, "buyer1");
  assert.equal(inbox.data.incoming[0].price_cents, 3000);

  assert.equal((await other.json("POST", `/api/purchase-requests/${sent.data.id}/grant`, {})).status, 404);
  assert.equal((await buyer.json("POST", `/api/purchase-requests/${sent.data.id}/grant`, {})).status, 404, "买家不能自己开通");
  assert.equal((await buyer.json("POST", `/api/packs/${packId}/download`, {})).status, 402);

  const grants = await Promise.all([
    author.json("POST", `/api/purchase-requests/${sent.data.id}/grant`, {}),
    author.json("POST", `/api/purchase-requests/${sent.data.id}/grant`, {}),
  ]);
  assert.equal(grants.filter((g) => g.status === 200).length, 1, "同一条通知只开通一次");
  assert.equal((await buyer.json("POST", `/api/packs/${packId}/download`, {})).status, 200);
  const entitlement = env.DB.db.prepare("SELECT source, price_cents FROM entitlements").get() as Record<string, unknown>;
  assert.deepEqual({ ...entitlement }, { source: "purchase", price_cents: 3000 });

  const mine = await author.json("GET", "/api/me/packs");
  assert.equal(mine.data.packs[0].buyers, 1);
  assert.equal(mine.data.packs[0].estimated_cents, 3000);
  assert.equal(mine.data.packs[0].pending_requests, 0);
  assert.equal((await author.json("GET", "/api/me")).data.user.pending_sales, 0);
  assert.equal((await buyer.json("POST", `/api/packs/${packId}/purchase`, { note: "（虚构）重复" })).data.already_owned, true);
});

test("author can decline with a reason and buyer can cancel", async () => {
  const { author, buyer, other, packId } = await paidPack();
  const first = await buyer.json("POST", `/api/packs/${packId}/purchase`, { note: "（虚构）支付宝" });
  assert.equal((await author.json("POST", `/api/purchase-requests/${first.data.id}/decline`, {})).status, 400);
  const declined = await author.json("POST", `/api/purchase-requests/${first.data.id}/decline`, { reason: "（虚构）没查到" });
  assert.equal(declined.status, 200);
  const view = await buyer.json("GET", `/api/packs/${packId}`);
  assert.equal(view.data.my_request.status, "declined");
  assert.equal(view.data.my_request.decline_reason, "（虚构）没查到");
  assert.equal((await buyer.json("POST", `/api/packs/${packId}/download`, {})).status, 402);

  const second = await buyer.json("POST", `/api/packs/${packId}/purchase`, { note: "（虚构）重新付了" });
  assert.equal(second.status, 201, "被拒后可以重新通知");
  assert.equal((await other.json("POST", `/api/purchase-requests/${second.data.id}/cancel`, {})).status, 404);
  assert.equal((await buyer.json("POST", `/api/purchase-requests/${second.data.id}/cancel`, {})).status, 200);
  assert.equal((await author.json("POST", `/api/purchase-requests/${second.data.id}/grant`, {})).status, 400);
  assert.equal((await author.json("GET", "/api/me/purchase-requests")).data.incoming.length, 1, "撤回的通知作者看不到");
});

test("taken-down packs cannot be granted", async () => {
  const { author, admin, buyer, packId } = await paidPack();
  const sent = await buyer.json("POST", `/api/packs/${packId}/purchase`, { note: "（虚构）微信" });
  await admin.json("POST", `/api/admin/packs/${packId}/review`, { action: "takedown", note: "（虚构）下架" });
  const res = await author.json("POST", `/api/purchase-requests/${sent.data.id}/grant`, {});
  assert.equal(res.status, 400);
  assert.match(res.data.error, /下架/);
});

test("payment QR accepts only small images and is visible only to signed-in users", async () => {
  const { env, author, admin, buyer, anonymous } = await people();
  assert.equal((await author.json("POST", "/api/me/payment/qr", qrForm(new TextEncoder().encode("<svg/>")))).status, 400);
  assert.equal((await author.json("POST", "/api/me/payment/qr", qrForm(new Uint8Array(600 * 1024)))).status, 413);
  assert.equal((await author.json("POST", "/api/me/payment/qr", qrForm(PNG))).status, 201);
  assert.equal((await author.json("GET", "/api/me/payment")).data.has_qr, true);

  assert.equal((await anonymous.request("GET", "/api/users/author1/pay-qr")).status, 401);
  const image = await buyer.request("GET", "/api/users/author1/pay-qr");
  assert.equal(image.status, 200);
  assert.equal(image.headers.get("Content-Type"), "image/png");
  assert.equal(image.headers.get("X-Content-Type-Options"), "nosniff");

  await author.json("POST", "/api/me/payment/qr", qrForm(PNG));
  assert.equal([...env.PACKS.objects.keys()].filter((key) => key.startsWith("pay-qr/")).length, 1, "换码后旧图删掉");

  const users = await admin.json("GET", "/api/admin/users?q=author1");
  const target = users.data.users.find((u: { username: string }) => u.username === "author1");
  assert.equal(Boolean(target.has_payment), true);
  assert.equal((await buyer.json("POST", `/api/admin/users/${target.id}/clear-payment`, {})).status, 403);
  assert.equal((await admin.json("POST", `/api/admin/users/${target.id}/clear-payment`, {})).status, 200);
  assert.deepEqual((await author.json("GET", "/api/me/payment")).data, { note: "", has_qr: false });
  assert.equal([...env.PACKS.objects.keys()].filter((key) => key.startsWith("pay-qr/")).length, 0);
});

test("redeemed codes record the price for the author's estimate", async () => {
  const { env, author, buyer, packId } = await paidPack("12.5");
  const batch = await author.json("POST", `/api/packs/${packId}/codes`, { count: 1 });
  await buyer.json("POST", "/api/redeem", { code: batch.data.codes[0] });
  const row = env.DB.db.prepare("SELECT source, price_cents FROM entitlements").get() as Record<string, unknown>;
  assert.deepEqual({ ...row }, { source: "redeem", price_cents: 1250 });
});
