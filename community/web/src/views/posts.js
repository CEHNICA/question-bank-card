import { api, state } from "../api.js";
import { escapeHtml, renderMarkdown, timeAgo } from "../render.js";
import { avatar, busy, needLogin, queryParams, setView, toast } from "../ui.js";
import { reportPrompt } from "./packs.js";

export async function postsView() {
  const params = queryParams();
  const q = params.get("q") || "";
  const sort = params.get("sort") || "";
  const page = Number(params.get("page") || 1);
  const query = new URLSearchParams();
  if (q) query.set("q", q);
  if (sort) query.set("sort", sort);
  if (page > 1) query.set("page", String(page));
  const data = await api(`/api/posts?${query}`);
  const link = (changes) => {
    const next = new URLSearchParams(query);
    next.delete("page");
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    return `#/posts${next.toString() ? `?${next}` : ""}`;
  };
  setView(`
    <div class="mx-auto max-w-3xl">
      <div class="flex items-center gap-2">
        <h1 class="text-xl font-semibold">教学心得</h1>
        <div class="ml-3 flex gap-1 text-sm">
          <a href="${link({ sort: "" })}" class="nav-link ${sort !== "hot" ? "is-active" : ""}">最新</a>
          <a href="${link({ sort: "hot" })}" class="nav-link ${sort === "hot" ? "is-active" : ""}">最多赞</a>
        </div>
        <a href="#/posts/new" class="btn btn-primary ml-auto">写心得</a>
      </div>
      <form id="postSearch" class="mt-4"><input class="input" name="q" value="${escapeHtml(q)}" placeholder="搜索心得"></form>
      <div class="mt-4 space-y-3">
        ${
          data.posts
            .map(
              (post) => `<a href="#/post/${post.id}" class="card block p-4 transition hover:shadow-md">
          <h2 class="font-medium text-slate-900">${escapeHtml(post.title)}</h2>
          <p class="mt-1 line-clamp-2 text-sm text-slate-500">${escapeHtml(post.excerpt)}</p>
          <div class="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <span>${escapeHtml(post.author_name || post.author_username)}</span><span>· ${timeAgo(post.created_at)}</span>
            <span>· ${post.like_count} 赞</span><span>· ${post.comment_count} 评论</span>
            ${post.pack_title ? `<span class="chip bg-emerald-50 text-emerald-700">📦 ${escapeHtml(post.pack_title)}</span>` : ""}
          </div></a>`,
            )
            .join("") || '<div class="card p-10 text-center text-sm text-slate-400">还没有心得，来写第一篇吧。</div>'
        }
      </div>
      <div class="mt-6 flex justify-center gap-2">
        ${page > 1 ? `<a class="btn" href="${link({ page: String(page - 1) })}">上一页</a>` : ""}
        ${data.has_more ? `<a class="btn" href="${link({ page: String(page + 1) })}">下一页</a>` : ""}
      </div>
    </div>`);
  document.getElementById("postSearch").addEventListener("submit", (event) => {
    event.preventDefault();
    location.hash = link({ q: new FormData(event.target).get("q").trim() });
  });
}

function editorHtml({ title = "", body = "", packId = "", packTitle = "", submit = "发布" }) {
  return `
    <form id="postForm" class="card mt-4 space-y-4 p-5">
      <div><label class="label">标题</label><input class="input" name="title" maxlength="100" required value="${escapeHtml(title)}"></div>
      ${packId ? `<input type="hidden" name="pack_id" value="${escapeHtml(packId)}">
        <p class="text-sm text-slate-500">关联题包：<span class="chip bg-emerald-50 text-emerald-700">📦 ${escapeHtml(packTitle)}</span></p>` : ""}
      <div class="grid gap-4 md:grid-cols-2">
        <div><label class="label">正文（Markdown，公式用 $…$ 或 $$…$$）</label>
          <textarea class="input min-h-[320px] font-mono text-[13px]" name="body" required maxlength="20000">${escapeHtml(body)}</textarea></div>
        <div><div class="label">预览</div><div id="postPreview" class="prose-lite min-h-[320px] rounded-lg border border-dashed border-slate-200 p-3"></div></div>
      </div>
      <button class="btn btn-primary" type="submit">${submit}</button>
    </form>`;
}

function bindEditor(onSubmit) {
  const form = document.getElementById("postForm");
  const preview = document.getElementById("postPreview");
  let timer = null;
  const refresh = () => {
    preview.innerHTML = renderMarkdown(form.body.value) || '<span class="text-slate-400">在左边写，这里实时预览。</span>';
  };
  form.body.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 200);
  });
  refresh();
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(form.querySelector("button[type=submit]"), () => onSubmit(Object.fromEntries(new FormData(form))));
  });
}

export async function newPostView() {
  if (!state.user) return needLogin("登录后才能写心得。");
  const packId = queryParams().get("pack") || "";
  let packTitle = "";
  if (packId) {
    const pack = await api(`/api/packs/${packId}`).catch(() => null);
    packTitle = pack?.pack?.title || "";
  }
  setView(`<div class="mx-auto max-w-5xl"><h1 class="text-xl font-semibold">写心得</h1>
    ${editorHtml({ packId: packTitle ? packId : "", packTitle })}</div>`);
  bindEditor(async (body) => {
    const result = await api("/api/posts", { method: "POST", body });
    toast("已发布。", "success");
    location.hash = `#/post/${result.id}`;
  });
}

export async function postDetailView(id) {
  const data = await api(`/api/posts/${id}`);
  const { post, viewer } = data;
  const comments = data.comments
    .map(
      (c) => `<li class="flex gap-3 border-t border-slate-100 py-3">${avatar(c.author_name)}
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 text-xs text-slate-400">
            <a href="#/u/${encodeURIComponent(c.author_username)}" class="font-medium text-slate-600">${escapeHtml(c.author_name || c.author_username)}</a>
            <span>${timeAgo(c.created_at)}</span>
            ${state.user && (state.user.id === c.author_id || state.user.role === "admin") ? `<button class="ml-auto hover:text-rose-600" data-del-comment="${c.id}">删除</button>` : ""}
            ${state.user && state.user.id !== c.author_id ? `<button class="hover:text-rose-600" data-report-comment="${c.id}">举报</button>` : ""}
          </div>
          <div class="prose-lite mt-1 text-sm">${renderMarkdown(c.body)}</div></div></li>`,
    )
    .join("");
  setView(`
    <article class="mx-auto max-w-3xl">
      <div class="card p-6">
        <h1 class="text-2xl font-semibold text-slate-900">${escapeHtml(post.title)}</h1>
        <div class="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-500">${avatar(post.author_name)}
          <a href="#/u/${encodeURIComponent(post.author_username)}" class="hover:text-emerald-700">${escapeHtml(post.author_name || post.author_username)}</a>
          <span>· ${timeAgo(post.created_at)}</span>
          ${post.pack_id ? `<a href="#/pack/${post.pack_id}" class="chip bg-emerald-50 text-emerald-700">📦 ${escapeHtml(post.pack_title)}</a>` : ""}
          ${post.status === "hidden" ? '<span class="chip">已隐藏</span>' : ""}
        </div>
        <div class="prose-lite mt-5">${renderMarkdown(post.body)}</div>
        <div class="mt-6 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4">
          <button class="btn ${viewer.liked ? "border-emerald-200 bg-emerald-50 text-emerald-700" : ""}" id="likeBtn">👍 <span id="likeCount">${post.like_count}</span></button>
          ${viewer.can_manage ? '<button class="btn" id="editBtn">编辑</button><button class="btn btn-danger" id="deleteBtn">删除</button>' : ""}
          ${state.user && !viewer.can_manage ? '<button class="btn btn-danger ml-auto" id="reportPostBtn">举报</button>' : ""}
        </div>
      </div>
      <section class="card mt-5 p-6" id="commentsBox">
        <h2 class="font-semibold">评论 ${data.comments.length}</h2>
        <ul class="mt-2">${comments || '<li class="py-3 text-sm text-slate-400">还没有评论。</li>'}</ul>
        ${
          state.user
            ? `<form id="commentForm" class="mt-3 space-y-2"><textarea class="input" name="body" rows="3" maxlength="3000" required placeholder="说点什么（支持 Markdown 与 $公式$）"></textarea>
               <button class="btn btn-primary" type="submit">发表评论</button></form>`
            : `<p class="mt-3 text-sm text-slate-500"><a class="text-emerald-700" href="#/login?next=${encodeURIComponent(location.hash)}">登录</a>后参与评论。</p>`
        }
      </section>
    </article>`);

  document.getElementById("likeBtn").addEventListener("click", (event) => {
    if (!state.user) {
      location.hash = `#/login?next=${encodeURIComponent(location.hash)}`;
      return;
    }
    const button = event.currentTarget;
    busy(button, async () => {
      const result = await api(`/api/posts/${post.id}/like`, { method: "POST", body: {} });
      document.getElementById("likeCount").textContent = result.like_count;
      button.classList.toggle("bg-emerald-50", result.liked);
      button.classList.toggle("text-emerald-700", result.liked);
      button.classList.toggle("border-emerald-200", result.liked);
    });
  });
  document.getElementById("deleteBtn")?.addEventListener("click", (event) => {
    if (!window.confirm("删除后这篇心得不再公开。确定？")) return;
    busy(event.currentTarget, async () => {
      await api(`/api/posts/${post.id}`, { method: "DELETE" });
      toast("已删除。");
      location.hash = "#/posts";
    });
  });
  document.getElementById("editBtn")?.addEventListener("click", () => {
    setView(`<div class="mx-auto max-w-5xl"><h1 class="text-xl font-semibold">编辑心得</h1>
      ${editorHtml({ title: post.title, body: post.body, submit: "保存" })}</div>`);
    bindEditor(async (body) => {
      await api(`/api/posts/${post.id}`, { method: "PATCH", body });
      toast("已保存。", "success");
      postDetailView(id);
    });
  });
  document.getElementById("reportPostBtn")?.addEventListener("click", (event) =>
    reportPrompt(event.currentTarget, `/api/posts/${post.id}/report`),
  );
  const commentForm = document.getElementById("commentForm");
  commentForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(commentForm.querySelector("button"), async () => {
      await api(`/api/posts/${post.id}/comments`, { method: "POST", body: Object.fromEntries(new FormData(commentForm)) });
      postDetailView(id);
    });
  });
  document.querySelectorAll("[data-del-comment]").forEach((button) =>
    button.addEventListener("click", () => {
      if (!window.confirm("删除这条评论？")) return;
      busy(button, async () => {
        await api(`/api/comments/${button.dataset.delComment}`, { method: "DELETE" });
        postDetailView(id);
      });
    }),
  );
  document.querySelectorAll("[data-report-comment]").forEach((button) =>
    button.addEventListener("click", () => reportPrompt(button, `/api/comments/${button.dataset.reportComment}/report`)),
  );
}
