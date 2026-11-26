import { unzipSync } from "fflate";
import { api, state } from "../api.js";
import { escapeHtml, megabytes, priceLabel, renderMarkdown, renderMathText, timeAgo, typeName, yuan } from "../render.js";
import { avatar, busy, needLogin, queryParams, setView, toast } from "../ui.js";
import { bindRedeemForm, statusChip } from "./account.js";

const COVER_PATTERN = /^community-cover\.(png|jpe?g|webp)$/;

function uint8ToBase64(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  }
  return btoa(binary);
}

async function inspectCommunityZip(file) {
  const seen = new Set();
  const bytes = new Uint8Array(await file.arrayBuffer());
  let files;
  try {
    files = unzipSync(bytes, {
      filter(entry) {
        seen.add(entry.name);
        if (entry.name === "题有据备份.json" || entry.name === "community-preview.json") {
          return entry.originalSize < 4 * 1024 * 1024;
        }
        return COVER_PATTERN.test(entry.name) && entry.originalSize <= 1024 * 1024;
      },
    });
  } catch {
    throw new Error("这个文件打不开，不是完整的 zip 题包。");
  }
  if (!seen.has("题库.sqlite3") || !files["题有据备份.json"]) {
    throw new Error("这不是题有据导出的题包：缺少清单或题库。");
  }
  const decoder = new TextDecoder();
  let manifest;
  let preview;
  try {
    manifest = JSON.parse(decoder.decode(files["题有据备份.json"]));
  } catch {
    throw new Error("题包清单读不出来。");
  }
  if (manifest.format !== 1) throw new Error("题包格式版本不认识，请升级题有据后重新导出。");
  if (!manifest.community || !files["community-preview.json"]) {
    throw new Error("请用题有据桌面版的「导出为社区题包」重新导出，普通备份包不能直接发到社区。");
  }
  try {
    preview = JSON.parse(decoder.decode(files["community-preview.json"]));
  } catch {
    throw new Error("题包预览读不出来，请重新导出。");
  }
  let coverBase64 = "";
  for (const [name, data] of Object.entries(files)) {
    if (COVER_PATTERN.test(name)) {
      coverBase64 = uint8ToBase64(data);
      break;
    }
  }
  return { manifest, preview, coverBase64 };
}

const STAGES = ["小学", "初中", "高中", "大学", "其他"];

function packCard(pack) {
  const cover = pack.has_cover
    ? `<img src="/api/packs/${pack.id}/cover" alt="" loading="lazy" class="h-full w-full object-cover object-top">`
    : `<div class="grid h-full place-items-center text-3xl text-slate-300">∑</div>`;
  const tags = String(pack.tags_text || "")
    .split(",")
    .filter(Boolean)
    .slice(0, 3)
    .map((tag) => `<span class="chip">${escapeHtml(tag)}</span>`)
    .join("");
  return `<a href="#/pack/${pack.id}" class="card group flex flex-col overflow-hidden transition hover:-translate-y-0.5 hover:shadow-md">
    <div class="relative aspect-[4/3] overflow-hidden border-b border-slate-100 bg-slate-50">${cover}
      <span class="absolute right-2 top-2 rounded-full px-2 py-0.5 text-xs font-medium ${
        pack.price_cents > 0 ? "bg-amber-500 text-white" : "bg-emerald-600 text-white"
      }">${priceLabel(pack.price_cents)}</span>
      ${pack.originality === "original" ? '<span class="absolute left-2 top-2 rounded-full bg-white/90 px-2 py-0.5 text-xs text-emerald-700">原创</span>' : ""}</div>
    <div class="flex flex-1 flex-col gap-1.5 p-3">
      <h3 class="line-clamp-2 text-sm font-medium text-slate-900 group-hover:text-emerald-700">${escapeHtml(pack.title)}</h3>
      <div class="flex flex-wrap gap-1">${tags}</div>
      <div class="mt-auto flex items-center justify-between pt-1 text-xs text-slate-400">
        <span>${escapeHtml(pack.author_name || pack.author_username)}</span>
        <span>${pack.question_count} 题 · ${pack.downloads} 下载</span>
      </div>
    </div></a>`;
}

export async function browseView() {
  const params = queryParams();
  const filters = {
    q: params.get("q") || "",
    stage: params.get("stage") || "",
    year: params.get("year") || "",
    price: params.get("price") || "",
    tag: params.get("tag") || "",
    sort: params.get("sort") || "new",
    page: params.get("page") || "1",
  };
  const query = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
  const data = await api(`/api/packs?${query}`);

  const stageCounts = {};
  const yearCounts = {};
  for (const row of data.facets) {
    if (row.stage) stageCounts[row.stage] = (stageCounts[row.stage] || 0) + row.n;
    if (row.year) yearCounts[row.year] = (yearCounts[row.year] || 0) + row.n;
  }
  const link = (changes) => {
    const next = new URLSearchParams(query);
    next.delete("page");
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    const text = next.toString();
    return `#/${text ? `?${text}` : ""}`;
  };
  const filterItem = (label, count, changes, active) =>
    `<a href="${link(changes)}" class="filter-item ${active ? "is-active" : ""}"><span>${escapeHtml(label)}</span>${
      count !== undefined ? `<span class="text-xs text-slate-400">${count}</span>` : ""
    }</a>`;
  const pillItem = (label, changes, active) =>
    `<a href="${link(changes)}" class="pill-filter ${active ? "is-active" : ""}">${escapeHtml(label)}</a>`;
  const years = Object.keys(yearCounts).sort((a, b) => b - a);

  setView(`
    <div class="grid gap-6 md:grid-cols-[200px_1fr]">
      <aside class="hidden space-y-5 text-sm md:block">
        <div><div class="label px-2">学段</div>
          ${filterItem("全部", undefined, { stage: "" }, !filters.stage)}
          ${STAGES.map((s) => filterItem(s, stageCounts[s] || 0, { stage: s }, filters.stage === s)).join("")}</div>
        <div><div class="label px-2">价格</div>
          ${filterItem("全部", undefined, { price: "" }, !filters.price)}
          ${filterItem("免费", undefined, { price: "free" }, filters.price === "free")}
          ${filterItem("付费", undefined, { price: "paid" }, filters.price === "paid")}</div>
        ${years.length ? `<div><div class="label px-2">年份</div>
          ${filterItem("全部", undefined, { year: "" }, !filters.year)}
          ${years.map((y) => filterItem(y, yearCounts[y], { year: y }, filters.year === y)).join("")}</div>` : ""}
      </aside>
      <section class="min-w-0">
        <form id="searchForm" class="mb-3 flex flex-wrap items-center gap-2">
          <div class="flex min-w-0 flex-1 items-center gap-2 sm:flex-initial">
            <input class="input min-w-0 flex-1 sm:w-64" name="q" value="${escapeHtml(filters.q)}" placeholder="搜索标题、介绍、标签">
            <button class="btn shrink-0" type="submit">搜索</button>
          </div>
          ${filters.tag ? `<a href="${link({ tag: "" })}" class="chip bg-emerald-50 text-emerald-700">标签：${escapeHtml(filters.tag)} ✕</a>` : ""}
          <div class="ml-auto flex shrink-0 gap-1 text-sm">
            <a href="${link({ sort: "" })}" class="nav-link ${filters.sort !== "popular" ? "is-active" : ""}">最新</a>
            <a href="${link({ sort: "popular" })}" class="nav-link ${filters.sort === "popular" ? "is-active" : ""}">最多下载</a>
          </div>
        </form>
        <div class="mb-4 space-y-2 md:hidden">
          <div class="flex items-center gap-1.5 overflow-x-auto pb-0.5">
            ${pillItem("全部学段", { stage: "" }, !filters.stage)}
            ${STAGES.map((s) => pillItem(s, { stage: s }, filters.stage === s)).join("")}
          </div>
          <div class="flex items-center gap-1.5 overflow-x-auto pb-0.5">
            ${pillItem("全部价格", { price: "" }, !filters.price)}
            ${pillItem("免费", { price: "free" }, filters.price === "free")}
            ${pillItem("付费", { price: "paid" }, filters.price === "paid")}
            ${years.map((y) => pillItem(`${y} 年`, { year: filters.year === y ? "" : y }, filters.year === y)).join("")}
          </div>
        </div>
        ${
          data.packs.length
            ? `<div class="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:grid-cols-3 xl:grid-cols-4">${data.packs.map(packCard).join("")}</div>`
            : `<div class="card p-10 text-center text-sm text-slate-400">还没有符合条件的题包。${
                state.user ? '<a href="#/upload" class="ml-1 text-emerald-700">发布第一份</a>' : ""
              }</div>`
        }
        <div class="mt-6 flex justify-center gap-2">
          ${data.page > 1 ? `<a class="btn" href="${link({ page: String(data.page - 1) })}">上一页</a>` : ""}
          ${data.has_more ? `<a class="btn" href="${link({ page: String(data.page + 1) })}">下一页</a>` : ""}
        </div>
      </section>
    </div>`);
  document.getElementById("searchForm").addEventListener("submit", (event) => {
    event.preventDefault();
    location.hash = link({ q: new FormData(event.target).get("q").trim() });
  });
}

function sampleHtml(sample, index) {
  const options = sample.options.length
    ? `<ol class="mt-2 grid gap-1 sm:grid-cols-2">${sample.options
        .map((o) => `<li class="flex gap-1.5"><span class="text-slate-400">${escapeHtml(o.key)}.</span><span>${renderMathText(o.text)}</span></li>`)
        .join("")}</ol>`
    : "";
  return `<div class="border-t border-slate-100 py-4 first:border-t-0">
    <div class="mb-1 flex items-center gap-2 text-xs text-slate-400">
      <span>第 ${escapeHtml(sample.number || index + 1)} 题</span><span class="chip">${escapeHtml(typeName(sample.question_type))}</span>
      ${sample.figures ? `<span>含 ${sample.figures} 张配图（下载后可见）</span>` : ""}</div>
    <div class="text-[15px] leading-7">${renderMathText(sample.stem) || '<span class="text-slate-400">（原图题，下载后查看）</span>'}</div>
    ${options}</div>`;
}

export async function packDetailView(id) {
  const data = await api(`/api/packs/${id}`);
  const { pack, preview, viewer } = data;
  const paid = pack.price_cents > 0;
  const types = preview
    ? Object.entries(preview.question_types)
        .map(([key, n]) => `<span class="chip">${escapeHtml(typeName(key))} ${n}</span>`)
        .join("")
    : "";
  const tags = String(pack.tags_text || "")
    .split(",")
    .filter(Boolean)
    .map((tag) => `<a href="#/?tag=${encodeURIComponent(tag)}" class="chip hover:bg-emerald-50">${escapeHtml(tag)}</a>`)
    .join("");

  let action;
  if (pack.r2_purged_at) {
    action = `<div class="rounded-lg bg-slate-100 p-3 text-sm text-slate-600">这份题包文件已被清理释放空间。</div>`;
  } else if (pack.status !== "approved") {
    action = `<div class="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">${statusChip(pack.status)}
      <span class="ml-1">${pack.status === "pending" ? "管理员审核通过后才会公开。" : escapeHtml(pack.review_note || "")}</span></div>`;
  } else if (viewer.can_download) {
    action = `<button class="btn btn-primary w-full py-2" id="downloadBtn">下载题包</button>
      <p class="mt-2 text-xs text-slate-500">下载后在题有据桌面版「设置 → 数据备份与迁移 → 从备份合并」导入，只增不减，可撤销。</p>`;
  } else if (!state.user) {
    action = `<a class="btn btn-primary w-full py-2" href="#/login?next=${encodeURIComponent(location.hash)}">登录后${paid ? "购买" : "下载"}</a>`;
  } else {
    action = purchaseHtml(pack, data.payment, data.my_request);
  }

  setView(`
    <div class="grid gap-6 lg:grid-cols-[1fr_320px]">
      <div class="min-w-0 space-y-5">
        <section class="card p-4 sm:p-6">
          <div class="flex flex-wrap items-center gap-2 text-xs text-slate-400">
            ${pack.stage ? `<span class="chip">${escapeHtml(pack.stage)}</span>` : ""}
            ${pack.subject ? `<span class="chip">${escapeHtml(pack.subject)}</span>` : ""}
            ${pack.year ? `<span class="chip">${pack.year} 年</span>` : ""}
            ${pack.originality === "original" ? '<span class="chip bg-emerald-50 text-emerald-700">作者声明原创</span>' : '<span class="chip">整理汇编</span>'}
            ${pack.include_originals ? '<span class="chip bg-sky-50 text-sky-700">含原卷</span>' : '<span class="chip">不含原卷</span>'}
          </div>
          <h1 class="mt-2 text-xl font-semibold text-slate-900 sm:text-2xl">${escapeHtml(pack.title)}</h1>
          <div class="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-500">${avatar(pack.author_name)}
            <a href="#/u/${encodeURIComponent(pack.author_username)}" class="hover:text-emerald-700">${escapeHtml(pack.author_name)}</a>
            <span>· ${timeAgo(pack.created_at)}</span></div>
          <div class="mt-4 flex items-center justify-between gap-3 rounded-xl border border-slate-200/80 bg-slate-50 px-3.5 py-2.5 lg:hidden">
            <div>
              <div class="text-lg font-semibold ${paid ? "text-amber-600" : "text-emerald-600"}">${priceLabel(pack.price_cents)}</div>
              <div class="text-xs text-slate-500">${pack.question_count} 题 · ${megabytes(pack.size)}</div>
            </div>
            <button type="button" class="btn btn-primary shrink-0" id="jumpActionBtn">${paid ? "去购买 / 下载 ↓" : "去下载 ↓"}</button>
          </div>
          ${pack.description ? `<div class="prose-lite mt-4">${renderMarkdown(pack.description)}</div>` : ""}
          ${tags ? `<div class="mt-4 flex flex-wrap gap-1.5">${tags}</div>` : ""}
        </section>
        ${
          preview
            ? `<section class="card p-4 sm:p-6">
          <div class="flex flex-wrap items-baseline justify-between gap-2"><h2 class="font-semibold">题目预览</h2>
            <span class="text-xs text-slate-400">只展示前 ${preview.samples.length} 道题的题干，预览不含答案与解析</span></div>
          <div class="mt-3 flex flex-wrap gap-1.5">${types}</div>
          <div class="mt-2">${preview.samples.map(sampleHtml).join("") || '<p class="py-4 text-sm text-slate-400">没有可预览的题目。</p>'}</div>
        </section>`
            : ""
        }
        <section class="card p-4 sm:p-6">
          <div class="flex flex-wrap items-center justify-between gap-2"><h2 class="font-semibold">相关心得</h2>
            ${state.user && pack.status === "approved" ? `<a class="btn" href="#/posts/new?pack=${pack.id}">写心得</a>` : ""}</div>
          <ul class="mt-2 text-sm">${data.posts.map((p) => `<li class="flex flex-wrap justify-between gap-2 border-t border-slate-100 py-2">
            <a href="#/post/${p.id}" class="hover:text-emerald-700">${escapeHtml(p.title)}</a>
            <span class="text-xs text-slate-400">${escapeHtml(p.author_name)} · ${p.like_count} 赞</span></li>`).join("") || '<li class="py-2 text-slate-400">还没有人写心得。</li>'}</ul>
        </section>
      </div>
      <aside class="min-w-0 space-y-5">
        <section class="card p-4 sm:p-5" id="packActionCard">
          ${pack.has_cover ? `<img src="/api/packs/${pack.id}/cover" alt="" class="mb-4 w-full rounded-lg border border-slate-100">` : ""}
          <div class="text-2xl font-semibold ${paid ? "text-amber-600" : "text-emerald-600"}">${priceLabel(pack.price_cents)}</div>
          <dl class="mt-3 grid grid-cols-2 gap-y-1 text-sm text-slate-500">
            <dt>题目</dt><dd class="text-right text-slate-700">${pack.question_count} 道</dd>
            <dt>资料</dt><dd class="text-right text-slate-700">${pack.paper_count} 份</dd>
            <dt>大小</dt><dd class="text-right text-slate-700">${megabytes(pack.size)}</dd>
            <dt>下载</dt><dd class="text-right text-slate-700">${pack.downloads} 次</dd>
          </dl>
          <div class="mt-4">${action}</div>
          ${viewer.owned && paid && !viewer.can_manage ? '<p class="mt-2 text-xs text-emerald-700">你已购买这份题包。</p>' : ""}
        </section>
        ${viewer.can_manage ? manageHtml(pack) : ""}
        ${state.user && !viewer.can_manage ? `<button class="btn btn-danger w-full" id="reportBtn">举报这份题包</button>` : ""}
      </aside>
    </div>`);

  document.getElementById("jumpActionBtn")?.addEventListener("click", () => {
    document.getElementById("packActionCard")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  document.getElementById("downloadBtn")?.addEventListener("click", (event) =>
    busy(event.currentTarget, async () => {
      const result = await api(`/api/packs/${pack.id}/download`, { method: "POST", body: {} });
      location.href = result.url;
    }),
  );
  const redeem = document.getElementById("redeemForm");
  if (redeem) bindRedeemForm(redeem, () => packDetailView(id));
  bindPurchase(pack, data.my_request, () => packDetailView(id));
  document.getElementById("reportBtn")?.addEventListener("click", (event) => reportPrompt(event.currentTarget, `/api/packs/${pack.id}/report`));
  if (viewer.can_manage) bindManage(pack, id);
}

function purchaseHtml(pack, payment, myRequest) {
  const author = `<a href="#/u/${encodeURIComponent(pack.author_username)}" class="text-emerald-700">${escapeHtml(pack.author_name)}</a>`;
  const hasPayment = payment && (payment.note || payment.has_qr);
  const howToPay = hasPayment
    ? `<div class="mt-3 rounded-lg border border-slate-200 p-3">
        <div class="text-xs text-slate-400">作者 ${author} 的收款方式</div>
        ${payment.note ? `<p class="mt-1 whitespace-pre-wrap text-sm text-slate-700">${escapeHtml(payment.note)}</p>` : ""}
        ${payment.has_qr ? `<img src="/api/users/${encodeURIComponent(pack.author_username)}/pay-qr" alt="作者收款码" class="mx-auto mt-2 max-h-56 rounded border border-slate-100">` : ""}
      </div>`
    : `<p class="mt-3 rounded-lg bg-slate-50 p-3 text-sm text-slate-500">作者暂时没有填写收款方式，请到 ${author} 的主页联系。</p>`;
  let status = "";
  if (myRequest?.status === "pending") {
    status = `<div class="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
        已通知作者（${timeAgo(myRequest.created_at)}）：${escapeHtml(myRequest.buyer_note)}<br>
        等作者核对到账后就能下载。
        <button type="button" class="ml-1 text-xs text-amber-700 underline" id="cancelRequestBtn">撤回通知</button></div>`;
  } else if (myRequest?.status === "declined") {
    status = `<div class="mt-3 rounded-lg bg-rose-50 p-3 text-sm text-rose-700">作者没有确认上次付款：${escapeHtml(myRequest.decline_reason)}</div>`;
  }
  const notifyForm =
    myRequest?.status === "pending" || !hasPayment
      ? ""
      : `<form id="purchaseForm" class="mt-3 space-y-2">
        <input class="input" name="note" maxlength="100" required placeholder="付款信息，如：微信转账，昵称小王，14:05">
        <button class="btn btn-primary w-full" type="submit">我已付款，通知作者</button></form>`;
  return `<div class="text-sm text-slate-600">
      <p class="font-medium text-slate-800">怎么购买</p>
      <ol class="mt-1 list-decimal space-y-1 pl-5">
        <li>按作者的收款方式付 <strong class="text-amber-600">${priceLabel(pack.price_cents)}</strong>，付款备注写你的用户名 <code>${escapeHtml(state.user.username)}</code>。</li>
        <li>点「我已付款，通知作者」，作者核对到账后给你开通下载。</li>
      </ol>
      ${howToPay}${status}${notifyForm}
      <details class="mt-3"><summary class="cursor-pointer text-xs text-slate-500">作者给了兑换码？在这里兑换</summary>
        <form id="redeemForm" class="mt-2 flex gap-2">
          <input class="input font-mono uppercase" name="code" placeholder="ABCD-EFGH-JKLM-NPQR" maxlength="24" required>
          <button class="btn shrink-0" type="submit">兑换</button></form></details>
      <p class="mt-3 text-xs leading-5 text-slate-400">平台不经手任何款项。付款前请核对收款人是否就是作者；交易与退款由你和作者自行协商，付款后长时间不开通可以举报这份题包。</p>
    </div>`;
}

function bindPurchase(pack, myRequest, onDone) {
  const form = document.getElementById("purchaseForm");
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(form.querySelector("button"), async () => {
      const result = await api(`/api/packs/${pack.id}/purchase`, { method: "POST", body: { note: new FormData(form).get("note") } });
      toast(result.already_owned ? "你已经拥有这份题包。" : "已通知作者，核对到账后会给你开通。", "success");
      onDone();
    });
  });
  document.getElementById("cancelRequestBtn")?.addEventListener("click", (event) => {
    if (!window.confirm("撤回后作者不会再看到这条付款通知。确定撤回？")) return;
    busy(event.currentTarget, async () => {
      await api(`/api/purchase-requests/${myRequest.id}/cancel`, { method: "POST", body: {} });
      toast("已撤回。");
      onDone();
    });
  });
}

export async function reportPrompt(button, path) {
  const reason = window.prompt("请写清楚举报理由（如侵权来源、错误内容），管理员会尽快处理：");
  if (!reason) return;
  await busy(button, async () => {
    await api(path, { method: "POST", body: { reason } });
    toast("已提交举报，谢谢。", "success");
  });
}

function manageHtml(pack) {
  const isAuthor = state.user && pack.author_id === state.user.id;
  const sellable = pack.originality === "original" && !pack.include_originals;
  return `<section class="card p-5">
    <h2 class="font-semibold">管理这份题包</h2>
    <form id="editPackForm" class="mt-3 space-y-3">
      <div><label class="label">标题</label><input class="input" name="title" maxlength="80" value="${escapeHtml(pack.title)}"></div>
      <div><label class="label">价格（元，0 为免费）</label>
        <input class="input" name="price_yuan" inputmode="decimal" value="${yuan(pack.price_cents)}" ${sellable ? "" : "disabled"}>
        <p class="mt-1 text-xs text-slate-400">${
          sellable
            ? '付费需在「<a href="#/me" class="text-emerald-700">我的 → 收款方式</a>」填好收款说明或收款码。'
            : "这份题包声明为整理汇编或带了原卷，只能免费分享。"
        }</p></div>
      <div><label class="label">介绍（支持 Markdown 与 $公式$）</label><textarea class="input" name="description" rows="4">${escapeHtml(pack.description || "")}</textarea></div>
      <button class="btn" type="submit">保存修改</button>
    </form>
    ${
      isAuthor && pack.price_cents > 0
        ? `<div class="mt-5 border-t border-slate-100 pt-4">
      <h3 class="text-sm font-semibold">兑换码（团购、线下发放用）</h3>
      <p class="mt-1 text-xs text-slate-500">买家点「我已付款」的，到「<a href="#/me" class="text-emerald-700">我的 → 付款通知</a>」一键开通即可，不用发码。兑换码适合一次卖给一个班或线下发放；码只在生成时显示一次，请立刻保存。</p>
      <form id="codesForm" class="mt-2 flex flex-wrap gap-2 sm:flex-nowrap">
        <input class="input w-20 shrink-0" type="number" name="count" min="1" max="200" value="10">
        <input class="input min-w-0 flex-1" name="note" maxlength="60" placeholder="备注（如：某班团购）">
        <button class="btn btn-primary shrink-0" type="submit">生成</button></form>
      <div id="newCodes"></div>
      <div id="codeList" class="mt-3 text-xs text-slate-500">正在读取兑换码…</div></div>`
        : ""
    }
    ${isAuthor && pack.status !== "withdrawn" && pack.status !== "taken_down" ? '<button class="btn btn-danger mt-4 w-full" id="withdrawBtn">撤回题包</button>' : ""}
    ${isAuthor && !pack.r2_purged_at && Number(pack.buyers || 0) === 0 && pack.status !== "taken_down" ? '<button class="btn mt-2 w-full" id="purgeOwnBtn">彻底删除并释放存储</button>' : ""}
  </section>`;
}

function bindManage(pack, id) {
  const edit = document.getElementById("editPackForm");
  edit.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(edit.querySelector("button"), async () => {
      await api(`/api/packs/${pack.id}`, { method: "PATCH", body: Object.fromEntries(new FormData(edit)) });
      toast("已保存。", "success");
      packDetailView(id);
    });
  });
  document.getElementById("withdrawBtn")?.addEventListener("click", (event) => {
    if (!window.confirm("撤回后题包不再公开，已兑换的买家仍可下载。确定撤回？")) return;
    busy(event.currentTarget, async () => {
      await api(`/api/packs/${pack.id}/withdraw`, { method: "POST", body: {} });
      toast("已撤回。");
      packDetailView(id);
    });
  });
  document.getElementById("purgeOwnBtn")?.addEventListener("click", (event) => {
    if (!window.confirm("目前还没有人兑换或购买这份题包。彻底删除后文件会从服务器清除、释放免费存储配额且不可恢复，确定？")) return;
    busy(event.currentTarget, async () => {
      await api(`/api/packs/${pack.id}`, { method: "DELETE", body: {} });
      toast("已清除文件并释放空间。", "success");
      packDetailView(id);
    });
  });
  const codes = document.getElementById("codesForm");
  if (!codes) return;
  const loadCodes = async () => {
    const data = await api(`/api/packs/${pack.id}/codes`);
    const { summary } = data;
    const batches = [...new Set(data.codes.map((c) => c.batch_id))];
    document.getElementById("codeList").innerHTML = `
      <p>共 ${summary.total} 个：已用 ${summary.used}，可用 ${summary.available}，作废 ${summary.revoked}</p>
      <ul class="mt-2 max-h-56 space-y-1 overflow-y-auto">${data.codes
        .map(
          (c) => `<li class="flex justify-between gap-2"><span class="font-mono">****-${escapeHtml(c.hint)}</span>
          <span>${c.revoked_at ? "已作废" : c.used_at ? `${escapeHtml(c.used_by_name || c.used_by_username)} 已兑换` : "未使用"}${
            c.note ? ` · ${escapeHtml(c.note)}` : ""
          }</span></li>`,
        )
        .join("")}</ul>
      ${batches.length ? `<div class="mt-2 flex flex-wrap gap-1">${batches
        .map((b) => `<button type="button" class="btn btn-danger px-2 py-0.5 text-xs" data-revoke="${escapeHtml(b)}">作废批次 ${escapeHtml(b)} 的未用码</button>`)
        .join("")}</div>` : ""}`;
    document.querySelectorAll("[data-revoke]").forEach((button) =>
      button.addEventListener("click", () => {
        if (!window.confirm("作废后这些码不能再兑换（已兑换的不受影响）。确定？")) return;
        busy(button, async () => {
          const result = await api(`/api/packs/${pack.id}/codes/revoke`, { method: "POST", body: { batch_id: button.dataset.revoke } });
          toast(`已作废 ${result.revoked} 个。`);
          loadCodes();
        });
      }),
    );
  };
  loadCodes().catch((error) => {
    document.getElementById("codeList").textContent = error.message;
  });
  codes.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(codes.querySelector("button"), async () => {
      const form = new FormData(codes);
      const result = await api(`/api/packs/${pack.id}/codes`, {
        method: "POST",
        body: { count: Number(form.get("count")), note: form.get("note") },
      });
      const text = result.codes.join("\n");
      document.getElementById("newCodes").innerHTML = `<div class="mt-3 rounded-lg bg-amber-50 p-3">
        <p class="text-xs font-medium text-amber-800">${escapeHtml(result.notice)}</p>
        <textarea class="input mt-2 font-mono text-xs" rows="6" readonly>${escapeHtml(text)}</textarea>
        <button type="button" class="btn mt-2" id="copyCodes">复制全部</button></div>`;
      document.getElementById("copyCodes").addEventListener("click", async () => {
        await navigator.clipboard.writeText(text).catch(() => null);
        toast("已复制。", "success");
      });
      loadCodes();
    });
  });
}

export async function uploadView() {
  if (!state.user) return needLogin("登录后才能发布题包。");
  const maxMb = state.config.max_pack_mb || 50;
  const payment = await api("/api/me/payment").catch(() => ({ note: "", has_qr: false }));
  const hasPayment = Boolean(payment.note || payment.has_qr);
  setView(`
    <div class="mx-auto max-w-2xl">
      <h1 class="text-xl font-semibold">发布题包</h1>
      <div class="card mt-4 p-4 text-sm text-slate-600 sm:p-5">
        <p class="font-medium text-slate-800">先在题有据桌面版导出</p>
        <ol class="mt-2 list-decimal space-y-1 pl-5">
          <li>在左侧文件夹树里右键一份试卷或书 →「更多」→「导出为社区题包…」。</li>
          <li>默认不带原卷 PDF / Word，只含题目、配图、解析和标签；预览只放前 3 道题的题干。</li>
          <li>把导出的 <code>题有据社区题包-*.zip</code> 传到下面。单个不超过 ${maxMb} MB。</li>
        </ol>
      </div>
      <form id="uploadForm" class="card mt-4 space-y-4 p-4 sm:p-5">
        <div><label class="label">题包文件</label><input class="input" type="file" name="file" accept=".zip,application/zip" required></div>
        <div><label class="label">标题</label><input class="input" name="title" maxlength="80" required placeholder="如：2026 届高一上期中数学（含解析）"></div>
        <div class="grid gap-4 sm:grid-cols-3">
          <div><label class="label">学段</label><select class="input" name="stage">
            ${STAGES.map((s) => `<option ${s === "高中" ? "selected" : ""}>${s}</option>`).join("")}</select></div>
          <div><label class="label">学科</label><input class="input" name="subject" value="数学" maxlength="20"></div>
          <div><label class="label">年份</label><input class="input" type="number" name="year" min="1990" max="2100" value="${new Date().getFullYear()}"></div>
        </div>
        <div><label class="label">标签（逗号分隔，留空则用题包里的知识点）</label><input class="input" name="tags" maxlength="300" placeholder="函数, 期中, 人教A版"></div>
        <fieldset><legend class="label">题目来源</legend>
          <label class="mt-1 flex items-start gap-2 text-sm text-slate-600"><input type="radio" name="originality" value="compiled" class="mt-1" checked>
            <span>整理、汇编（教辅、真题、讲义整理）<span class="text-xs text-slate-400">— 只能免费分享</span></span></label>
          <label class="mt-1 flex items-start gap-2 text-sm text-slate-600"><input type="radio" name="originality" value="original" class="mt-1">
            <span>全部是我本人原创的题目 <span class="text-xs text-slate-400">— 可以设价出售</span></span></label></fieldset>
        <div><label class="label">价格（元，0 为免费）</label>
          <input class="input" name="price_yuan" inputmode="decimal" value="0" disabled>
          <p class="mt-1 text-xs text-slate-400" id="priceHint">选「本人原创」后才能设价。</p></div>
        <div><label class="label">介绍（支持 Markdown 与 $公式$）</label><textarea class="input" name="description" rows="5" maxlength="5000"></textarea></div>
        <label class="flex items-start gap-2 text-sm text-slate-600"><input type="checkbox" name="rights_confirmed" value="1" class="mt-1" required>
          <span>我确认对这份题包有分发权利（自己编写、整理，或已获得授权），不含盗版教辅、未公开考试题等侵权内容。</span></label>
        <label class="flex items-start gap-2 text-sm text-slate-600"><input type="checkbox" name="originals_confirmed" value="1" class="mt-1">
          <span>如果题包里附带了原卷文件：我确认原卷版权归我或已获授权。（不带原卷可不勾）</span></label>
        <div class="flex flex-wrap items-center gap-3"><button class="btn btn-primary" type="submit">提交审核</button>
          <span class="text-xs text-slate-400" id="uploadProgress">新题包先审后发，审核通过后公开。</span></div>
      </form>
    </div>`);
  const form = document.getElementById("uploadForm");
  const priceHint = document.getElementById("priceHint");
  const progress = document.getElementById("uploadProgress");
  form.querySelectorAll("input[name=originality]").forEach((radio) =>
    radio.addEventListener("change", () => {
      const original = form.originality.value === "original";
      form.price_yuan.disabled = !original;
      if (!original) form.price_yuan.value = "0";
      priceHint.innerHTML = !original
        ? "选「本人原创」后才能设价。"
        : hasPayment
          ? "买家按你的收款方式付款后点「我已付款」，你核对到账再开通。平台不经手任何款项，最低 1 元。"
          : '要收费，先到「<a href="#/me" class="text-emerald-700">我的 → 收款方式</a>」填收款说明或上传收款码；不收费填 0。';
    }),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const file = form.file.files[0];
    if (!file) {
      toast("请选择一个题包 zip 文件。", "error");
      return;
    }
    if (file.size > maxMb * 1024 * 1024) {
      toast(`题包太大了，单个不能超过 ${maxMb} MB。`, "error");
      return;
    }
    busy(form.querySelector("button[type=submit]"), async () => {
      try {
        if (progress) progress.textContent = "正在本机校验题包结构与封面…";
        const inspected = await inspectCommunityZip(file);
        const fd = new FormData(form);
        if (progress) progress.textContent = "正在登记元数据与免费存储配额…";
        const init = await api("/api/packs/init", {
          method: "POST",
          body: {
            title: fd.get("title"),
            stage: fd.get("stage"),
            subject: fd.get("subject"),
            year: fd.get("year"),
            tags: fd.get("tags"),
            price_yuan: fd.get("price_yuan"),
            originality: fd.get("originality"),
            description: fd.get("description"),
            rights_confirmed: fd.get("rights_confirmed") === "1",
            originals_confirmed: fd.get("originals_confirmed") === "1",
            file_size: file.size,
            manifest: inspected.manifest,
            preview: inspected.preview,
            cover_base64: inspected.coverBase64 || undefined,
          },
        });
        if (progress) progress.textContent = `正在流式上传题包（${megabytes(file.size)}）…`;
        await api(init.upload_url, {
          method: "PUT",
          rawBody: file,
          headers: { "Content-Type": "application/zip" },
        });
        toast("已提交，等待管理员审核。", "success");
        location.hash = `#/pack/${init.id}`;
      } catch (error) {
        if (progress) progress.textContent = "新题包先审后发，审核通过后公开。";
        throw error;
      }
    });
  });
}
