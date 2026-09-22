# -*- coding: utf-8 -*-
"""
utilidades.py — Funciones de soporte transversales de Planning Tools.

Responsabilidad única: resolución de rutas (script vs .exe congelado),
carga/creación de la configuración externa y utilidades generales.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

NOMBRE_APP = "Planning Tools"
VERSION = "1.2.0"

# ------------------------------------------------------------------ rutas ----
def directorio_base() -> Path:
    """
    Carpeta raíz de la aplicación.

    - Ejecución desde caché local: usa PLANNING_TOOLS_BASE para conservar
      entradas, salidas y configuración en la carpeta sincronizada.
    - Ejecutable PyInstaller (--onefile): carpeta donde está el .exe
      (NO sys._MEIPASS, que es la carpeta temporal de descompresión).
    - Modo script: carpeta donde vive este archivo.
    """
    base_compartida = os.environ.get("PLANNING_TOOLS_BASE", "").strip()
    if base_compartida:
        return Path(base_compartida).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def ruta_recurso(ruta_relativa: str | Path) -> Path:
    """Resuelve un recurso tanto en modo fuente como dentro de PyInstaller."""
    relativa = Path(ruta_relativa)
    carpeta_bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    embebida = carpeta_bundle / relativa
    # Un icono de OneDrive puede existir como marcador sin estar descargado.
    # Los recursos inmutables del EXE siempre se leen de su propio paquete.
    if getattr(sys, "frozen", False) and embebida.is_file():
        return embebida
    externa = directorio_base() / relativa
    if externa.exists():
        return externa
    return embebida


def rutas_app() -> dict[str, Path]:
    """Crea (si no existen) y devuelve las carpetas estándar de la app."""
    base = directorio_base()
    rutas = {
        "base": base,
        "entrada": base / "Entrada Depuracion",
        "entrada_depuracion": base / "Entrada Depuracion",
        "salida": base / "Salida Depuracion",
        "salida_depuracion": base / "Salida Depuracion",
        "entrada_seleccion": base / "Entrada Seleccion",
        "salida_muestra": base / "Salida Muestras",
        "salida_optimizacion": base / "Salida Optimizacion Rutas",
        "salida_matriz_distancias": base / "Salida Matriz Distancias",
        "salida_poligonos": base / "Salida Cruce y Poligonos",
        "asignaciones": base.parent / "ASIGNACIONES",
        "poligonos_latam": base / "Poligonos Muestras" / "LATAM",
        "poligonos_delimitacion": base / "Poligonos Muestras" / "DELIMITACION PAISES",
        "logs": Path(os.environ.get("PLANNING_TOOLS_LOG_DIR") or base / "Logs"),
        "config": base / "Config",
        "resumenes": base / "Resumenes",
    }
    for clave, ruta in rutas.items():
        if clave not in ("base", "asignaciones"):
            ruta.mkdir(parents=True, exist_ok=True)
    return rutas


# ----------------------------------------------------------- configuración ----
CONFIG_DEFECTO: dict = {
    "pais_activo": "Costa Rica",
    "paises": {
        "Chile": {
    "modulo_depuracion": {
        "archivo_universo": "Universo.xlsx",
        "hoja_universo": "BASE ICE AGOSTO",
        "llave_universo": "CÓDIGO",
        "archivo_incidencias": "Incidencia.xlsx",
        "hoja_incidencias": "Export",
        "llave_incidencias": "Id_PDV",
        # El Id_PDV (77…/78…) no coincide literalmente con CÓDIGO (27…/28…)
        # pero sí con la columna 'Codigo DN'. Si existe, el cruce es exacto
        # Id_PDV <-> Codigo DN; si no, se usan los últimos N dígitos.
        "columna_puente": "Codigo DN",
        "n_digitos_cruce": 8,
        "reglas": {
            "estatus": "Ultima Incidencia",
            "comentario": "No Elegible",
            "ufa_anio": 2026,
            "estudio_pais": "COCA COLA (KO TRD)- ABVO GUATEMALA",
        },
        "columnas_reporte_universo": [
            "CÓDIGO", "Nombre de Cliente", "Departamento",
            "Municipio", "GEC", "Canal País",
        ],
        "columnas_reporte_incidencia": ["Estatus", "Comentario", "UFA", "Estudio_pais"],
        # Etapa de rotación: cupo anual de visitas por GEC contra A_Actual.
        # Fijos: listado opcional Entrada/Fijos.xlsx (columnas con 'COD').
        "rotacion": {
            "activa": True,
            "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
            "cupo_por_defecto": 2,
            "columna_gec": "GEC",
            "archivo_fijos": "Fijos.xlsx",
            "hoja_fijos": 0,
        },
    },
    # Módulo de Selección de Muestra (parámetros de la matriz de Términos
    # y Condiciones del país; editar sin recompilar).
    "modulo_seleccion": {
        "archivo_universo_elegible": "Universo_Elegible.xlsx",
        "tamano_muestra": 1636,
        "ratio_suplentes": 4,
        "cuotas_gec": {"ORO": 0.25, "PLATA": 0.63, "BRONCE": 0.12},
        "tolerancia_cuotas": 0.05,
        "columna_gec": "GEC",
        "columna_ruta": "Ruta Venta",
        "columna_fijo": "Cliente fijo",
        "valor_fijo": "SI",
        "columna_canal": "Canal País",
        "min_pdv_ruta": 5,
        "max_pdv_ruta": 12,
        "puntos_rutas": {
            "max_diferencia_ruta": 0,
            "min_ruta_base": 0
        },
        "muestra_ruta": {
            "oro": 0,
            "plata": 0,
            "bronce": 0,
            "fijos": 0,
            "variables": 0,
            "canal_on": 0,
            "canal_off": 0
        },
        "columna_lat": "LATITUD",
        "columna_lon": "LONGITUD",
        "limites_pais": {"lat": [13.0, 18.5], "lon": [-93.0, -88.0]},
        "semilla": 2026
    }
},
        "Costa Rica": {
    "modulo_depuracion": {
        "archivo_universo": "Universo.xlsx",
        "hoja_universo": "BASE ICE AGOSTO",
        "llave_universo": "CÓDIGO",
        "archivo_incidencias": "Incidencia.xlsx",
        "hoja_incidencias": "Export",
        "llave_incidencias": "Id_PDV",
        # El Id_PDV (77…/78…) no coincide literalmente con CÓDIGO (27…/28…)
        # pero sí con la columna 'Codigo DN'. Si existe, el cruce es exacto
        # Id_PDV <-> Codigo DN; si no, se usan los últimos N dígitos.
        "columna_puente": "Codigo DN",
        "n_digitos_cruce": 8,
        "reglas": {
            "estatus": "Ultima Incidencia",
            "comentario": "No Elegible",
            "ufa_anio": 2026,
            "estudio_pais": "COCA COLA (KO TRD)- ABVO GUATEMALA",
        },
        "columnas_reporte_universo": [
            "CÓDIGO", "Nombre de Cliente", "Departamento",
            "Municipio", "GEC", "Canal País",
        ],
        "columnas_reporte_incidencia": ["Estatus", "Comentario", "UFA", "Estudio_pais"],
        # Etapa de rotación: cupo anual de visitas por GEC contra A_Actual.
        # Fijos: listado opcional Entrada/Fijos.xlsx (columnas con 'COD').
        "rotacion": {
            "activa": True,
            "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
            "cupo_por_defecto": 2,
            "columna_gec": "GEC",
            "archivo_fijos": "Fijos.xlsx",
            "hoja_fijos": 0,
        },
    },
    # Módulo de Selección de Muestra (parámetros de la matriz de Términos
    # y Condiciones del país; editar sin recompilar).
    "modulo_seleccion": {
        "archivo_universo_elegible": "Universo_Elegible.xlsx",
        "tamano_muestra": 1636,
        "ratio_suplentes": 4,
        "cuotas_gec": {"ORO": 0.25, "PLATA": 0.63, "BRONCE": 0.12},
        "tolerancia_cuotas": 0.05,
        "columna_gec": "GEC",
        "columna_ruta": "Ruta Venta",
        "columna_fijo": "Cliente fijo",
        "valor_fijo": "SI",
        "columna_canal": "Canal País",
        "min_pdv_ruta": 5,
        "max_pdv_ruta": 12,
        "puntos_rutas": {
            "max_diferencia_ruta": 0,
            "min_ruta_base": 0
        },
        "muestra_ruta": {
            "oro": 0,
            "plata": 0,
            "bronce": 0,
            "fijos": 0,
            "variables": 0,
            "canal_on": 0,
            "canal_off": 0
        },
        "columna_lat": "LATITUD",
        "columna_lon": "LONGITUD",
        "limites_pais": {"lat": [13.0, 18.5], "lon": [-93.0, -88.0]},
        "semilla": 2026
    }
},
        "Ecuador": {
    "modulo_depuracion": {
        "archivo_universo": "Universo.xlsx",
        "hoja_universo": "BASE ICE AGOSTO",
        "llave_universo": "CÓDIGO",
        "archivo_incidencias": "Incidencia.xlsx",
        "hoja_incidencias": "Export",
        "llave_incidencias": "Id_PDV",
        # El Id_PDV (77…/78…) no coincide literalmente con CÓDIGO (27…/28…)
        # pero sí con la columna 'Codigo DN'. Si existe, el cruce es exacto
        # Id_PDV <-> Codigo DN; si no, se usan los últimos N dígitos.
        "columna_puente": "Codigo DN",
        "n_digitos_cruce": 8,
        "reglas": {
            "estatus": "Ultima Incidencia",
            "comentario": "No Elegible",
            "ufa_anio": 2026,
            "estudio_pais": "COCA COLA (KO TRD)- ABVO GUATEMALA",
        },
        "columnas_reporte_universo": [
            "CÓDIGO", "Nombre de Cliente", "Departamento",
            "Municipio", "GEC", "Canal País",
        ],
        "columnas_reporte_incidencia": ["Estatus", "Comentario", "UFA", "Estudio_pais"],
        # Etapa de rotación: cupo anual de visitas por GEC contra A_Actual.
        # Fijos: listado opcional Entrada/Fijos.xlsx (columnas con 'COD').
        "rotacion": {
            "activa": True,
            "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
            "cupo_por_defecto": 2,
            "columna_gec": "GEC",
            "archivo_fijos": "Fijos.xlsx",
            "hoja_fijos": 0,
        },
    },
    # Módulo de Selección de Muestra (parámetros de la matriz de Términos
    # y Condiciones del país; editar sin recompilar).
    "modulo_seleccion": {
        "archivo_universo_elegible": "Universo_Elegible.xlsx",
        "tamano_muestra": 1636,
        "ratio_suplentes": 4,
        "cuotas_gec": {"ORO": 0.25, "PLATA": 0.63, "BRONCE": 0.12},
        "tolerancia_cuotas": 0.05,
        "columna_gec": "GEC",
        "columna_ruta": "Ruta Venta",
        "columna_fijo": "Cliente fijo",
        "valor_fijo": "SI",
        "columna_canal": "Canal País",
        "min_pdv_ruta": 5,
        "max_pdv_ruta": 12,
        "puntos_rutas": {
            "max_diferencia_ruta": 0,
            "min_ruta_base": 0
        },
        "muestra_ruta": {
            "oro": 0,
            "plata": 0,
            "bronce": 0,
            "fijos": 0,
            "variables": 0,
            "canal_on": 0,
            "canal_off": 0
        },
        "columna_lat": "LATITUD",
        "columna_lon": "LONGITUD",
        "limites_pais": {"lat": [13.0, 18.5], "lon": [-93.0, -88.0]},
        "semilla": 2026
    }
},
        "El Salvador": {
    "modulo_depuracion": {
        "archivo_universo": "Universo.xlsx",
        "hoja_universo": "BASE ICE AGOSTO",
        "llave_universo": "CÓDIGO",
        "archivo_incidencias": "Incidencia.xlsx",
        "hoja_incidencias": "Export",
        "llave_incidencias": "Id_PDV",
        # El Id_PDV (77…/78…) no coincide literalmente con CÓDIGO (27…/28…)
        # pero sí con la columna 'Codigo DN'. Si existe, el cruce es exacto
        # Id_PDV <-> Codigo DN; si no, se usan los últimos N dígitos.
        "columna_puente": "Codigo DN",
        "n_digitos_cruce": 8,
        "reglas": {
            "estatus": "Ultima Incidencia",
            "comentario": "No Elegible",
            "ufa_anio": 2026,
            "estudio_pais": "COCA COLA (KO TRD)- ABVO GUATEMALA",
        },
        "columnas_reporte_universo": [
            "CÓDIGO", "Nombre de Cliente", "Departamento",
            "Municipio", "GEC", "Canal País",
        ],
        "columnas_reporte_incidencia": ["Estatus", "Comentario", "UFA", "Estudio_pais"],
        # Etapa de rotación: cupo anual de visitas por GEC contra A_Actual.
        # Fijos: listado opcional Entrada/Fijos.xlsx (columnas con 'COD').
        "rotacion": {
            "activa": True,
            "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
            "cupo_por_defecto": 2,
            "columna_gec": "GEC",
            "archivo_fijos": "Fijos.xlsx",
            "hoja_fijos": 0,
        },
    },
    # Módulo de Selección de Muestra (parámetros de la matriz de Términos
    # y Condiciones del país; editar sin recompilar).
    "modulo_seleccion": {
        "archivo_universo_elegible": "Universo_Elegible.xlsx",
        "tamano_muestra": 1636,
        "ratio_suplentes": 4,
        "cuotas_gec": {"ORO": 0.25, "PLATA": 0.63, "BRONCE": 0.12},
        "tolerancia_cuotas": 0.05,
        "columna_gec": "GEC",
        "columna_ruta": "Ruta Venta",
        "columna_fijo": "Cliente fijo",
        "valor_fijo": "SI",
        "columna_canal": "Canal País",
        "min_pdv_ruta": 5,
        "max_pdv_ruta": 12,
        "puntos_rutas": {
            "max_diferencia_ruta": 0,
            "min_ruta_base": 0
        },
        "muestra_ruta": {
            "oro": 0,
            "plata": 0,
            "bronce": 0,
            "fijos": 0,
            "variables": 0,
            "canal_on": 0,
            "canal_off": 0
        },
        "columna_lat": "LATITUD",
        "columna_lon": "LONGITUD",
        "limites_pais": {"lat": [13.0, 18.5], "lon": [-93.0, -88.0]},
        "semilla": 2026
    }
},
        "Guatemala ABVO": {
    "modulo_depuracion": {
        "archivo_universo": "Universo.xlsx",
        "hoja_universo": "BASE ICE AGOSTO",
        "llave_universo": "CÓDIGO",
        "archivo_incidencias": "Incidencia.xlsx",
        "hoja_incidencias": "Export",
        "llave_incidencias": "Id_PDV",
        # El Id_PDV (77…/78…) no coincide literalmente con CÓDIGO (27…/28…)
        # pero sí con la columna 'Codigo DN'. Si existe, el cruce es exacto
        # Id_PDV <-> Codigo DN; si no, se usan los últimos N dígitos.
        "columna_puente": "Codigo DN",
        "n_digitos_cruce": 8,
        "reglas": {
            "estatus": "Ultima Incidencia",
            "comentario": "No Elegible",
            "ufa_anio": 2026,
            "estudio_pais": "COCA COLA (KO TRD)- ABVO GUATEMALA",
        },
        "columnas_reporte_universo": [
            "CÓDIGO", "Nombre de Cliente", "Departamento",
            "Municipio", "GEC", "Canal País",
        ],
        "columnas_reporte_incidencia": ["Estatus", "Comentario", "UFA", "Estudio_pais"],
        # Etapa de rotación: cupo anual de visitas por GEC contra A_Actual.
        # Fijos: listado opcional Entrada/Fijos.xlsx (columnas con 'COD').
        "rotacion": {
            "activa": True,
            "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
            "cupo_por_defecto": 2,
            "columna_gec": "GEC",
            "archivo_fijos": "Fijos.xlsx",
            "hoja_fijos": 0,
        },
    },
    # Módulo de Selección de Muestra (parámetros de la matriz de Términos
    # y Condiciones del país; editar sin recompilar).
    "modulo_seleccion": {
        "archivo_universo_elegible": "Universo_Elegible.xlsx",
        "tamano_muestra": 1636,
        "ratio_suplentes": 4,
        "cuotas_gec": {"ORO": 0.25, "PLATA": 0.63, "BRONCE": 0.12},
        "tolerancia_cuotas": 0.05,
        "columna_gec": "GEC",
        "columna_ruta": "Ruta Venta",
        "columna_fijo": "Cliente fijo",
        "valor_fijo": "SI",
        "columna_canal": "Canal País",
        "min_pdv_ruta": 5,
        "max_pdv_ruta": 12,
        "puntos_rutas": {
            "max_diferencia_ruta": 0,
            "min_ruta_base": 0
        },
        "muestra_ruta": {
            "oro": 0,
            "plata": 0,
            "bronce": 0,
            "fijos": 0,
            "variables": 0,
            "canal_on": 0,
            "canal_off": 0
        },
        "columna_lat": "LATITUD",
        "columna_lon": "LONGITUD",
        "limites_pais": {"lat": [13.0, 18.5], "lon": [-93.0, -88.0]},
        "semilla": 2026
    }
},
        "Guatemala EMBOCEN": {
    "modulo_depuracion": {
        "archivo_universo": "Universo.xlsx",
        "hoja_universo": "BASE ICE AGOSTO",
        "llave_universo": "CÓDIGO",
        "archivo_incidencias": "Incidencia.xlsx",
        "hoja_incidencias": "Export",
        "llave_incidencias": "Id_PDV",
        # El Id_PDV (77…/78…) no coincide literalmente con CÓDIGO (27…/28…)
        # pero sí con la columna 'Codigo DN'. Si existe, el cruce es exacto
        # Id_PDV <-> Codigo DN; si no, se usan los últimos N dígitos.
        "columna_puente": "Codigo DN",
        "n_digitos_cruce": 8,
        "reglas": {
            "estatus": "Ultima Incidencia",
            "comentario": "No Elegible",
            "ufa_anio": 2026,
            "estudio_pais": "COCA COLA (KO TRD)- ABVO GUATEMALA",
        },
        "columnas_reporte_universo": [
            "CÓDIGO", "Nombre de Cliente", "Departamento",
            "Municipio", "GEC", "Canal País",
        ],
        "columnas_reporte_incidencia": ["Estatus", "Comentario", "UFA", "Estudio_pais"],
        # Etapa de rotación: cupo anual de visitas por GEC contra A_Actual.
        # Fijos: listado opcional Entrada/Fijos.xlsx (columnas con 'COD').
        "rotacion": {
            "activa": True,
            "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
            "cupo_por_defecto": 2,
            "columna_gec": "GEC",
            "archivo_fijos": "Fijos.xlsx",
            "hoja_fijos": 0,
        },
    },
    # Módulo de Selección de Muestra (parámetros de la matriz de Términos
    # y Condiciones del país; editar sin recompilar).
    "modulo_seleccion": {
        "archivo_universo_elegible": "Universo_Elegible.xlsx",
        "tamano_muestra": 1636,
        "ratio_suplentes": 4,
        "cuotas_gec": {"ORO": 0.25, "PLATA": 0.63, "BRONCE": 0.12},
        "tolerancia_cuotas": 0.05,
        "columna_gec": "GEC",
        "columna_ruta": "Ruta Venta",
        "columna_fijo": "Cliente fijo",
        "valor_fijo": "SI",
        "columna_canal": "Canal País",
        "min_pdv_ruta": 5,
        "max_pdv_ruta": 12,
        "puntos_rutas": {
            "max_diferencia_ruta": 0,
            "min_ruta_base": 0
        },
        "muestra_ruta": {
            "oro": 0,
            "plata": 0,
            "bronce": 0,
            "fijos": 0,
            "variables": 0,
            "canal_on": 0,
            "canal_off": 0
        },
        "columna_lat": "LATITUD",
        "columna_lon": "LONGITUD",
        "limites_pais": {"lat": [13.0, 18.5], "lon": [-93.0, -88.0]},
        "semilla": 2026
    }
},
        "Honduras": {
    "modulo_depuracion": {
        "archivo_universo": "Universo.xlsx",
        "hoja_universo": "BASE ICE AGOSTO",
        "llave_universo": "CÓDIGO",
        "archivo_incidencias": "Incidencia.xlsx",
        "hoja_incidencias": "Export",
        "llave_incidencias": "Id_PDV",
        # El Id_PDV (77…/78…) no coincide literalmente con CÓDIGO (27…/28…)
        # pero sí con la columna 'Codigo DN'. Si existe, el cruce es exacto
        # Id_PDV <-> Codigo DN; si no, se usan los últimos N dígitos.
        "columna_puente": "Codigo DN",
        "n_digitos_cruce": 8,
        "reglas": {
            "estatus": "Ultima Incidencia",
            "comentario": "No Elegible",
            "ufa_anio": 2026,
            "estudio_pais": "COCA COLA (KO TRD)- ABVO GUATEMALA",
        },
        "columnas_reporte_universo": [
            "CÓDIGO", "Nombre de Cliente", "Departamento",
            "Municipio", "GEC", "Canal País",
        ],
        "columnas_reporte_incidencia": ["Estatus", "Comentario", "UFA", "Estudio_pais"],
        # Etapa de rotación: cupo anual de visitas por GEC contra A_Actual.
        # Fijos: listado opcional Entrada/Fijos.xlsx (columnas con 'COD').
        "rotacion": {
            "activa": True,
            "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
            "cupo_por_defecto": 2,
            "columna_gec": "GEC",
            "archivo_fijos": "Fijos.xlsx",
            "hoja_fijos": 0,
        },
    },
    # Módulo de Selección de Muestra (parámetros de la matriz de Términos
    # y Condiciones del país; editar sin recompilar).
    "modulo_seleccion": {
        "archivo_universo_elegible": "Universo_Elegible.xlsx",
        "tamano_muestra": 1636,
        "ratio_suplentes": 4,
        "cuotas_gec": {"ORO": 0.25, "PLATA": 0.63, "BRONCE": 0.12},
        "tolerancia_cuotas": 0.05,
        "columna_gec": "GEC",
        "columna_ruta": "Ruta Venta",
        "columna_fijo": "Cliente fijo",
        "valor_fijo": "SI",
        "columna_canal": "Canal País",
        "min_pdv_ruta": 5,
        "max_pdv_ruta": 12,
        "puntos_rutas": {
            "max_diferencia_ruta": 0,
            "min_ruta_base": 0
        },
        "muestra_ruta": {
            "oro": 0,
            "plata": 0,
            "bronce": 0,
            "fijos": 0,
            "variables": 0,
            "canal_on": 0,
            "canal_off": 0
        },
        "columna_lat": "LATITUD",
        "columna_lon": "LONGITUD",
        "limites_pais": {"lat": [13.0, 18.5], "lon": [-93.0, -88.0]},
        "semilla": 2026
    }
},
        "Nicaragua": {
    "modulo_depuracion": {
        "archivo_universo": "Universo.xlsx",
        "hoja_universo": "BASE ICE AGOSTO",
        "llave_universo": "CÓDIGO",
        "archivo_incidencias": "Incidencia.xlsx",
        "hoja_incidencias": "Export",
        "llave_incidencias": "Id_PDV",
        # El Id_PDV (77…/78…) no coincide literalmente con CÓDIGO (27…/28…)
        # pero sí con la columna 'Codigo DN'. Si existe, el cruce es exacto
        # Id_PDV <-> Codigo DN; si no, se usan los últimos N dígitos.
        "columna_puente": "Codigo DN",
        "n_digitos_cruce": 8,
        "reglas": {
            "estatus": "Ultima Incidencia",
            "comentario": "No Elegible",
            "ufa_anio": 2026,
            "estudio_pais": "COCA COLA (KO TRD)- ABVO GUATEMALA",
        },
        "columnas_reporte_universo": [
            "CÓDIGO", "Nombre de Cliente", "Departamento",
            "Municipio", "GEC", "Canal País",
        ],
        "columnas_reporte_incidencia": ["Estatus", "Comentario", "UFA", "Estudio_pais"],
        # Etapa de rotación: cupo anual de visitas por GEC contra A_Actual.
        # Fijos: listado opcional Entrada/Fijos.xlsx (columnas con 'COD').
        "rotacion": {
            "activa": True,
            "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
            "cupo_por_defecto": 2,
            "columna_gec": "GEC",
            "archivo_fijos": "Fijos.xlsx",
            "hoja_fijos": 0,
        },
    },
    # Módulo de Selección de Muestra (parámetros de la matriz de Términos
    # y Condiciones del país; editar sin recompilar).
    "modulo_seleccion": {
        "archivo_universo_elegible": "Universo_Elegible.xlsx",
        "tamano_muestra": 1636,
        "ratio_suplentes": 4,
        "cuotas_gec": {"ORO": 0.25, "PLATA": 0.63, "BRONCE": 0.12},
        "tolerancia_cuotas": 0.05,
        "columna_gec": "GEC",
        "columna_ruta": "Ruta Venta",
        "columna_fijo": "Cliente fijo",
        "valor_fijo": "SI",
        "columna_canal": "Canal País",
        "min_pdv_ruta": 5,
        "max_pdv_ruta": 12,
        "puntos_rutas": {
            "max_diferencia_ruta": 0,
            "min_ruta_base": 0
        },
        "muestra_ruta": {
            "oro": 0,
            "plata": 0,
            "bronce": 0,
            "fijos": 0,
            "variables": 0,
            "canal_on": 0,
            "canal_off": 0
        },
        "columna_lat": "LATITUD",
        "columna_lon": "LONGITUD",
        "limites_pais": {"lat": [13.0, 18.5], "lon": [-93.0, -88.0]},
        "semilla": 2026
    }
},
        "Panamá": {
    "modulo_depuracion": {
        "archivo_universo": "Universo.xlsx",
        "hoja_universo": "BASE ICE AGOSTO",
        "llave_universo": "CÓDIGO",
        "archivo_incidencias": "Incidencia.xlsx",
        "hoja_incidencias": "Export",
        "llave_incidencias": "Id_PDV",
        # El Id_PDV (77…/78…) no coincide literalmente con CÓDIGO (27…/28…)
        # pero sí con la columna 'Codigo DN'. Si existe, el cruce es exacto
        # Id_PDV <-> Codigo DN; si no, se usan los últimos N dígitos.
        "columna_puente": "Codigo DN",
        "n_digitos_cruce": 8,
        "reglas": {
            "estatus": "Ultima Incidencia",
            "comentario": "No Elegible",
            "ufa_anio": 2026,
            "estudio_pais": "COCA COLA (KO TRD)- ABVO GUATEMALA",
        },
        "columnas_reporte_universo": [
            "CÓDIGO", "Nombre de Cliente", "Departamento",
            "Municipio", "GEC", "Canal País",
        ],
        "columnas_reporte_incidencia": ["Estatus", "Comentario", "UFA", "Estudio_pais"],
        # Etapa de rotación: cupo anual de visitas por GEC contra A_Actual.
        # Fijos: listado opcional Entrada/Fijos.xlsx (columnas con 'COD').
        "rotacion": {
            "activa": True,
            "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
            "cupo_por_defecto": 2,
            "columna_gec": "GEC",
            "archivo_fijos": "Fijos.xlsx",
            "hoja_fijos": 0,
        },
    },
    # Módulo de Selección de Muestra (parámetros de la matriz de Términos
    # y Condiciones del país; editar sin recompilar).
    "modulo_seleccion": {
        "archivo_universo_elegible": "Universo_Elegible.xlsx",
        "tamano_muestra": 1636,
        "ratio_suplentes": 4,
        "cuotas_gec": {"ORO": 0.25, "PLATA": 0.63, "BRONCE": 0.12},
        "tolerancia_cuotas": 0.05,
        "columna_gec": "GEC",
        "columna_ruta": "Ruta Venta",
        "columna_fijo": "Cliente fijo",
        "valor_fijo": "SI",
        "columna_canal": "Canal País",
        "min_pdv_ruta": 5,
        "max_pdv_ruta": 12,
        "puntos_rutas": {
            "max_diferencia_ruta": 0,
            "min_ruta_base": 0
        },
        "muestra_ruta": {
            "oro": 0,
            "plata": 0,
            "bronce": 0,
            "fijos": 0,
            "variables": 0,
            "canal_on": 0,
            "canal_off": 0
        },
        "columna_lat": "LATITUD",
        "columna_lon": "LONGITUD",
        "limites_pais": {"lat": [13.0, 18.5], "lon": [-93.0, -88.0]},
        "semilla": 2026
    }
},
        "República Dominicana": {
    "modulo_depuracion": {
        "archivo_universo": "Universo.xlsx",
        "hoja_universo": "BASE ICE AGOSTO",
        "llave_universo": "CÓDIGO",
        "archivo_incidencias": "Incidencia.xlsx",
        "hoja_incidencias": "Export",
        "llave_incidencias": "Id_PDV",
        # El Id_PDV (77…/78…) no coincide literalmente con CÓDIGO (27…/28…)
        # pero sí con la columna 'Codigo DN'. Si existe, el cruce es exacto
        # Id_PDV <-> Codigo DN; si no, se usan los últimos N dígitos.
        "columna_puente": "Codigo DN",
        "n_digitos_cruce": 8,
        "reglas": {
            "estatus": "Ultima Incidencia",
            "comentario": "No Elegible",
            "ufa_anio": 2026,
            "estudio_pais": "COCA COLA (KO TRD)- ABVO GUATEMALA",
        },
        "columnas_reporte_universo": [
            "CÓDIGO", "Nombre de Cliente", "Departamento",
            "Municipio", "GEC", "Canal País",
        ],
        "columnas_reporte_incidencia": ["Estatus", "Comentario", "UFA", "Estudio_pais"],
        # Etapa de rotación: cupo anual de visitas por GEC contra A_Actual.
        # Fijos: listado opcional Entrada/Fijos.xlsx (columnas con 'COD').
        "rotacion": {
            "activa": True,
            "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
            "cupo_por_defecto": 2,
            "columna_gec": "GEC",
            "archivo_fijos": "Fijos.xlsx",
            "hoja_fijos": 0,
        },
    },
    # Módulo de Selección de Muestra (parámetros de la matriz de Términos
    # y Condiciones del país; editar sin recompilar).
    "modulo_seleccion": {
        "archivo_universo_elegible": "Universo_Elegible.xlsx",
        "tamano_muestra": 1636,
        "ratio_suplentes": 4,
        "cuotas_gec": {"ORO": 0.25, "PLATA": 0.63, "BRONCE": 0.12},
        "tolerancia_cuotas": 0.05,
        "columna_gec": "GEC",
        "columna_ruta": "Ruta Venta",
        "columna_fijo": "Cliente fijo",
        "valor_fijo": "SI",
        "columna_canal": "Canal País",
        "min_pdv_ruta": 5,
        "max_pdv_ruta": 12,
        "puntos_rutas": {
            "max_diferencia_ruta": 0,
            "min_ruta_base": 0
        },
        "muestra_ruta": {
            "oro": 0,
            "plata": 0,
            "bronce": 0,
            "fijos": 0,
            "variables": 0,
            "canal_on": 0,
            "canal_off": 0
        },
        "columna_lat": "LATITUD",
        "columna_lon": "LONGITUD",
        "limites_pais": {"lat": [13.0, 18.5], "lon": [-93.0, -88.0]},
        "semilla": 2026
    }
},
    }
}


def guardar_config(config: dict) -> None:
    """Guarda la configuración actual en config.json."""
    ruta = rutas_app()["config"] / "config.json"
    ruta.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def cargar_config() -> dict:
    """
    Lee Config/config.json. Migra a multi-país si es el formato antiguo.
    """
    ruta = rutas_app()["config"] / "config.json"
    if not ruta.exists():
        cfg_defecto = json.loads(json.dumps(CONFIG_DEFECTO))
        for p in cfg_defecto.get("paises", {}).values():
            pxr = p.get("modulo_seleccion", {}).setdefault("pxr_minimo", 10)
            p.setdefault("modulo_depuracion", {})["pxr_minimo"] = pxr
        guardar_config(cfg_defecto)
        return cfg_defecto
    try:
        cfg = json.loads(ruta.read_text(encoding="utf-8"))
        # Migración de formato antiguo a nuevo
        if "modulo_depuracion" in cfg and "paises" not in cfg:
            nuevo_cfg = json.loads(json.dumps(CONFIG_DEFECTO))
            nuevo_cfg["paises"]["Costa Rica"] = cfg
            nuevo_cfg["pais_activo"] = "Costa Rica"
            cfg = nuevo_cfg
            guardar_config(cfg)
            
        # Asegurar llaves nuevas en todos los países
        configuracion_actualizada = False
        for p in cfg.get("paises", {}).values():
            if "modulo_seleccion" not in p or not isinstance(p["modulo_seleccion"], dict):
                p["modulo_seleccion"] = {
                    "archivo_universo_elegible": "Universo_Elegible.xlsx",
                    "tamano_muestra": 1000,
                    "ratio_suplentes": 4,
                    "cuotas_gec": {"ORO": 0.25, "PLATA": 0.50, "BRONCE": 0.25},
                    "tolerancia_cuotas": 0.05,
                    "columna_gec": "GEC",
                    "columna_ruta": "Ruta Venta",
                    "columna_fijo": "Cliente fijo",
                    "valor_fijo": "SI",
                    "columna_canal": "Canal País",
                    "min_pdv_ruta": 5,
                    "max_pdv_ruta": 200,
                    "pxr_minimo": 10,
                    "puntos_rutas": {"max_diferencia_ruta": 0, "min_ruta_base": 0},
                    "muestra_ruta": {"oro": 0, "plata": 0, "bronce": 0, "fijos": 0, "variables": 0, "canal_on": 0, "canal_off": 0},
                    "columna_lat": "LATITUD",
                    "columna_lon": "LONGITUD",
                    "semilla": 2026
                }
                configuracion_actualizada = True
            if "puntos_rutas" not in p["modulo_seleccion"]:
                p["modulo_seleccion"]["puntos_rutas"] = {"max_diferencia_ruta": 0, "min_ruta_base": 0}
                configuracion_actualizada = True
            if "muestra_ruta" not in p["modulo_seleccion"]:
                p["modulo_seleccion"]["muestra_ruta"] = {"oro": 0, "plata": 0, "bronce": 0, "fijos": 0, "variables": 0, "canal_on": 0, "canal_off": 0}
                configuracion_actualizada = True
            if "columna_canal" not in p["modulo_seleccion"]:
                p["modulo_seleccion"]["columna_canal"] = "Canal País"
                configuracion_actualizada = True
            if "pxr_minimo" not in p["modulo_seleccion"]:
                p["modulo_seleccion"]["pxr_minimo"] = 10
                configuracion_actualizada = True
            modulo_depuracion = p.setdefault("modulo_depuracion", {})
            if "pxr_minimo" not in modulo_depuracion:
                modulo_depuracion["pxr_minimo"] = p["modulo_seleccion"]["pxr_minimo"]
                configuracion_actualizada = True
        if configuracion_actualizada:
            guardar_config(cfg)
        return cfg
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"El archivo de configuración está dañado: {ruta}\\n"
            f"Corríjalo o elimínelo para regenerarlo. Detalle: {exc}"
        ) from exc


# --------------------------------------------------------------- generales ----
def formatear_duracion(segundos: float) -> str:
    """Convierte segundos en 'HH:MM:SS' legible."""
    return str(timedelta(seconds=int(segundos)))


def abrir_carpeta(ruta: Path) -> None:
    """Abre una carpeta en el explorador del sistema operativo."""
    if sys.platform.startswith("win"):
        os.startfile(str(ruta))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(ruta)])
    else:
        subprocess.Popen(["xdg-open", str(ruta)])




# --------------------------------------------------- matcher de universo seleccion ----
PAIS_TOKENS_MAP = {
    "Chile": {
        "match": ["CL", "CHILE"],
        "avoid": []
    },
    "Ecuador": {
        "match": ["EC", "ECUADOR"],
        "avoid": []
    },
    "Panamá": {
        "match": ["PA", "PANAMA"],
        "avoid": []
    },
    "Panama": {
        "match": ["PA", "PANAMA"],
        "avoid": []
    },
    "Honduras": {
        "match": ["HN", "HONDURAS"],
        "avoid": []
    },
    "Guatemala ABVO": {
        "match": ["ABVO", "GT_AB", "GT AB", "GTAB", "ABVO GUATEMALA", "GUATEMALA ABVO"],
        "avoid": ["EMBOCEN", "EMBO", "GT_EM", "GT EM", "GTEM"]
    },
    "Guatemala EMBOCEN": {
        "match": ["EMBOCEN", "EMBO", "GT_EM", "GT EM", "GTEM", "EMBOCEN GUATEMALA", "GUATEMALA EMBOCEN"],
        "avoid": ["ABVO", "GT_AB", "GT AB", "GTAB"]
    },
    "Nicaragua": {
        "match": ["NI", "NICA", "NICARAGUA"],
        "avoid": []
    },
    "El Salvador": {
        "match": ["ES", "SALVADOR", "ELSALVADOR", "EL SALVADOR"],
        "avoid": []
    },
    "Costa Rica": {
        "match": ["CR", "COSTARICA", "COSTA RICA"],
        "avoid": []
    },
    "República Dominicana": {
        "match": ["RD", "DOMINICANA", "REPUBLICA DOMINICANA", "REP DOMINICANA"],
        "avoid": []
    },
    "Republica Dominicana": {
        "match": ["RD", "DOMINICANA", "REPUBLICA DOMINICANA", "REP DOMINICANA"],
        "avoid": []
    }
}


def buscar_archivo_universo_seleccion(dir_sel: Path, dir_dep: Path, pais_activo: str) -> tuple[Path | None, list[str]]:
    """
    Busca estrictamente en 'Entrada Seleccion' (y fallback en 'Salida Depuracion')
    el archivo que corresponde ÚNICAMENTE al país activo.

    Diferencia estrictamente Guatemala ABVO vs Guatemala EMBOCEN.
    """
    import re
    spec = PAIS_TOKENS_MAP.get(pais_activo, {"match": [pais_activo.upper()], "avoid": []})
    match_tokens = spec["match"]
    avoid_tokens = spec["avoid"]
    
    # 1. Buscar en Entrada Seleccion
    if dir_sel and dir_sel.exists():
        files_sel = [f for f in dir_sel.iterdir() if f.is_file() and f.suffix.lower() in ('.xlsx', '.xls', '.xlsb')]
        for f in files_sel:
            name_upper = f.stem.upper().replace("_", " ").replace("-", " ")
            
            # Verificar si contiene tokens prohibidos de otro estudio (ej. EMBOCEN en ABVO)
            tiene_prohibido = False
            for av in avoid_tokens:
                if re.search(rf"\b{re.escape(av)}\b", name_upper):
                    tiene_prohibido = True
                    break
            if tiene_prohibido:
                continue
                
            # Verificar si coincide con tokens del país
            for tk in match_tokens:
                regex_pattern = rf"\b{re.escape(tk)}\b"
                if re.search(regex_pattern, name_upper):
                    return f, match_tokens

    # 2. Fallback: Buscar en Salida Depuracion
    if dir_dep and dir_dep.exists():
        files_dep = [f for f in dir_dep.iterdir() if f.is_file() and f.suffix.lower() in ('.xlsx', '.xls', '.xlsb')]
        for f in files_dep:
            name_upper = f.stem.upper().replace("_", " ").replace("-", " ")
            
            tiene_prohibido = False
            for av in avoid_tokens:
                if re.search(rf"\b{re.escape(av)}\b", name_upper):
                    tiene_prohibido = True
                    break
            if tiene_prohibido:
                continue
                
            for tk in match_tokens:
                regex_pattern = rf"\b{re.escape(tk)}\b"
                if re.search(regex_pattern, name_upper):
                    return f, match_tokens
                    
        # Fallback genérico Universo_Elegible.xlsx en Salida Depuracion si no hay conflicto
        gen_file = dir_dep / "Universo_Elegible.xlsx"
        if gen_file.exists():
            return gen_file, match_tokens
            
    return None, match_tokens
