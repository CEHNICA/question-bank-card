# 题有据 2.0.1 更新说明

## 本版修复与改进

1. **打通 Eagle 工作台统一答案解析编辑器（`LibraryAnswerEditor`）**
   - 在 `/eagle` 页面正式引入 `eagle-solution-editor.js`，并暴露 `window.renderQuickLook` 与 `window.quickLookQuestionId`，编辑保存后自动联动刷新卡片与快速审题弹窗。
   - QuickLook 解析区的「录入正式解析」「✎ 编辑正式解析」「转为正式解析 →」、题卡展开解析区、右键菜单及左上角主菜单均接入统一 `LibraryAnswerEditor`。

2. **修复 Eagle「修改与校订题目」弹窗版本迁移与保存保护**
   - 打开弹窗时优先回填已保存的正式答案与解析（`savedSolution`），并等待修订元数据请求避免提交竞态。
   - 题干未改时直接提交解析；题干改版生成新版 `PublishedQuestion` 后自动迁移前端题卡 ID、试题篮/收藏状态，并基于新版本 UUID 与权威 `base_revision` 保存解析。
   - 后端校验拒绝时保留弹窗输入并明确提示错误信息，不再静默关闭丢修改；同步修复 `saveAiSolutionToBank` 冲突与假成功提示。

3. **OneNote 单测注册表隔离**
   - `backend/core/test_library_onenote.py` 在 `setUp` 中统一 mock `core.tiyouju_protocol.ensure_registered`，避免本地跑单测覆盖已安装客户端的 `tiyouju://` 自定义协议关联。
