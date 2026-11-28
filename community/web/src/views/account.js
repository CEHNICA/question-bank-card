import { api, mountTurnstile, state } from "../api.js";
import { escapeHtml, priceLabel, timeAgo } from "../render.js";
import { busy, needLogin, queryParams, setView, toast } from "../ui.js";

const STATUS_NAMES = {
  pending: "待审核",
  approved: "已上架",
  rejected: "未通过",
  taken_down: "已下架",
  withdrawn: "已撤回",
};

export function statusChip(status) {
  const tone =
    status === "approved"
      ? "bg-emerald-50 text-emerald-700"
      : status === "pending"
        ? "bg-amber-50 text-amber-700"
        : "bg-slate-100 text-slate-500";
  return `<span class="chip ${tone}">${STATUS_NAMES[status] || status}</span>`;
}

function nextHash() {
  const next = queryParams().get("next") || "#/";
  return next.startsWith("#/") && !next.startsWith("#/login") && !next.startsWith("#/register") ? next : "#/";
}

async function authForm({ title, submitLabel, register }) {
  setView(`
    <div class="card mx-auto mt-6 max-w-sm p-6">
      <h1 class="text-lg font-semibold text-slate-900">${title}</h1>
      <form id="authForm" class="mt-5 space-y-4" onsubmit="return false">
        <div><label class="label" for="username">用户名</label>
          <input class="input" id="username" name="username" autocomplete="username" required maxlength="20"></div>
        ${register ? `<div><label class="label" for="displayName">显示名（可选）</label>
          <input class="input" id="displayName" name="display_name" maxlength="30" placeholder="如：某某中学 王老师"></div>` : ""}
        <div><label class="label" for="password">密码</label>
          <input class="input" id="password" name="password" type="password" minlength="8" required
            autocomplete="${register ? "new-password" : "current-password"}"></div>
        ${register ? `<label class="flex items-start gap-2 text-xs text-slate-500">
          <input type="checkbox" name="agree_terms" class="mt-0.5" required>
          <span>我已阅读并同意<a href="#/rules" target="_blank" class="text-emerald-700 underline">社区规则与版权说明</a>，只上传自己有权分发的内容。</span></label>` : ""}
        <div id="turnstileBox" class="min-h-[65px]"></div>
        <button class="btn btn-primary w-full" type="submit">${submitLabel}</button>
      </form>
      <p class="mt-4 text-center text-xs text-slate-500">
        ${register ? '已经有账号？<a href="#/login" class="text-emerald-700">登录</a>' : '还没有账号？<a href="#/register" class="text-emerald-700">注册</a>'}
      </p>
    </div>`);
  const form = document.getElementById("authForm");
  const box = document.getElementById("turnstileBox");
  let captcha = null;
  let captchaError = "";

  // 提交处理必须先挂上。人机验证脚本是第三方资源，慢或者被墙时它会一直挂着；
  // 要是等它就绪再绑监听，这期间点「登录」会走浏览器原生提交 —— 密码会被拼进
  // URL 查询串（进历史、进访问日志、进 Referer），页面还会刷新把输入清掉。
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!captcha) {
      toast(captchaError || "人机验证还没加载好，等一下再点。", "error");
      return;
    }
    const data = Object.fromEntries(new FormData(form));
    const payload = { ...data, agree_terms: Boolean(data.agree_terms), turnstile_token: captcha.token() };
    busy(form.querySelector("button[type=submit]"), async () => {
      try {
        const result = await api(register ? "/api/register" : "/api/login", { method: "POST", body: payload });
        state.user = result.user;
        window.dispatchEvent(new Event("tyj:session"));
        toast(register ? "注册成功，欢迎加入。" : "登录成功。", "success");
        location.hash = nextHash();
      } finally {
        captcha.reset();
      }
    });
  });

  box.textContent = "人机验证加载中…";
  captcha = await mountTurnstile(box).catch((error) => {
    captchaError = error.message;
    box.textContent = error.message;
    return null;
  });
}

export function loginView() {
  return authForm({ title: "登录题有据社区", submitLabel: "登录", register: false });
}

export function registerView() {
  return authForm({ title: "注册账号", submitLabel: "注册", register: true });
}

const REQUEST_STATUS = {
  pending: '<span class="chip bg-amber-50 text-amber-700">待核对</span>',
  granted: '<span class="chip bg-emerald-50 text-emerald-700">已开通</span>',
  declined: '<span class="chip bg-rose-50 text-rose-600">未确认</span>',
  cancelled: '<span class="chip">已撤回</span>',
};

function incomingHtml(incoming) {
  if (!incoming.length) return '<p class="mt-3 text-sm text-slate-400">还没有买家通知你付款。</p>';
  return `<ul class="mt-2 text-sm">${incoming
    .map(
      (r) => `<li class="border-t border-slate-100 py-3">
        <div class="flex flex-wrap items-center gap-2">${REQUEST_STATUS[r.status]}
          <a href="#/u/${encodeURIComponent(r.buyer_username)}" class="font-medium">${escapeHtml(r.buyer_name || r.buyer_username)}</a>
          <span class="text-xs text-slate-400">@${escapeHtml(r.buyer_username)} · ${timeAgo(r.created_at)}</span>
          <span class="ml-auto font-medium text-amber-600">${priceLabel(r.price_cents)}</span></div>
        <div class="mt-1 text-slate-500">《<a href="#/pack/${r.pack_id}" class="hover:text-emerald-700">${escapeHtml(r.pack_title)}</a>》 · 付款信息：${escapeHtml(r.buyer_note)}</div>
        ${r.status === "declined" ? `<div class="mt-1 text-xs text-rose-600">你的回复：${escapeHtml(r.decline_reason)}</div>` : ""}
        ${
          r.status === "pending"
            ? `<div class="mt-2 flex gap-2">
          <button class="btn btn-primary" data-grant="${r.id}">已收到，开通</button>
          <button class="btn" data-decline="${r.id}">没收到</button></div>`
            : ""
        }</li>`,
    )
    .join("")}</ul>`;
}

function outgoingHtml(outgoing) {
  if (!outgoing.length) return "";
  return `<section class="card p-5">
    <h2 class="font-semibold">我的付款通知</h2>
    <ul class="mt-2 text-sm">${outgoing
      .map(
        (r) => `<li class="flex flex-wrap items-center gap-2 border-t border-slate-100 py-2">${REQUEST_STATUS[r.status]}
          <a href="#/pack/${r.pack_id}" class="hover:text-emerald-700">${escapeHtml(r.pack_title)}</a>
          <span class="text-xs text-slate-400">${escapeHtml(r.author_name || r.author_username)} · ${priceLabel(r.price_cents)} · ${timeAgo(r.created_at)}</span>
          ${r.status === "declined" ? `<span class="w-full text-xs text-rose-600">作者回复：${escapeHtml(r.decline_reason)}</span>` : ""}</li>`,
      )
      .join("")}</ul></section>`;
}

function paymentHtml(payment) {
  return `<section class="card p-5" id="payment">
    <h2 class="font-semibold">收款方式</h2>
    <p class="mt-1 text-xs leading-5 text-slate-500">卖原创题包用。只有登录用户在你的付费题包页能看到。平台不经手钱，收款与退款由你和买家自行处理，收款合规由你自己负责。</p>
    <form id="paymentForm" class="mt-3 space-y-2">
      <textarea class="input" name="note" rows="3" maxlength="300" placeholder="如：微信 / 支付宝扫下方收款码，备注你的社区用户名；有问题加微信 xxx。">${escapeHtml(payment.note)}</textarea>
      <button class="btn" type="submit">保存收款说明</button>
    </form>
    <div class="mt-4">
      <div class="label">收款码（可选，PNG / JPG / WebP，512 KB 以内）</div>
      ${payment.has_qr ? `<img src="/api/users/${encodeURIComponent(state.user.username)}/pay-qr?t=${Date.now()}" alt="我的收款码" class="mt-1 max-h-40 rounded border border-slate-100">` : ""}
      <div class="mt-2 flex items-center gap-2">
        <label class="btn cursor-pointer">${payment.has_qr ? "更换" : "上传"}收款码<input type="file" id="qrInput" accept="image/png,image/jpeg,image/webp" class="hidden"></label>
        ${payment.has_qr ? '<button type="button" class="btn btn-danger" id="qrDelete">删除</button>' : ""}
      </div>
    </div>
  </section>`;
}

export async function meView() {
  if (!state.user) return needLogin("登录后才能看「我的」。");
  const [data, profileData, payment, requests, tokensData] = await Promise.all([
    api("/api/me/packs"),
    api(`/api/users/${encodeURIComponent(state.user.username)}`).catch(() => ({ user: {} })),
    api("/api/me/payment"),
    api("/api/me/purchase-requests"),
    api("/api/desktop-tokens").catch(() => ({ tokens: [] })),
  ]);
  const bio = profileData.user?.bio || "";
  const pendingCount = requests.incoming.filter((r) => r.status === "pending").length;
  if (state.user.pending_sales !== pendingCount) {
    state.user.pending_sales = pendingCount;
    window.dispatchEvent(new Event("tyj:session"));
  }
  const totalBuyers = data.packs.reduce((sum, p) => sum + Number(p.buyers || 0), 0);
  const totalCents = data.packs.reduce((sum, p) => sum + Number(p.estimated_cents || 0), 0);
  const packs = data.packs
    .map(
      (pack) => `<tr class="border-t border-slate-100">
        <td class="py-2 pr-3"><a href="#/pack/${pack.id}" class="text-slate-800 hover:text-emerald-700">${escapeHtml(pack.title)}</a>
          ${pack.review_note ? `<div class="text-xs text-slate-400">审核意见：${escapeHtml(pack.review_note)}</div>` : ""}</td>
        <td class="py-2 pr-3">${statusChip(pack.status)}</td>
        <td class="py-2 pr-3 text-slate-500">${priceLabel(pack.price_cents)}</td>
        <td class="py-2 pr-3 text-slate-500">${pack.question_count} 题 · ${pack.downloads} 次下载</td>
        <td class="py-2 text-right text-slate-500">${
          pack.price_cents > 0
            ? `${pack.buyers} 人购买 · 约 ${priceLabel(pack.estimated_cents)}${
                pack.pending_requests ? ` <span class="chip bg-amber-50 text-amber-700">${pack.pending_requests} 条待核对</span>` : ""
              }`
            : "—"
        }</td></tr>`,
    )
    .join("");
  const packCards = data.packs
    .map(
      (pack) => `<div class="border-t border-slate-100 py-3 text-sm">
        <div class="flex items-start justify-between gap-2">
          <a href="#/pack/${pack.id}" class="font-medium text-slate-800 hover:text-emerald-700">${escapeHtml(pack.title)}</a>
          ${statusChip(pack.status)}
        </div>
        <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
          <span>${priceLabel(pack.price_cents)}</span>
          <span>${pack.question_count} 题 · ${pack.downloads} 次下载</span>
          ${
            pack.price_cents > 0
              ? `<span>${pack.buyers} 人购买 · 约 ${priceLabel(pack.estimated_cents)}${
                  pack.pending_requests ? ` <span class="chip bg-amber-50 text-amber-700">${pack.pending_requests} 条待核对</span>` : ""
                }</span>`
              : ""
          }
        </div>
        ${pack.review_note ? `<div class="mt-1 text-xs text-slate-400">审核意见：${escapeHtml(pack.review_note)}</div>` : ""}
      </div>`,
    )
    .join("");
  const owned = data.owned
    .map(
      (pack) => `<li class="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 py-2">
        <a href="#/pack/${pack.id}" class="hover:text-emerald-700">${escapeHtml(pack.title)}</a>
        <span class="text-xs text-slate-400">${escapeHtml(pack.author_name)} · ${timeAgo(pack.redeemed_at)}${pack.source === "redeem" ? "兑换" : "开通"}</span></li>`,
    )
    .join("");
  setView(`
    <div class="grid gap-5 md:grid-cols-[1fr_320px]">
      <div class="min-w-0 space-y-5">
        <section class="card p-4 sm:p-5">
          <div class="flex flex-wrap items-center justify-between gap-2"><h2 class="font-semibold">我发布的题包</h2>
            <a href="#/upload" class="btn btn-primary">发布题包</a></div>
          ${
            packs
              ? `<div class="mt-3 sm:hidden">${packCards}</div>
                 <div class="mt-3 hidden overflow-x-auto sm:block"><table class="w-full text-sm"><tbody>${packs}</tbody></table></div>`
              : '<p class="mt-3 text-sm text-slate-400">还没有发布过题包。</p>'
          }
          ${totalBuyers ? `<p class="mt-3 text-xs text-slate-400">合计 ${totalBuyers} 人购买，按开通时标价估算约 ${priceLabel(totalCents)}。兑换码可能是团购价，实际收入以你自己的收款记录为准。</p>` : ""}
        </section>
        <section class="card p-4 sm:p-5" id="sales">
          <h2 class="font-semibold">付款通知 ${pendingCount ? `<span class="chip bg-amber-50 text-amber-700">${pendingCount} 条待核对</span>` : ""}</h2>
          <p class="mt-1 text-xs text-slate-500">买家付款后点了「我已付款」。先在你的微信 / 支付宝里核对到账，再点开通；没收到就点「没收到」并写原因。</p>
          ${incomingHtml(requests.incoming)}
        </section>
        <section class="card p-4 sm:p-5">
          <h2 class="font-semibold">我购买的题包</h2>
          ${owned ? `<ul class="mt-2 text-sm">${owned}</ul>` : '<p class="mt-3 text-sm text-slate-400">还没有购买过付费题包。</p>'}
        </section>
        ${outgoingHtml(requests.outgoing)}
      </div>
      <aside class="min-w-0 space-y-5">
        ${paymentHtml(payment)}
        <section class="card p-5">
          <h2 class="font-semibold">输入兑换码</h2>
          <p class="mt-1 text-xs text-slate-500">向作者购买后拿到的 16 位兑换码。</p>
          <form id="redeemForm" class="mt-3 flex gap-2">
            <input class="input font-mono uppercase" name="code" placeholder="ABCD-EFGH-JKLM-NPQR" maxlength="24" required>
            <button class="btn btn-primary shrink-0" type="submit">兑换</button>
          </form>
        </section>
        <section class="card p-5">
          <h2 class="font-semibold">个人资料</h2>
          <form id="profileForm" class="mt-3 space-y-3">
            <div><label class="label">显示名</label><input class="input" name="display_name" maxlength="30"
              value="${escapeHtml(state.user.display_name)}"></div>
            <div><label class="label">简介</label><textarea class="input" name="bio" rows="3" maxlength="300">${escapeHtml(bio)}</textarea></div>
            <button class="btn" type="submit">保存</button>
          </form>
        </section>
        <section class="card p-5">
          <h2 class="font-semibold">已授权的桌面端</h2>
          <p class="mt-1 text-xs text-slate-500">用于从题有据桌面端一键打包上传题包，可随时吊销。</p>
          <ul class="mt-2 space-y-2 text-xs" id="desktopTokensList">${
            (tokensData.tokens || []).length
              ? tokensData.tokens
                  .map(
                    (t) => `<li class="flex items-center justify-between gap-2 border-t border-slate-100 pt-2">
                <div class="min-w-0"><div class="truncate font-medium text-slate-700">${escapeHtml(t.label)}</div>
                  <div class="text-slate-400">${timeAgo(t.created_at)}授权${t.last_used_at ? ` · 最近使用 ${timeAgo(t.last_used_at)}` : ""}</div></div>
                <button type="button" class="btn btn-danger shrink-0 px-2 py-1 text-xs" data-revoke-token="${t.id}">吊销</button></li>`,
                  )
                  .join("")
              : '<li class="text-slate-400">暂无已授权设备。</li>'
          }</ul>
        </section>
        <section class="card p-5">
          <h2 class="font-semibold">修改密码</h2>
          <form id="passwordForm" class="mt-3 space-y-3">
            <input class="input" type="password" name="current_password" placeholder="原密码" autocomplete="current-password" required>
            <input class="input" type="password" name="new_password" placeholder="新密码（至少 8 位）" minlength="8" autocomplete="new-password" required>
            <button class="btn" type="submit">修改</button>
          </form>
        </section>
      </aside>
    </div>`);
  bindRedeemForm(document.getElementById("redeemForm"));
  bindSales();
  bindPayment();
  const profile = document.getElementById("profileForm");
  profile.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(profile.querySelector("button"), async () => {
      const body = Object.fromEntries(new FormData(profile));
      await api("/api/me/profile", { method: "POST", body });
      state.user.display_name = body.display_name || state.user.username;
      window.dispatchEvent(new Event("tyj:session"));
      toast("已保存。", "success");
    });
  });
  const password = document.getElementById("passwordForm");
  password.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(password.querySelector("button"), async () => {
      await api("/api/me/password", { method: "POST", body: Object.fromEntries(new FormData(password)) });
      password.reset();
      toast("密码已修改，其他设备上的登录已失效。", "success");
    });
  });
  document.querySelectorAll("[data-revoke-token]").forEach((btn) =>
    btn.addEventListener("click", () => {
      busy(btn, async () => {
        await api(`/api/desktop-tokens/${btn.dataset.revokeToken}`, { method: "DELETE", body: {} });
        toast("已吊销该桌面端授权。", "success");
        meView();
      });
    }),
  );
}

export async function desktopAuthView() {
  if (!state.user) {
    return needLogin("请先登录社区账号，再授权题有据桌面端一键上传题包。");
  }
  const params = queryParams();
  const label = (params.get("label") || "题有据桌面端").slice(0, 40);
  setView(`
    <div class="card mx-auto mt-6 max-w-md p-6">
      <h1 class="text-lg font-semibold text-slate-900">授权题有据桌面端上传</h1>
      <p class="mt-2 text-sm leading-6 text-slate-600">
        当前已登录账号：<strong class="text-slate-900">${escapeHtml(state.user.display_name || state.user.username)}</strong>
        <span class="text-xs text-slate-400">(@${escapeHtml(state.user.username)})</span>
      </p>
      <p class="mt-2 text-xs leading-5 text-slate-500">
        授权后，题有据桌面端将只获取上传题包所需的专用令牌（本机加密存储），可在「我的 → 已授权的桌面端」随时吊销。
      </p>
      <div class="mt-5 flex flex-wrap gap-2">
        <button id="grantDesktopBtn" type="button" class="btn btn-primary flex-1">确认授权并回到题有据</button>
        <a href="#/" class="btn">取消</a>
      </div>
      <div id="desktopAuthFallback" class="mt-4 hidden rounded-lg bg-emerald-50 p-3 text-xs text-emerald-900">
        <p class="font-medium">正在唤起题有据桌面端…</p>
        <p class="mt-1">如果浏览器没有自动弹出打开提示，请点击：<a id="desktopAuthUriLink" href="#" class="font-semibold underline">立即回到题有据</a></p>
      </div>
    </div>`);
  document.getElementById("grantDesktopBtn").addEventListener("click", (event) =>
    busy(event.currentTarget, async () => {
      const result = await api("/api/desktop-tokens", { method: "POST", body: { label } });
      const u = result.user || state.user;
      const uri =
        "tiyouju://community-auth?" +
        new URLSearchParams({
          token: result.token,
          username: u.username || "",
          display_name: u.display_name || u.username || "",
          role: u.role || "user",
          site: location.origin + "/",
        }).toString();
      const box = document.getElementById("desktopAuthFallback");
      const link = document.getElementById("desktopAuthUriLink");
      if (link) link.href = uri;
      if (box) box.classList.remove("hidden");
      toast("授权成功，正在返回题有据桌面端…", "success");
      location.href = uri;
    }),
  );
}

function bindSales() {
  document.querySelectorAll("[data-grant]").forEach((button) =>
    button.addEventListener("click", () => {
      if (!window.confirm("确认已经在你的收款记录里看到这笔钱了？开通后买家立刻可以下载。")) return;
      busy(button, async () => {
        await api(`/api/purchase-requests/${button.dataset.grant}/grant`, { method: "POST", body: {} });
        toast("已开通。", "success");
        meView();
      });
    }),
  );
  document.querySelectorAll("[data-decline]").forEach((button) =>
    button.addEventListener("click", () => {
      const reason = window.prompt("写一句原因，买家能看到（如：没有查到这笔转账，请核对后重新通知）：");
      if (!reason) return;
      busy(button, async () => {
        await api(`/api/purchase-requests/${button.dataset.decline}/decline`, { method: "POST", body: { reason } });
        toast("已回复买家。");
        meView();
      });
    }),
  );
}

function bindPayment() {
  const form = document.getElementById("paymentForm");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(form.querySelector("button"), async () => {
      await api("/api/me/payment", { method: "POST", body: { note: new FormData(form).get("note") } });
      toast("收款说明已保存。", "success");
    });
  });
  document.getElementById("qrInput").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    if (file.size > 512 * 1024) {
      toast("收款码图片不能超过 512 KB。", "error");
      return;
    }
    const body = new FormData();
    body.append("file", file);
    try {
      await api("/api/me/payment/qr", { method: "POST", form: body });
      toast("收款码已上传。", "success");
      meView();
    } catch (error) {
      toast(error.message, "error");
    }
  });
  document.getElementById("qrDelete")?.addEventListener("click", (event) => {
    if (!window.confirm("删除收款码？")) return;
    busy(event.currentTarget, async () => {
      await api("/api/me/payment/qr", { method: "DELETE" });
      toast("已删除。");
      meView();
    });
  });
}

export function bindRedeemForm(form, onDone) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(form.querySelector("button"), async () => {
      const result = await api("/api/redeem", { method: "POST", body: { code: new FormData(form).get("code") } });
      form.reset();
      toast(result.already_owned ? `你已经拥有《${result.title}》，这个码没有被用掉。` : `兑换成功：《${result.title}》`, "success");
      if (onDone) onDone(result);
      else location.hash = `#/pack/${result.pack_id}`;
    });
  });
}

export async function userView(username) {
  const data = await api(`/api/users/${encodeURIComponent(username)}`);
  const user = data.user;
  setView(`
    <div class="card p-4 sm:p-6">
      <h1 class="text-xl font-semibold">${escapeHtml(user.display_name || user.username)}</h1>
      <p class="text-sm text-slate-400">@${escapeHtml(user.username)} · ${timeAgo(user.created_at)}加入</p>
      ${user.bio ? `<p class="mt-3 text-sm text-slate-600">${escapeHtml(user.bio)}</p>` : ""}
    </div>
    <div class="mt-5 grid gap-5 md:grid-cols-2">
      <section class="card p-4 sm:p-5"><h2 class="font-semibold">题包</h2>
        <ul class="mt-2 text-sm">${data.packs.map((p) => `<li class="flex flex-wrap justify-between gap-2 border-t border-slate-100 py-2">
          <a href="#/pack/${p.id}" class="hover:text-emerald-700">${escapeHtml(p.title)}</a>
          <span class="text-xs text-slate-400">${p.originality === "original" ? "原创 · " : ""}${priceLabel(p.price_cents)} · ${p.question_count} 题</span></li>`).join("") || '<li class="py-2 text-slate-400">暂无</li>'}</ul>
      </section>
      <section class="card p-4 sm:p-5"><h2 class="font-semibold">心得</h2>
        <ul class="mt-2 text-sm">${data.posts.map((p) => `<li class="flex flex-wrap justify-between gap-2 border-t border-slate-100 py-2">
          <a href="#/post/${p.id}" class="hover:text-emerald-700">${escapeHtml(p.title)}</a>
          <span class="text-xs text-slate-400">${p.like_count} 赞 · ${timeAgo(p.created_at)}</span></li>`).join("") || '<li class="py-2 text-slate-400">暂无</li>'}</ul>
      </section>
    </div>`);
}
