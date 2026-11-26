import { escapeHtml } from "./render.js";

export const app = () => document.getElementById("app");

export function toast(message, kind = "info") {
  const box = document.getElementById("toast");
  const tone =
    kind === "error" ? "bg-rose-600 text-white" : kind === "success" ? "bg-slate-900 text-emerald-300" : "bg-slate-800 text-white";
  const node = document.createElement("div");
  node.className = `pointer-events-auto mt-2 max-w-sm rounded-xl px-4 py-2.5 text-sm shadow-lg ${tone}`;
  node.textContent = message;
  box.append(node);
  setTimeout(() => node.remove(), kind === "error" ? 6000 : 3500);
}

export function setView(html) {
  app().innerHTML = html;
  window.scrollTo({ top: 0 });
}

export function loading(text = "正在加载…") {
  setView(`<div class="py-24 text-center text-sm text-slate-400">${escapeHtml(text)}</div>`);
}

export function errorView(error) {
  setView(`<div class="card mx-auto max-w-md p-8 text-center">
    <p class="text-slate-700">${escapeHtml(error?.message || "页面加载失败。")}</p>
    <a href="#/" class="btn mt-4">回到首页</a></div>`);
}

export function needLogin(message = "这一步需要先登录。") {
  setView(`<div class="card mx-auto max-w-md p-8 text-center">
    <p class="text-slate-700">${escapeHtml(message)}</p>
    <div class="mt-4 flex justify-center gap-2">
      <a href="#/login?next=${encodeURIComponent(location.hash)}" class="btn btn-primary">登录</a>
      <a href="#/register?next=${encodeURIComponent(location.hash)}" class="btn">注册</a>
    </div></div>`);
}

/** 按钮执行期间禁用，出错弹提示。 */
export async function busy(button, task) {
  if (button) button.disabled = true;
  try {
    return await task();
  } catch (error) {
    toast(error.message, "error");
    return undefined;
  } finally {
    if (button) button.disabled = false;
  }
}

export function queryParams() {
  const hash = location.hash.replace(/^#/, "");
  const index = hash.indexOf("?");
  return new URLSearchParams(index >= 0 ? hash.slice(index + 1) : "");
}

export function avatar(name) {
  const letter = escapeHtml(String(name || "?").slice(0, 1));
  return `<span class="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-slate-200 text-xs font-medium text-slate-600">${letter}</span>`;
}
