## 题有据 2.3.16 更新说明

### 更新亮点

1. **修复工作台题卡跨行显示公式与数据表无法渲染**：
   - 修复工作台题卡将跨行 `$$ ... \begin{array}...\end{array} ... $$`、`\[...\]` 按换行拆成多段 `<p>` 导致 KaTeX 原文露出的问题；
   - 统一题干段落格式化逻辑，支持宽数学表格在题卡内横向滚动，并自动包裹裸 `\begin{array}` / `\begin{cases}` 等常用数学环境。

### 验证范围

- `ruff check`、`ruff format --check`、`npm run lint`、根目录 `unittest`、Django `core` 单元测试与全部前端 `node --test` 用例通过。
