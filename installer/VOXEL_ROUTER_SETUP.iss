; VOXEL ROUTER DESKTOP — instalador único Windows (Inno Setup 6)
; Pré-requisitos de build: vendor\orthanc\Orthanc.exe (+ plugins) e vendor\wireguard\wireguard-installer.msi
; homologados, conferidos por SHA-256 (e Authenticode no MSI) em scripts\build_windows.ps1.
#define AppName "VOXEL Router"
#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
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
; WireGuard for Windows oficial: instalado só se ainda não existir; nunca removido na desinstalação.
Source: "..\vendor\wireguard\wireguard-installer.msi"; DestDir: "{tmp}"; Flags: deleteafterinstall

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
Name: "{commonappdata}\VOXEL\Router\vpn"

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
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""VOXEL Router DICOM SCP"""; Flags: runhidden waituntilterminated
; Regra legada da 4243 (instalações anteriores à Fase 4).
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""VOXEL Orthanc DICOM"""; Flags: runhidden waituntilterminated

[Code]
var
  InstallationVerified: Boolean;
  OrthancInstallationWasValid: Boolean;

{ /SILENT e /VERYSILENT são tratados pelo próprio Inno Setup (WizardSilent()). }
function InitializeSetup(): Boolean;
begin
  InstallationVerified := False;
  Result := True;
end;

{ Usado em [Run] Check: o Dashboard só abre se a instalação foi verificada. }
function InstallVerified(): Boolean;
begin
  Result := InstallationVerified;
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

{ Porta TCP já escutando nesta máquina (netstat + findstr; ResultCode 0 = encontrou). }
function PortInUse(const Port: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/C netstat -ano -p TCP | findstr /R /C:":' + Port + ' .*LISTENING"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  Log('Porta ' + Port + ' em uso: ' + BoolText(Result));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  { Clínicas podem já ter outro PACS/Orthanc na máquina. Instalação nova não sobe em cima de portas
    ocupadas (o Router e o Orthanc local falhariam ao iniciar). Em atualização as portas são nossas. }
  if not ServiceExists('{#RouterServiceName}') and PortInUse('4242') then begin
    Result := 'A porta 4242/TCP (recepção DICOM do VOXEL Router) já está em uso por outro programa nesta máquina (outro PACS ou Orthanc?). Pare esse serviço ou use outra máquina e execute o instalador de novo.';
    exit;
  end;
  if not ServiceExists('{#OrthancServiceName}') and PortInUse('8042') then begin
    Result := 'A porta 8042/TCP (Orthanc local do VOXEL Router) já está em uso por outro programa nesta máquina (outro Orthanc?). Pare esse serviço ou use outra máquina e execute o instalador de novo.';
    exit;
  end;
  OrthancInstallationWasValid := OrthancInstallationIsValid();
  if OrthancInstallationWasValid then
    Log('Orthanc válido detectado: binário, storage e senha interna serão preservados; orthanc.json é regenerado.');
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

function WireGuardInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\WireGuard\wireguard.exe')) and FileExists(ExpandConstant('{commonpf64}\WireGuard\wg.exe'));
end;

procedure StopServiceIfInstalled(const ServiceName: String);
begin
  if ServiceExists(ServiceName) then begin
    ExecuteAndCheck('sc.exe', 'stop ' + ServiceName, 'Parar serviço para atualização ' + ServiceName);
  end;
end;

procedure LogExistingInstallation();
begin
  Log('Detecção WireGuard: ' + BoolText(WireGuardInstalled()));
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
  InstallationVerified := False;
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

    { Regenera orthanc.json também em atualizações: aplica DicomServerEnabled=false e REST só em
      loopback. A senha interna vem do cofre DPAPI e é reaproveitada; storage e índice não mudam. }
    if not RunCritical(ExpandConstant('{app}\{#OrthancServiceExeName}'), '--configure', 'Configurar Orthanc') then begin
      SetInstallationIncomplete();
      exit;
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
    if ServiceRunning('{#OrthancServiceName}') then begin
      { Orthanc preservado em execução: reinicia para carregar o orthanc.json regenerado. }
      if not RunCritical(ExpandConstant('{app}\{#OrthancServiceExeName}'), 'restart', 'Reiniciar VOXEL Orthanc') then begin
        SetInstallationIncomplete();
        exit;
      end;
    end else begin
      if not RunCritical('sc.exe', 'start {#OrthancServiceName}', 'Iniciar VOXEL Orthanc') then begin
        SetInstallationIncomplete();
        exit;
      end;
    end;
    if not RunCritical(ExpandConstant('{app}\{#DiagnosticsExeName}'), '--component orthanc', 'Verificar VOXEL Orthanc') then begin
      SetInstallationIncomplete();
      exit;
    end;

    { WireGuard for Windows (túnel VOXEL VPN). Instala silenciosamente só se ausente; o túnel em si é
      configurado depois, pela página VOXEL VPN do Router, com o kit do VOXEL PACS. }
    if not WireGuardInstalled() then begin
      if not RunCritical(ExpandConstant('{sys}\msiexec.exe'), '/i "' + ExpandConstant('{tmp}\wireguard-installer.msi') + '" /qn /norestart DO_NOT_LAUNCH=1', 'Instalar WireGuard for Windows') then begin
        SetInstallationIncomplete();
        exit;
      end;
    end;
    if not WireGuardInstalled() then begin
      Log('WireGuard não encontrado após a instalação.');
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

    { Firewall: só 4242/TCP de entrada, perfis Domínio e Privada (nunca Pública). A regra antiga da
      4243 (Orthanc DICOM, hoje desligado) é removida, inclusive em atualizações. Remover antes de
      criar evita regra duplicada ou com perfil antigo. A falha da exclusão (regra inexistente) só é registrada. }
    ExecuteAndCheck(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="VOXEL Orthanc DICOM"', 'Remover regra legada Orthanc 4243');
    ExecuteAndCheck(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name="VOXEL Router DICOM SCP"', 'Remover regra anterior Router 4242');
    if not RunCritical(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall add rule name="VOXEL Router DICOM SCP" dir=in action=allow protocol=TCP localport=4242 profile=domain,private', 'Configurar firewall Router 4242 (Domínio/Privada)') then begin
      SetInstallationIncomplete();
      exit;
    end;

    if not RunCritical(ExpandConstant('{app}\{#DiagnosticsExeName}'), '', 'Diagnóstico final Router e Orthanc') then begin
      SetInstallationIncomplete();
      exit;
    end;
    InstallationVerified := True;
  end;
end;

{ Nenhum diretório em ProgramData é removido na desinstalação normal. Estudos DICOM, índices,
  fila, logs e configurações permanecem disponíveis para atualização, recuperação ou rollback. }

{ Desinstalação: o WireGuard e outros túneis da máquina ficam. Só o túnel VOXELVPN é removido, e só se
  o operador confirmar (em desinstalação silenciosa ele é mantido). }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if (CurUninstallStep = usUninstall) and ServiceExists('WireGuardTunnel$VOXELVPN') and not UninstallSilent() then begin
    if MsgBox('Remover também o túnel VOXEL VPN (VOXELVPN)? O WireGuard continuará instalado.', mbConfirmation, MB_YESNO) = IDYES then
      Exec(ExpandConstant('{commonpf64}\WireGuard\wireguard.exe'), '/uninstalltunnelservice VOXELVPN', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;
