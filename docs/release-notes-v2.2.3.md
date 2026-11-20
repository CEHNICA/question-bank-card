# 题有据 2.2.3 更新说明

## 升级与改进

1. **全站统一 API 配置与单一加密凭据池（`api_config.py`、`provider_catalog.py`、`api-settings.js`）**
   - **唯一服务商目录与单一凭据池**：`mineru`、`modelscope`、`minimax`、`siliconflow`、`deepseek`、`doubao`、`custom` 全平台 7 家服务的端点、默认模型与角色归属统一由 `provider_catalog.py` 下发；所有 API 密钥统一加密保存至 `credentials.dat`（同一家服务全站共用，消除原先读题/解题双份保存与手动「共用读题密钥」步骤）。
   - **历史独立密钥无损自动迁移**：首次读取 `/api/settings/api-config` 时自动将旧版 `library-ai*.dat` 中保存的密钥合并入统一池（已有账号绝不覆盖，旧 `.dat` 文件保留作只读备份，支持幂等清除不再复活）。
   - **统一协调接口与精简前端**：新增 `GET/POST /api/settings/api-config`、`/api/settings/api-config/test`（需显式 `confirm: true`）与 `/api/settings/api-config/key/reveal`（保留原接口兼容 CLI 及既有工具）；前端收敛为单宿主渲染组件 `api-settings.js`，统一 60 秒查看明文/切后台自动掩码、单一「保存全部 API 配置」动作和唯一未保存离开守卫，彻底去除三卡片重复 HTML 与 DOM 搬运逻辑。

2. **原图切题直入库、Eagle 按需转文字与题卡界面修复**
   - **「只切题不识读」与原图题卡直接入库**：上传与拍照流程支持「只切题不识读」，切好的原图题卡可直接批量发布进入正式题库，并支持未定题型（`unknown`）快捷键标定、记忆题型与混合组卷分组导出。
   - **Eagle 按需 AI 转文字与 OneNote 联动优化**：Eagle 工作台支持原图题面筛选、空题干原图保存、单题及批量 AI 转文字升级轮询及 OneNote 导出/定位细节改进。
   - **修复展开态题卡头部徽章重叠**：将展开态已通过题卡右上「收起」按钮直接排入 `.card-head` 弹性布局流，避免「已入库」徽章与「收起」按钮重叠。
