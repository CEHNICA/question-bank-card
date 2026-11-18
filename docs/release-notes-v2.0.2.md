# 题有据 2.0.2 更新说明

## 本版修复与改进

1. **修复 Eagle「移入回收站失败」及回收站恢复/永久删除 415 拦截**
   - 后端无请求体接口 `paper_archive`、`paper_restore`、`library_withdraw`、`library_trash.restore_view`、`library_trash.purge_view` 统一改用 `_guard(request, json_body=False)`（仍严格校验 `X-QB-Request` 同源防跨站请求头），消除 HTTP 415 误拦。
   - 前端 `movePaperToTrash`、`restoreTrashPaper`、`restoreTrashQuestion`、`purgeTrashQuestion` 统一补全 `Content-Type: application/json` 请求头，并真实解析后端返回的错误文案。

2. **提升整卷移入回收站与超大容量批处理的可靠性**
   - 删除整份资料改为「先校验归档、再按资料整批下架题目」，若下架失败则自动回滚归档，彻底避免“移入回收站失败但题已经被全部撤空”的不一致状态。
   - 批量移入回收站、恢复、彻底删除与清空回收站按后端 `500` 条上限自动分片执行，勾选超过 500 题也能正常处理。
