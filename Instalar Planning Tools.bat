@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP_HOME=%LOCALAPPDATA%\DichterNeira\PlanningTools"
set "VENV_DIR=%APP_HOME%\venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if /I "%~1"=="--diagnostico" (
    echo INSTALADOR_DIAGNOSTICO_OK
    echo Carpeta compartida: "%~dp0"
    echo Entorno del usuario: %VENV_DIR%
    if exist "%VENV_PY%" (
        "%VENV_PY%" --version
        "%VENV_PY%" -c "import customtkinter, geopandas, numpy, openpyxl, ortools, pandas, PIL, pyogrio, scipy, shapely; print('DEPENDENCIAS_OK')"
        if errorlevel 1 exit /b 1
        exit /b 0
    )
    echo ENTORNO_NO_INSTALADO
    exit /b 1
)

echo ============================================================
echo   Planning Tools v1.2.0 - Instalador
echo ============================================================
echo Usuario: %USERNAME%
echo Entorno local: %VENV_DIR%
echo.

if not exist "%~dp0requirements.txt" (
    echo No se encontro requirements.txt en la carpeta compartida.
    echo Marque la carpeta PlanningTools como disponible sin conexion.
    goto :error
)

set "PY_EXE="
set "PY_ARGS="

py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_EXE=py"
    set "PY_ARGS=-3.12"
    goto :crear_entorno
)

py -3.11 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_EXE=py"
    set "PY_ARGS=-3.11"
    goto :crear_entorno
)

py -3.13 --version >nul 2>&1
if not errorlevel 1 (
    set "PY_EXE=py"
    set "PY_ARGS=-3.13"
    goto :crear_entorno
)

echo Python no esta instalado. Se intentara instalar Python 3.12 con winget.
where winget >nul 2>&1
if errorlevel 1 (
    echo.
    echo No se encontro winget. Instale Python 3.12 desde:
    echo https://www.python.org/downloads/windows/
    echo Marque la opcion "Add python.exe to PATH" y vuelva a ejecutar este instalador.
    goto :error
)

winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :error

set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY_EXE%" (
    echo Python fue instalado, pero Windows todavia no actualizo la ruta.
    echo Cierre esta ventana y ejecute nuevamente el instalador.
    goto :error
)

:crear_entorno
if not exist "%APP_HOME%" mkdir "%APP_HOME%"
if errorlevel 1 (
    echo No se pudo crear la carpeta local %APP_HOME%.
    goto :error
)

set "VENV_OK="
if exist "%VENV_PY%" (
    "%VENV_PY%" --version >nul 2>&1
    if not errorlevel 1 set "VENV_OK=1"
)
if not defined VENV_OK (
    if exist "%VENV_DIR%" (
        echo Reparando el entorno local de este usuario...
        rmdir /s /q "%VENV_DIR%"
    )
    echo Creando entorno privado fuera de la carpeta compartida...
    "%PY_EXE%" %PY_ARGS% -m venv "%VENV_DIR%"
    if errorlevel 1 goto :error
)

echo Verificando el instalador de paquetes...
"%VENV_PY%" -m pip --version
if errorlevel 1 goto :error

echo Instalando dependencias de Planning Tools...
"%VENV_PY%" -m pip install --disable-pip-version-check --only-binary=:all: --timeout 30 --retries 2 -r "%~dp0requirements.txt"
if errorlevel 1 goto :error

echo Verificando dependencias...
"%VENV_PY%" -c "import customtkinter, geopandas, numpy, openpyxl, ortools, pandas, PIL, pyogrio, scipy, shapely"
if errorlevel 1 goto :error

echo.
echo Instalacion finalizada correctamente.
echo El entorno pertenece unicamente al usuario %USERNAME%.
echo Use "Ejecutar Planning Tools.bat" para abrir la aplicacion.
if /I not "%~1"=="--silencioso" pause
exit /b 0

:error
echo.
echo La instalacion no pudo completarse. Revise los mensajes anteriores.
if /I not "%~1"=="--silencioso" pause
exit /b 1
