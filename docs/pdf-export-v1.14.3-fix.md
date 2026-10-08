# v1.14.3 的 PDF 导出修复

修复版：**1.14.3.1**，基于标签 `v1.14.3` 的提交 `20935dd010071efa20981b1643695fa6004acc44`。

## 已实现

Windows Edge 在兼容层环境 `__COMPAT_LAYER=DetectorsAppHealth` 下会重新启动自身：原启动进程以 0 退出，新的排版进程随后正常开启。旧代码把原进程退出当作浏览器启动失败，显示“本机浏览器未能启动 PDF 排版，请重试或先导出 Word。”

修复为 Edge 加入 `--edge-skip-compat-layer-relaunch`，使应用持有实际排版进程。PDF 与 Word 的公式图片共用同一启动函数，继续使用独立临时配置、回环连接、固定资源及联网屏蔽，并在导出后清理自有进程。

该参数也用于 [Microsoft Playwright 的浏览器启动代码](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/server/chromium/chromiumSwitches.ts)。此次修复没有修改 Windows 的兼容设置。

## 验证证据

- Windows 后端：1514 项通过，包含真实浏览器导出检查，无跳过。
- 前端：101 项通过。
- Windows 桌面及打包检查：125 项通过。
- PDF、分页与 Word 公式图片的针对性检查：44 项通过。
- 新增 Edge/Chrome 的 4 项真实进程与 PDF 回归，强制兼容层环境，全部通过。
- 故障注入：仅在临时源码副本中移除修复参数，Edge 的两项回归准确失败为原始 503，Chrome 的两项继续通过。
- 原安装版 1.14.3 的隔离实测：4 道示例题预览成功，导出 PDF 返回 503，完整复现截图。
- 修复构建 1.14.3.1 的隔离实测：真实点击选题、预览和下载，得到 178177 字节 PDF，4 题、中文、公式、表格和配图保留，A4、1 页，与预览一致。已查看导出页面与 PDF 渲染图。
- 最后两次冻结程序验收各自的测试服务、浏览器及私有排版进程已核验停止；正式入库、识读任务、凭据均为零。
- 安装包构建和敏感数据审计通过；安装包未包含用户题库、试卷、凭据或运行日志。

证据保存在本修复目录的 `tmp/backend-tests.log`、`tmp/frontend-tests.log`、`tmp/desktop-tests.log`、`tmp/pdf-export-tests.log`、`tmp/frozen-pdf-old-confined-final/report.json`、`tmp/frozen-pdf-fixed-confined/report.json` 以及 `packaging/.build/pdf-browser-mutation-evidence.txt`。这些运行文件不提交仓库。

## 交付与当前边界

安装包位于 `packaging/dist/installer/TiYouJu-Setup-1.14.3.1.exe`，对应源码、第三方源码和许可证同时放在该目录，校验值见 `SHA256SUMS.txt`。

这是基于指定旧版本的本地修复交付。当前已安装的 1.14.3、正式题库和运行中的应用没有被覆盖；此修复尚未合并或发布到 GitHub Releases。安装包内的对应源码说明包含预留发布页地址，本地交付以同目录的源码文件为准。

升级时先用原程序备份题库并关闭窗口，再运行修复安装包。保留原安装包可供回退。

## 开发者复验

在 Windows 的项目根目录运行，Python 环境使用仓库锁定的依赖：

```powershell
$env:QB_EXPORT_BROWSER_TEST = '1'
python backend/manage.py test core --noinput
node --test frontend/test_*.js
python -m unittest test_launcher test_backup test_app_window test_credential_dialog test_packaging test_assistant_setup test_create_shortcut
python tools/check_frozen_pdf_browser.py --run --bundle packaging/dist/QuestionBankCard --port 8993 --output tmp/new-frozen-pdf-check
```

冻结程序验收只用全新隔离目录和随程序发布的虚构示例，不调用正式服务、不启动识读 worker。冻结后端没有注入 Python 层的网络拦截钩子；浏览器请求白名单、无凭据及独立数据边界均在报告中明确记录。
