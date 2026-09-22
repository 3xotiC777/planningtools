@echo off
setlocal EnableExtensions
REM Cada usuario copia el ejecutable desde SU carpeta sincronizada a AppData.
REM El codigo no depende de un entorno Python compartido ni de SharePoint API.
cd /d "%~dp0"

if not exist "%~dp0Iniciar Planning Tools.ps1" (
    echo No se encontro "Iniciar Planning Tools.ps1" en esta carpeta.
    echo Espere a que la carpeta sincronizada termine de descargar.
    pause
    exit /b 1
)

set "APP_ARGS=%*"
if /I "%~1"=="--diagnostico" set "APP_ARGS=-Diagnostico"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Iniciar Planning Tools.ps1" %APP_ARGS%
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" pause
exit /b %APP_EXIT%
