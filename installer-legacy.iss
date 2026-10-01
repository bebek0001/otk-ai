
#define AppName "OTK AI (старые ПК)"
#define AppVersion "1.0.0"
#define AppExe "OTK AI.exe"

[Setup]
; Отдельный, собственный AppId — ставится независимо от основной версии,
; обе могут быть установлены на одном компьютере одновременно для сравнения.
AppId={{5E649CB7-D636-4D50-A80A-BD80E5A80380}
AppName={#AppName}
AppVersion={#AppVersion}

DefaultDirName={localappdata}\Programs\OTK AI (Legacy)
DefaultGroupName={#AppName}

OutputDir=installer
OutputBaseFilename=OTK_AI_Setup_LegacyCPU

Compression=lzma2
SolidCompression=yes
WizardStyle=modern

PrivilegesRequired=lowest
UninstallDisplayName={#AppName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные задачи:"

[Files]
Source: "dist\OTK AI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить OTK AI"; Flags: nowait postinstall skipifsilent
