# 官网深墨绿工程感改版设计

日期：2026-11-26
范围：`question-bank-card` 仓库的 `website/` 目录（tiyouju.com 官网静态站）

## 1. 背景与目标

官网 `https://tiyouju.com/` 当前是米白纸质感（`--paper: #fbf8f1`）、淡雅文艺风，
信息密度高但视觉冲击弱。2026-11-26 补 SEO 时新生成的 Open Graph 分享图
（`website/public/assets/og-cover.png`）采用深墨绿底 + 金色标题高亮 + 产品实拍
截图的冷峻专业风，用户明确表示这一视觉方向"非常帅气"，希望整站向它靠拢。

目标：把官网整站改成与 OG 分享图统一的深墨绿工程感，重点加强首屏大图的
冲击力，**内容文案、结构顺序、元素集合一律不动**。

非目标：不加新功能、不加新区块、不改文案措辞、不改 SEO 标签、不动 `app.js`。

## 2. 设计语言

### 2.1 色彩系统

替换 `styles.css` 的 `:root` 变量：

| 变量 | 值 | 用途 |
|---|---|---|
| `--bg` | `#0d2b27` | 页面底色（深墨绿，非纯黑） |
| `--surface` | `#123631` | 卡片底，比底色亮一档 |
| `--surface-2` | `#16403a` | 卡片高亮态 / 悬浮层 |
| `--gold` | `#d6a65d` | 标题高亮、序号、强调线 |
| `--gold-hi` | `#efc07a` | 金色 hover / 强调 |
| `--emerald` | `#35b07d` | 主色点缀、图标、辉光 |
| `--text` | `#eaf2ef` | 正文主色 |
| `--muted` | `#93b0aa` | 次级文字 |
| `--line` | `rgba(214,166,93,.16)` | 低饱和金描边 |

金色使用纪律：**只用于标题高亮、区块序号、强调线、卡片描边**，像工程图纸上的
标注线。正文一律用 `--text` / `--muted`。翠绿只用于图标和辉光，不用于大面积
填充。主按钮用金色实心（不是翠绿）——金色是这套视觉的招牌。

### 2.2 背景与纹理

- 底色不是平涂：`body` 加极淡网格（48px，`rgba(255,255,255,.02)`），像工程图纸
- 首屏右上加大面积径向光晕（翠绿 `rgba(53,176,125,.14)`），制造纵深
- 区块之间用一条 1px 金色分隔线过渡，不靠留白硬隔

### 2.3 区块编号

每个 `.section-head` 前加编号（`01 / 02 / 03 / 04 / 05`），用等宽字体 + 淡金
色，是"工程感"最关键的符号。编号由 HTML 静态写死，不做 JS 注入。

## 3. 首屏大图（核心改造）

现状是"一张带白框的图"，改造为浮起的软件面板：

1. 截图占据首屏右侧约 58% 宽度
2. 去掉浅色白底边框，改为深色面板 + 1px 金色描边
3. 双层投影：
   - 近层 `0 30px 60px -20px rgba(0,0,0,.7)` —— 悬浮感
   - 远层 `0 0 90px -30px rgba(53,176,125,.25)` —— 翠绿辉光
4. 面板顶部加**工具栏条**：左侧三个圆点（窗口控件），右侧写
   「题有据 · 逐题核对工作台」，让截图像真软件界面而非图片
5. 截图下方加**状态栏**：左「本地题库 · 1,284 题」，右「离线可用」，小字低透明度
6. 页面首屏顶部加 3px 金色渐变发光线

## 4. 各区块处理

| 区块 | 处理 |
|---|---|
| 顶栏 | 半透明深色 + `backdrop-filter: blur(12px)`，底部 1px 金线 |
| 首屏 | 见第 3 节 |
| 四步流程 | 流程图 SVG 换深色版（卡片底改深墨绿、线条改金/绿）；步骤框改为左竖金线 + 序号方块 |
| 界面预览 | tab 选中态改金色描边 + 浅金字；主图浮起带投影；缩略图选中态改金色边框 |
| 六项能力 | 卡片改深面板；hover 时金色描边浮现（8% → 35%）并上浮 3px |
| 下载安装 | 主按钮金色实心 + 深墨绿字；次按钮透明底 + 金描边；提示框改左侧金色竖条 |
| 社区横幅 | 保留深绿渐变，加金色描边与金色标题 |
| 页脚 | 深底 + 顶部金线分隔 |
| 灯箱 | 遮罩加深到 `rgba(8,18,16,.94)`，图框加金边 |

## 5. 交互

- 主按钮：金色实心，hover 提亮到 `--gold-hi` 并上浮 1px
- 次按钮：透明底 + 金描边，hover 填充淡金
- 卡片 hover：边框金色透明度 8% → 35%，`translateY(-3px)`
- 过渡时长统一 0.14s / 0.2s，不做夸张动效
- 尊重 `prefers-reduced-motion: reduce` —— 用户关闭动效时不做强位移

## 6. 改动范围

| 文件 | 改动 |
|---|---|
| `website/public/styles.css` | 重写（约 760 行），变量全换、配色全改 |
| `website/public/index.html` | 微调约 20 行：首屏加工具栏条与状态栏、section 加编号、图集区结构调整 |
| `website/public/assets/workflow.svg` | 改深色版（卡片底、线条、字色） |
| `website/public/app.js` | **一行不改** |

### 6.1 HTML 改动清单（精确）

首屏 `<figure class="shot-frame">` 内：

```html
<figure class="shot-frame shot-frame--hero">
  <div class="shot-toolbar">
    <span class="shot-dots" aria-hidden="true"><i></i><i></i><i></i></span>
    <span class="shot-toolbar-title">题有据 · 逐题核对工作台</span>
  </div>
  <button type="button" class="shot-trigger" ...>   <!-- 原样保留 -->
  <figcaption class="shot-status">
    <span>本地题库 · 1,284 题</span>
    <span>离线可用</span>
  </figcaption>
</figure>
```

`figcaption.shot-caption` 的第二行「点击图片放大」提示移入工具栏右端，
原 `.shot-caption` 在首屏改为 `.shot-status`；图集区的 `.shot-caption` 保持原样。

每个 `.section-head` 前插入编号：

```html
<div class="section-index">01</div>
```

依次为：四步流程 `01`、界面预览 `02`、核心能力 `03`、下载安装 `04`。

### 6.2 不变量（必须守住）

- 所有 `id` 不变：`versionBadge` / `downloadInstallerBtn` / `downloadPortableBtn` /
  `releaseMeta` / `downloadInstallerCardBtn` / `downloadPortableCardBtn` /
  `downloadCardMeta` / `copyPsBtn` / `psInstallCmd` / `galleryZoomBtn` /
  `galleryMainImg` / `galleryCaption` / `lightbox` / `lightboxImg` /
  `lightboxCaption` / `lightboxClose` / `install-cli`
- 所有 `class` 保持兼容：`app.js` 依赖的 `.tab-btn` / `.thumb-btn` /
  `.shot-trigger` / `.is-active` 全部保留
- `data-src` / `data-caption` / `data-zoom-src` / `data-zoom-caption` 属性不改
- SEO 标签（og / twitter / canonical / sitemap）不动
- 文案一字不改

## 7. 验收标准

1. 本地预览（`python -m http.server`）下逐屏截图确认：首屏、流程、图集、
   能力、下载、社区、页脚，无浅色残留、无文字与背景同色
2. 首屏面板工具栏条与状态栏正常显示，截图区可点击放大
3. 图集 tab 切换后主图、`galleryCaption`、`data-zoom-*` 同步更新（回归）
4. 「复制命令」按钮仍能复制 PowerShell 命令（回归）
5. 320px / 768px / 1440px 三档宽度下无横向溢出
6. `console` 无 error/warn
7. 部署后线上回归：`/api/latest-release`、两个下载接口响应头、OG 标签、
   sitemap、robots 全部与改版前一致
8. GitHub CI 全绿

## 8. 部署

`website/` 是独立的 Cloudflare Worker（`tiyouju-site`），**push 不会自动上线**，
必须手动：

```powershell
$env:HTTP_PROXY=""; $env:HTTPS_PROXY=""
[System.Net.WebRequest]::DefaultWebProxy = $null
cd website
npx wrangler deploy
```

推 GitHub 前同样需要清代理 —— 253KB 级 PNG 走代理会被掐断
（`RPC failed; curl 56 Recv failure`）。
