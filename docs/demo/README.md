# 公开演示资料

`tiyouju-demo-paper.pdf` 是为题有据截图和功能演示制作的原创三页试卷，不含真实学生、学校、题库或 API 信息。

## 重新生成演示卷

1. 安装 `requirements.txt` 中锁定的 Python 依赖。
2. 安装 Noto Sans SC，或把环境变量 `QB_DEMO_NOTO_FONT` 指向一份 Noto Sans SC 的 OFL 字体文件。
3. 在仓库根目录运行 `python docs/demo/build_demo_paper.py`。

生成器会从可变字体临时导出 400 和 700 字重，只把当前文档用到的字形子集嵌入 PDF。Noto Sans SC 的版权与 SIL Open Font License 1.1 见 `OFL-NotoSansSC.txt`。

## 重新截取界面

先用题有据导入演示卷、人工核对并入库至少三题，再运行：

```powershell
node docs/demo/capture_showcase.mjs --base-url http://127.0.0.1:8768 --paper-id <演示任务 ID>
```

不传 `--paper-id` 时，脚本只会选择名为“题有据功能演示卷”的已完成任务，不会回退到其他资料。截图脚本需要 Playwright 与 Sharp。它只读取本机题库接口；试题篮仅写入临时浏览器的 `localStorage`。
