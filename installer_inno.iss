#define MyAppName "V CLI"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "V CLI"
#define MyAppExeName "V-CLI.exe"
#define MyAppAssocName MyAppName + " Projeto"

#ifndef AppSourceDir
  #define AppSourceDir "build_nuitka\release\V-CLI"
#endif

[Setup]
AppId={{8B623742-7065-49B5-B783-FA0213F9FEA4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\V CLI
DefaultGroupName=V CLI
AllowNoIcons=yes
LicenseFile={#AppSourceDir}\LICENSE.txt
OutputBaseFilename=V-CLI-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
SetupIconFile=build_nuitka\vcli_icon.ico
UninstallDisplayIcon={app}\vcli_icon.ico

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\V CLI"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Prompt de Comando V CLI"; Filename: "{cmd}"; Parameters: "/K cd /d ""{app}"""
Name: "{autodesktop}\V CLI"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir V CLI"; Flags: nowait postinstall skipifsilent
