## 题有据 2.3.9 更新说明

### 更新亮点

1. **CLI / Skill / MCP AI Agent 协同能力全面补齐**：
   - **只读读库、统计与查重**：新增 `tiyouju stats`（MCP `library_stats`）统计全景、`tiyouju detail`（MCP `get_question_detail`）查看版本历史/多源证据/已保存解析与原卷图、`tiyouju tags`（`list_tags`）只读查看标签树、`tiyouju dedup scan`（`scan_duplicates`）只读扫描重题簇，以及 `tiyouju variants bind/unbind`（`bind_variants`/`unbind_variant`）管理变式题组；`library` / `search_library` 开放 `offset` 翻页、`sort` 排序、`exclude_used_days` 排除近期已用题及 `has_analysis`/`untagged` 筛选，`MCP` 补全 `locate_question`、`list_cards(summary)` 与 `create_backup`。
   - **OneNote 笔记读写互通**：新增 `GET /api/library/onenote/notes`、`GET /api/library/<id>/onenote/read`（只读返回笔记文本块与裁边手写墨迹图）以及 CLI `tiyouju onenote list/read/open/create`、MCP `list_onenote_notes` / `read_onenote_note` / `open_onenote_note` / `create_onenote_note`。
   - **防撞车智慧组卷**：新增 `tiyouju usage`（`list_exam_usage`）与 `tiyouju drafts list/show/create/update`（`list_drafts`/`get_draft`/`save_draft`），让 Agent 选好不撞车好题后直接保存为 A4/A3 组卷草稿（严格禁止删除或篡改安全边界）。
2. **题库原卷定位图增强**：`/api/library/locate` 返回的 `local_images` 补齐原卷裁剪主图 `crop.png`，方便 Agent 对照原卷文字与配图核对细节。
3. **社区后端与官网体验同步升级**：社区服务端启用 R2 热层 + 123 云盘 WebDAV 冷归档分层架构；官网（`tiyouju.com`）全面升级为曜石翡翠视觉并新增 AI-Native、OneNote 联动与杂卷抽卡专题展示。

### 验证范围

- `ruff check`、`ruff format --check`、`mypy`、`npm run lint`、前端 `node --test`、桌面启动器与备份单元测试及全套 Django `core` 测试通过。
