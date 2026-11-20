@echo off
REM VOXEL Router - atualiza do GitHub e gera o instalador. Nao precisa de administrador.
REM Commits locais da branch ficam salvos numa branch backup-local-* antes da atualizacao.
cd /d "%~dp0"
set LOG=%TEMP%\voxel-router-precheck.log
echo ==== Pre-check %date% %time% ==== > "%LOG%"
echo [servicos] >> "%LOG%"
for %%S in (VOXELRouter VOXELOrthanc Orthanc "WireGuardTunnel$VOXELVPN") do (sc query %%S >> "%LOG%" 2>&1)
echo [portas 4242 8042 8765 11190 104] >> "%LOG%"
netstat -ano | findstr /R /C:":4242 " /C:":8042 " /C:":8765 " /C:":11190 " /C:":104 " >> "%LOG%" 2>&1
echo [git] >> "%LOG%"
git fetch origin >> "%LOG%" 2>&1
if errorlevel 1 goto falhou
for /f %%H in ('git rev-parse --short HEAD') do set ANTES=%%H
git update-ref refs/heads/backup-local-build refs/heads/feature/router-fases-5-8 >> "%LOG%" 2>&1
echo HEAD antes: %ANTES% (branch local anterior salva em backup-local-build) >> "%LOG%"
git checkout -f -B feature/router-fases-5-8 origin/feature/router-fases-5-8 >> "%LOG%" 2>&1
if errorlevel 1 goto falhou
git log --oneline -1 >> "%LOG%" 2>&1
if not exist logs-local mkdir logs-local
copy /y "%LOG%" logs-local\pre-check.log >nul
echo Codigo atualizado. Pre-check em logs-local\pre-check.log
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_local_test.ps1 -Yes
echo.
echo Fim. Exit code: %ERRORLEVEL%
echo %ERRORLEVEL% > logs-local\build-exit-code.txt
pause
exit /b 0

:falhou
if not exist logs-local mkdir logs-local
copy /y "%LOG%" logs-local\pre-check-falha.log >nul
echo.
echo FALHOU ao atualizar o codigo. Veja logs-local\pre-check-falha.log
pause
exit /b 1
