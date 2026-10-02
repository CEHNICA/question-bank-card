# 公开演示资料

`tiyouju-demo-paper.pdf` 是供新手试用和复现功能演示的原创三页试卷，不含真实学生、学校、题库或 API 信息。

项目首页的当前截图使用维护者指定的 `202510口镇高中数学.pdf`，从本地实际界面拍摄。来源与选题见 [首页图片说明](../assets/screenshots/README.md)。

## 重新生成演示卷

1. 安装 `requirements.txt` 中锁定的 Python 依赖。
2. 安装 Noto Sans SC，或把环境变量 `QB_DEMO_NOTO_FONT` 指向一份 Noto Sans SC 的 OFL 字体文件。
3. 在仓库根目录运行 `python docs/demo/build_demo_paper.py`。

生成器会从可变字体临时导出 400 和 700 字重，只把当前文档用到的字形子集嵌入 PDF。Noto Sans SC 的版权与 SIL Open Font License 1.1 见 `OFL-NotoSansSC.txt`。

## 截取原创演示卷的界面

先用题有据导入演示卷、人工核对并入库至少三题，再运行：

```powershell
node docs/demo/capture_showcase.mjs --base-url http://127.0.0.1:8768 --paper-id <演示任务 ID> --out-dir docs/assets/screenshots/demo
```

不传 `--paper-id` 时，脚本只会选择名为“题有据功能演示卷”的已完成任务，不会回退到其他资料。默认输出到 `docs/assets/screenshots/demo`，避免覆盖首页的实际试卷截图。截图脚本需要 Playwright 与 Sharp。它只读取本机题库接口；试题篮仅写入临时浏览器的 `localStorage`。

## 新手教学用的示例试卷

软件里的“用示例试卷学一遍”使用 `backend/core/demo_data/` 中的示例试卷：同一份演示卷，已经用真实流程（MinerU + 视觉模型）读过一次，再故意留下两处练习（第 9 题“5 个单位”读成“3 个单位”、第 2 题的配图没有绑定）。重新生成：先用题有据或 `qb_bench.py` 读一遍演示卷，然后在仓库根目录运行

```powershell
$env:QB_DATABASE="<那次读题的 db.sqlite3>"; $env:QB_DATA_ROOT="<对应的 data 目录>"; python docs/demo/build_demo_fixture.py
```

示例试卷随安装包附带，不会进入正式题库。
