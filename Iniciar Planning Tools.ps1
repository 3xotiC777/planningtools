param([switch]$Diagnostico)

$ErrorActionPreference = 'Stop'

function Get-Sha256([string]$Ruta) {
    $algoritmo = [System.Security.Cryptography.SHA256]::Create()
    $archivo = [System.IO.File]::Open($Ruta, 'Open', 'Read', 'ReadWrite')
    try {
        return [System.BitConverter]::ToString($algoritmo.ComputeHash($archivo)).Replace('-', '')
    } finally {
        $archivo.Dispose()
        $algoritmo.Dispose()
    }
}

$carpetaCompartida = Split-Path -Parent $PSCommandPath
$carpetaLocal = if ($env:PLANNING_TOOLS_LOCAL_HOME) {
    $env:PLANNING_TOOLS_LOCAL_HOME
} else {
    Join-Path $env:LOCALAPPDATA 'DichterNeira\PlanningTools'
}
$exeOrigen = Join-Path $carpetaCompartida 'Planning Tools.exe'
$exeLocal = Join-Path $carpetaLocal 'Planning Tools.exe'
$exeTemporal = Join-Path $carpetaLocal 'Planning Tools.nuevo.exe'
$exeAnterior = Join-Path $carpetaLocal ("Planning Tools.anterior-{0}-{1}.exe" -f (Get-Date -Format 'yyyyMMddHHmmss'), [Guid]::NewGuid().ToString('N').Substring(0, 8))
$carpetaLogs = Join-Path $carpetaLocal 'Logs'

try {
    New-Item -ItemType Directory -Path $carpetaLocal, $carpetaLogs -Force | Out-Null
} catch {
    Write-Host "No se pudo crear la carpeta local: $carpetaLocal"
    Write-Host $_.Exception.Message
    exit 2
}

$actualizado = $false
try {
    if (-not (Test-Path -LiteralPath $exeOrigen -PathType Leaf)) {
        throw "Todavia no aparece Planning Tools.exe en la carpeta sincronizada."
    }
    # Leer un solo archivo activa su descarga si OneDrive lo dejo como marcador.
    $hashOrigen = Get-Sha256 $exeOrigen
    $hashLocal = if (Test-Path -LiteralPath $exeLocal -PathType Leaf) {
        Get-Sha256 $exeLocal
    } else { '' }

    if ($hashOrigen -ne $hashLocal) {
        Copy-Item -LiteralPath $exeOrigen -Destination $exeTemporal -Force
        $hashTemporal = Get-Sha256 $exeTemporal
        if ($hashTemporal -ne $hashOrigen) {
            throw 'La copia local no coincide con el archivo de origen.'
        }
        if (Test-Path -LiteralPath $exeLocal -PathType Leaf) {
            Move-Item -LiteralPath $exeLocal -Destination $exeAnterior -ErrorAction Stop
            try {
                Move-Item -LiteralPath $exeTemporal -Destination $exeLocal -ErrorAction Stop
            } catch {
                Move-Item -LiteralPath $exeAnterior -Destination $exeLocal -ErrorAction Stop
                throw
            }
        } else {
            Move-Item -LiteralPath $exeTemporal -Destination $exeLocal
        }
        $actualizado = $true
        Write-Host 'Planning Tools actualizado en este equipo.'
    }
} catch {
    if (Test-Path -LiteralPath $exeTemporal -PathType Leaf) {
        Remove-Item -LiteralPath $exeTemporal -Force
    }
    if (-not (Test-Path -LiteralPath $exeLocal -PathType Leaf)) {
        Write-Host 'No se pudo preparar Planning Tools en este equipo.'
        Write-Host $_.Exception.Message
        Write-Host 'Compruebe que Planning Tools.exe este disponible en su carpeta sincronizada.'
        exit 2
    }
    Write-Host 'Aviso: no se pudo actualizar. Se abrira la ultima copia local.'
    Write-Host $_.Exception.Message
}

if ($Diagnostico) {
    Write-Host "Carpeta sincronizada: $carpetaCompartida"
    Write-Host "Ejecutable local: $exeLocal"
    Write-Host "Registros locales: $carpetaLogs"
    Write-Host "Actualizado: $actualizado"
    exit 0
}

$env:PLANNING_TOOLS_BASE = $carpetaCompartida
$env:PLANNING_TOOLS_LOG_DIR = $carpetaLogs
$env:PYTHONNOUSERSITE = '1'

try {
    $proceso = Start-Process -FilePath $exeLocal -WorkingDirectory $carpetaCompartida -Wait -PassThru
    if ($proceso.ExitCode -ne 0) {
        Write-Host "Planning Tools termino con error $($proceso.ExitCode)."
        Write-Host "Revise los registros en: $carpetaLogs"
    }
    exit $proceso.ExitCode
} catch {
    Write-Host 'Windows no pudo abrir el ejecutable local.'
    Write-Host $_.Exception.Message
    Write-Host "Ejecutable local: $exeLocal"
    exit 3
}
