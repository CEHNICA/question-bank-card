<#
  on.ps1 - OneNote COM core (ASCII-only source on purpose).

  Why PowerShell: OneNote.Application is exposed through the .NET PIA
  (Microsoft.Office.Interop.OneNote). Only a .NET host can bind it, so
  python late-binding fails with TYPE_E_LIBNOTREGISTERED. PowerShell is a
  .NET host, so it is the COM bridge. All Chinese/Unicode payloads travel
  through JSON files, never through this source file, to dodge the
  PS 5.1 ANSI-decoding trap on .ps1 files.

  Protocol:
    powershell -NoProfile -ExecutionPolicy Bypass -File on.ps1 <req.json> <resp.json>

  Request  JSON: { "op": "<name>", "params": { ... } }
  Response JSON: { "ok": true,  "data": <any> }
                 { "ok": false, "error": "<message>" }
#>
param(
  [Parameter(Mandatory = $true)][string]$Request,
  [Parameter(Mandatory = $true)][string]$Response
)

$ErrorActionPreference = 'Stop'
$UTF8NoBom = New-Object System.Text.UTF8Encoding($false)

function Read-JsonFile([string]$p) {
  $raw = [IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)
  return ($raw | ConvertFrom-Json)
}

function Write-JsonFile([string]$p, $obj) {
  $json = $obj | ConvertTo-Json -Depth 24 -Compress
  [IO.File]::WriteAllText($p, $json, $UTF8NoBom)
}

# ---------- COM scope constants ----------
# HierarchyScope : hsSelf=0 hsChildren=1 hsNotebooks=2 hsSections=3 hsPages=4
# MsoPublishScope: psUnknown=0 psPDF=1 psXPS=2 psOnePkg=3 psOne=4 psHTML=5 psMht=6 psEML=7 psFeeds=8 psRDF=9
$HS_NOTEBOOKS = 2
$HS_SECTIONS  = 3
$HS_PAGES     = 4

function Reset-OneNoteCrashFlags {
  $regPath = 'HKCU:\Software\Microsoft\Office\16.0\OneNote\General'
  if (Test-Path $regPath) {
    Set-ItemProperty -Path $regPath -Name 'ConsecutiveBootCrashes' -Value 0 -Type DWord -ErrorAction SilentlyContinue
    Set-ItemProperty -Path $regPath -Name 'ConsecutiveEarlyCrashes' -Value 0 -Type DWord -ErrorAction SilentlyContinue
  }
}

function Get-OneNote {
  Reset-OneNoteCrashFlags
  # Clear stale headless /hyperlink instances (no visible window) that block COM registration with 0x80080005
  try {
    $stale = Get-CimInstance Win32_Process -Filter "Name='ONENOTE.EXE'" -ErrorAction SilentlyContinue |
      Where-Object { $_.CommandLine -like '*/hyperlink*' }
    foreach ($procInfo in $stale) {
      $p = Get-Process -Id $procInfo.ProcessId -ErrorAction SilentlyContinue
      if ($p -and $p.MainWindowHandle -eq [IntPtr]::Zero -and ((Get-Date) - $p.StartTime).TotalSeconds -gt 8) {
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
      }
    }
  } catch {}
  Reset-OneNoteCrashFlags
  try { return New-Object -ComObject OneNote.Application }
  catch { return New-Object -ComObject ONENOTE.Application }
}

function Get-Hierarchy([object]$one, [string]$hid, [int]$scope) {
  $x = $null
  [void]$one.GetHierarchy($hid, $scope, [ref]$x)
  if (-not $x) { return '' }
  return $x
}

function Get-PageContent([object]$one, [string]$id) {
  $x = $null
  [void]$one.GetPageContent($id, [ref]$x)
  if (-not $x) { return '' }
  return $x
}

# ---------- HTML -> text (the OneNote page body is HTML inside CDATA) ----------
function Convert-HtmlToText([string]$html) {
  if (-not $html) { return '' }
  $s = $html
  $s = [regex]::Replace($s, '(?is)<!--.*?-->', '')
  $s = [regex]::Replace($s, '(?i)<br\s*/?>', "`n")
  $s = [regex]::Replace($s, '(?i)</p\s*>', "`n")
  $s = [regex]::Replace($s, '(?i)</div\s*>', "`n")
  $s = [regex]::Replace($s, '(?i)</li\s*>', "`n")
  $s = [regex]::Replace($s, '(?i)<li[^>]*>', '- ')
  $s = [regex]::Replace($s, '(?i)</t[dh]\s*>', "`t")
  $s = [regex]::Replace($s, '<[^>]+>', '')
  $s = [System.Net.WebUtility]::HtmlDecode($s)
  $s = $s -replace "[`r`n]+", "`n"
  $s = $s -replace "[ \t]{2,}", ' '
  return $s.Trim()
}

# ---------- Page XML -> { title, text, blocks } ----------
function Convert-PageXml([string]$pageXml) {
  $res = [ordered]@{ title = ''; text = ''; blocks = @() }
  if (-not $pageXml) { return $res }
  try { $doc = New-Object System.Xml.XmlDocument; $doc.PreserveWhitespace = $true; $doc.LoadXml($pageXml) }
  catch { return $res }

  $ns = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
  $ns.AddNamespace('one', 'http://schemas.microsoft.com/office/onenote/2013/onenote')

  # title lives in <one:Title><one:OE><one:T><![CDATA[html]]></one:T></one:OE></one:Title>
  $tnode = $doc.SelectSingleNode('//one:Title/one:OE/one:T', $ns)
  if (-not $tnode) { $tnode = $doc.SelectSingleNode('//one:Title//one:T', $ns) }
  if ($tnode) { $res.title = (Convert-HtmlToText $tnode.InnerText) }

  $lines = New-Object System.Collections.ArrayList
  # body outline elements: <one:Outline><one:OEChildren><one:OE><one:T>...
  foreach ($oe in $doc.SelectNodes('//one:Outline//one:OE', $ns)) {
    $t = $oe.SelectSingleNode('./one:T', $ns)
    if ($t) {
      $txt = Convert-HtmlToText $t.InnerText
      if ($txt) { [void]$lines.Add($txt) }
    }
  }
  if ($lines.Count -eq 0) {
    # fallback: raw regex over <one:OE> blocks
    foreach ($m in [regex]::Matches($pageXml, '(?s)<one:OE\b.*?</one:OE>')) {
      $tm = [regex]::Match($m.Value, '(?s)<one:T\b[^>]*>(.*?)</one:T>')
      if ($tm.Success) {
        $txt = Convert-HtmlToText $tm.Groups[1].Value
        if ($txt) { [void]$lines.Add($txt) }
      }
    }
  }
  $res.blocks = @($lines)
  $res.text = ($lines -join "`n")
  return $res
}

# ---------- walk hierarchy into a flat structure ----------
function Get-SectionList($container, $ns) {
  # sections may sit under <one:SectionGroup>, so walk descendants
  $sections = @()
  foreach ($sec in $container.SelectNodes('.//one:Section', $ns)) {
    # Sections with path="" are read-only scratch copies left behind by
    # OpenHierarchy (backup snapshots we scanned).  They are not user
    # content, so they are skipped unless includeScratch is set.
    if (-not $script:IncludeScratch) {
      if (-not $sec.GetAttribute('path')) { continue }
    }
    # nearest ancestor SectionGroup name (that is where the section really lives)
    $grp = ''
    $anc = $sec.ParentNode
    while ($anc -ne $null -and $anc.NodeType -eq [System.Xml.XmlNodeType]::Element) {
      if ($anc.LocalName -eq 'SectionGroup') { $grp = $anc.GetAttribute('name'); break }
      $anc = $anc.ParentNode
    }
    $pages = @()
    foreach ($pg in $sec.SelectNodes('.//one:Page', $ns)) {
      $pages += [ordered]@{
        id = $pg.GetAttribute('ID')
        name = $pg.GetAttribute('name')
        dateTime = $pg.GetAttribute('dateTime')
        lastModifiedTime = $pg.GetAttribute('lastModifiedTime')
        pageLevel = $pg.GetAttribute('pageLevel')
      }
    }
    $sections += [ordered]@{
      id = $sec.GetAttribute('ID')
      name = $sec.GetAttribute('name')
      group = $grp
      path = $sec.GetAttribute('path')
      lastModifiedTime = $sec.GetAttribute('lastModifiedTime')
      color = $sec.GetAttribute('color')
      pages = @($pages)
    }
  }
  return $sections
}

function Get-HierarchyTree([string]$hierXml) {
  $doc = New-Object System.Xml.XmlDocument
  $doc.LoadXml($hierXml)
  $ns = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
  $ns.AddNamespace('one', 'http://schemas.microsoft.com/office/onenote/2013/onenote')

  $notebooks = @()
  foreach ($nb in $doc.SelectNodes('/one:Notebooks/one:Notebook', $ns)) {
    $sections = Get-SectionList $nb $ns
    $groupNames = @()
    foreach ($g in $nb.SelectNodes('.//one:SectionGroup', $ns)) {
      $gn = $g.GetAttribute('name')
      if ($gn) { $groupNames += $gn }
    }
    $notebooks += [ordered]@{
      id = $nb.GetAttribute('ID')
      name = $nb.GetAttribute('name')
      path = $nb.GetAttribute('path')
      lastModifiedTime = $nb.GetAttribute('lastModifiedTime')
      color = $nb.GetAttribute('color')
      kind = 'notebook'
      sectionGroups = @($groupNames)
      sections = @($sections)
    }
  }

  # root-level containers that are siblings of <one:Notebook>
  # OpenSections holds sections OneNote has open but not filed (e.g. the local 日记 section)
  $extras = @(
    @{ xp = '/one:Notebooks/one:OpenSections';  label = 'OpenSections' },
    @{ xp = '/one:Notebooks/one:UnfiledNotes'; label = 'UnfiledNotes' }
  )
  foreach ($e in $extras) {
    $node = $doc.SelectSingleNode($e.xp, $ns)
    if (-not $node) { continue }
    $sections = Get-SectionList $node $ns
    $loose = @()
    foreach ($pg in $node.SelectNodes('.//one:Page', $ns)) {
      $par = $pg.ParentNode
      if (-not $par -or $par.LocalName -ne 'Section') {
        $loose += [ordered]@{
          id = $pg.GetAttribute('ID')
          name = $pg.GetAttribute('name')
          dateTime = $pg.GetAttribute('dateTime')
          lastModifiedTime = $pg.GetAttribute('lastModifiedTime')
          pageLevel = $pg.GetAttribute('pageLevel')
        }
      }
    }
    if (($sections.Count -eq 0) -and ($loose.Count -eq 0)) { continue }
    $notebooks += [ordered]@{
      id = $node.GetAttribute('ID')
      name = $e.label
      path = $node.GetAttribute('path')
      lastModifiedTime = $node.GetAttribute('lastModifiedTime')
      color = ''
      kind = $e.label
      sectionGroups = @()
      sections = @($sections)
      loosePages = @($loose)
    }
  }
  return $notebooks
}

function Save-Utf8([string]$path, [string]$text) {
  [IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

# ---------- page content authoring ----------
function Escape-Xml([string]$s) {
  if ($null -eq $s) { return '' }
  return $s.Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;').Replace('"', '&quot;')
}

# CDATA payloads must not contain the "]]>" terminator
function Escape-Cdata([string]$s) {
  if ($null -eq $s) { return '' }
  return $s.Replace(']]>', ']]&gt;')
}

$ONE_NS = 'http://schemas.microsoft.com/office/onenote/2013/onenote'

function New-OneElement($doc, $localName) {
  return $doc.CreateElement('one', $localName, $ONE_NS)
}

function New-OneTextElement($doc, [string]$text) {
  $t = New-OneElement $doc 'T'
  $payload = (Escape-Cdata $text).Replace('&', '&amp;').Replace('<', '&lt;').Replace('>', '&gt;')
  [void]$t.AppendChild($doc.CreateCDataSection($payload))
  return $t
}

function Set-PageText([string]$pageXml, [string]$title, [string]$text) {
  # Verified recipe: keep the <one:Outline> shell (Position/Size) and swap only its
  # <one:OEChildren> content; OneNote ignores a wholesale Outline replacement.
  # DOM edits keep the child order valid (QuickStyleDef, PageSettings, Title, Outline...),
  # otherwise OneNote rejects the page with a content-model error.
  $doc = New-Object System.Xml.XmlDocument
  $doc.PreserveWhitespace = $true
  $doc.LoadXml($pageXml)
  $ns = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
  $ns.AddNamespace('one', $ONE_NS)
  $page = $doc.DocumentElement

  if ($title) {
    $oldTitle = $page.SelectSingleNode('./one:Title', $ns)
    $titleEl = New-OneElement $doc 'Title'
    $oe = New-OneElement $doc 'OE'
    [void]$oe.AppendChild((New-OneTextElement $doc $title))
    [void]$titleEl.AppendChild($oe)
    if ($oldTitle) {
      [void]$page.ReplaceChild($titleEl, $oldTitle)
    } else {
      $anchor = $page.SelectSingleNode('./one:Outline | ./one:Image | ./one:InkDrawing | ./one:InsertedFile | ./one:MediaFile | ./one:htmlBody', $ns)
      if ($anchor) { [void]$page.InsertBefore($titleEl, $anchor) } else { [void]$page.AppendChild($titleEl) }
    }
  }

  if ($null -ne $text -and $text.Length -gt 0) {
    $ocNew = New-OneElement $doc 'OEChildren'
    foreach ($line in ($text -split "`r?`n")) {
      $t = $line.TrimEnd()
      if (-not $t.Trim()) { continue }
      $oe = New-OneElement $doc 'OE'
      [void]$oe.AppendChild((New-OneTextElement $doc $t))
      [void]$ocNew.AppendChild($oe)
    }
    $outline = $page.SelectSingleNode('./one:Outline', $ns)
    if ($outline) {
      $ocOld = $outline.SelectSingleNode('./one:OEChildren', $ns)
      if ($ocOld) { [void]$ocOld.ParentNode.ReplaceChild($ocNew, $ocOld) }
      else { [void]$outline.AppendChild($ocNew) }
    } else {
      $ol = New-OneElement $doc 'Outline'
      [void]$ol.AppendChild($ocNew)
      $titleEl = $page.SelectSingleNode('./one:Title', $ns)
      if ($titleEl) { [void]$page.InsertAfter($ol, $titleEl) } else { [void]$page.AppendChild($ol) }
    }
  }
  return $doc.OuterXml
}

# ---------- hierarchy changes (remove a page through UpdateHierarchy) ----------
function New-RemovePageXml([string]$hierXml, [string]$pageId) {
  $doc = New-Object System.Xml.XmlDocument
  $doc.LoadXml($hierXml)
  $ns = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
  $ns.AddNamespace('one', 'http://schemas.microsoft.com/office/onenote/2013/onenote')

  $pg = $doc.SelectSingleNode("//one:Page[@ID='" + $pageId + "']", $ns)
  if (-not $pg) { throw ('page not found in hierarchy: ' + $pageId) }

  $sec = $pg.ParentNode
  while ($sec -and $sec.LocalName -ne 'Section') { $sec = $sec.ParentNode }
  if (-not $sec) { throw 'section ancestor not found' }
  $secId = $sec.GetAttribute('ID')

  # section may live under one or more SectionGroups
  $groups = @()
  $walk = $sec.ParentNode
  while ($walk -and $walk.LocalName -ne 'Notebook' -and $walk.LocalName -ne 'Notebooks') {
    if ($walk.LocalName -eq 'SectionGroup') { $groups = @($walk.GetAttribute('ID')) + $groups }
    $walk = $walk.ParentNode
  }
  $nb = $walk
  if ($nb -and $nb.LocalName -eq 'Notebook') { $nbId = $nb.GetAttribute('ID') } else { $nbId = '' }

  $sb = New-Object System.Text.StringBuilder
  [void]$sb.Append('<?xml version="1.0"?><one:Notebooks xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote">')
  if ($nbId) { [void]$sb.Append('<one:Notebook ID="' + $nbId + '">') }
  foreach ($g in $groups) { [void]$sb.Append('<one:SectionGroup ID="' + $g + '">') }
  [void]$sb.Append('<one:Section ID="' + $secId + '">')
  [void]$sb.Append('<one:Page ID="' + $pageId + '"><one:Remove/></one:Page>')
  [void]$sb.Append('</one:Section>')
  for ($i = $groups.Count - 1; $i -ge 0; $i--) { [void]$sb.Append('</one:SectionGroup>') }
  if ($nbId) { [void]$sb.Append('</one:Notebook>') }
  [void]$sb.Append('</one:Notebooks>')
  return $sb.ToString()
}

# ================= main =================
$one = $null
$req = Read-JsonFile $Request
$op = [string]$req.op
$params = $req.params
$data = $null

# scratch copies (path="") are hidden unless explicitly requested
$script:IncludeScratch = $false
if ($params -and $params.includeScratch) { $script:IncludeScratch = $true }

$exitCode = 0
try {
  $one = Get-OneNote

  switch ($op) {

    'hierarchy' {
      $scope = 4
      if ($params -and $params.scope) { $scope = [int]$params.scope }
      $data = [ordered]@{ xml = (Get-Hierarchy $one ([string]$params.id) $scope) }
    }

    'list' {
      $hier = Get-Hierarchy $one '' $HS_PAGES
      $tree = Get-HierarchyTree $hier
      $summary = @()
      foreach ($nb in $tree) {
        $pgCount = (@($nb.sections | ForEach-Object { $_.pages.Count }) | Measure-Object -Sum).Sum
        if ($nb.Contains('loosePages')) { $pgCount = $pgCount + $nb.loosePages.Count }
        $summary += [ordered]@{
          name = $nb.name; id = $nb.id; path = $nb.path
          kind = $nb.kind
          groups = $nb.sectionGroups.Count
          sections = $nb.sections.Count
          pages = $pgCount
        }
      }
      $data = [ordered]@{ notebooks = @($summary) }
    }

    'page' {
      $xml = Get-PageContent $one ([string]$params.id)
      $conv = Convert-PageXml $xml
      $data = [ordered]@{ id = [string]$params.id; title = $conv.title; text = $conv.text; blocks = @($conv.blocks); xml = $xml }
    }

    'dump' {
      $outDir = [string]$params.outDir
      if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
      $xmlDir = Join-Path $outDir 'xml'
      if (-not (Test-Path $xmlDir)) { New-Item -ItemType Directory -Path $xmlDir -Force | Out-Null }

      $hier = Get-Hierarchy $one '' $HS_PAGES
      Save-Utf8 (Join-Path $outDir 'hierarchy.xml') $hier
      $tree = Get-HierarchyTree $hier

      $jsonl = Join-Path $outDir 'pages.jsonl'
      $sw = New-Object System.IO.StreamWriter($jsonl, $false, (New-Object System.Text.UTF8Encoding($false)))
      $mdSb = New-Object System.Text.StringBuilder
      $total = 0; $totalXml = 0; $ok = 0; $fail = 0

      try {
        foreach ($nb in $tree) {
          [void]$mdSb.AppendLine("## " + $nb.name)
          [void]$mdSb.AppendLine("")
          foreach ($sec in $nb.sections) {
            [void]$mdSb.AppendLine("### " + $sec.name)
            [void]$mdSb.AppendLine("")
            # recycle bin sections hold already-deleted pages; skip them
            # unless explicitly requested
            $skipBin = ($sec.path -like '*RecycleBin*')
            if ($skipBin -and -not $params.includeRecycleBin) { continue }
            foreach ($pg in $sec.pages) {
              $total++
              $pxml = ''
              $conv = $null
              try {
                $pxml = Get-PageContent $one $pg.id
                $conv = Convert-PageXml $pxml
                $ok++
                $totalXml += $pxml.Length
              } catch {
                $fail++
              }
              $title = $pg.name
              if ($conv -and $conv.title) { $title = $conv.title }
              $safeId = ($pg.id -replace '[^0-9A-Za-z]', '_')
              if ($pxml) { Save-Utf8 (Join-Path $xmlDir ($safeId + '.xml')) $pxml }
              $rec = [ordered]@{
                id = $pg.id
                notebook = $nb.name
                section = $sec.name
                title = $title
                dateTime = $pg.dateTime
                lastModifiedTime = $pg.lastModifiedTime
                pageLevel = $pg.pageLevel
                xmlBytes = $pxml.Length
                text = if ($conv) { $conv.text } else { '' }
              }
              $sw.WriteLine(($rec | ConvertTo-Json -Depth 8 -Compress))

              [void]$mdSb.AppendLine("#### " + $title)
              [void]$mdSb.AppendLine("")
              [void]$mdSb.AppendLine("- notebook: " + $nb.name + " | section: " + $sec.name + " | date: " + $pg.dateTime)
              [void]$mdSb.AppendLine("- id: `"$($pg.id)`"")
              [void]$mdSb.AppendLine("")
              if ($conv) {
                foreach ($b in $conv.blocks) { [void]$mdSb.AppendLine($b) }
              }
              [void]$mdSb.AppendLine("")
            }
          }
        }
      } finally { $sw.Close() }

      Save-Utf8 (Join-Path $outDir 'all.md') $mdSb.ToString()
      $stats = [ordered]@{
        notebooks = $tree.Count
        sections = (@($tree | ForEach-Object { $_.sections.Count }) | Measure-Object -Sum).Sum
        pages = $total
        pagesRead = $ok
        pagesFailed = $fail
        totalXmlBytes = $totalXml
        outDir = $outDir
      }
      Save-Utf8 (Join-Path $outDir 'stats.json') (($stats | ConvertTo-Json -Depth 6))
      $data = $stats
    }

    'publish' {
      # Publish(bstrHierarchyID, bstrTargetFilePath, pfPublishFormat[, bstrCLSIDofExporter])
      # PublishFormat: pfOneNote=0 pfOneNotePackage=1 pfMHTML=2 pfPDF=3 pfXPS=4 pfWord=5 pfEMF=6 pfHTML=7 pfOneNote2007=8
      $id = [string]$params.id
      $dest = [string]$params.dest
      $pf = 1
      if ($params.format) { $pf = [int]$params.format }
      $clsid = [string]$params.clsid
      if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force }
      if ($clsid) { [void]$one.Publish($id, $dest, $pf, $clsid) }
      else { [void]$one.Publish($id, $dest, $pf) }
      # OneNote can finish writing the package after the call returns
      for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 250
        if (Test-Path -LiteralPath $dest) {
          $sz = (Get-Item -LiteralPath $dest).Length
          if ($sz -gt 0) { break }
        }
      }
      $bytes = 0
      $exists = $false
      if (Test-Path -LiteralPath $dest) { $exists = $true; $bytes = (Get-Item -LiteralPath $dest).Length }
      $data = [ordered]@{ dest = $dest; bytes = $bytes; exists = $exists; publishFormat = $pf }
    }

    'create' {
      # CreateNewPage(bstrSectionID, out pbstrPageID) then UpdatePageContent(pageXml)
      $sectionId = [string]$params.sectionId
      $newId = $null
      [void]$one.CreateNewPage($sectionId, [ref]$newId)
      if (-not $newId) { throw 'CreateNewPage returned no page id' }
      $title = [string]$params.title
      $text = [string]$params.text
      if ($title -or $text) {
        $skeleton = Get-PageContent $one $newId
        $patched = Set-PageText $skeleton $title $text
        [void]$one.UpdatePageContent($patched)
      }
      $link = $null
      try { [void]$one.GetHyperlinkToObject([string]$newId, '', [ref]$link) } catch {}
      $data = [ordered]@{ id = [string]$newId; link = [string]$link }
    }

    'create_xml' {
      $sectionId = [string]$params.sectionId
      $rawPageXml = [string]$params.pageXml
      $targetId = [string]$params.pageId
      $skeleton = $null
      if ($targetId) {
        try {
          $skeleton = Get-PageContent $one $targetId
        } catch {
          $targetId = $null
        }
      }
      if (-not $targetId) {
        [void]$one.CreateNewPage($sectionId, [ref]$targetId)
        if (-not $targetId) { throw 'CreateNewPage returned no page id' }
        $skeleton = Get-PageContent $one $targetId
      }

      $srcDoc = New-Object System.Xml.XmlDocument
      $srcDoc.PreserveWhitespace = $true
      $srcDoc.LoadXml($rawPageXml.Replace('__QB_PAGE_ID__', $targetId))
      $srcNs = New-Object System.Xml.XmlNamespaceManager($srcDoc.NameTable)
      $srcNs.AddNamespace('one', $ONE_NS)

      $doc = New-Object System.Xml.XmlDocument
      $doc.PreserveWhitespace = $true
      $doc.LoadXml($skeleton)
      $ns = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
      $ns.AddNamespace('one', $ONE_NS)
      $page = $doc.DocumentElement

      $srcTitle = $srcDoc.DocumentElement.SelectSingleNode('./one:Title', $srcNs)
      if ($srcTitle) {
        $importedTitle = $doc.ImportNode($srcTitle, $true)
        $oldTitle = $page.SelectSingleNode('./one:Title', $ns)
        if ($oldTitle) {
          [void]$page.ReplaceChild($importedTitle, $oldTitle)
        } else {
          $anchor = $page.SelectSingleNode('./one:Outline | ./one:Image | ./one:InkDrawing | ./one:InsertedFile | ./one:MediaFile | ./one:htmlBody', $ns)
          if ($anchor) { [void]$page.InsertBefore($importedTitle, $anchor) } else { [void]$page.AppendChild($importedTitle) }
        }
      }

      $srcChildren = $srcDoc.DocumentElement.SelectSingleNode('./one:Outline/one:OEChildren', $srcNs)
      if ($srcChildren) {
        $importedChildren = $doc.ImportNode($srcChildren, $true)
        $outline = $page.SelectSingleNode('./one:Outline', $ns)
        if ($outline) {
          $ocOld = $outline.SelectSingleNode('./one:OEChildren', $ns)
          if ($ocOld) { [void]$ocOld.ParentNode.ReplaceChild($importedChildren, $ocOld) }
          else { [void]$outline.AppendChild($importedChildren) }
        } else {
          $ol = New-OneElement $doc 'Outline'
          [void]$ol.AppendChild($importedChildren)
          $titleEl = $page.SelectSingleNode('./one:Title', $ns)
          if ($titleEl) { [void]$page.InsertAfter($ol, $titleEl) } else { [void]$page.AppendChild($ol) }
        }
      }

      [void]$one.UpdatePageContent($doc.OuterXml)
      $link = $null
      try { [void]$one.GetHyperlinkToObject([string]$targetId, '', [ref]$link) } catch {}
      $data = [ordered]@{ id = [string]$targetId; link = [string]$link }
    }

    'update' {
      $id = [string]$params.id
      $title = [string]$params.title
      $text = [string]$params.text
      $skeleton = Get-PageContent $one $id
      $patched = Set-PageText $skeleton $title $text
      [void]$one.UpdatePageContent($patched)
      $data = [ordered]@{ id = $id; updated = $true }
    }

    'update_raw' {
      # raw page XML straight into UpdatePageContent (for schema experiments)
      $id = [string]$params.id
      $body = [string]$params.bodyXml
      [void]$one.UpdatePageContent($body)
      $data = [ordered]@{ id = $id; updated = $true; bytes = $body.Length }
    }

    'update_hierarchy' {
      # raw hierarchy-changes XML (add/remove/move sections and pages)
      $body = [string]$params.bodyXml
      [void]$one.UpdateHierarchy($body)
      $data = [ordered]@{ updated = $true; bytes = $body.Length }
    }

    'delete' {
      # no DeletePage in the object model: page removal goes through DeleteHierarchy
      $id = [string]$params.id
      [void]$one.DeleteHierarchy($id)
      $data = [ordered]@{ id = $id; deleted = $true }
    }

    'delete_hierarchy' {
      $oid = [string]$params.objectId
      [void]$one.DeleteHierarchy($oid)
      $data = [ordered]@{ objectId = $oid; deleted = $true }
    }

    'delete_page_content' {
      $pid2 = [string]$params.pageId
      $oid = [string]$params.objectId
      [void]$one.DeletePageContent($pid2, $oid)
      $data = [ordered]@{ pageId = $pid2; objectId = $oid; deleted = $true }
    }

    'find' {
      # FindPages(startNodeId, searchString, out xml, includeUnindexedPages, display)
      $x = $null
      [void]$one.FindPages([string]$params.startId, [string]$params.query, [ref]$x, $true, $true)
      $data = [ordered]@{ xml = [string]$x }
    }

    'open_hierarchy' {
      # OpenHierarchy(path, relativeToObjectId, out objectId, createFileType)
      $x = $null
      [void]$one.OpenHierarchy([string]$params.path, [string]$params.relativeTo, [ref]$x, 0)
      $data = [ordered]@{ objectId = [string]$x }
    }

    'open_package' {
      $x = $null
      [void]$one.OpenPackage([string]$params.path, [string]$params.dest, [ref]$x)
      $data = [ordered]@{ path = [string]$x }
    }

    'special_location' {
      $x = $null
      [void]$one.GetSpecialLocation([int]$params.kind, [ref]$x)
      $data = [ordered]@{ path = [string]$x }
    }

    'binary_content' {
      # GetBinaryPageContent(pageId, callbackId, out base64)
      $x = $null
      [void]$one.GetBinaryPageContent([string]$params.pageId, [string]$params.callbackId, [ref]$x)
      $data = [ordered]@{ base64 = [string]$x }
    }

    'navigate' {
      [void]$one.NavigateTo([string]$params.id)
      $data = [ordered]@{ id = [string]$params.id; navigated = $true }
    }

    'hyperlink' {
      $link = $null
      [void]$one.GetHyperlinkToObject([string]$params.id, '', [ref]$link)
      $data = [ordered]@{ id = [string]$params.id; link = [string]$link }
    }

    'scan_backups' {
      # open many .one files in one process and return their page lists.
      # NOTE: opening a local copy re-stamps both the guid prefix AND the object id,
      # so pages must be matched on (name, lastModifiedTime), never on id.
      $paths = @($params.paths)
      $result = @{}
      foreach ($p in $paths) {
        $rows = @()
        try {
          $oid = $null
          [void]$one.OpenHierarchy([string]$p, '', [ref]$oid, 0)
          if ($oid) {
            $hx = Get-Hierarchy $one ([string]$oid) $HS_PAGES
            foreach ($m in [regex]::Matches($hx, '<one:Page ID="([^"]+)" name="([^"]*)" dateTime="([^"]*)" lastModifiedTime="([^"]*)"')) {
              $rows += [ordered]@{
                id = $m.Groups[1].Value
                name = $m.Groups[2].Value
                dateTime = $m.Groups[3].Value
                lastModifiedTime = $m.Groups[4].Value
              }
            }
          }
        } catch { }
        $result[[string]$p] = @($rows)
      }
      $data = [ordered]@{ files = $result }
    }

    'parent' {
      $x = $null
      [void]$one.GetHierarchyParent([string]$params.objectId, [ref]$x)
      $data = [ordered]@{ parentId = [string]$x }
    }

    'close_notebook' {
      # CloseNotebook(bstrNotebookID) -- closes a notebook that was opened
      # from a local .one file, so it stops showing up in the hierarchy tree
      $oid = [string]$params.objectId
      [void]$one.CloseNotebook($oid)
      $data = [ordered]@{ objectId = $oid; closed = $true }
    }

    'sync' {
      [void]$one.SyncHierarchy([string]$params.id)
      $data = [ordered]@{ id = [string]$params.id; synced = $true }
    }

    'open_local' {
      $path = [string]$params.path
      $exe = [string]$params.exe
      if (-not $exe) { $exe = 'C:\Program Files\Microsoft Office\root\Office16\ONENOTE.EXE' }
      Start-Process -FilePath $exe -ArgumentList ('"' + $path + '"')
      Start-Sleep -Seconds ([int]$(if ($params.waitSeconds) { $params.waitSeconds } else { 8 }))
      $hier = Get-Hierarchy $one '' $HS_PAGES
      $tree = Get-HierarchyTree $hier
      $summary = @()
      foreach ($nb in $tree) {
        $summary += [ordered]@{
          name = $nb.name; path = $nb.path
          sections = $nb.sections.Count
          pages = (@($nb.sections | ForEach-Object { $_.pages.Count }) | Measure-Object -Sum).Sum
        }
      }
      $data = [ordered]@{ notebooks = @($summary) }
    }

    default { throw ("unknown op: " + $op) }
  }

  Write-JsonFile $Response ([ordered]@{ ok = $true; data = $data })
}
catch {
  $msg = $_.Exception.Message
  if ($_.Exception.InnerException) { $msg = $msg + ' | inner: ' + $_.Exception.InnerException.Message }
  Write-JsonFile $Response ([ordered]@{ ok = $false; error = $msg })
  $exitCode = 1
}
finally {
  if ($null -ne $one) {
    try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($one) } catch {}
  }
  Start-Sleep -Milliseconds 200
  Reset-OneNoteCrashFlags
}
if ($exitCode -ne 0) { exit $exitCode }
