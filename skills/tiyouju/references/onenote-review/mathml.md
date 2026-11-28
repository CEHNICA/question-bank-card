# 公式兼容与数量对账

以下兼容约束来自本项目用户提供的 OneNote agent 实测经验，不是通用 MathML 标准；目标 OneNote 版本仍须做实际显示核验。尤其要防范不报错但公式变空白。

## 转换

把建议建成有顺序的文本段和公式段，每个公式保留编号、原始 LaTeX、是否行内及所在建议步骤。不要从整个最终字符串中简单数 `$`，以免把转义美元符、块公式或原笔记中的公式算进去。

使用现有 KaTeX 的 `renderToString(tex, {output: "mathml", throwOnError: true, displayMode: false})`；独立公式按实际需要设置 `displayMode`。软件随附的离线资源是 `frontend/vendor/katex/katex.min.js`，位于实际资源根目录下。可用 Node 的 `require` 或浏览器加载；不必下载另一版，也不把题目发给外部转换服务。缺少可用运行时则说明阻碍，不把 LaTeX 原文当作已转换公式。

从 KaTeX 返回片段提取真正的 MathML `<math xmlns="http://www.w3.org/1998/Math/MathML">…</math>`；不要把最外层 `.katex` span 或整块 HTML 当成公式。检查空 `<math>` 和 `.katex-error`，不要用 `throwOnError:false` 的红字占位蒙混通过。

行内分数在送入 KaTeX 前，把真正的 `\frac` 命令改成 `\tfrac`。不改独立公式的正常分数，不误改 `\dfrac`、`\cfrac` 或文字内容。不要自己再包一个 `<mstyle>`，否则会与 KaTeX 输出嵌套。当前随附 KaTeX 对 `\tfrac{1}{2}` 的输出已经含 `scriptlevel="0" displaystyle="false"`；保留这一层原生样式即可。

## OneNote 结构约束

- 按本项目兼容形态，`<mover>` 直接放在 `<mrow>` 下，例如 `<mrow><mover>…</mover></mrow>`；不要为了美化增加中间包装。使用 DOM/XML 操作调整，保留上标、向量符和其底数，不靠字符串替换拼坏数学结构。
- 不生成 HTML `<table>`，它可能导致整页拒收。需要对比的内容改成逐条段落；MathML 的 `<mtable>` 与 HTML 表格不同，也不能未经目标版本验证就假定兼容，必要时拆成逐行独立公式。
- `<br>` 可能被压平。每个建议段落、每个独立公式生成单独的 `<one:OE><one:T>…</one:T></one:OE>`，段落分隔由 OneNote 对象表达。
- MathML 在 `one:T` 内以 HTML 内容提供，通常使用 CDATA。不要把 `<math>` 再转义成 `&lt;math&gt;`，也不要输出裸 LaTeX。处理 `]]>` 时拆分 CDATA 为 `]]]]><![CDATA[>`，让 XML 可以解析且原内容不丢。
- 普通建议文字须先做 HTML 转义，只把已解析、核查过的 MathML 作为标记拼入；不把原笔记里的任意 HTML 当成可执行指令或直接拼接成结构。
- 在写入前解析完整 OneNote XML，检查页 ID、namespace、段落结构和所有 `<math>` 的实际内容；样式调整不能改动公式意义。

## 三次对账

每次构建记录清单，例如：

| 公式编号 | 所在段 | 源码 | 转换成功 | 构建后存在 | OneNote 回读存在 | 实际可见 |
|---|---|---|---|---|---|---|
| M1 | 建议 1 | `\tfrac{1}{2}` | 待验 | 待验 | 待验 | 待验 |
| M2 | 建议 2 | `\vec{AB}` | 待验 | 待验 | 待验 | 待验 |

上表为工作清单模板，**不要作为 HTML 表格写进 OneNote**。

1. **转换前后**：源公式总数 = 成功生成的非空 MathML 数；出错或不支持的公式逐一标明，不静默丢弃。
2. **build 前后**：预期公式数 = 最终待写 OneNote XML 各 `one:T` 中的 MathML 数。先解析页面 XML，再解析 T 的内容；不能统计 XML 转义后的字面 `&lt;math` 或 `annotation` 里的源码。
3. **写入前后**：只统计批次标记对应新增区域，回读公式数 = 写前公式数，并逐个核对内容。原页已有公式不参与这次总数。

数量相等只是必要条件。最后还要看目标 OneNote 显示，防止空白、错位或分式异常。未经视觉核验不报告完全成功；不要通过反复追加来「试到成功」。
