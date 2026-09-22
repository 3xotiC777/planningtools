@echo off
setlocal EnableExtensions
REM El codigo y los datos permanecen compartidos, pero cada usuario usa
REM su propio entorno de Python fuera de OneDrive.
cd /d "%~dp0"

set "APP_HOME=%LOCALAPPDATA%\DichterNeira\PlanningTools"
set "VENV_DIR=%APP_HOME%\venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "APP_RUNTIME=%APP_HOME%\app"

if /I "%~1"=="--diagnostico" (
    call "Instalar Planning Tools.bat" --diagnostico
    if errorlevel 1 exit /b 1
    exit /b 0
)

if not exist "%~dp0main.py" (
    echo.
    echo No se pudo descargar main.py desde la carpeta compartida.
    echo En el Explorador de archivos, haga clic derecho sobre PlanningTools
    echo y seleccione "Mantener siempre en este dispositivo".
    echo.
    pause
    exit /b 1
)

set "VENV_OK="
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import customtkinter, geopandas, numpy, openpyxl, ortools, pandas, PIL, pyogrio, scipy, shapely" >nul 2>&1
    if not errorlevel 1 set "VENV_OK=1"
)

if not defined VENV_OK (
    echo Preparando Planning Tools para el usuario %USERNAME%...
    call "Instalar Planning Tools.bat" --silencioso
    if errorlevel 1 (
        echo.
        echo No fue posible preparar Planning Tools en este equipo.
        echo Ejecute "Ejecutar Planning Tools.bat --diagnostico" para ver detalles.
        pause
        exit /b 1
    )
)

if not exist "%APP_RUNTIME%" mkdir "%APP_RUNTIME%" >nul 2>&1
if not exist "%APP_RUNTIME%" (
    echo.
    echo No se pudo crear la cache local de Planning Tools.
    echo Ruta: %APP_RUNTIME%
    pause
    exit /b 1
)

echo Actualizando codigo local de Planning Tools...
robocopy "%~dp0" "%APP_RUNTIME%" *.py /COPY:DT /FFT /R:2 /W:2 /NFL /NDL /NJH /NJS /NP >nul
set "COPY_ERROR="
if errorlevel 8 set "COPY_ERROR=1"

set "CACHE_OK=1"
for %%F in (main.py interfaz.py depuracion.py utilidades.py logs.py lector_excel.py validaciones.py normalizacion.py rotacion.py seleccion.py optimizacion_rutas.py herramientas_geograficas.py generador_mallas.py modulos_geograficos.py mapa_base.py matriz_distancias.py poligonos.py proyecto.py reportes.py vista_geografica.py) do if not exist "%APP_RUNTIME%\%%F" set "CACHE_OK="

if not defined CACHE_OK (
    echo.
    echo OneDrive no pudo descargar todos los archivos de la aplicacion.
    echo Marque la carpeta PlanningTools como "Mantener siempre en este dispositivo"
    echo y espere hasta que todos los archivos tengan el icono verde.
    echo.
    pause
    exit /b 1
)
if defined COPY_ERROR (
    echo Aviso: OneDrive no respondio. Se usara la ultima copia local disponible.
)

if not exist "%~dp0Logs" mkdir "%~dp0Logs" >nul 2>&1
set "WRITE_TEST=%~dp0Logs\.planning_tools_write_%USERNAME%_%RANDOM%.tmp"
>"%WRITE_TEST%" echo OK 2>nul
if not exist "%WRITE_TEST%" (
    echo.
    echo Su usuario no tiene permiso de escritura en la carpeta compartida.
    echo Solicite acceso de EDICION a la carpeta PlanningTools.
    echo.
    pause
    exit /b 1
)
del /q "%WRITE_TEST%" >nul 2>&1

set "PYTHONNOUSERSITE=1"
set "PLANNING_TOOLS_BASE=%~dp0"
pushd "%APP_RUNTIME%"
"%VENV_PY%" "%APP_RUNTIME%\main.py"
set "APP_EXIT=%ERRORLEVEL%"
popd
if not "%APP_EXIT%"=="0" pause
exit /b %APP_EXIT%
