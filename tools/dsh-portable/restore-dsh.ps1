#Requires -Version 5.1
<#
.SYNOPSIS
    Restore a DeepSeek Harness migration package on a new PC.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\restore-dsh.ps1 .
.EXAMPLE
    .\restore-dsh.ps1 . -InstallApp
.EXAMPLE
    .\restore-dsh.ps1 . -Mode Replace -Workspace 'D:\question_card_bank'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string] $Path,                                   # .zip or an already extracted folder
    [ValidateSet('Merge','Replace')]
    [string] $Mode = 'Merge',                          # Merge = overlay, Replace = back up then replace
    [switch] $InstallApp,                              # also install package\app
    [string] $Workspace,                               # new workspace path (renames session archive)
    [switch] $SkipCredentials,                         # do not restore .credentials.yaml
    [switch] $WhatIfOnly                               # dry run
)

$ErrorActionPreference = 'Stop'

function Section($t) { Write-Host ''; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Ok($t)     { Write-Host "  [OK]  $t" -ForegroundColor Green }
function Warn2($t)   { Write-Host "  [!]   $t" -ForegroundColor Yellow }
function Info($t)    { Write-Host "        $t" -ForegroundColor DarkGray }

# ---------- 1. unpack ----------
if (Test-Path -LiteralPath $Path -PathType Leaf) {
    Section 'Extract package'
    $tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("dsh-restore-" + (Get-Date).ToString('yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
    $zipFull = (Resolve-Path -LiteralPath $Path).Path
    try {
        Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zipFull, $tempDir)
    } catch {
        & tar.exe -a -xf $zipFull -C $tempDir
        if ($LASTEXITCODE -ne 0) { throw "extract failed: $($_.Exception.Message)" }
    }
    Ok "extracted to $tempDir"
    $root = Get-ChildItem -LiteralPath $tempDir -Directory | Select-Object -First 1
    if (-not $root) { throw 'the archive is empty or unreadable.' }
    $rootPath = $root.FullName
} elseif (Test-Path -LiteralPath $Path -PathType Container) {
    $rootPath = (Resolve-Path -LiteralPath $Path).Path
} else {
    throw "path not found: $Path"
}

$pkgDir = Join-Path $rootPath 'package'
if (-not (Test-Path -LiteralPath $pkgDir)) {
    $nested = Get-ChildItem -LiteralPath $rootPath -Directory -ErrorAction SilentlyContinue |
              Where-Object { $_.Name -eq 'package' } | Select-Object -First 1
    if (-not $nested) { throw "no package\ directory found under $rootPath" }
    $pkgDir = $nested.FullName
}

$manifest = $null
$mFile = Join-Path $rootPath 'manifest.json'
if (Test-Path -LiteralPath $mFile) {
    $manifest = Get-Content -LiteralPath $mFile -Raw -Encoding UTF8 | ConvertFrom-Json
    Ok ("manifest: version {0}, packed {1}" -f $manifest.appVersion, $manifest.createdAt)
}

$dshHome      = Join-Path $env:USERPROFILE '.dsh'
$appDir       = Join-Path $env:LOCALAPPDATA 'Programs\DeepSeek Harness'
$electronRoot = Join-Path $env:APPDATA '@deepseek-ai\dsh-desktop'
$suffix       = (Get-Date).ToString('yyyyMMdd-HHmmss')

# ---------- 2. safety checks ----------
if (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'DeepSeek' }) {
    throw 'DeepSeek Harness is still running. Quit it (including the tray icon) and run this again.'
}
if ($WhatIfOnly) { Section 'DRY RUN - nothing will be written' }

$backupPaths = @()
function Prepare-Target {
    param([Parameter(Mandatory)][string] $Target)
    if (-not (Test-Path -LiteralPath $Target)) {
        if (-not $WhatIfOnly) { New-Item -ItemType Directory -Path $Target -Force | Out-Null }
        return
    }
    if ($Mode -eq 'Replace') {
        $bak = "$Target.bak-$suffix"
        Info "old data -> $bak"
        if (-not $WhatIfOnly) {
            Rename-Item -LiteralPath $Target -NewName (Split-Path -Leaf $bak)
            New-Item -ItemType Directory -Path $Target -Force | Out-Null
        }
        $script:backupPaths += $bak
    }
}

# ---------- 3. DSH home ----------
Section 'Restore DSH home (~/.dsh)'
$srcDsh = Join-Path $pkgDir 'dsh-home'
if (Test-Path -LiteralPath $srcDsh) {
    if ($SkipCredentials) {
        Info '.credentials.yaml will be skipped'
        if (-not $WhatIfOnly) {
            Remove-Item -LiteralPath (Join-Path $dshHome '.credentials.yaml') -Force -ErrorAction SilentlyContinue
        }
    }
    if ($WhatIfOnly) {
        Info "would merge $srcDsh -> $dshHome"
    } else {
        Prepare-Target -Target $dshHome
        $null = & robocopy $srcDsh $dshHome /E /R:1 /W:1 /XJ /NFL /NDL /NJH /NJS /NP
        if ($LASTEXITCODE -ge 8) { Warn2 "some files were not copied (robocopy=$LASTEXITCODE)" }
    }
    $n = (Get-ChildItem -LiteralPath $srcDsh -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object).Count
    Ok "sessions / profiles / plugins restored (source files: $n)"
} else {
    Warn2 'package has no dsh-home, skipped'
}

# ---------- 4. desktop settings ----------
Section 'Restore desktop settings'
$srcEu = Join-Path $pkgDir 'electron-userdata'
if (Test-Path -LiteralPath $srcEu) {
    if ($WhatIfOnly) {
        Info "would merge $srcEu -> $electronRoot"
    } else {
        Prepare-Target -Target $electronRoot
        $null = & robocopy $srcEu $electronRoot /E /R:1 /W:1 /XJ /NFL /NDL /NJH /NJS /NP
        if ($LASTEXITCODE -ge 8) { Warn2 "some files were not copied (robocopy=$LASTEXITCODE)" }
    }
    Ok 'desktop settings restored'
} else {
    Info 'package has no electron-userdata, skipped'
}

# ---------- 5. program files ----------
if ($InstallApp) {
    Section 'Install program files'
    $srcApp = Join-Path $pkgDir 'app'
    if (Test-Path -LiteralPath $srcApp) {
        if (-not $WhatIfOnly) {
            if (Test-Path -LiteralPath $appDir) {
                $bak = "$appDir.bak-$suffix"
                Info "previous install kept as $bak"
                Rename-Item -LiteralPath $appDir -NewName (Split-Path -Leaf $bak)
            }
            New-Item -ItemType Directory -Path $appDir -Force | Out-Null
            $null = & robocopy $srcApp $appDir /E /R:1 /W:1 /XJ /NFL /NDL /NJH /NJS /NP
            if ($LASTEXITCODE -ge 8) { Warn2 "some files were not copied (robocopy=$LASTEXITCODE); just reinstall DSH instead" }
        }
        Ok "program files placed at $appDir"
    } else {
        Warn2 'package has no app\ folder: install DeepSeek Harness normally on this PC'
    }
}

# ---------- 6. workspace / session paths ----------
Section 'Workspace alignment'
$oldWs = $null
if ($manifest -and $manifest.sourcePaths -and $manifest.sourcePaths.workspace) { $oldWs = $manifest.sourcePaths.workspace }
if ($Workspace)      { Info "new workspace: $Workspace" }
elseif ($oldWs)      { Info "workspace recorded in the package: $oldWs" }

if ($Workspace -and $oldWs -and ($Workspace -ne $oldWs) -and -not $WhatIfOnly) {
    $sessionsRoot = Join-Path $dshHome 'sessions'
    if (Test-Path -LiteralPath $sessionsRoot) {
        function Encode-SessionDir([string] $p) { return '--' + ($p -replace '[^A-Za-z0-9_-]', '-') + '--' }
        $oldEnc = Encode-SessionDir $oldWs
        $newEnc = Encode-SessionDir $Workspace
        $src = Join-Path $sessionsRoot $oldEnc
        if ((Test-Path -LiteralPath $src) -and -not (Test-Path -LiteralPath (Join-Path $sessionsRoot $newEnc))) {
            Rename-Item -LiteralPath $src -NewName $newEnc
            Ok "session archive renamed to $newEnc"
        } elseif (-not (Test-Path -LiteralPath $src)) {
            Info "no session archive named $oldEnc (that workspace had no history)"
        }
    }
}

$wsInPkg = Join-Path $pkgDir 'workspace'
if (Test-Path -LiteralPath $wsInPkg) {
    Info "the package also contains workspace sources at $wsInPkg"
    $target = if ($Workspace) { $Workspace } else { $oldWs }
    if ($target) { Info "copy them to $target so old sessions line up" }
}

# ---------- 7. summary ----------
if ($backupPaths.Count) {
    Section 'Backups kept'
    $backupPaths | ForEach-Object { Info $_ }
}

Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host '  RESTORE COMPLETE' -ForegroundColor Green
Write-Host '==============================================' -ForegroundColor Green
Write-Host "  DSH home : $dshHome"
Write-Host "  settings : $electronRoot"
Write-Host ''
Write-Host '  Open DeepSeek Harness now. If the web login is rejected, log in again -'
Write-Host '  that cookie is bound to the old machine and cannot be copied.'
if ($tempDir) { Write-Host "  temp dir : $tempDir (safe to delete)" }
Write-Host ''

if (-not $WhatIfOnly) {
    $appExe = Join-Path $appDir 'DeepSeek Harness.exe'
    if (Test-Path -LiteralPath $appExe) {
        $ans = Read-Host 'Start DeepSeek Harness now? (Y/N)'
        if ($ans -match '^[Yy]') { Start-Process -FilePath $appExe }
    } else {
        Warn2 'program files not found, please install DeepSeek Harness first.'
    }
}
