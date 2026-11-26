# 官网深墨绿工程感改版 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 tiyouju.com 官网整站从米白纸质感改成与 OG 分享图统一的深墨绿 + 金色工程感，重点加强首屏大图冲击力。

**Architecture:** 纯前端静态站（`website/public/`），无构建步骤。改版主体是重写 `styles.css` 的 `:root` 变量与各组件配色，辅以 `index.html` 的少量结构微调（首屏加工具栏条/状态栏、section 加编号）与 `workflow.svg` 的深色化。`app.js` 一行不改，所有 id/class/data-* 属性保持兼容。

**Tech Stack:** 原生 HTML + CSS（CSS 变量 + Grid + Flex）、内联 SVG、Cloudflare Worker 静态资源、Python + Playwright 做视觉与回归验收。

## Global Constraints

- **内容零改动**：所有中文文案、元素顺序、区块数量一律不动。纯视觉改造。
- **`app.js` 一行不改**：`website/public/app.js` 不进入本次改动范围。
- **必须保留的 id**：`versionBadge` / `downloadInstallerBtn` / `downloadPortableBtn` / `releaseMeta` / `downloadInstallerCardBtn` / `downloadPortableCardBtn` / `downloadCardMeta` / `copyPsBtn` / `psInstallCmd` / `galleryZoomBtn` / `galleryMainImg` / `galleryCaption` / `lightbox` / `lightboxImg` / `lightboxCaption` / `lightboxClose` / `install-cli`
- **必须保留的 class**：`app.js` 依赖的 `.tab-btn` / `.thumb-btn` / `.shot-trigger` / `.is-active`
- **必须保留的属性**：`data-src` / `data-caption` / `data-zoom-src` / `data-zoom-caption`
- **SEO 标签不动**：`og:*` / `twitter:*` / `rel="canonical"` / `rel="sitemap"` / `theme-color` / `robots` / `keywords`
- **金色使用纪律**：金色只用于标题高亮、区块序号、强调线、卡片描边。主按钮用金色实心。翠绿只用于图标与辉光。正文一律 `--text` / `--muted`。
- **动效纪律**：过渡时长 0.14s / 0.2s，不做夸张动效；必须支持 `prefers-reduced-motion: reduce`。
- **行尾**：仓库 `.gitattributes` 规定 `*.css` / `*.html` / `*.py` 为 `eol=lf`，新增文件写入必须是 LF。
- **工作目录**：所有命令在仓库根执行；本地工作副本为 `C:\Users\17321\Documents\破甲\_webseo`（稀疏检出 `website` + `docs`）。
- **代理**：跑 `wrangler` / `git push` 前必须清代理 —— `$env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; [System.Net.WebRequest]::DefaultWebProxy = $null`。

---

## 色彩变量（所有任务的共同基准）

```
--bg:        #0d2b27
--bg-deep:   #081a17
--surface:   #123631
--surface-2: #16403a
--gold:      #d6a65d
--gold-hi:   #efc07a
--emerald:   #35b07d
--text:      #eaf2ef
--muted:     #93b0aa
--line:      rgba(214, 166, 93, .16)
--line-hi:   rgba(214, 166, 93, .35)
--radius-lg: 20px
--radius-md: 14px
```

---

### Task 1: 建立验收尺子

**Files:**
- Create: `website/tools/check_dark_theme.py`
- Create: `website/tools/_shots/`（脚本自动创建，截图产物不入库）

**Interfaces:**
- Consumes: 无
- Produces: `python website/tools/check_dark_theme.py` — 退出码 0 = 全部通过；1 = 有 FAIL 项。截图写到 `website/tools/_shots/`。

- [ ] **Step 1: 写检查脚本**

脚本必须做到五件事，缺一不可：

1. 三档宽度（320 / 768 / 1440）整页截图 + 首屏截图，输出到 `website/tools/_shots/`
2. 每档断言无横向溢出：`document.documentElement.scrollWidth - clientWidth <= 1`
3. 断言 body 背景**不含** `rgb(251, 248, 241)`（改版前的米白）—— 注意 body 用的是渐变，要读 `backgroundImage` 不是 `backgroundColor`
4. 断言卡片（`.feature-card` / `.download-card` / `.workflow-card` / `.shot-frame`）背景不含 `rgb(251, 248, 241)` 或 `rgb(255, 252, 246)`
5. 回归断言：
   - `.shot-toolbar` 存在 1 个、`.shot-status` 存在 1 个、`.shot-dots i` 存在 3 个
   - `.section-index` 存在 4 个
   - 17 个关键 id 全部存在
   - 点第 5 个 `.tab-btn` 后：`#galleryMainImg` 的 `src` == `assets/paper-preview.webp`，`#galleryCaption` 文本含「组卷」与「A3」，`#galleryZoomBtn` 的 `data-zoom-src` == `assets/paper-preview.webp`
   - 点 `#copyPsBtn` 后按钮文本含「复制」或「已」，且 `navigator.clipboard.readText()` 结果含 `install.ps1`
   - 5 个 SEO 标签各存在 1 个

**实现要点（踩过的坑）**：

```python
# 剪贴板权限必须在 context 上授予，page 级别授予无效
browser_context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
page = browser_context.new_page()
page.set_viewport_size({"width": width, "height": 900})

# file:// 下 fetch("/api/latest-release") 必然失败，那是本地预览固有现象，不算缺陷
page.on("console", lambda m: errors.append(m.text)
        if m.type == "error" and "latest-release" not in m.text else None)
```

- [ ] **Step 2: 运行尺子，确认它能抓到「还没改」**

Run: `python website/tools/check_dark_theme.py`
Expected: 退出码 1，且 FAIL 项恰好是 5 项 —— body 背景未深色、工具栏条缺失、状态栏缺失、圆点缺失、区块编号为 0。回归项（图集切换 / 复制按钮 / id 保留 / SEO 标签）必须全 OK。

**这条 FAIL 是预期的**：它证明尺子有效。改完代码后同一命令必须变成全绿。

- [ ] **Step 3: 提交**

```bash
git add website/tools/check_dark_theme.py
git commit -m "test(web): 新增官网深色改版的视觉与回归验收尺子"
```

---

### Task 2: 重写 styles.css 为深墨绿工程感

**Files:**
- Modify: `website/public/styles.css`（整文件重写）

**Interfaces:**
- Consumes: Task 1 的尺子
- Produces: 全部 class 的深色配色；新增 class `.shot-toolbar` / `.shot-dots` / `.shot-status` / `.section-index`（Task 3 的 HTML 会用到）

- [ ] **Step 1: 替换 `:root` 变量块**

把文件开头的 `:root { ... }` 整块换成 Global Constraints 里那份变量表，并额外加：

```css
  --glow: rgba(53, 176, 125, .25);
  --shadow-near: 0 30px 60px -20px rgba(0, 0, 0, .7);
  --shadow-far: 0 0 90px -30px rgba(53, 176, 125, .25);
```

- [ ] **Step 2: 改 body 背景为深绿渐变 + 工程网格纹理**

```css
body {
  margin: 0;
  overflow-x: clip;
  max-width: 100%;
  font-family: "PingFang SC", "Microsoft YaHei UI", "Noto Sans SC", system-ui, -apple-system, sans-serif;
  color: var(--text);
  line-height: 1.65;
  -webkit-font-smoothing: antialiased;
  background-color: var(--bg);
  background-image:
    radial-gradient(ellipse 900px 520px at 82% -6%, rgba(53, 176, 125, .16), transparent 62%),
    linear-gradient(rgba(255, 255, 255, .02) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, .02) 1px, transparent 1px);
  background-size: auto, 48px 48px, 48px 48px;
  background-attachment: fixed, scroll, scroll;
}
```

- [ ] **Step 3: 逐组件改配色**

必须改的组件与要点：

| 组件 | 改动 |
|---|---|
| `.site-header` | `background: rgba(13, 43, 39, .82)`；`backdrop-filter: blur(12px)`；`border-bottom: 1px solid var(--line)` |
| `.brand` | `color: var(--text)` |
| `.version-badge` | 底 `rgba(214,166,93,.14)`，字 `var(--gold)` |
| `.nav` | `color: var(--muted)`；hover `color: var(--gold-hi)` |
| `.btn-primary` | `background: var(--gold)`；`color: #14261f`（深墨绿字，金底必须配深字才够帅）；`box-shadow: 0 10px 26px -12px rgba(214,166,93,.6)`；hover 底色 `var(--gold-hi)` |
| `.btn-secondary` | 透明底 + `border: 1px solid var(--line-hi)`；字 `var(--text)`；hover 底 `rgba(214,166,93,.10)` |
| `.eyebrow` | 底 `rgba(214,166,93,.10)`，边 `var(--line)`，字 `var(--gold)` |
| `.hero h1` | `color: var(--text)`；金色高亮由 HTML 用 `<span class="accent">` 包裹实现 |
| `.hero-sub` | `color: var(--muted)`；`strong` 用 `var(--gold-hi)` |
| `.shot-frame` | 底 `var(--surface)`，边 `var(--line)`，双层阴影 `var(--shadow-near), var(--shadow-far)` |
| `.trust-pill` | 底 `rgba(255,255,255,.04)`，边 `var(--line)`，字 `var(--muted)` |
| `.section-head h2` | `color: var(--text)` |
| `.workflow-card` / `.download-card` / `.feature-card` | 底 `var(--surface)`，边 `var(--line)` |
| `.feature-card:hover` | 边 `var(--line-hi)`；`transform: translateY(-3px)` |
| `.feature-tag` | 底 `rgba(214,166,93,.12)`，字 `var(--gold)` |
| `.step-box` | 底 `var(--surface-2)`；左侧 `border-left: 2px solid var(--gold)`；圆角只留右侧 |
| `.step-num` | 底 `var(--gold)`，字 `#14261f` |
| `.tab-btn` | 底 `rgba(255,255,255,.04)`，边 `var(--line)`，字 `var(--muted)` |
| `.tab-btn.is-active` | 边 `var(--gold)`，底 `rgba(214,166,93,.14)`，字 `var(--gold-hi)` |
| `.thumb-btn` | 底 `var(--surface)`，边 `rgba(255,255,255,.08)` |
| `.thumb-btn.is-active` | 边 `var(--gold)` |
| `.hint-box` | 底 `rgba(214,166,93,.08)`，左边 `border-left: 3px solid var(--gold)`，字 `var(--text)` |
| `.code-box` | 保持深色 `background: #0a1e1b`，这是唯一本来就是深色的区块 |
| `.community-banner` | 渐变 `#0d2b27 → #16403a`；加 `border: 1px solid var(--line)`；`h2` 用 `var(--gold)` |
| `.site-footer` | `border-top: 1px solid var(--line)`；字 `var(--muted)` |
| `.lightbox` | 遮罩 `rgba(8, 18, 16, .94)`；`.lightbox-inner` 加 `border: 1px solid var(--line-hi)` |
| `.lightbox-close` | 深底 + 金描边，金字 |

- [ ] **Step 4: 新增 4 个新 class 的样式**

```css
/* 首屏软件面板工具栏条 */
.shot-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 4px 10px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 12px;
}

.shot-dots {
  display: inline-flex;
  gap: 6px;
}

.shot-dots i {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: rgba(255, 255, 255, .16);
}

.shot-dots i:first-child { background: #d95f5f; }
.shot-dots i:nth-child(2) { background: #d6a65d; }
.shot-dots i:nth-child(3) { background: #35b07d; }

.shot-toolbar-title {
  font-size: 12.5px;
  color: var(--muted);
  letter-spacing: .02em;
}

/* 首屏状态栏 */
.shot-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 10px 4px 0;
  font-size: 12px;
  color: rgba(147, 176, 170, .72);
  letter-spacing: .02em;
}

/* 区块编号 */
.section-index {
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 13px;
  letter-spacing: .22em;
  color: var(--gold);
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--line);
  display: inline-block;
}

/* 标题金色高亮 */
.accent {
  color: var(--gold);
}
```

- [ ] **Step 5: 首屏面板专属样式**

`.shot-frame` 在首屏要更浮起，加：

```css
.shot-frame--hero {
  padding: 16px 16px 14px;
  background: linear-gradient(180deg, var(--surface-2), var(--surface));
}

.shot-frame--hero .shot-trigger img {
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, .07);
}
```

- [ ] **Step 6: 支持 prefers-reduced-motion**

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
    scroll-behavior: auto !important;
  }

  .feature-card:hover,
  .btn:hover {
    transform: none;
  }
}
```

- [ ] **Step 7: 跑尺子**

Run: `python website/tools/check_dark_theme.py`
Expected: 「body 背景已深色化」转为 OK；「卡片无浅色纸质感残留」仍 OK。其余 4 项（工具栏条、状态栏、圆点、区块编号）**仍然 FAIL** —— 它们属于 Task 3 的 HTML 改动，此时 FAIL 是正常的。

- [ ] **Step 8: 提交**

```bash
git add website/public/styles.css
git commit -m "feat(web): 官网视觉改为深墨绿工程感"
```

---

### Task 3: 首屏与区块的 HTML 微调

**Files:**
- Modify: `website/public/index.html`（约 20 行）

**Interfaces:**
- Consumes: Task 2 新增的 `.shot-toolbar` / `.shot-dots` / `.shot-status` / `.section-index` / `.accent`
- Produces: Task 1 尺子所断言的 DOM 结构

- [ ] **Step 1: 首屏加软件面板工具栏条与状态栏**

把首屏的：

```html
<figure class="shot-frame">
  <button type="button" class="shot-trigger" ...>
    <img src="assets/review-workspace.webp" alt="题有据逐题核对工作台界面" />
  </button>
  <figcaption class="shot-caption">
    <span>逐题核对工作台 · 左侧原卷对照，右侧整理入库</span>
    <span>点击图片放大</span>
  </figcaption>
</figure>
```

换成：

```html
<figure class="shot-frame shot-frame--hero">
  <div class="shot-toolbar">
    <span class="shot-dots" aria-hidden="true"><i></i><i></i><i></i></span>
    <span class="shot-toolbar-title">题有据 · 逐题核对工作台</span>
    <span class="shot-toolbar-title">点击图片放大</span>
  </div>
  <button
    type="button"
    class="shot-trigger"
    data-zoom-src="assets/review-workspace.webp"
    data-zoom-caption="逐题核对工作台：左侧保留真实原卷与手写痕迹，右侧核对题面、公式与配图，核验后一键入库。"
    aria-label="放大查看逐题核对工作台截图"
  >
    <img src="assets/review-workspace.webp" alt="题有据逐题核对工作台界面" />
  </button>
  <figcaption class="shot-status">
    <span>本地题库 · 1,284 题</span>
    <span>离线可用</span>
  </figcaption>
</figure>
```

注意：`.shot-trigger` 的 `data-zoom-src` / `data-zoom-caption` / `aria-label` **一字不改**；`img` 的 `src` / `alt` 不改。

- [ ] **Step 2: 首屏标题加金色高亮**

`<h1>把零散试卷，变成你的私家题库。</h1>` 改为：

```html
<h1>把零散试卷，<br />变成<span class="accent">你的私家题库。</span></h1>
```

文字内容完全没变，只是加了 `<br>` 与 `<span>` 包裹。

- [ ] **Step 3: 四个区块加编号**

在 `.section-head` **之前**插入 `<div class="section-index">NN</div>`，编号依次为：

- `#workflow` → `01`
- `#showcase` → `02`
- `#features` → `03`
- `#download` → `04`

形如：

```html
<div class="section-index">01</div>
<div class="section-head">
  <h2>四步本地闭环：从整卷散题到可印刷试卷</h2>
  <p>不上传你的私人试卷，不未经确认改动题面，每一步都保留原卷依据。</p>
</div>
```

- [ ] **Step 4: 跑尺子**

Run: `python website/tools/check_dark_theme.py`
Expected: **全部 OK，退出码 0**，输出 `ALL CHECKS PASSED`。

- [ ] **Step 5: 提交**

```bash
git add website/public/index.html
git commit -m "feat(web): 首屏加软件面板工具栏条、区块编号与标题金色高亮"
```

---

### Task 4: 流程图 SVG 深色化

**Files:**
- Modify: `website/public/assets/workflow.svg`

**Interfaces:**
- Consumes: 无
- Produces: 与新配色一致的流程图

- [ ] **Step 1: 读原 SVG 看清结构**

Run: `cat website/public/assets/workflow.svg`
先看清它用了哪些颜色（浅色卡片底 `#fff`、深色文字、彩色线条等），再逐个替换。

- [ ] **Step 2: 换成深色配色**

映射规则：

| 原色系 | 换成 |
|---|---|
| 浅色卡片底（白 / 米白） | `#123631` |
| 卡片描边（浅灰米色） | `rgba(214,166,93,.16)` |
| 正文深色文字 | `#eaf2ef` |
| 次级灰字 | `#93b0aa` |
| 步骤序号圆点底色 | `#d6a65d`，序号字 `#14261f` |
| 连接箭头/线条 | `#35b07d` 或 `#d6a65d` |

SVG 的 `fill` / `stroke` 要用**字面色值或 `rgba()`**，不要用 CSS 变量（SVG 作为 `<img>` 引入时无法访问外部 CSS 变量）。

- [ ] **Step 3: 确认 SVG 里没有写死的白色**

Run: `grep -nE '#fff|#ffffff|white|#f[0-9a-f]{2}' website/public/assets/workflow.svg`
Expected: 无匹配（或仅剩确实需要的极小高光）。

- [ ] **Step 4: 跑尺子 + 肉眼看图**

Run: `python website/tools/check_dark_theme.py`
Expected: 仍全绿。

然后打开 `website/tools/_shots/desktop-1440.png`，确认流程图在深色背景上清晰可读、无白底方块残留。

- [ ] **Step 5: 提交**

```bash
git add website/public/assets/workflow.svg
git commit -m "feat(web): 流程图 SVG 改为深色配色"
```

---

### Task 5: 本地视觉终审

**Files:**
- 无代码改动（只产出截图）

**Interfaces:**
- Consumes: Task 1-4 的全部产出
- Produces: 人工确认视觉质量

- [ ] **Step 1: 跑完整尺子**

Run: `python website/tools/check_dark_theme.py`
Expected: `ALL CHECKS PASSED`，退出码 0。

- [ ] **Step 2: 逐张看截图**

打开 `website/tools/_shots/` 下这些图，逐张确认：

- `desktop-hero.png` —— 首屏：深墨绿底 + 金色标题 + 右侧浮起面板（工具栏条/圆点/状态栏都在），右上角有翠绿光晕，底部有金色发光线
- `desktop-1440.png` —— 整页：无浅色残留，区块编号 `01/02/03/04` 可见，卡片 hover 前是深色
- `tablet-768.png` —— 平板：单列布局不塌
- `mobile-320.png` —— 手机：无横向溢出，导航折叠正常
- `mobile-hero.png` —— 手机首屏：按钮换行正常

发现问题就回到 Task 2/3 修，改完重跑尺子。

- [ ] **Step 3: 确认内容零改动**

Run: `git diff origin/main --stat -- website/public/index.html`
Expected: 只有新增行（`+`），没有删除行（`-`）——除了 `<figcaption class="shot-caption">` 换成 `<figcaption class="shot-status">` 这一处类名替换。

逐条核对 Global Constraints 里列的 17 个 id 与 4 个 class 仍在（尺子已断言，此处人工复核一遍）。

- [ ] **Step 4: 提交（若有修正）**

```bash
git add -A website/public
git commit -m "fix(web): 深色改版视觉终审修正"
```

若本步无改动则跳过提交。

---

### Task 6: 部署上线与线上回归

**Files:**
- 无代码改动

**Interfaces:**
- Consumes: Task 1-5 的全部产出
- Produces: 线上生效的深色官网

- [ ] **Step 1: 部署到 Cloudflare**

```powershell
$env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:http_proxy=""; $env:https_proxy=""
[System.Net.WebRequest]::DefaultWebProxy = $null
cd C:\Users\17321\Documents\破甲\_webseo\website
npx --yes wrangler deploy
```

Expected: `Uploaded tiyouju-site`，触发器显示 `tiyouju.com/*` 与 `www.tiyouju.com`。

**必须先清代理** —— 走代理时 Cloudflare API 会 TLS 超时或令牌刷新失败。

- [ ] **Step 2: 线上视觉验证**

用 Browser 打开 `https://tiyouju.com/`，截图确认：
- 首屏是深色、金色标题、面板浮起
- 无浅色残留
- 图集切换正常、灯箱可开
- Console 无 error

- [ ] **Step 3: 线上功能回归**

```powershell
curl -sIL --max-time 30 https://tiyouju.com/download/installer | tr -d '\r' | grep -iE '^(HTTP/|content-length:|content-disposition:)'
curl -sIL --max-time 30 https://tiyouju.com/download/portable | tr -d '\r' | grep -iE '^(HTTP/|content-length:|content-disposition:)'
(Invoke-RestMethod -Uri 'https://tiyouju.com/api/latest-release').tag
```

Expected:
- installer: `Content-Length: 47906651`，`Content-Disposition: attachment; filename="TiYouJu-Setup-2.3.6.exe"`
- portable: `Content-Length: 68968458`
- api 返回 `v2.3.6`

**用 curl 查下载头**，不要用 `Invoke-WebRequest` —— PowerShell 对 HEAD 请求的 Header 索引返回空，会误判成下载挂了。

- [ ] **Step 4: 线上 SEO 回归**

```powershell
$c = (Invoke-WebRequest -Uri 'https://tiyouju.com/' -UseBasicParsing).Content
foreach ($k in @('og:image','og:title','twitter:card','rel="canonical"','rel="sitemap"')) {
  "{0} {1}" -f $k, ([regex]::Matches($c, [regex]::Escape($k))).Count
}
```

Expected: 每项 ≥ 1。

再确认 `https://tiyouju.com/sitemap.xml` 与 `https://tiyouju.com/robots.txt` 仍是 200 且内容正确。

- [ ] **Step 5: 推送到 GitHub**

```powershell
$env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:http_proxy=""; $env:https_proxy=""
[System.Net.WebRequest]::DefaultWebProxy = $null
cd C:\Users\17321\Documents\破甲\_webseo
git push origin HEAD:main
```

**必须先清代理** —— 走代理推 PNG 会 `RPC failed; curl 56 Recv failure`。

- [ ] **Step 6: 等 CI 跑完**

查 `https://api.github.com/repos/CEHNICA/question-bank-card/actions/runs?per_page=1`，
等 `status == completed` 且 `conclusion == success`。

若 CI 失败：看哪个 step 挂的。本改版只碰 `website/`，CI 跑的是后端与前端测试，
理论上不该挂；若真挂了，先确认是否与本次改动有关。

- [ ] **Step 7: 更新 OG 分享图（可选但推荐）**

新配色上线后，`assets/og-cover.png` 仍是旧配色，与新站不协调。

```powershell
cd C:\Users\17321\Documents\破甲\_webseo
python website/tools/make_og_cover.py
cd website
npx --yes wrangler deploy
```

然后确认 `https://tiyouju.com/assets/og-cover.png` 已是新配色。

---

## 自检记录

**规格覆盖**：
- §2.1 色彩系统 → Task 2 Step 1 ✓
- §2.2 背景与纹理 → Task 2 Step 2 ✓
- §2.3 区块编号 → Task 2 Step 4 + Task 3 Step 3 ✓
- §3 首屏大图 → Task 2 Step 5 + Task 3 Step 1 ✓
- §4 各区块处理 → Task 2 Step 3 ✓
- §5 交互 → Task 2 Step 3/4/6 ✓
- §6 改动范围 → Task 2/3/4 ✓
- §6.1 HTML 改动清单 → Task 3 ✓
- §6.2 不变量 → Task 1（断言）+ Task 5 Step 3 ✓
- §7 验收标准 → Task 1/5/6 ✓
- §8 部署 → Task 6 ✓

**占位符扫描**：无 TBD / TODO / 待定 / FIXME。
**命名一致性**：`.shot-toolbar` / `.shot-dots` / `.shot-status` / `.section-index` / `.accent` / `.shot-frame--hero` 在 Task 2 定义、Task 3 使用、Task 1 断言，三处一致。
