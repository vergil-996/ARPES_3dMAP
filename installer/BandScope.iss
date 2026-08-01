; BandScope per-user installer.
; Build examples:
;   ISCC.exe /DAppVersion=1.1.1 /DBuildFlavor=CPU BandScope.iss
;   ISCC.exe /DAppVersion=1.1.1 /DBuildFlavor=NVIDIA BandScope.iss

#ifndef AppVersion
  #define AppVersion "1.1.1"
#endif

#ifndef BuildFlavor
  #define BuildFlavor "CPU"
#endif

#if BuildFlavor == "NVIDIA"
  #define AppExeName "BandScope_NVIDIA.exe"
  #define SourceDir "..\dist\BandScope_NVIDIA"
#else
  #define AppExeName "BandScope.exe"
  #define SourceDir "..\dist\BandScope"
#endif

[Setup]
AppId={{C27AF165-8849-4704-A56F-69C63C6C2821}
AppName=BandScope
AppVersion={#AppVersion}
AppVerName=BandScope {#AppVersion} ({#BuildFlavor})
AppPublisher=vergil-996
AppPublisherURL=https://github.com/vergil-996/ARPES_3dMAP
AppSupportURL=https://github.com/vergil-996/ARPES_3dMAP/issues
AppUpdatesURL=https://github.com/vergil-996/ARPES_3dMAP/releases
DefaultDirName={localappdata}\Programs\BandScope
DefaultGroupName=BandScope
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=BandScope-{#AppVersion}-Windows-x64-{#BuildFlavor}-Setup
SetupIconFile=..\app.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName=BandScope
VersionInfoProductVersion={#AppVersion}.0
VersionInfoCompany=vergil-996
VersionInfoDescription=BandScope multidimensional ARPES visualization and analysis workbench

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[InstallDelete]
; PyInstaller onedir dependencies live below _internal.  Removing that exact
; generated directory prevents stale CPU/GPU libraries surviving a flavor
; switch while leaving user-created files elsewhere untouched.
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\BandScope.exe"
Type: files; Name: "{app}\BandScope_NVIDIA.exe"
Type: files; Name: "{app}\ARPES_3dMAP_v2.exe"
Type: files; Name: "{app}\ARPES_3dMAP_v2_NVIDIA.exe"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\BandScope"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\BandScope"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 BandScope"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
