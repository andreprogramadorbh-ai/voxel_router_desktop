; VOXEL ROUTER DESKTOP — instalador único Windows (Inno Setup 6)
; Pré-requisito de build: vendor\orthanc\Orthanc.exe e plugins homologados.
#define AppName "VOXEL Router"
#define AppVersion "1.0.0"
#define AppPublisher "VOXEL"
#define RouterExeName "VOXELRouter.exe"
#define RouterServiceExeName "VOXELRouterService.exe"
#define OrthancServiceExeName "VOXELOrthancService.exe"
#define DiagnosticsExeName "VOXELDiagnostics.exe"
#define RouterServiceName "VOXELRouter"
#define OrthancServiceName "VOXELOrthanc"

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
UninstallDisplayIcon={app}\{#RouterExeName}
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
Source: "..\dist\VOXELOrthancService\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\dist\VOXELDiagnostics\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "..\vendor\orthanc\*"; DestDir: "{app}\orthanc"; Flags: recursesubdirs ignoreversion; Check: not OrthancInstallationWasValid
Source: "..\config\default.json"; DestDir: "{commonappdata}\VOXEL\Router\config"; DestName: "router.json"; Flags: onlyifdoesntexist
Source: "..\frontend\static\img\router.ico"; DestDir: "{app}\assets"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\VOXEL\Router\config"
Name: "{commonappdata}\VOXEL\Router\database"
Name: "{commonappdata}\VOXEL\Router\logs"
Name: "{commonappdata}\VOXEL\Router\queue"
Name: "{commonappdata}\VOXEL\Router\storage"
Name: "{commonappdata}\VOXEL\Router\orthanc"
Name: "{commonappdata}\VOXEL\Router\orthanc\storage"
Name: "{commonappdata}\VOXEL\Router\orthanc\database"
Name: "{commonappdata}\VOXEL\Router\certificates"
Name: "{commonappdata}\VOXEL\Router\cache"
Name: "{commonappdata}\VOXEL\Router\backup"

[Icons]
Name: "{group}\VOXEL Router"; Filename: "http://127.0.0.1:8765"; IconFilename: "{app}\assets\router.ico"
Name: "{autodesktop}\VOXEL Router"; Filename: "http://127.0.0.1:8765"; Tasks: desktopicon; IconFilename: "{app}\assets\router.ico"
Name: "{group}\Diagnóstico de instalação"; Filename: "{app}\{#DiagnosticsExeName}"; IconFilename: "{app}\assets\router.ico"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos adicionais:"

[Run]
Filename: "http://127.0.0.1:8765"; Description: "Abrir Dashboard do VOXEL Router"; Flags: shellexec nowait postinstall skipifsilent; Check: InstallVerified

[UninstallRun]
Filename: "sc.exe"; Parameters: "stop {#RouterServiceName}"; Flags: runhidden waituntilterminated
Filename: "sc.exe"; Parameters: "stop {#OrthancServiceName}"; Flags: runhidden waituntilterminated
Filename: "{app}\{#RouterServiceExeName}"; Parameters: "remove"; Flags: runhidden waituntilterminated; Check: FileExists(ExpandConstant('{app}\{#RouterServiceExeName}'))
Filename: "{app}\{#OrthancServiceExeName}"; Parameters: "remove"; Flags: runhidden waituntilterminated; Check: FileExists(ExpandConstant('{app}\{#OrthancServiceExeName}'))
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=\"VOXEL Router DICOM SCP\""; Flags: runhidden waituntilterminated
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=\"VOXEL Orthanc DICOM\""; Flags: runhidden waituntilterminated

[Code]
var
  InstallVerified: Boolean;
  OrthancInstallationWasValid: Boolean;

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
  InstallVerified := False;
  Result := True;
end;

function ExecuteAndCheck(const Filename, Parameters, Description: String): Boolean;
var
  ResultCode: Integer;
begin
  Log(Description + ': ' + Filename + ' ' + Parameters);
  Result := Exec(Filename, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  if not Result then Log(Description + ' falhou com código ' + IntToStr(ResultCode));
end;

function BoolText(const Value: Boolean): String;
begin
  if Value then Result := 'True' else Result := 'False';
end;

function ServiceExists(const ServiceName: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c sc.exe query ' + ServiceName + ' >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function ServiceRunning(const ServiceName: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c sc.exe query ' + ServiceName +
    ' | find "RUNNING" >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function OrthancInstallationIsValid(): Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{app}\orthanc\Orthanc.exe')) and
    FileExists(ExpandConstant('{commonappdata}\VOXEL\Router\config\orthanc.json')) and
    ServiceExists('{#OrthancServiceName}');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  OrthancInstallationWasValid := OrthancInstallationIsValid();
  if OrthancInstallationWasValid then
    Log('Orthanc válido detectado: binário, configuração e serviço em execução serão preservados.');
  Result := '';
end;

function RunCritical(const Filename, Parameters, Component: String): Boolean;
var
  Choice: Integer;
  ResultCode: Integer;
begin
  while True do begin
    if Exec(Filename, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then begin
      Result := True;
      exit;
    end;
    Log(Component + ' falhou com código ' + IntToStr(ResultCode));
    Choice := MsgBox(
      Component + ': FALHA.' + #13#10#13#10 +
      'Sim: Tentar novamente.' + #13#10 +
      'Não: Executar diagnóstico e continuar sem declarar a instalação concluída.' + #13#10 +
      'Cancelar: Encerrar o instalador.',
      mbError, MB_YESNOCANCEL);
    if Choice = IDYES then begin
      continue;
    end;
    if Choice = IDNO then begin
      Exec(ExpandConstant('{app}\{#DiagnosticsExeName}'), '', '', SW_SHOW, ewNoWait, ResultCode);
      Result := False;
      exit;
    end;
    Abort;
  end;
end;

procedure StopServiceIfInstalled(const ServiceName: String);
begin
  if ServiceExists(ServiceName) then begin
    ExecuteAndCheck('sc.exe', 'stop ' + ServiceName, 'Parar serviço para atualização ' + ServiceName);
  end;
end;

procedure LogExistingInstallation();
begin
  Log('Detecção Router: ' + BoolText(FileExists(ExpandConstant('{app}\{#RouterExeName}'))));
  Log('Detecção Orthanc: ' + BoolText(FileExists(ExpandConstant('{app}\orthanc\Orthanc.exe'))));
  Log('Detecção configuração: ' + BoolText(FileExists(ExpandConstant('{commonappdata}\VOXEL\Router\config\orthanc.json'))));
  Log('Detecção storage: ' + BoolText(DirExists(ExpandConstant('{commonappdata}\VOXEL\Router\orthanc\storage'))));
  Log('Detecção serviço Router: ' + BoolText(ServiceExists('{#RouterServiceName}')));
  Log('Detecção serviço Orthanc: ' + BoolText(ServiceExists('{#OrthancServiceName}')));
  Log('Orthanc válido e preservado: ' + BoolText(OrthancInstallationWasValid));
end;

procedure SetInstallationIncomplete();
begin
  InstallVerified := False;
  WizardForm.FinishedLabel.Caption :=
    'A instalação requer diagnóstico adicional. O Dashboard não será aberto automaticamente. ' +
    'Use o atalho Diagnóstico de instalação depois de corrigir a falha do componente informado.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then begin
    LogExistingInstallation();
    StopServiceIfInstalled('{#RouterServiceName}');
    if not OrthancInstallationWasValid then
      StopServiceIfInstalled('{#OrthancServiceName}');
  end;

  if CurStep = ssPostInstall then begin
    { Router instalado e Orthanc instalado pelo mesmo VOXEL_ROUTER_SETUP.exe. }
    if not FileExists(ExpandConstant('{app}\{#RouterExeName}')) then begin
      SetInstallationIncomplete();
      exit;
    end;
    if not FileExists(ExpandConstant('{app}\orthanc\Orthanc.exe')) then begin
      SetInstallationIncomplete();
      exit;
    end;

    { Preserva orthanc.json existente em updates; cria somente na primeira instalação. }
    if not FileExists(ExpandConstant('{commonappdata}\VOXEL\Router\config\orthanc.json')) then begin
      if not RunCritical(ExpandConstant('{app}\{#OrthancServiceExeName}'), '--configure', 'Configurar Orthanc') then begin
        SetInstallationIncomplete();
        exit;
      end;
    end;

    if not ServiceExists('{#OrthancServiceName}') then begin
      if not RunCritical(ExpandConstant('{app}\{#OrthancServiceExeName}'), '--startup auto install', 'Instalar serviço VOXEL Orthanc') then begin
        SetInstallationIncomplete();
        exit;
      end;
    end;
    if not RunCritical('sc.exe', 'failure {#OrthancServiceName} reset= 86400 actions= restart/60000/restart/60000/restart/120000', 'Configurar recuperação VOXEL Orthanc') then begin
      SetInstallationIncomplete();
      exit;
    end;
    if not ServiceRunning('{#OrthancServiceName}') then begin
      if not RunCritical('sc.exe', 'start {#OrthancServiceName}', 'Iniciar VOXEL Orthanc') then begin
        SetInstallationIncomplete();
        exit;
      end;
    end;
    if not RunCritical(ExpandConstant('{app}\{#DiagnosticsExeName}'), '--component orthanc', 'Verificar VOXEL Orthanc') then begin
      SetInstallationIncomplete();
      exit;
    end;

    if not ServiceExists('{#RouterServiceName}') then begin
      if not RunCritical(ExpandConstant('{app}\{#RouterServiceExeName}'), '--startup auto install', 'Instalar serviço VOXEL Router') then begin
        SetInstallationIncomplete();
        exit;
      end;
    end;
    if not RunCritical('sc.exe', 'failure {#RouterServiceName} reset= 86400 actions= restart/60000/restart/60000/restart/120000', 'Configurar recuperação VOXEL Router') then begin
      SetInstallationIncomplete();
      exit;
    end;
    if not RunCritical('sc.exe', 'start {#RouterServiceName}', 'Iniciar VOXEL Router') then begin
      SetInstallationIncomplete();
      exit;
    end;
    if not RunCritical(ExpandConstant('{app}\{#DiagnosticsExeName}'), '--component router', 'Verificar VOXEL Router') then begin
      SetInstallationIncomplete();
      exit;
    end;

    ExecuteAndCheck('netsh.exe', 'advfirewall firewall add rule name=\"VOXEL Router DICOM SCP\" dir=in action=allow protocol=TCP localport=4242 profile=private', 'Configurar firewall Router');
    ExecuteAndCheck('netsh.exe', 'advfirewall firewall add rule name=\"VOXEL Orthanc DICOM\" dir=in action=allow protocol=TCP localport=4243 profile=private', 'Configurar firewall Orthanc');

    if not RunCritical(ExpandConstant('{app}\{#DiagnosticsExeName}'), '', 'Diagnóstico final Router e Orthanc') then begin
      SetInstallationIncomplete();
      exit;
    end;
    InstallVerified := True;
  end;
end;

{ Nenhum diretório em ProgramData é removido na desinstalação normal. Estudos DICOM, índices,
  fila, logs e configurações permanecem disponíveis para atualização, recuperação ou rollback. }
