import { api, state } from "../api.js";
import { escapeHtml, megabytes, priceLabel, timeAgo } from "../render.js";
import { busy, queryParams, setView, toast } from "../ui.js";
import { statusChip } from "./account.js";

const TABS = [
  ["pending", "待审题包"],
  ["approved", "已上架"],
  ["taken_down", "已下架"],
  ["reports", "举报"],
  ["users", "用户"],
];

export async function adminView() {
  if (state.user?.role !== "admin") {
    setView('<div class="card mx-auto max-w-md p-8 text-center text-slate-600">只有管理员能打开这一页。</div>');
    return;
  }
  const tab = queryParams().get("tab") || "pending";
  const nav = TABS.map(
    ([key, label]) => `<a href="#/admin?tab=${key}" class="nav-link ${tab === key ? "is-active" : ""}">${label}</a>`,
  ).join("");
  let body = "";
  if (tab === "reports") body = await reportsHtml();
  else if (tab === "users") body = await usersHtml();
  else body = await packsHtml(tab);
  setView(`<h1 class="text-xl font-semibold">管理</h1>
    <nav class="mt-3 flex flex-wrap gap-1 text-sm">${nav}</nav>
    <div class="mt-4">${body}</div>`);
  bindActions();
}

async function packsHtml(status) {
  const { packs } = await api(`/api/admin/packs?status=${status}`);
  if (!packs.length) return '<div class="card p-8 text-center text-sm text-slate-400">没有题包。</div>';
  return `<div class="space-y-3">${packs
    .map(
      (p) => `<div class="card flex flex-wrap items-center gap-3 p-4">
      <div class="min-w-0 flex-1">
        <a href="#/pack/${p.id}" class="font-medium text-slate-900 hover:text-emerald-700">${escapeHtml(p.title)}</a>
        <div class="mt-1 flex flex-wrap gap-2 text-xs text-slate-400">${statusChip(p.status)}
          <span>${escapeHtml(p.author_name)}（@${escapeHtml(p.author_username)}）</span>
          <span>${escapeHtml(p.stage)} ${escapeHtml(p.subject)} ${p.year || ""}</span>
          <span>${p.question_count} 题 · ${megabytes(p.size)} · ${priceLabel(p.price_cents)}</span>
          ${p.originality === "original" ? `<span class="chip bg-emerald-50 text-emerald-700">声明原创${p.price_cents > 0 ? "，付费：抽查预览题是否为教辅 / 真题原题" : ""}</span>` : ""}
          ${p.include_originals ? '<span class="chip bg-sky-50 text-sky-700">含原卷，注意核实版权</span>' : ""}
          <span>${timeAgo(p.created_at)}提交</span></div>
        ${p.review_note ? `<div class="mt-1 text-xs text-slate-500">意见：${escapeHtml(p.review_note)}</div>` : ""}
      </div>
      <div class="flex gap-2">
        ${status === "pending" ? `<button class="btn btn-primary" data-review="${p.id}" data-action="approve">通过</button>
          <button class="btn btn-danger" data-review="${p.id}" data-action="reject">驳回</button>` : ""}
        ${status === "approved" ? `<button class="btn btn-danger" data-review="${p.id}" data-action="takedown">下架</button>` : ""}
        ${status === "taken_down" ? `<button class="btn" data-review="${p.id}" data-action="restore">恢复上架</button>` : ""}
      </div></div>`,
    )
    .join("")}</div>`;
}

async function reportsHtml() {
  const { reports } = await api("/api/admin/reports");
  if (!reports.length) return '<div class="card p-8 text-center text-sm text-slate-400">没有待处理的举报。</div>';
  const targetLink = (r) =>
    r.target_type === "pack" ? `#/pack/${r.target_id}` : r.target_type === "post" ? `#/post/${r.target_id}` : null;
  const typeName = { pack: "题包", post: "心得", comment: "评论" };
  return `<div class="space-y-3">${reports
    .map((r) => {
      const href = targetLink(r);
      return `<div class="card p-4">
        <div class="flex flex-wrap items-center gap-2 text-sm">
          <span class="chip">${typeName[r.target_type]}</span>
          ${href ? `<a href="${href}" class="font-medium hover:text-emerald-700">${escapeHtml(r.target_title || r.target_id)}</a>` : `<span>${escapeHtml(r.target_title || r.target_id)}</span>`}
          <span class="text-xs text-slate-400">@${escapeHtml(r.reporter_username)} · ${timeAgo(r.created_at)}</span></div>
        <p class="mt-2 text-sm text-slate-600">${escapeHtml(r.reason)}</p>
        <div class="mt-3 flex gap-2">
          <button class="btn btn-danger" data-resolve="${r.id}" data-action="takedown">下架该内容</button>
          <button class="btn" data-resolve="${r.id}" data-action="dismiss">驳回举报</button></div></div>`;
    })
    .join("")}</div>`;
}

async function usersHtml() {
  const q = queryParams().get("q") || "";
  const { users } = await api(`/api/admin/users?q=${encodeURIComponent(q)}`);
  return `<form id="userSearch" class="mb-3"><input class="input max-w-xs" name="q" value="${escapeHtml(q)}" placeholder="搜索用户名"></form>
    <div class="card divide-y divide-slate-100">${users
      .map(
        (u) => `<div class="flex items-center gap-3 p-3 text-sm">
        <a href="#/u/${encodeURIComponent(u.username)}" class="font-medium">${escapeHtml(u.display_name || u.username)}</a>
        <span class="text-xs text-slate-400">@${escapeHtml(u.username)} · ${timeAgo(u.created_at)}注册</span>
        ${u.role === "admin" ? '<span class="chip">管理员</span>' : ""}
        ${u.banned_at ? '<span class="chip bg-rose-50 text-rose-600">已停用</span>' : ""}
        <span class="ml-auto"></span>
        ${u.has_payment ? `<button class="btn" data-clear-pay="${u.id}">清除收款信息</button>` : ""}
        ${u.role !== "admin" ? `<button class="btn ${u.banned_at ? "" : "btn-danger"}" data-ban="${u.id}" data-banned="${u.banned_at ? "0" : "1"}">${u.banned_at ? "恢复" : "停用"}</button>` : ""}
      </div>`,
      )
      .join("")}</div>`;
}

function bindActions() {
  document.querySelectorAll("[data-review]").forEach((button) =>
    button.addEventListener("click", () => {
      const action = button.dataset.action;
      let note = "";
      if (action === "reject" || action === "takedown") {
        note = window.prompt(action === "reject" ? "驳回理由（作者能看到）：" : "下架理由（作者能看到）：") || "";
        if (!note) return;
      }
      busy(button, async () => {
        await api(`/api/admin/packs/${button.dataset.review}/review`, { method: "POST", body: { action, note } });
        toast("已处理。", "success");
        adminView();
      });
    }),
  );
  document.querySelectorAll("[data-resolve]").forEach((button) =>
    button.addEventListener("click", () => {
      const note = window.prompt("处理说明（可选）：") ?? null;
      if (note === null) return;
      busy(button, async () => {
        await api(`/api/admin/reports/${button.dataset.resolve}/resolve`, {
          method: "POST",
          body: { action: button.dataset.action, note },
        });
        toast("已处理。", "success");
        adminView();
      });
    }),
  );
  document.querySelectorAll("[data-ban]").forEach((button) =>
    button.addEventListener("click", () => {
      const banned = button.dataset.banned === "1";
      if (banned && !window.confirm("停用后对方立刻被登出，不能再登录。确定？")) return;
      busy(button, async () => {
        await api(`/api/admin/users/${button.dataset.ban}/ban`, { method: "POST", body: { banned } });
        adminView();
      });
    }),
  );
  document.querySelectorAll("[data-clear-pay]").forEach((button) =>
    button.addEventListener("click", () => {
      if (!window.confirm("清除对方的收款说明和收款码？对方的付费题包将无法付款，需要的话请另外下架。")) return;
      busy(button, async () => {
        await api(`/api/admin/users/${button.dataset.clearPay}/clear-payment`, { method: "POST", body: {} });
        toast("已清除。");
        adminView();
      });
    }),
  );
  document.getElementById("userSearch")?.addEventListener("submit", (event) => {
    event.preventDefault();
    location.hash = `#/admin?tab=users&q=${encodeURIComponent(new FormData(event.target).get("q"))}`;
  });
}
