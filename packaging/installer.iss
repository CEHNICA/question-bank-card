#define MyAppName "题有据"
#define MyLegacyAppName "题库题卡版"
#define MyAppExeName "QuestionBankCard.exe"

#ifndef AppVersion
  #define AppVersion "1.3.0"
#endif

#ifndef SourceDir
  #error SourceDir must point to the audited PyInstaller onedir bundle.
#endif

#ifndef OutputDir
  #define OutputDir "dist\installer"
#endif

[Setup]
AppId={{8C1BC21C-A8B7-4E81-9E25-59032D0967D8}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppVerName={#MyAppName} {#AppVersion}
AppPublisher=CEHNICA
AppPublisherURL=https://github.com/CEHNICA/question-bank-card
AppSupportURL=https://github.com/CEHNICA/question-bank-card/issues
AppUpdatesURL=https://github.com/CEHNICA/question-bank-card/releases
DefaultDirName={localappdata}\Programs\QuestionBankCard
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=TiYouJu-Setup-{#AppVersion}
SetupIconFile=..\assets\app.ico
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
; 品牌改名后不要沿用旧版“题库题卡版”的开始菜单分组。
UsePreviousGroup=no
ChangesAssociations=no
ChangesEnvironment=no
AllowNetworkDrive=no
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoCompany=CEHNICA
VersionInfoCopyright=Copyright (c) 2026 CEHNICA and contributors

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Dirs]
; 用户数据独立于程序目录，卸载或覆盖安装都不会删除。
Name: "{localappdata}\QuestionBankCard"; Flags: uninsneveruninstall

[InstallDelete]
; 升级前移除旧的 PyInstaller 运行时，避免已删除的模块残留。
; 绝不触碰 {localappdata}\QuestionBankCard 下的题库和用户文件。
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\{#MyAppExeName}"
Type: files; Name: "{app}\tiyouju.exe"
Type: files; Name: "{app}\INSTALLATION-NOTICE.txt"
Type: files; Name: "{app}\THIRD_PARTY_NOTICES.txt"
Type: files; Name: "{app}\LICENSE"
Type: filesandordirs; Name: "{app}\THIRD_PARTY_LICENSES"
Type: files; Name: "{app}\CORRESPONDING_SOURCE.txt"
; 同一 AppId 覆盖升级时清理旧品牌快捷方式，用户数据和程序内部路径保持不变。
Type: files; Name: "{userdesktop}\{#MyLegacyAppName}.lnk"
; 源码版创建器曾把快捷方式直接放在开始菜单根目录。
Type: files; Name: "{userprograms}\{#MyLegacyAppName}.lnk"
; v1.1.0 安装器在旧品牌分组内创建的三个快捷方式。
Type: files; Name: "{userprograms}\{#MyLegacyAppName}\{#MyLegacyAppName}.lnk"
Type: files; Name: "{userprograms}\{#MyLegacyAppName}\配置 API.lnk"
Type: files; Name: "{userprograms}\{#MyLegacyAppName}\卸载 {#MyLegacyAppName}.lnk"
; v1.2.0 曾在新品牌分组中暴露独立配置程序；升级后收回软件内设置。
Type: files; Name: "{group}\配置 API.lnk"
; 只有旧分组已经为空时才移除目录，保留用户自行放入的其他内容。
Type: dirifempty; Name: "{userprograms}\{#MyLegacyAppName}"

[Files]
; SourceDir 必须在 build.ps1 中先经 audit_bundle.py 审计。
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
