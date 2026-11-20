# Build + testes locais do VOXEL Router numa máquina Windows de desenvolvimento/homologação.
# Baixa os componentes oficiais de terceiros (versões fixas), mostra os SHA-256 para homologação,
# roda a suíte de testes no Windows e gera o instalador via scripts\build_windows.ps1.
# Uso (PowerShell normal, NÃO precisa ser administrador):
#   powershell -ExecutionPolicy Bypass -File .\scripts\build_local_test.ps1
# Log completo: logs-local\build-local-<data>.log
[CmdletBinding()]
param(
    [string]$OrthancVersion = '1.13.0',
    [string]$WireGuardVersion = '1.1',
    [switch]$SkipTests,
    [switch]$Yes
)

# 'Continue': comandos nativos escrevem avisos no stderr; cada passo confere o exit code explicitamente.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force -Path logs-local | Out-Null
$Log = Join-Path $Root ("logs-local\build-local-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
Start-Transcript -Path $Log | Out-Null

function Step([string]$Text) { Write-Host ''; Write-Host "==> $Text" -ForegroundColor Cyan }
function Fail([string]$Text) { Write-Host "FALHOU: $Text" -ForegroundColor Red; Stop-Transcript | Out-Null; exit 1 }
# Executa um programa externo com a saída passando pelo pipeline (fica no log) e devolve o exit code.
function Native([string]$Exe, [string[]]$Arguments) { & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host; return $LASTEXITCODE }

try {
    Step 'Pré-requisitos'
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { Fail 'Python Launcher (py) ausente. Instale: winget install -e --id Python.Python.3.12' }
    if ((Native 'py' @('-3.12', '-c', 'import sys; print(sys.version)')) -ne 0) { Fail 'Python 3.12 x64 ausente. Instale: winget install -e --id Python.Python.3.12' }
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "${env:ProgramFiles}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { Fail 'Inno Setup 6 ausente. Instale: winget install -e --id JRSoftware.InnoSetup' }
    Write-Host "Inno Setup: $iscc"
    Write-Host "Branch: $(git rev-parse --abbrev-ref HEAD 2>$null) @ $(git rev-parse --short HEAD 2>$null)"

    Step "Baixar Orthanc $OrthancVersion (oficial, orthanc.uclouvain.be)"
    New-Item -ItemType Directory -Force -Path vendor\orthanc, vendor\wireguard | Out-Null
    $orthanc = Join-Path $Root 'vendor\orthanc\Orthanc.exe'
    if (-not (Test-Path $orthanc)) {
        Invoke-WebRequest -ErrorAction Stop -UseBasicParsing -Uri "https://orthanc.uclouvain.be/downloads/windows-64/orthanc/$OrthancVersion/Orthanc.exe" -OutFile $orthanc
    } else { Write-Host 'Já existe; reaproveitado.' }

    Step "Baixar WireGuard for Windows $WireGuardVersion (MSI oficial, download.wireguard.com)"
    $msi = Join-Path $Root 'vendor\wireguard\wireguard-installer.msi'
    if (-not (Test-Path $msi)) {
        Invoke-WebRequest -ErrorAction Stop -UseBasicParsing -Uri "https://download.wireguard.com/windows-client/wireguard-amd64-$WireGuardVersion.msi" -OutFile $msi
    } else { Write-Host 'Já existe; reaproveitado.' }
    $signature = Get-AuthenticodeSignature -FilePath $msi
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'WireGuard') { Fail "Assinatura do MSI do WireGuard inválida: $($signature.Status)" }
    Write-Host "MSI assinado por: $($signature.SignerCertificate.Subject)"

    $orthancHash = (Get-FileHash -Algorithm SHA256 $orthanc).Hash
    $msiHash = (Get-FileHash -Algorithm SHA256 $msi).Hash
    $record = @(
        "Homologação local $(Get-Date -Format s)",
        "Orthanc.exe $OrthancVersion  SHA256 $orthancHash  (orthanc.uclouvain.be/downloads/windows-64/orthanc/$OrthancVersion/)",
        "wireguard-amd64-$WireGuardVersion.msi  SHA256 $msiHash  (download.wireguard.com/windows-client/)  assinado: $($signature.SignerCertificate.Subject)"
    )
    $record | Out-File -Encoding utf8 vendor\HASHES-homologacao.txt
    Step 'SHA-256 dos componentes (registre na homologação)'
    $record | ForEach-Object { Write-Host $_ }
    if (-not $Yes) {
        $answer = Read-Host 'Aprovar estes componentes para o build? (S/N)'
        if ($answer -notmatch '^[sS]') { Fail 'Build cancelado pelo operador.' }
    }

    if (-not $SkipTests) {
        Step 'Testes no Windows (pytest + ruff)'
        $venv = Join-Path $Root '.venv-test'
        if (-not (Test-Path "$venv\Scripts\python.exe")) { if ((Native 'py' @('-3.12', '-m', 'venv', $venv)) -ne 0) { Fail 'venv de testes' } }
        $py = "$venv\Scripts\python.exe"
        if ((Native $py @('-m', 'pip', 'install', '--quiet', '--upgrade', 'pip')) -ne 0) { Fail 'pip' }
        if ((Native $py @('-m', 'pip', 'install', '--quiet', '-c', 'requirements-build.txt', '-e', '.[dev]')) -ne 0) { Fail 'dependências de teste' }
        if ((Native $py @('-m', 'ruff', 'check', 'app', 'scripts', '--select', 'F,E9')) -ne 0) { Fail 'ruff' }
        if ((Native $py @('-m', 'pytest', '-q', '-p', 'no:cacheprovider')) -ne 0) { Fail 'pytest (veja o log)' }
    }

    Step 'Gerar instalador'
    $build = Join-Path $PSScriptRoot 'build_windows.ps1'
    if ((Native 'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $build, '-OrthancSha256', $orthancHash, '-WireGuardSha256', $msiHash)) -ne 0) { Fail 'build_windows.ps1 (veja o log)' }

    Step 'Concluído'
    Get-Content dist\VOXEL_ROUTER_SETUP.exe.sha256
    Write-Host "Instalador: $Root\dist\VOXEL_ROUTER_SETUP.exe"
    Write-Host 'Próximo passo: docs\teste-local-server-dell.md'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Stop-Transcript | Out-Null
    exit 1
}
Stop-Transcript | Out-Null
exit 0
