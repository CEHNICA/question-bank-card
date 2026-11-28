# Windows 同页追加方法

此方法通过软件现有 COM 桥接，向用户已授权的题目笔记追加一个独立区域。不会运行新的 AI 模型，也不需要 API 密钥。所有页 ID 都从本次 `onenote read` 的返回值取，不能把本示例 ID 用到实际用户笔记。

## 找到软件随附桥接

`tiyouju skill` 给出实际主程序路径。正常安装时，桥接位于该目录下 `_internal/backend/core/onenote_bridge.ps1`；源码运行时位于仓库 `backend/core/onenote_bridge.ps1`。确认文件存在再调用，不要仅凭默认安装目录猜。普通安装的离线 KaTeX 同样在 `_internal/frontend/vendor/katex/katex.min.js`。

桥接使用 UTF-8 JSON 请求与响应文件，避免 PowerShell 5.1 参数编码问题。每次使用自己的临时目录。下面的调用函数只负责传送请求，不会自动选择题目或修改原对象：

```powershell
function Invoke-QBOneNoteBridge {
  param([string]$Bridge, [string]$Op, [hashtable]$Params)
  $taskDir = Join-Path ([IO.Path]::GetTempPath()) ('qb-note-review-' + [guid]::NewGuid().ToString('N'))
  [void][IO.Directory]::CreateDirectory($taskDir)
  $requestPath = Join-Path $taskDir 'request.json'
  $responsePath = Join-Path $taskDir 'response.json'
  $utf8 = New-Object Text.UTF8Encoding($false)
  $json = @{op=$Op; params=$Params} | ConvertTo-Json -Depth 30 -Compress
  [IO.File]::WriteAllText($requestPath, $json, $utf8)
  & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Bridge $requestPath $responsePath
  if (-not (Test-Path -LiteralPath $responsePath -PathType Leaf)) {
    throw 'OneNote did not return a result. Read back the page before retrying.'
  }
  $result = [IO.File]::ReadAllText($responsePath, [Text.Encoding]::UTF8) | ConvertFrom-Json
  if (-not $result.ok) { throw $result.error }
  Remove-Item -LiteralPath $requestPath, $responsePath
  Remove-Item -LiteralPath $taskDir
  return $result.data
}
```

该函数的失败分支保留本次临时文件用于核对；不要输出整页 XML 到公开日志，也不要扫描其他题目。若调用中断，先回读页并查本批标记，再决定是否重试。

## 先读，再决定位置

```powershell
$current = Invoke-QBOneNoteBridge -Bridge $bridgePath -Op 'page' -Params @{id=$pageId}
[xml]$doc = $current.xml
$ns = New-Object Xml.XmlNamespaceManager($doc.NameTable)
$ns.AddNamespace('one', $doc.DocumentElement.NamespaceURI)
```

确认根节点为 OneNote `Page`，ID 与题目关联一致。从实际根元素取 namespace，不硬编码 2013。先在所有文本节点查本次唯一标记；已经存在则进入回读和显示核验，不再次追加。

扫描现有 Outline、InkDrawing、Image、InsertedFile、MediaFile 的 Position/Size，为新区域选择右侧空白坐标。右边界是现有对象 `x + width` 的最大值，另留空隙；遇到尺寸缺失或相对坐标无法确定，要保守留足余量或让用户指定空白位置，不能默认放回 `(36,86)` 覆盖题面。追加前保留当前 XML 快照用于核对，而不是拿它整体替换页面。

## 构建追加片段

只构造一个新的 Outline，保留所有现有对象 ID。片段使用实际页面的 namespace 和已确认的空白坐标：

```xml
<one:Outline xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">
  <one:Position x="900.0" y="86.0" z="0"/>
  <one:Size width="540.0" height="240.0" isSetByUser="true"/>
  <one:OEChildren>
    <one:OE><one:T><![CDATA[<span>AI 批改建议 · 本次唯一批次标记</span>]]></one:T></one:OE>
    <one:OE><one:T><![CDATA[<span>题目定位码、助手名称、批改时间及原步骤位置。</span>]]></one:T></one:OE>
    <one:OE><one:T><![CDATA[<span>第一处问题、依据和最小修改建议。</span>]]></one:T></one:OE>
  </one:OEChildren>
</one:Outline>
```

这是结构示例，`900.0/86.0` 不是通用安全位置；批次标记要替换为实际唯一值，并真实写进第一段。公式按 [mathml.md](mathml.md) 完成转换、结构核查和计数，之后才放入独立的 T 节点。不把 `<table>` 或 `<br>` 混进片段。

在本地解析片段，核对公式数与正文，之后只执行一次：

```powershell
$written = Invoke-QBOneNoteBridge -Bridge $bridgePath -Op 'append_outline' -Params @{
  pageId = $pageId
  outlineXml = $outlineXml
  marker = $reviewMarker
}
$after = Invoke-QBOneNoteBridge -Bridge $bridgePath -Op 'page' -Params @{id=$pageId}
```

`append_outline` 是现有底层操作，不是 CLI/MCP 的命令名；不要编造 `tiyouju onenote review/write` 或直接写 SQLite。其返回字段包括 `outlineId`、`focusObjectId` 和 `objectUrl`，不同版本可能还有 `navigated`。这些字段不替代回读：旧版可能在定位失败时仍返回成功，必须检查新增对象与本次标记、文字、公式数。

定位可用桥接 `navigate`，参数 `{id=$pageId; objectId=$written.focusObjectId}`，或本次区域的 `objectUrl`。显示整页不等于定位到批改区。原题目 `onenote.page_id` 和 URL 不应被替换。

## 结束

在 OneNote 或其导出图查看本次新增区，确认原步骤保留、建议段落分开、公式实际可见。无法视觉核验时如实说明；失败不自动重复写，不删原笔记、不撤销整个页面、不触碰其他题目。
