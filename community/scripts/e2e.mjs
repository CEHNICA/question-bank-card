// 端到端验收：对着正在运行的社区站（本机 wrangler dev 或线上域名）走一遍完整流程。
// 用法：node scripts/e2e.mjs <站点地址> <桌面版导出的社区题包.zip> [--local] [--keep]
//   --local  用 `wrangler d1 execute --local` 把测试作者提升为管理员（线上请手动提升，见 README）。
//   --keep   验收完保留这份已上架题包（不做最后的举报下架），方便在网页上看效果。
// 只用虚构账号和虚构题包；Turnstile 需是官方测试密钥（永远通过），否则注册会被拦。
import { readFileSync } from "node:fs";
import { basename } from "node:path";
import { execSync } from "node:child_process";

const [base, zipPath, ...flags] = process.argv.slice(2);
if (!base || !zipPath) {
  console.error("用法：node scripts/e2e.mjs <站点地址> <社区题包.zip> [--local]");
  process.exit(2);
}
const local = flags.includes("--local");
const stamp = Date.now().toString(36);
const DUMMY_TURNSTILE = "XXXX.DUMMY.TOKEN.XXXX";

class Browser {
  constructor(name) {
    this.name = name;
    this.cookie = "";
  }
  async call(method, path, body, { raw = false } = {}) {
    const headers = { "X-TYJ": "1", Origin: base };
    if (this.cookie) headers.Cookie = this.cookie;
    let payload;
    if (body instanceof FormData) payload = body;
    else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const response = await fetch(new URL(path, base), { method, headers, body: payload, redirect: "manual" });
    const setCookie = response.headers.get("set-cookie");
    if (setCookie) this.cookie = setCookie.split(";")[0];
    if (raw) return response;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`${this.name} ${method} ${path} → ${response.status} ${data.error || ""}`);
    return data;
  }
}

function step(message) {
  console.log(`✓ ${message}`);
}

const zip = readFileSync(zipPath);
const author = new Browser("作者");
const buyer = new Browser("买家");
const authorName = `e2e_author_${stamp}`;
const buyerName = `e2e_buyer_${stamp}`;

await author.call("POST", "/api/register", {
  username: authorName,
  password: "e2e-password-1",
  agree_terms: true,
  turnstile_token: DUMMY_TURNSTILE,
});
await buyer.call("POST", "/api/register", {
  username: buyerName,
  password: "e2e-password-1",
  agree_terms: true,
  turnstile_token: DUMMY_TURNSTILE,
});
step("注册作者和买家（经过 Turnstile 服务端校验）");

if (local) {
  execSync(
    `npx wrangler d1 execute tiyouju-community --local --config worker/wrangler.toml ` +
      `--command "UPDATE users SET role = 'admin' WHERE username = '${authorName}'"`,
    { stdio: "pipe" },
  );
  step("本机把测试作者提升为管理员");
}

const form = new FormData();
form.append("file", new File([zip], basename(zipPath), { type: "application/zip" }));
form.append("title", `（虚构）端到端题包 ${stamp}`);
form.append("stage", "高中");
form.append("subject", "数学");
form.append("year", "2026");
form.append("tags", "端到端,虚构");
form.append("price_yuan", "0");
form.append("originality", "original");
form.append("description", "（虚构）验收用，含公式 $x^2$。");
form.append("rights_confirmed", "1");
const { id } = await author.call("POST", "/api/packs", form);
step(`上传题包 → 待审核（${id}）`);

const hidden = await buyer.call("GET", `/api/packs/${id}`, undefined, { raw: true });
if (hidden.status !== 404) throw new Error("待审题包不该对买家可见");
step("待审题包对其他人不可见");

await author.call("POST", `/api/admin/packs/${id}/review`, { action: "approve" });
step("管理员审核通过");

const detail = await buyer.call("GET", `/api/packs/${id}`);
if (JSON.stringify(detail.preview).match(/"answer"|"analysis"/)) throw new Error("预览里出现了答案字段");
step(`详情页预览：${detail.preview.counts.questions} 题，${detail.preview.samples.length} 道示例，无答案`);

await author.call("POST", "/api/me/payment", { note: "（虚构）微信扫码，备注社区用户名" });
await author.call("PATCH", `/api/packs/${id}`, { title: detail.pack.title, description: "（虚构）", price_yuan: "19.9" });
const blocked = await buyer.call("POST", `/api/packs/${id}/download`, {}, { raw: true });
if (blocked.status !== 402) throw new Error(`付费题包未购买应返回 402，实际 ${blocked.status}`);
step("作者填好收款方式、改成 ¥19.9 后，未购买的买家下载被拦（402）");

const paidView = await buyer.call("GET", `/api/packs/${id}`);
if (!paidView.payment?.note) throw new Error("登录买家应能看到作者收款方式");
const request = await buyer.call("POST", `/api/packs/${id}/purchase`, { note: "（虚构）微信转账 14:05" });
const inbox = await author.call("GET", "/api/me/purchase-requests");
if (!inbox.incoming.some((r) => r.id === request.id && r.status === "pending")) throw new Error("作者没收到付款通知");
await author.call("POST", `/api/purchase-requests/${request.id}/grant`, {});
step("买家点「我已付款」→ 作者核对后开通");

const { codes } = await author.call("POST", `/api/packs/${id}/codes`, { count: 2, note: "e2e" });
step(`作者另外生成 ${codes.length} 个兑换码（团购用）`);
const already = await buyer.call("POST", "/api/redeem", { code: codes[0] });
if (!already.already_owned) throw new Error("已开通的买家兑换应提示已拥有、不消耗兑换码");
step("已开通的买家再兑换不会浪费兑换码");

const mine = await author.call("GET", "/api/me/packs");
const sold = mine.packs.find((p) => p.id === id);
if (sold.buyers !== 1 || sold.estimated_cents !== 1990) throw new Error(`作者统计不对：${JSON.stringify(sold)}`);
step("作者「我的」页：1 人购买，估算 ¥19.9");

const { url } = await buyer.call("POST", `/api/packs/${id}/download`, {});
const file = await fetch(new URL(url, base));
const bytes = new Uint8Array(await file.arrayBuffer());
if (file.status !== 200 || bytes.length !== zip.length || !bytes.every((b, i) => b === zip[i])) {
  throw new Error("下载内容和上传的题包不一致");
}
step(`短时链接下载成功，${bytes.length} 字节与原文件一致`);

const post = await buyer.call("POST", "/api/posts", {
  title: `（虚构）心得 ${stamp}`,
  body: "（虚构）讲评时 $f(x)=x^2$ 很好用。",
  pack_id: id,
});
await author.call("POST", `/api/posts/${post.id}/comments`, { body: "（虚构）谢谢" });
await author.call("POST", `/api/posts/${post.id}/like`, {});
step("心得、评论、点赞");

if (flags.includes("--keep")) {
  console.log(`\n端到端验收通过（--keep：保留已上架题包 ${base}/#/pack/${id}，跳过举报下架）。`);
  process.exit(0);
}

await buyer.call("POST", `/api/packs/${id}/report`, { reason: "（虚构）端到端举报测试" });
const reports = await author.call("GET", "/api/admin/reports");
const report = reports.reports.find((r) => r.target_id === id);
await author.call("POST", `/api/admin/reports/${report.id}/resolve`, { action: "takedown", note: "（虚构）验收下架" });
const gone = await buyer.call("GET", `/api/packs/${id}`, undefined, { raw: true });
if (gone.status !== 404) throw new Error("下架后题包仍可见");
step("举报 → 管理员下架 → 不再可见");

console.log("\n端到端验收全部通过。");
