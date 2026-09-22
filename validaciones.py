# -*- coding: utf-8 -*-
"""
validaciones.py — Validaciones previas al proceso de depuración.

Responsabilidad única: verificar archivos, hojas, columnas requeridas y
calidad de las llaves (vacíos y duplicados) ANTES de ejecutar el cruce.
Cualquier incumplimiento detiene el proceso con un mensaje claro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from lector_excel import ErrorLectura, listar_hojas
from logs import obtener_logger


class ErrorValidacion(Exception):
    """Validación fallida; el mensaje es apto para ventana emergente."""


@dataclass
class ResultadoCalidadLlave:
    """Métricas de calidad de una columna llave."""
    total: int = 0
    vacios: int = 0
    duplicados: int = 0
    detalle: list[str] = field(default_factory=list)


def validar_archivo_y_hoja(ruta: Path, hoja: str, descripcion: str) -> None:
    """Verifica existencia del archivo y de la hoja indicada."""
    if not ruta.exists():
        raise ErrorValidacion(
            f"Falta el archivo de {descripcion}:\n{ruta.name}\n\n"
            f"Colóquelo en la carpeta 'Entrada' y vuelva a intentar."
        )
    try:
        hojas = listar_hojas(ruta)
    except ErrorLectura as exc:
        raise ErrorValidacion(str(exc)) from exc
    if hoja not in hojas:
        raise ErrorValidacion(
            f"La hoja '{hoja}' no existe en '{ruta.name}'.\n"
            f"Hojas disponibles: {', '.join(hojas)}"
        )
    obtener_logger().info(
        "Validación de %s: archivo y hoja '%s' OK.", descripcion, hoja
    )


def validar_columnas(df: pd.DataFrame, requeridas: list[str], nombre: str) -> None:
    """Verifica que el DataFrame contenga todas las columnas requeridas."""
    faltantes = [c for c in requeridas if c not in df.columns]
    if faltantes:
        disponibles = [c for c in df.columns if isinstance(c, str)][:25]
        raise ErrorValidacion(
            f"En la base '{nombre}' faltan las columnas: {', '.join(faltantes)}.\n\n"
            f"Columnas encontradas: {', '.join(disponibles)}"
        )
    obtener_logger().info(
        "Validación de columnas en '%s': OK (%s).", nombre, ", ".join(requeridas)
    )


def evaluar_calidad_llave(
    llave_normalizada: pd.Series, nombre: str
) -> ResultadoCalidadLlave:
    """
    Detecta códigos vacíos y duplicados en una llave YA normalizada.
    No detiene el proceso: reporta métricas para el log y la auditoría.
    """
    log = obtener_logger()
    r = ResultadoCalidadLlave(total=len(llave_normalizada))
    r.vacios = int((llave_normalizada == "").sum())
    no_vacias = llave_normalizada[llave_normalizada != ""]
    r.duplicados = int(no_vacias.duplicated().sum())

    if r.vacios:
        r.detalle.append(f"{nombre}: {r.vacios:,} códigos vacíos.")
    if r.duplicados:
        r.detalle.append(f"{nombre}: {r.duplicados:,} códigos duplicados.")
    for d in r.detalle:
        log.warning("Calidad de llave -> %s", d)
    if not r.detalle:
        log.info("Calidad de llave en '%s': sin vacíos ni duplicados.", nombre)
    return r


def resolver_columna(
    df: pd.DataFrame, candidato_o_alias: str | list[str], default: str | None = None
) -> str | None:
    """
    Busca la mejor coincidencia de nombre de columna en df.columns.
    Acepta una cadena única o una lista de alias candidatos.
    """
    if isinstance(candidato_o_alias, str):
        candidatos = [candidato_o_alias]
    else:
        candidatos = list(candidato_o_alias)

    cols_existentes = list(df.columns)
    # 1. Coincidencia exacta
    for c in candidatos:
        if c in cols_existentes:
            return c

    # 2. Coincidencia sin importar mayúsculas / minúsculas ni espacios
    cols_norm = {str(col).strip().upper(): col for col in cols_existentes}
    for c in candidatos:
        c_norm = str(c).strip().upper()
        if c_norm in cols_norm:
            return cols_norm[c_norm]

    # 3. Retornar default
    return default

