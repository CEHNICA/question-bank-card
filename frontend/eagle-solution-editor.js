/* Hot-patched install: single-question LibraryAnswerEditor bridge (served as eagle-solution-editor.js for frozen URL). */
/* Eagle → LibraryAnswerEditor 单题桥接。
 *
 * 只打开一道题；复用正式答案解析编辑器，不再维护 Eagle 多列并排版。
 * 依赖按需加载：library-answer-editor.css / library-solutions.js /
 * control-wording.js / library-answer-editor.js。
 */
(function () {
  'use strict';

  const EMBEDDED_ANSWER_EDITOR_CSS = "/* \u7b54\u6848\u89e3\u6790\u7f16\u8f91\u5668\u4e13\u7528\u6837\u5f0f\u3002\n * library.html \u4e0e Eagle \u5171\u7528\u3002Eagle \u4e0d\u6574\u9875\u52a0\u8f7d library.css / styles.css\uff0c\n * \u6240\u4ee5\u8fd9\u91cc\u81ea\u5e26\u5bf9\u8bdd\u6846\u4f5c\u7528\u57df\u4e0b\u7684\u4ee4\u724c\u548c\u6309\u94ae\uff0c\u907f\u514d\u6c61\u67d3 Eagle \u4e3b\u754c\u9762\u3002\n */\n.answer-editor-dialog {\n  --bg: #f4f3ee;\n  --bg-sunken: #ebe9e2;\n  --surface: #ffffff;\n  --surface-2: #faf9f5;\n  --line: #e5e2d9;\n  --line-strong: #d2cec2;\n  --ink: #1f2320;\n  --ink-2: #4a4e49;\n  --ink-3: #5d6b63;\n  --muted: #7b7e76;\n  --faint: #a5a79e;\n  --accent: #1f6b5f;\n  --accent-hover: #175a4f;\n  --accent-soft: #e4f1ec;\n  --accent-ring: rgba(31, 107, 95, .26);\n  --accent-dark: #174f46;\n  --green-bar: #9fd4b3;\n  --amber: #93590b;\n  --amber-soft: #fbf3e2;\n  --amber-line: #ecd3a2;\n  --amber-bar: #dfa33a;\n  --red: #b3412e;\n  --violet: #6b54a0;\n  --radius: 9px;\n  --ui-font: \"Microsoft YaHei UI\", \"Microsoft YaHei\", \"PingFang SC\", \"Segoe UI\", system-ui, sans-serif;\n  --mono: \"Cascadia Mono\", Consolas, \"Microsoft YaHei\", monospace;\n  color: var(--ink);\n  font-family: var(--ui-font);\n  box-sizing: border-box;\n}\n.answer-editor-dialog *,\n.answer-editor-dialog *::before,\n.answer-editor-dialog *::after { box-sizing: border-box; }\n.answer-editor-dialog .helper { color: var(--muted); font-size: 12.5px; line-height: 1.55; margin: 6px 0 0; }\n.answer-editor-dialog .eyebrow { margin: 0 0 3px; font-size: 11.5px; font-weight: 700; letter-spacing: .08em; color: var(--muted); }\n.answer-editor-dialog .button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 34px; padding: 6px 14px;\n  border: 1px solid var(--line-strong); border-radius: 8px; background: var(--surface); color: var(--ink);\n  font-size: 13.5px; font-weight: 500; line-height: 1.2; text-decoration: none; white-space: nowrap;\n  box-shadow: none; cursor: pointer; transition: background .15s, border-color .15s, color .15s; }\n.answer-editor-dialog .button:hover:not(:disabled) { border-color: #bdb8aa; background: var(--surface-2); }\n.answer-editor-dialog .button:disabled { cursor: not-allowed; opacity: .45; }\n.answer-editor-dialog .button.primary,\n.answer-editor-dialog .button.button-primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }\n.answer-editor-dialog .button.primary:hover:not(:disabled),\n.answer-editor-dialog .button.button-primary:hover:not(:disabled) { background: var(--accent-hover); border-color: var(--accent-hover); }\n.answer-editor-dialog .button.small,\n.answer-editor-dialog .button.button-small { min-height: 28px; padding: 4px 10px; font-size: 12.5px; border-radius: 8px; }\n.answer-editor-dialog .button.quiet,\n.answer-editor-dialog .button.button-quiet { border-color: transparent; background: none; box-shadow: none; color: var(--ink-2); }\n.answer-editor-dialog .button.quiet:hover:not(:disabled),\n.answer-editor-dialog .button.button-quiet:hover:not(:disabled) { background: var(--bg-sunken); border-color: transparent; color: var(--ink); }\n.answer-editor-dialog .paper { min-width: 0; }\n@keyframes pulse { 50% { opacity: .35; } }\n\n.solution-label { font-weight: 600; }\n.solution-paragraph { margin: 3pt 0; }\n.solution-figure { margin: 6pt 0; max-width: 100%; break-inside: avoid; }\n.solution-figure img { display: block; max-width: 100%; height: auto; }\n/* 1.12.7\uff1a\u7b54\u6848\u89e3\u6790\u6539\u6210\u5168\u5c4f\u3002\u9876\u680f\u4e00\u6761\u6a2a\u6392\u64cd\u4f5c\u680f\uff08\u8fd4\u56de\u3001\u9898\u53f7\u8fdb\u5ea6\u3001\u5b58\u9898\u5e93\u3001\u4fdd\u5b58\uff09\uff0c\n   \u7b2c\u4e8c\u6761\u662f\u6a2a\u6392\u9898\u53f7\u5e26\u548c AI \u6309\u94ae\uff1b\u6b63\u6587\u4e09\u5757 \u2014\u2014 \u5de6\u4e0a\u539f\u5377\u672c\u9898\u3001\u5de6\u4e0b\u7f16\u8f91\u533a\u3001\u53f3\u8fb9\u6574\u5217\n   \u9884\u89c8\u3002\u539f\u6765\u5de6\u8fb9\u90a3\u6761 230px \u7684\u9898\u53f7\u4fa7\u680f\u548c\u5e95\u90e8\u9489\u4f4f\u7684\u4fdd\u5b58\u6761\u90fd\u53d6\u6d88\u4e86\uff1a\u4fa7\u680f\u5403\u6389\u4e86\u672c\u8be5\n   \u7ed9\u7f16\u8f91\u533a\u7684\u5bbd\u5ea6\uff0c\u5e95\u90e8\u90a3\u6761\u53c8\u6b63\u597d\u538b\u4f4f\u5149\u6807\u6240\u5728\u7684\u90a3\u4e00\u884c\u3002 */\n.answer-editor-dialog { width: 100vw; max-width: none; height: 100dvh; max-height: none; padding: 0;\n  border: 0; border-radius: 0; background: var(--bg); }\n.answer-editor-dialog[open] { display: grid; grid-template-rows: auto auto minmax(0, 1fr); }\n/* \u5355\u9898\uff1a\u6ca1\u6709\u9898\u53f7\u5e26\uff0c\u9876\u680f\u76f4\u63a5\u63a5\u6b63\u6587\u3002 */\n.answer-editor-dialog.is-single[open] { grid-template-rows: auto minmax(0, 1fr); }\n.answer-editor-dialog.is-single .answer-editor-strip[hidden] { display: none; }\n.answer-editor-dialog::backdrop { background: rgba(24, 42, 38, .5); }\n.answer-editor-bar { display: flex; align-items: center; gap: 10px 14px; flex-wrap: wrap;\n  padding: 10px 22px; background: var(--surface); border-bottom: 1px solid var(--line); }\n/* \u6807\u9898\u548c\u300c\u7b2c N \u9898 / \u5171 M \u9898\u300d\u5e76\u6210\u4e00\u884c\u3002\u539f\u6765\u7684\u56db\u884c\uff08\u7709\u6807\u3001\u6807\u9898\u3001\u9898\u53f7\u3001\u8bf4\u660e\uff09\n   \u628a\u8fd9\u6761\u64cd\u4f5c\u680f\u6491\u5230 133px\uff0c\u6b63\u6587\u88ab\u538b\u5230\u89c6\u53e3\u7684\u56db\u5206\u4e4b\u4e00\u4ee5\u4e0b\u3002 */\n.answer-editor-heading { display: flex; align-items: baseline; gap: 10px; min-width: 0; }\n.answer-editor-heading .eyebrow { margin: 0; font-size: 11px; letter-spacing: .08em; color: var(--muted); }\n.answer-editor-heading h3 { margin: 0; font-size: 16px; line-height: 1.3; color: var(--ink); white-space: nowrap; font-weight: 650; }\n.answer-editor-heading .helper { margin: 0; }\n.answer-editor-place { margin: 0; font-size: 13px; font-weight: 600; color: var(--accent); font-variant-numeric: tabular-nums; white-space: nowrap; }\n.answer-editor-bar > .answer-editor-status { flex: 1 1 12em; min-width: 0; }\n.answer-editor-bar > .answer-sync, .answer-editor-bar > .button-primary, .answer-editor-bar > .answer-bar-ai { flex: none; }\n.answer-bar-ai { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; min-width: 0; }\n.answer-bar-ai[hidden] { display: none; }\n.answer-bar-ai > .answer-ai-status { flex: 0 1 18em; margin: 0; padding: 4px 8px; }\n.answer-editor-strip { display: flex; align-items: center; gap: 8px 14px; flex-wrap: wrap;\n  padding: 6px 22px; background: var(--surface); border-bottom: 1px solid var(--line); }\n.answer-editor-strip[hidden] { display: none; }\n.answer-editor-strip > .helper { flex: 0 1 auto; margin: 0; max-width: 26em; }\n.answer-editor-list { display: flex; gap: 6px; flex: 0 1 auto; max-width: 46%; min-width: 0; overflow-x: auto;\n  padding: 2px 2px 5px; scrollbar-width: thin; }\n.answer-ai-row { display: flex; align-items: center; gap: 6px 12px; flex-wrap: wrap; flex: 1 1 auto; min-width: 0; }\n.answer-ai-row > .answer-ai-status { flex: 1 1 18em; min-width: 0; margin: 0; }\n.answer-ai-row > .answer-api-note { flex: 0 1 30em; margin: 0; }\n.answer-editor-dialog .helper { color: var(--ink-2); }\n.answer-ai-status { padding: 6px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-2); font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }\n/* \u72b6\u6001\u884c\u53ea\u5728\u771f\u6709\u8bdd\u8bf4\u7684\u65f6\u5019\u624d\u5360\u5730\u65b9\u3002 */\n.answer-ai-status:empty, .answer-api-note:empty { display: none; }\n.answer-ai-tools { display: flex; gap: 7px; }\n.answer-ai-tools > [hidden], .answer-task-details[hidden] { display: none; }\n.answer-api-settings { text-align: center; color: var(--accent-dark); border-color: #9cc2b5; background: #f8fcf8; }\n.answer-api-note { font-size: 12px; line-height: 1.6; }\n.answer-task-details { margin: 10px 0; color: var(--ink-2); }\n.answer-task-details summary { cursor: pointer; font-size: 13px; font-weight: 600; }\n.answer-task-details textarea { width: 100%; margin-top: 8px; padding: 8px; box-sizing: border-box; font: 12px/1.6 var(--ui-font); color: var(--ink); background: #fff; border: 1px solid var(--line-strong); border-radius: 7px; resize: vertical; }\n/* \u9898\u53f7\u5e26\uff1a\u4e00\u683c\u4e00\u4e2a\u9898\u53f7\u52a0\u4e00\u4e2a\u72b6\u6001\u70b9\u3002\u5b8c\u6574\u8bf4\u660e\u653e\u5728 title \u91cc\uff0c\u9f20\u6807\u505c\u4e0a\u53bb\u624d\u8bf4\u5168\u3002 */\n.answer-list-row { display: grid; grid-template-columns: 18px minmax(0, 1fr); align-items: center; gap: 5px;\n  flex: none; border: 1px solid var(--line); background: var(--surface); border-radius: 9px; padding: 3px 9px 3px 6px; }\n.answer-list-row.active { background: var(--accent-soft); border-color: var(--accent); }\n.answer-list-row:has(> input[hidden]) { grid-template-columns: minmax(0, 1fr); padding-left: 9px; }\n.answer-list-question { display: flex; align-items: center; gap: 6px; border: 0; background: transparent;\n  color: var(--ink); padding: 3px 1px; white-space: nowrap; cursor: pointer; }\n.answer-list-question strong { font-size: 13px; font-weight: 600; }\n.answer-list-question > .answer-quality, .answer-list-question > .answer-job-state,\n.answer-list-question > .answer-job-error, .answer-list-question > .answer-job-deadline { display: none; }\n.answer-list-question::after { content: \"\"; flex: none; width: 7px; height: 7px; border-radius: 50%; background: var(--faint); }\n.answer-list-row[data-state=\"missing\"] .answer-list-question::after { background: var(--amber-bar); }\n.answer-list-row[data-state=\"result_only\"] .answer-list-question::after { background: var(--violet); }\n.answer-list-row[data-state=\"ready\"] .answer-list-question::after { background: var(--green-bar); }\n.answer-list-row[data-state=\"review\"] .answer-list-question::after, .answer-list-row[data-state=\"failed\"] .answer-list-question::after { background: var(--red); }\n.answer-list-row[data-state=\"working\"] .answer-list-question::after { background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }\n/* \u5fc5\u987b\u5199 animation: none\uff1a\u5168\u5c40\u90a3\u6761 reduced-motion \u53ea\u538b animation-duration\uff0c\n   \u4e0d\u538b animation-iteration-count\uff0cinfinite \u52a8\u753b\u4f1a\u4ee5 0.01ms \u7684\u5468\u671f\u4e00\u76f4\u8f6c\u3002\n   \u8fd9\u6761\u8981 !important\uff1a\u4e0a\u9762\u90a3\u6761\u72b6\u6001\u9009\u62e9\u5668\u5e26\u4e86\u5c5e\u6027\u9009\u62e9\u5668\uff0c\u4e0d\u52a0\u4f1a\u88ab\u5b83\u76d6\u4f4f\u3002 */\n@media (prefers-reduced-motion: reduce) {\n  .answer-list-row[data-state=\"working\"] .answer-list-question::after { animation: none !important; }\n}\n.answer-list-row.active .answer-list-question::after { outline: 2px solid var(--accent); outline-offset: 2px; }\n.answer-editor-status { margin: 0; color: var(--ink-2); font-size: 12.5px; line-height: 1.5; }\n.answer-question, .answer-origin { padding: 12px 14px; border: 1px solid var(--line); background: var(--surface); border-radius: var(--radius); }\n.answer-question > summary, .answer-origin > summary { cursor: pointer; color: var(--ink-2); font-size: 13px; font-weight: 600; }\n/* 1.25.40\uff1a\u7167\u300c\u5f55\u9898 \u2192 \u6539\u5b57\u300d\u90a3\u4e00\u9875 \u2014\u2014 \u5de6\u6e90\u7801\u3001\u53f3\u9884\u89c8\uff0c\u4e00\u884c\u4e00\u5bf9\u3002\n   \u9898\u76ee\u6a2a\u8de8\u6574\u884c\u5728\u6700\u4e0a\u9762\uff1b\u7b54\u6848/\u89e3\u6790\u5404\u81ea\u548c\u81ea\u5df1\u7684\u9884\u89c8\u540c\u4e00\u884c\u3002 */\n.answer-editor-workspace { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);\n  grid-auto-rows: auto; gap: 12px 22px; align-content: start; min-height: 0; overflow-y: auto;\n  padding: 18px 28px 40px; background: linear-gradient(180deg, #f7f6f1 0%, var(--surface-2) 48px); }\n.answer-editor-workspace > .answer-editor-source { grid-column: 1 / -1; min-width: 0; padding: 10px 14px 12px;\n  border: 1px solid var(--line); border-radius: 10px; background: #fff; }\n.answer-editor-workspace > .answer-editor-source .answer-question { padding: 0; border: 0; border-radius: 0; background: transparent; }\n.answer-editor-workspace > .answer-editor-source .answer-question > summary { color: var(--ink); font-size: 13.5px; }\n.answer-editor-pair-hint { grid-column: 1 / -1; margin: 0; color: var(--muted); font-size: 12px; line-height: 1.6; }\n.answer-editor-workspace > label { display: grid; gap: 7px; align-content: start; min-width: 0;\n  font-weight: 600; font-size: 13px; letter-spacing: .02em; color: var(--ink-3); }\n.answer-editor-workspace > label textarea { width: 100%; resize: vertical; font: 15px/1.7 var(--mono);\n  padding: 12px 14px; border: 1px solid var(--line); border-radius: 10px; background: #fff;\n  color: var(--ink); box-sizing: border-box; }\n.answer-editor-workspace > label textarea::placeholder { color: var(--muted); }\n.answer-editor-workspace > label textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-ring); }\n.answer-editor-preview-cell { min-width: 0; padding: 14px 16px; border: 1px solid var(--line);\n  border-radius: 10px; background: #fff; box-shadow: 0 1px 0 rgba(31, 35, 32, .03); }\n.answer-editor-preview-cell .answer-editor-preview { margin: 0; padding: 0; border: 0; min-height: 48px; }\n.answer-editor-tools-cell { grid-column: 1 / -1; min-width: 0; display: grid; gap: 10px; align-content: start;\n  padding-top: 4px; border-top: 1px solid var(--line); margin-top: 4px; }\n.answer-editor-tools-cell > .answer-set-aside { margin: 0; }\n.answer-editor-tools-cell > .answer-origin,\n.answer-editor-tools-cell > .answer-versions,\n.answer-editor-tools-cell > .answer-ai-draft { margin: 0; }\n.answer-ai-draft { grid-column: 1 / -1; }\n.answer-sync-note { margin: 0; color: var(--muted); font-size: 12px; }\n.answer-sync-note[hidden] { display: none; }\n.answer-image-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; margin: 0; }\n.answer-image-list { display: grid; gap: 10px; }\n.answer-image-row { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 14px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-2); }\n.answer-image-row > img { width: 112px; max-height: 120px; object-fit: contain; }\n.answer-image-fields { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }\n.answer-image-fields label { font-size: 12px; display: flex; align-items: center; gap: 6px; }\n.answer-image-fields input { width: 74px; }\n.answer-image-fields select { max-width: 155px; }\n.answer-image-fields .answer-image-tools { flex-basis: 100%; margin: 0; }\n.answer-editor-preview, .answer-origin > .paper, .answer-ai-draft > .paper { padding: 16px; margin: 8px 0 16px; border: 1px solid var(--line); border-radius: 10px; background: #fff; }\n/* The display caret and selection are absolutely positioned inside the preview,\n   so the preview itself has to be their containing block. Without this they\n   resolve against the sticky column and land in the wrong place. */\n.answer-editor-preview { position: relative; }\n.answer-origin { margin: 0; }\n.answer-origin select { max-width: 100%; }\n/* \u7a84\u7a97\u53e3\uff1a\u4e00\u884c\u653e\u4e0d\u4e0b\u4e24\u680f\uff0c\u53d8\u6210\u300c\u8f93\u5165\u5728\u4e0a\u3001\u9884\u89c8\u5728\u4e0b\u300d\uff0c\u4ecd\u662f\u4e00\u5bf9\u4e00\u5bf9\u3002 */\n@media (max-width: 1080px) {\n  .answer-editor-workspace { grid-template-columns: minmax(0, 1fr); }\n}\n/* 1.13.5\uff1a\u9898\u9762\u6539\u8fc7\u4e4b\u540e\u88ab\u6401\u7f6e\u7684\u90a3\u4efd\u89e3\u6790\u3002\u7528\u7425\u73c0\u5e95\u548c\u4e00\u6761\u4fa7\u8fb9\u7ebf\u6807\u6210\u300c\u65c1\u7684\u4e00\u680f\u300d\uff0c\n   \u522b\u8ba9\u5b83\u770b\u8d77\u6765\u50cf\u5f53\u524d\u7f16\u8f91\u533a\u91cc\u7684\u5185\u5bb9 \u2014\u2014 \u586b\u56de\u6765\u8981\u70b9\u6309\u94ae\uff0c\u4e0d\u4f1a\u81ea\u52a8\u751f\u6548\u3002 */\n.answer-set-aside { margin: 12px 0 0; padding: 12px 14px; border: 1px solid var(--amber-line);\n  border-left: 3px solid var(--amber); border-radius: var(--radius); background: var(--amber-soft); }\n.answer-set-aside > strong { display: block; font-size: 13px; }\n.answer-set-aside > .helper { margin: 4px 0 0; }\n.answer-set-aside > .paper { padding: 12px; margin: 8px 0; border: 1px solid var(--line);\n  border-radius: 9px; background: #fff; }\n.answer-set-aside > .answer-image-tools { margin: 0; }\n/* 1.25.98\uff1a\u4e00\u4efd\u89e3\u6790\u6512\u4e86\u51e0\u7248\uff0c\u8fd9\u91cc\u628a\u5b83\u4eec\u6446\u51fa\u6765\u3002\u523b\u610f\u4e0d\u505a\u6210\u4e0b\u62c9\u3001\u4e5f\u4e0d\u505a\u6210\u5207\u6362\u5668\n   \u2014\u2014 1.11.5 \u90a3\u4e2a\u300c\u7248\u672c\u4e0b\u62c9\u300d\u88ab\u5220\u6389\u5c31\u662f\u56e0\u4e3a\u5b83\u5207\u7684\u662f\u6574\u4e2a\u7f16\u8f91\u533a\uff0c\u8001\u5e08\u4f1a\u4ee5\u4e3a\u4e0b\u9762\u7684\n   \u5b57\u4e5f\u8ddf\u7740\u6362\u3002\u73b0\u5728\u662f\u4e00\u5757\u53ea\u8bfb\u6e05\u5355\uff1a\u70b9\u300c\u770b\u5185\u5bb9\u300d\u624d\u5c55\u5f00\uff0c\u70b9\u300c\u586b\u56de\u7f16\u8f91\u533a\u300d\u624d\u628a\n   \u90a3\u4e00\u7248\u653e\u8fdb\u7f16\u8f91\u533a\uff0c\u8fd8\u8981\u81ea\u5df1\u4fdd\u5b58\u3002 */\n.answer-versions { margin: 12px 0 0; padding: 10px 14px; border: 1px solid var(--line);\n  border-radius: var(--radius); background: #fbfcfd; }\n.answer-versions > summary { cursor: pointer; font-size: 13px; color: var(--ink-2); }\n.answer-version-list { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }\n.answer-version { padding: 10px 12px; border: 1px solid var(--line); border-radius: 9px; background: #fff; }\n.answer-version.current { border-color: var(--line); background: #f4fbf7; }\n.answer-version-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }\n.answer-version-head > strong { font-size: 13px; }\n.answer-version-body { margin: 8px 0 0; padding: 10px 12px; border: 1px solid var(--line); border-radius: 9px; }\n.answer-version > .answer-image-tools { margin: 8px 0 0; }\n.answer-sync { font-size: 13px; display: flex; align-items: center; gap: 8px; color: var(--ink-2); }\n.answer-sync[hidden] { display: none; }\n.answer-ai-draft { padding: 12px; background: var(--amber-soft); border-radius: 9px; }\n/* 1.25.80\uff1a\u7b54\u6848 / \u89e3\u6790\u4e24\u4e2a\u6846\u5728\u6709 AI \u53c2\u8003\u7b54\u6848\u81ea\u52a8\u586b\u8fdb\u6765\u65f6\u7ed9\u4e00\u9053\u6d45\u5e95\u8272 + \u89d2\u843d\u5c0f\u89d2\u6807\uff1b\n   \u8001\u5e08\u6539\u8fc7\u4e00\u4e2a\u5b57\u7b26\u4e4b\u540e\u81ea\u52a8\u6d88\uff0c\u6062\u590d\u666e\u901a\u6837\u5f0f\u3002 */\n#answerEditorResult, #answerEditorAnalysis { position: relative; }\n#answerEditorResult.is-ai-suggested,\n#answerEditorAnalysis.is-ai-suggested { background: linear-gradient(180deg, #fff7e6 0%, #fffaf0 100%); box-shadow: inset 0 0 0 1px #f4d59c; }\n#answerEditorResult.is-ai-suggested::after,\n#answerEditorAnalysis.is-ai-suggested::after { content: \"AI \u521d\u7a3f\"; position: absolute; top: 6px; right: 10px; padding: 1px 7px; border-radius: 99px; background: #f4d59c; color: #6a4a14; font-size: 10.5px; font-weight: 600; pointer-events: none; }\n.answer-crop { border: 1px solid var(--line); border-radius: 10px; padding: 12px; }\n.answer-crop-scroll { position: relative; max-height: 420px; overflow: auto; }\n.answer-crop-guidance { position: absolute; top: 8px; right: 8px; left: 8px; z-index: 2; display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; pointer-events: none; }\n.answer-crop-guidance > span { padding: 8px 10px; background: rgba(232, 244, 239, .95); border: 1px solid var(--accent); border-radius: 7px; }\n.answer-crop-guidance > button { pointer-events: auto; background: var(--surface); }\n.answer-crop-guidance[hidden] { display: none; }\n.answer-crop-hint-switch { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ink-3); }\n.answer-crop-surface { position: relative; width: 100%; touch-action: none; }\n.answer-crop-surface > img { display: block; width: 100%; user-select: none; }\n.answer-crop-box { position: absolute; border: 2px solid var(--accent); background: rgba(29, 107, 94, .12); pointer-events: none; box-sizing: border-box; }\n@media (max-width: 1100px) {\n  /* 1.12.7\uff1a\u7a84\u5c4f\u9000\u56de\u5355\u5217\uff0c\u4ece\u4e0a\u5f80\u4e0b\u662f\u9876\u680f\u3001\u9898\u53f7\u5e26\u3001\u539f\u5377\u672c\u9898\u3001\u7f16\u8f91\u533a\u3001\u9884\u89c8\uff0c\u6574\u680f\u6eda\u52a8\u3002 */\n  .answer-editor-workspace { display: block; overflow-y: auto; }\n  .answer-editor-input-column { display: block; border-right: 0; }\n  .answer-editor-source { max-height: 30dvh; }\n  .answer-editor-edit { overflow: visible; }\n  .answer-editor-preview-column { overflow: visible; border-left: 0; border-top: 1px solid var(--line); }\n  .answer-editor-strip > .helper { display: none; }\n}\n@media (max-width: 700px) {\n  .answer-editor-bar, .answer-editor-strip { padding: 8px 12px; }\n  .answer-editor-bar > .answer-editor-status { flex-basis: 100%; order: 5; }\n  .answer-editor-source, .answer-editor-edit, .answer-editor-preview-column { padding-left: 12px; padding-right: 12px; }\n}\n";
  function injectEmbeddedCss() {
    if (document.getElementById('eagle-answer-editor-css')) return;
    const style = document.createElement('style');
    style.id = 'eagle-answer-editor-css';
    style.textContent = EMBEDDED_ANSWER_EDITOR_CSS;
    document.head.appendChild(style);
  }

  const ASSETS = [
    { kind: 'js', src: '/library-solutions.js?v=ui-clean-20261117b' },
    { kind: 'js', src: '/control-wording.js?v=ui-clean-20261117b' },
    { kind: 'js', src: '/library-answer-editor.js?v=ui-clean-20261117b' }
  ];

  let loadPromise = null;
  let editor = null;
  let opening = false;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function notify(message, tone) {
    if (typeof window.showToast === 'function') {
      const type = tone === 'error' ? 'error' : tone === 'success' ? 'success' : 'info';
      window.showToast(String(message || ''), type);
      return;
    }
    if (message) console.info('[答案解析]', message);
  }

  async function confirmDialog(options = {}) {
    const title = options.title || '确认';
    const text = options.text || '';
    const ok = options.ok || '确定';
    return window.confirm([title, text, `（确定 = ${ok}）`].filter(Boolean).join('\n\n'));
  }

  function loadAsset(asset) {
    if (asset.kind === 'css') {
      const existing = document.querySelector(`link[data-eagle-answer-asset="${asset.href}"]`);
      if (existing) return Promise.resolve();
      return new Promise((resolve, reject) => {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = asset.href;
        link.dataset.eagleAnswerAsset = asset.href;
        link.onload = () => resolve();
        link.onerror = () => reject(new Error(`样式加载失败：${asset.href}`));
        document.head.appendChild(link);
      });
    }
    const existing = document.querySelector(`script[data-eagle-answer-asset="${asset.src}"]`);
    if (existing) {
      return existing.dataset.loaded === '1'
        ? Promise.resolve()
        : new Promise((resolve, reject) => {
            existing.addEventListener('load', () => resolve(), { once: true });
            existing.addEventListener('error', () => reject(new Error(`脚本加载失败：${asset.src}`)), { once: true });
          });
    }
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = asset.src;
      script.async = false;
      script.dataset.eagleAnswerAsset = asset.src;
      script.onload = () => { script.dataset.loaded = '1'; resolve(); };
      script.onerror = () => reject(new Error(`脚本加载失败：${asset.src}`));
      document.head.appendChild(script);
    });
  }

  function ensureAssets() {
    injectEmbeddedCss();
    if (!loadPromise) {
      loadPromise = ASSETS.reduce(
        (chain, asset) => chain.then(() => loadAsset(asset)),
        Promise.resolve()
      ).catch((error) => {
        loadPromise = null;
        throw error;
      });
    }
    return loadPromise;
  }

  function hostList(name) {
    const value = window[name];
    return Array.isArray(value) ? value : [];
  }

  function findQuestion(id) {
    const wanted = String(id || '');
    if (!wanted) return null;
    const pool = hostList('questionsData');
    const filtered = hostList('filteredQuestions');
    return pool.find((q) => String(q.id) === wanted)
      || filtered.find((q) => String(q.id) === wanted)
      || null;
  }

  function resolveQuestionId(ids) {
    if (Array.isArray(ids) && ids.length) return String(ids[0]);
    if (ids != null && ids !== '' && !Array.isArray(ids)) return String(ids);
    if (window.currentActiveQuestionId) return String(window.currentActiveQuestionId);
    const filtered = hostList('filteredQuestions');
    if (filtered[0]?.id) return String(filtered[0].id);
    const all = hostList('questionsData');
    if (all[0]?.id) return String(all[0].id);
    return '';
  }

  async function fetchPublication(id) {
    const response = await fetch(`/api/library/${encodeURIComponent(id)}`, {
      headers: { 'X-QB-Request': '1' },
      cache: 'no-store'
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `读取题目失败（HTTP ${response.status}）`);
    return body.publication || body.item || body;
  }

  function toEditorItem(publication, fallback) {
    const id = String(publication.id || fallback?.id || '');
    const number = publication.exam_number
      ?? publication.number
      ?? fallback?.questionNumber
      ?? fallback?.number
      ?? '?';
    return {
      id,
      exam_number: number,
      number,
      document_id: publication.document_id || fallback?.documentId || '',
      question_type: publication.question_type || fallback?.questionTypeCode || 'free_response',
      content: publication.content || {},
      solution: publication.solution || fallback?.savedSolution || null,
      solution_revision: publication.solution?.id
        || (fallback?.savedSolution && fallback.savedSolution.id)
        || null,
      solution_needs_review: Boolean(publication.solution_needs_review)
    };
  }

  function ensureEditor() {
    if (editor) return editor;
    const QB = window.QBRender || window.QB;
    if (!window.LibraryAnswerEditor?.create) throw new Error('答案解析编辑器脚本未就绪');
    if (!window.LibrarySolutions) throw new Error('答案解析模型脚本未就绪');
    if (!QB?.renderQuestion) throw new Error('题面渲染脚本未就绪');
    editor = window.LibraryAnswerEditor.create({
      node,
      QB,
      notify,
      confirm: confirmDialog,
      onSaved: (item, solution) => {
        const card = findQuestion(item.id);
        if (card) {
          card.savedSolution = solution;
          card.hasAnswer = Boolean(solution?.answer || card.rawAnswer);
          card.hasAnalysis = Boolean(solution?.analysis || card.rawAnalysis);
        }
        if (typeof window.refreshQuestionSolution === 'function' && card) {
          try { window.refreshQuestionSolution(card); } catch (error) {
            console.warn('答案保存后刷新题卡失败', error);
          }
        }
        if (typeof window.renderQuestions === 'function') {
          try { window.renderQuestions(); } catch (error) {
            console.warn('答案保存后重绘列表失败', error);
          }
        }
        if (typeof window.renderQuickLook === 'function' && window.quickLookQuestionId
          && String(window.quickLookQuestionId) === String(item.id)) {
          try { window.renderQuickLook(); } catch (error) {
            console.warn('答案保存后刷新快速审题失败', error);
          }
        }
        notify('答案解析已保存到题库', 'success');
      }
    });
    return editor;
  }

  async function openSolutionEditor(ids) {
    if (opening) return;
    const id = resolveQuestionId(ids);
    if (!id) {
      notify('先选一道题，再打开答案解析编辑器', 'error');
      return;
    }
    opening = true;
    try {
      await ensureAssets();
      const publication = await fetchPublication(id);
      const item = toEditorItem(publication, findQuestion(id));
      await ensureEditor().open([item], { scope: 'library', focus: item.id });
    } catch (error) {
      notify(error?.message || '打开答案解析编辑器失败', 'error');
    } finally {
      opening = false;
    }
  }

  function closeSolutionEditor() {
    const dialog = document.getElementById('answerEditorDialog');
    if (dialog?.open) {
      const closeBtn = dialog.querySelector('.answer-editor-bar .button-quiet, .answer-editor-bar button');
      if (closeBtn) closeBtn.click();
      else if (typeof dialog.close === 'function') dialog.close();
    }
  }

  window.openSolutionEditor = openSolutionEditor;
  window.closeSolutionEditor = closeSolutionEditor;
})();
