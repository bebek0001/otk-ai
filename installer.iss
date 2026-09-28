
#define AppName "OTK AI"
#define AppVersion "1.0.0"
#define AppExe "OTK AI.exe"

[Setup]
AppId={{CFC428B3-1555-4741-B63E-73AA4E19CE21}
AppName={#AppName}
AppVersion={#AppVersion}

DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}

OutputDir=installer
OutputBaseFilename=OTK_AI_Setup

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
Name: "{autoprograms}\OTK AI"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\OTK AI"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить OTK AI"; Flags: nowait postinstall skipifsilent
