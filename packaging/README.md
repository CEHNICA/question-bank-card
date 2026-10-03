# 题有据 Windows 安装包构建

构建流程使用 PyInstaller onedir 生成无控制台的 Windows 程序，再用 Inno Setup 生成当前用户安装包。目标电脑不需要预装 Python，安装和日常启动不需要管理员权限。

## 前置条件

- 64 位 Windows 10 或更高版本
- Python 3.12（仅构建电脑需要）
- Inno Setup 7.1.0

```powershell
winget install --id JRSoftware.InnoSetup.7 --exact --source winget --accept-source-agreements --accept-package-agreements
```

## 构建

在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build.ps1 -Version 1.11.5
```

默认产物：

- `packaging/dist/QuestionBankCard/QuestionBankCard.exe`
- `packaging/dist/installer/TiYouJu-Setup-1.11.5.exe`
- `packaging/dist/installer/SHA256SUMS.txt`

只生成 onedir：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\build.ps1 -Version 1.11.5 -SkipInstaller
```

## 数据边界

构建只选择应用代码、前端静态文件、迁移、图标、许可文件和维护中的公开 `skills/tiyouju` Markdown 文档。技能随软件附带，安装收尾不需要再下载；不会打包用户电脑上的技能目录。PyInstaller 完成后，`audit_bundle.py` 会检查最终目录；检出数据库、`backend/data`、日志、备份、凭据、非演示用 PDF、ZIP/TGZ 或测试缓存时，构建立即失败。

安装位置为 `%LOCALAPPDATA%\Programs\QuestionBankCard`，用户数据位于 `%LOCALAPPDATA%\QuestionBankCard`。卸载会移除程序和快捷方式，但故意保留用户数据。

`v1.2.0` 起产品显示名改为“题有据”，但继续使用原来的 AppId、安装目录、程序文件名和用户数据目录。直接运行新安装包即可覆盖升级；安装器会精确移除旧版桌面及开始菜单快捷方式，并在“题有据”分组中创建新快捷方式，不会迁移或删除题库数据。

`v1.10.14` 起桌面图标由用户选择：手动安装时自行勾选，一键安装不新建桌面图标，结束后由 AI 询问并执行 `tiyouju assistant-setup --desktop show|hide`。已有图标可能保留；隐藏只移动本软件的快捷方式，备份留在程序目录的 `assistant-shortcut-backups`，覆盖升级不会删除它。

完成构建后，可验证冻结 CLI、完整技能资源和真实 Windows 快捷方式：

```powershell
py -3.12 tools/check_assistant_setup.py --bundle packaging/dist/QuestionBankCard
```

该检查使用临时技能目录和临时桌面，不启动软件、不调用识读服务、不修改真实桌面。

## 许可证与二进制分发

第一方源代码采用 `AGPL-3.0-only`。PyMuPDF 1.28.2 / MuPDF 1.28.2 采用 AGPLv3 / 商业双许可；KaTeX 等组件保留各自许可证。

构建脚本会把根 `LICENSE`、安装说明、第三方组件摘要和 `THIRD_PARTY_LICENSES` 完整许可证目录放入程序目录，并让安装器展示 AGPLv3。公开分发二进制版本时，发布者仍有责任：

1. 在同一发布页提供该二进制所对应的完整第一方源码、锁文件、PyInstaller spec、构建脚本和本地修改；
2. 提供 PyMuPDF 1.28.2 与 MuPDF 1.28.2 的对应源码、构建材料和完整许可证，而不只是上游链接；
3. 核对 `THIRD_PARTY_LICENSES` 与实际打包版本一致；
4. 保留源代码地址和无担保声明；
5. 在发布前再次审计敏感数据，并根据需要完成代码签名。

这些说明不构成法律意见；商业分发前请自行完成专业许可审查，或取得适用的商业许可。

## 可重复性

应用与构建依赖锁定到明确版本，并为最终安装包生成 SHA-256。PyInstaller 和 Inno Setup 会写入时间与 PE 元数据，因此这里的“可重复”指流程与依赖可重复，不保证产物逐字节相同。
