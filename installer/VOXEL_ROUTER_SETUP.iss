; VOXEL ROUTER DESKTOP — instalador Windows (Inno Setup 6)
; Pré-requisito de build: coloque Orthanc e seus plugins homologados em vendor\orthanc\.
#define AppName "VOXEL Router"
#define AppVersion "1.0.0"
#define AppPublisher "VOXEL"
#define AppExeName "VOXELRouter.exe"

[Setup]
AppId={{4970E917-108A-4F1A-AC47-9E3A1A005A12}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\VOXEL\Router
DefaultGroupName=VOXEL Router
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=..\dist
OutputBaseFilename=VOXEL_ROUTER_SETUP
SetupIconFile=..\frontend\static\img\router.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
WizardImageFile=..\frontend\static\img\splash.bmp
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\dist\VOXELRouter\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\dist\VOXELRouterService\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\vendor\orthanc\*"; DestDir: "{app}\orthanc"; Flags: recursesubdirs ignoreversion
Source: "..\config\default.json"; DestDir: "{commonappdata}\VOXEL\Router\config"; DestName: "router.json"; Flags: onlyifdoesntexist
Source: "..\frontend\static\img\router.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\VOXEL\Router\config"
Name: "{commonappdata}\VOXEL\Router\database"
Name: "{commonappdata}\VOXEL\Router\logs"
Name: "{commonappdata}\VOXEL\Router\storage"
Name: "{commonappdata}\VOXEL\Router\certificates"
Name: "{commonappdata}\VOXEL\Router\cache"
Name: "{commonappdata}\VOXEL\Router\backup"

[Icons]
Name: "{group}\VOXEL Router"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\router.ico"
Name: "{autodesktop}\VOXEL Router"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon; IconFilename: "{app}\assets\router.ico"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos adicionais:"

[Run]
Filename: "{app}\VOXELRouterService.exe"; Parameters: "--startup auto install"; Flags: runhidden waituntilterminated
Filename: "sc.exe"; Parameters: "failure VOXELRouterEngine reset= 86400 actions= restart/60000/restart/60000/restart/120000"; Flags: runhidden waituntilterminated
Filename: "sc.exe"; Parameters: "start VOXELRouterEngine"; Flags: runhidden waituntilterminated
Filename: "{app}\orthanc\Orthanc.exe"; Parameters: "--install --name \"VOXEL Orthanc Service\" \"{commonappdata}\VOXEL\Router\config\orthanc.json\""; Flags: runhidden waituntilterminated; Check: FileExists(ExpandConstant('{app}\orthanc\Orthanc.exe'))
Filename: "netsh.exe"; Parameters: "advfirewall firewall add rule name=\"VOXEL Router DICOM SCP\" dir=in action=allow protocol=TCP localport=4242 profile=private"; Flags: runhidden waituntilterminated
Filename: "{app}\{#AppExeName}"; Description: "Abrir VOXEL Router"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "sc.exe"; Parameters: "stop VOXELRouterEngine"; Flags: runhidden waituntilterminated
Filename: "{app}\VOXELRouterService.exe"; Parameters: "remove"; Flags: runhidden waituntilterminated; Check: FileExists(ExpandConstant('{app}\VOXELRouterService.exe'))
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=\"VOXEL Router DICOM SCP\""; Flags: runhidden waituntilterminated

[Code]
var
  DeleteDataPage: TInputOptionWizardPage;

function CmdLineHasSilentFlag(): Boolean;
begin
  Result := CmdLineParamExists('/S') or CmdLineParamExists('/VERYSILENT') or CmdLineParamExists('/SILENT');
end;

function InitializeSetup(): Boolean;
begin
  if CmdLineHasSilentFlag() then begin
    WizardSilent := True;
    Silent := True;
  end;
  Result := True;
end;

procedure InitializeWizard();
begin
  DeleteDataPage := CreateInputOptionPage(wpSelectTasks,
    'Dados locais', 'Escolha o comportamento de desinstalação',
    'Na desinstalação, dados de estudos nunca serão excluídos sem confirmação explícita.', True, False);
  DeleteDataPage.Add('Manter dados operacionais locais (recomendado)');
  DeleteDataPage.Add('Excluir dados locais durante a desinstalação');
  DeleteDataPage.SelectedValueIndex := 0;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then begin
    if DeleteDataPage.SelectedValueIndex = 1 then begin
      DelTree(ExpandConstant('{commonappdata}\VOXEL\Router'), True, True, True);
    end;
  end;
end;
