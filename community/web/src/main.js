import "katex/dist/katex.min.css";
import "./style.css";
import { api, loadSession, state } from "./api.js";
import { escapeHtml } from "./render.js";
import { errorView, loading, toast } from "./ui.js";
import { browseView, packDetailView, uploadView } from "./views/packs.js";
import { newPostView, postDetailView, postsView } from "./views/posts.js";
import { loginView, meView, registerView, userView } from "./views/account.js";
import { adminView } from "./views/admin.js";
import { rulesView } from "./views/rules.js";

const routes = [
  [/^\/$/, browseView, "packs"],
  [/^\/pack\/([\w-]+)$/, packDetailView, "packs"],
  [/^\/upload$/, uploadView, "upload"],
  [/^\/posts$/, postsView, "posts"],
  [/^\/posts\/new$/, newPostView, "posts"],
  [/^\/post\/(\d+)$/, postDetailView, "posts"],
  [/^\/login$/, loginView, ""],
  [/^\/register$/, registerView, ""],
  [/^\/me$/, meView, ""],
  [/^\/u\/([^/]+)$/, userView, ""],
  [/^\/admin$/, adminView, ""],
  [/^\/rules$/, rulesView, ""],
];

export function renderUserBox() {
  const box = document.getElementById("userBox");
  const user = state.user;
  if (!user) {
    box.innerHTML = `<a href="#/login" class="btn">登录</a><a href="#/register" class="btn btn-primary">注册</a>`;
    return;
  }
  box.innerHTML = `
    ${user.role === "admin" ? '<a href="#/admin" class="nav-link">管理</a>' : ""}
    <a href="#/me" class="nav-link"><span class="max-w-[96px] truncate sm:max-w-none">${escapeHtml(user.display_name || user.username)}</span>${
      user.pending_sales ? ` <span class="chip ml-1 bg-amber-500 text-white" title="待核对的付款通知">${user.pending_sales}</span>` : ""
    }</a>
    <button type="button" class="btn" id="logoutBtn">退出</button>`;
  document.getElementById("logoutBtn").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST", body: {} }).catch(() => null);
    state.user = null;
    renderUserBox();
    toast("已退出登录。");
    location.hash = "#/";
  });
}

async function route() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const path = hash.split("?")[0] || "/";
  for (const [pattern, view, nav] of routes) {
    const match = pattern.exec(path);
    if (!match) continue;
    document.querySelectorAll("[data-nav]").forEach((link) => {
      link.classList.toggle("is-active", link.dataset.nav === nav);
    });
    loading();
    try {
      await view(...match.slice(1).map(decodeURIComponent));
    } catch (error) {
      errorView(error);
    }
    return;
  }
  errorView(new Error("没有这个页面。"));
}

window.addEventListener("hashchange", route);
window.addEventListener("tyj:session", renderUserBox);

(async () => {
  await loadSession();
  document.getElementById("siteName").textContent = state.config.site_name || "题有据社区";
  document.title = state.config.site_name || "题有据社区";
  renderUserBox();
  await route();
})();
