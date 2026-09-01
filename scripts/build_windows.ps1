# Requires: Windows PowerShell 5.1+, Python 3.12 x64, Inno Setup 6 and vendor\orthanc homologado.
# Execute em uma estação Windows de build: powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ($Clean) { Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue }
if ((Get-Command py -ErrorAction SilentlyContinue) -eq $null) { throw 'Python Launcher não encontrado. Instale Python 3.12 x64 no ambiente de build.' }

py -3.12 -m pip install --upgrade pip
py -3.12 -m pip install ".[dev]" pyinstaller pillow
py -3.12 scripts\generate_assets.py

# Para gerar config Orthanc em build de desenvolvimento, use chave Fernet transitória; produção usa DPAPI no destino.
$env:VOXEL_ROUTER_DATA_DIR = Join-Path $Root 'build\bootstrap-data'
$env:VOXEL_ROUTER_DEV_SECRET_KEY = py -3.12 -c "from app.security.secrets import create_development_key; print(create_development_key())"
py -3.12 scripts\configure_orthanc.py

py -3.12 -m PyInstaller --noconfirm --clean --name VOXELRouter --icon frontend\static\img\router.ico --add-data "frontend;frontend" --add-data "config;config" app\main.py
py -3.12 -m PyInstaller --noconfirm --clean --name VOXELRouterService --icon frontend\static\img\router.ico --add-data "config;config" app\service_main.py

if (-not $SkipInstaller) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "${env:ProgramFiles}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw 'Inno Setup 6 não encontrado.' }
    & $iscc installer\VOXEL_ROUTER_SETUP.iss
}

Write-Host 'Build concluído. Artefato esperado: dist\VOXEL_ROUTER_SETUP.exe'
