[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+(\.\d+){0,2}$')]
    [string]$Version = '1.3.0',

    [switch]$SkipInstaller,
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$PackagingRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PackagingRoot))
$BuildRoot = Join-Path $PackagingRoot '.build'
$BuildVenv = Join-Path $BuildRoot 'venv'
$BuildPython = Join-Path $BuildVenv 'Scripts\python.exe'
$PyInstallerWork = Join-Path $BuildRoot 'pyinstaller'
$DistRoot = Join-Path $PackagingRoot 'dist'
$BundleRoot = Join-Path $DistRoot 'QuestionBankCard'
$InstallerRoot = Join-Path $DistRoot 'installer'
$SpecFile = Join-Path $PackagingRoot 'QuestionBankCard.spec'
$AuditScript = Join-Path $PackagingRoot 'audit_bundle.py'
$ApplicationRequirements = Join-Path $ProjectRoot 'backend\requirements.lock.txt'
$BuildRequirements = Join-Path $PackagingRoot 'requirements-build.lock.txt'
$ProjectLicense = Join-Path $ProjectRoot 'LICENSE'
$ThirdPartyLicenses = Join-Path $ProjectRoot 'THIRD_PARTY_LICENSES'


function Invoke-Native {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $FilePath $($ArgumentList -join ' ')"
    }
}


function Assert-ChildPath {
    param(
        [Parameter(Mandatory)] [string]$Candidate,
        [Parameter(Mandatory)] [string]$ExpectedParent
    )
    $candidateFull = [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\')
    $parentFull = [System.IO.Path]::GetFullPath($ExpectedParent).TrimEnd('\') + '\'
    if (-not $candidateFull.StartsWith($parentFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside the packaging directory: $candidateFull"
    }
}


function Remove-BuildDirectory {
    param([Parameter(Mandatory)] [string]$Path)
    Assert-ChildPath -Candidate $Path -ExpectedParent $PackagingRoot
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}


function New-BuildVirtualEnvironment {
    if (Test-Path -LiteralPath $BuildPython -PathType Leaf) {
        return
    }

    New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
    $preferredPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (Test-Path -LiteralPath $preferredPython -PathType Leaf) {
        $systemPythonPath = $preferredPython
    }
    else {
        $systemPython = Get-Command 'python.exe' -ErrorAction SilentlyContinue
        $systemPythonPath = if ($null -eq $systemPython) { $null } else { $systemPython.Source }
    }
    if (-not $systemPythonPath) {
        throw '64-bit Python 3.12 was not found.'
    }
    Invoke-Native -FilePath $systemPythonPath -ArgumentList @('-c', 'import sys;sys.exit(not(sys.version_info[:2]==(3,12)and sys.maxsize>2**32))')
    Invoke-Native -FilePath $systemPythonPath -ArgumentList @('-m', 'venv', $BuildVenv)
}


function Write-VersionResource {
    $parts = @($Version.Split('.') | ForEach-Object { [int]$_ })
    while ($parts.Count -lt 4) {
        $parts += 0
    }
    $numericVersion = "$($parts[0]), $($parts[1]), $($parts[2]), $($parts[3])"
    $versionResource = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($numericVersion),
    prodvers=($numericVersion),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [StringStruct('CompanyName', 'CEHNICA'),
         StringStruct('FileDescription', '\u9898\u6709\u636e - \u539f\u5377\u53ef\u8ffd\u6eaf\u7684\u9898\u5e93\u6574\u7406\u5de5\u5177'),
         StringStruct('FileVersion', '$Version'),
         StringStruct('InternalName', 'QuestionBankCard'),
         StringStruct('OriginalFilename', 'QuestionBankCard.exe'),
         StringStruct('ProductName', '\u9898\u6709\u636e'),
         StringStruct('ProductVersion', '$Version'),
         StringStruct('LegalCopyright', 'Copyright (c) 2026 CEHNICA and contributors')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"@
    New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $BuildRoot 'version_info.txt') -Value $versionResource -Encoding utf8
}


function Find-InnoCompiler {
    $command = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 7\ISCC.exe')
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    throw @'
Inno Setup 7 was not found. Install it first with:
winget install --id JRSoftware.InnoSetup.7 --exact --source winget --accept-source-agreements --accept-package-agreements
'@
}


if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw 'The installer must be built on Windows.'
}

foreach ($requiredFile in @(
    (Join-Path $ProjectRoot 'app_launcher.pyw'),
    $SpecFile,
    $AuditScript,
    $ApplicationRequirements,
    $BuildRequirements,
    $ProjectLicense,
    (Join-Path $ProjectRoot 'assets\app.ico')
)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required build file is missing: $requiredFile"
    }
}
if (-not (Test-Path -LiteralPath $ThirdPartyLicenses -PathType Container)) {
    throw "Required third-party license directory is missing: $ThirdPartyLicenses"
}

Write-Host "[1/6] Preparing isolated Python 3.12 build environment..."
New-BuildVirtualEnvironment
Invoke-Native -FilePath $BuildPython -ArgumentList @('-c', 'import sys;sys.exit(not(sys.version_info[:2]==(3,12)and sys.maxsize>2**32))')

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

if (-not $SkipDependencyInstall) {
    Write-Host "[2/6] Installing pinned build and runtime dependencies..."
    Invoke-Native -FilePath $BuildPython -ArgumentList @('-m', 'pip', 'install', '--only-binary=:all:', '--requirement', $BuildRequirements)
    Invoke-Native -FilePath $BuildPython -ArgumentList @('-m', 'pip', 'install', '--only-binary=:all:', '--requirement', $ApplicationRequirements)
}
else {
    Write-Host "[2/6] Skipping dependency installation."
}

Write-Host "[3/6] Building the PyInstaller onedir application..."
Write-VersionResource
Remove-BuildDirectory -Path $PyInstallerWork
Remove-BuildDirectory -Path $BundleRoot
New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
$env:QB_BUILD_VERSION = $Version
Invoke-Native -FilePath $BuildPython -ArgumentList @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--distpath', $DistRoot,
    '--workpath', $PyInstallerWork,
    $SpecFile
)

Copy-Item -LiteralPath (Join-Path $PackagingRoot 'INSTALLATION-NOTICE.txt') -Destination $BundleRoot -Force
Copy-Item -LiteralPath (Join-Path $PackagingRoot 'THIRD_PARTY_NOTICES.txt') -Destination $BundleRoot -Force
Copy-Item -LiteralPath $ProjectLicense -Destination $BundleRoot -Force
Copy-Item -LiteralPath $ThirdPartyLicenses -Destination $BundleRoot -Recurse -Force
$strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)
$correspondingSourceTemplate = Join-Path $PackagingRoot 'CORRESPONDING_SOURCE.template.txt'
$correspondingSource = $strictUtf8.GetString(
    [System.IO.File]::ReadAllBytes($correspondingSourceTemplate)
).Replace('{{VERSION}}', $Version)
$correspondingSourcePath = Join-Path $BundleRoot 'CORRESPONDING_SOURCE.txt'
[System.IO.File]::WriteAllText($correspondingSourcePath, $correspondingSource, $strictUtf8)
$writtenCorrespondingSource = $strictUtf8.GetString(
    [System.IO.File]::ReadAllBytes($correspondingSourcePath)
)
if ($writtenCorrespondingSource -ne $correspondingSource -or
        $writtenCorrespondingSource -notmatch [regex]::Escape("releases/tag/v${Version}") -or
        $writtenCorrespondingSource -notmatch [regex]::Escape("question-bank-card-${Version}-source.zip")) {
    throw 'CORRESPONDING_SOURCE.txt failed its UTF-8 or version-substitution check.'
}

# The command line for AI assistants must start and report this version.
$cliExe = Join-Path $BundleRoot 'tiyouju.exe'
$cliVersion = (& $cliExe --version) -join ''
if ($LASTEXITCODE -ne 0 -or $cliVersion.Trim() -ne "tiyouju $Version") {
    throw "tiyouju.exe --version reported '$cliVersion' instead of 'tiyouju $Version'."
}

Write-Host "[4/6] Auditing the bundle for private data and runtime files..."
Invoke-Native -FilePath $BuildPython -ArgumentList @($AuditScript, '--bundle', $BundleRoot)

if ($SkipInstaller) {
    Write-Host "[5/6] Inno Setup skipped."
    Write-Host "[6/6] Complete: $BundleRoot"
    exit 0
}

Write-Host "[5/6] Building the per-user installer..."
$Iscc = Find-InnoCompiler
New-Item -ItemType Directory -Path $InstallerRoot -Force | Out-Null
Invoke-Native -FilePath $Iscc -ArgumentList @(
    "/DAppVersion=$Version",
    "/DSourceDir=$BundleRoot",
    "/DOutputDir=$InstallerRoot",
    (Join-Path $PackagingRoot 'installer.iss')
)

$installerItem = Get-ChildItem -LiteralPath $InstallerRoot -Filter "*Setup-$Version.exe" -File |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($null -eq $installerItem) {
    throw "Inno Setup did not create the expected installer for version $Version."
}
$InstallerPath = $installerItem.FullName
Write-Host "[6/6] Auditing the installer and generating SHA-256..."
Invoke-Native -FilePath $BuildPython -ArgumentList @($AuditScript, '--bundle', $BundleRoot, '--installer', $InstallerPath)
$hash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = Join-Path $InstallerRoot 'SHA256SUMS.txt'
$checksumLine = "$hash *$(Split-Path -Leaf $InstallerPath)`n"
[System.IO.File]::WriteAllText(
    $checksumPath,
    $checksumLine,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host ''
Write-Host 'Build complete:'
Write-Host "  Installer: $InstallerPath"
Write-Host "  SHA-256: $hash"
Write-Host "  Checksum: $checksumPath"
