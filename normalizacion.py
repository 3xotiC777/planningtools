# -*- coding: utf-8 -*-
"""
normalizacion.py — Normalización de llaves de cruce.

Responsabilidad única: convertir las llaves (CÓDIGO, Id_PDV, Codigo DN) a un
formato comparable: texto, sin espacios, sin caracteres invisibles, en
mayúsculas y sin artefactos numéricos ('.0' de floats de Excel).
"""

from __future__ import annotations

import re
import unicodedata

import pandas as pd

# Caracteres invisibles frecuentes en datos exportados de Excel/BI:
# zero-width space/joiner, word-joiner, BOM, espacio duro, tabulaciones.
_INVISIBLES = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\xa0\t\r\n]")
_SUFIJO_DECIMAL = re.compile(r"\.0+$")


def normalizar_llave(serie: pd.Series) -> pd.Series:
    """
    Normaliza una serie llave (vectorizado, apto para 500k+ filas):

      1. Nulos -> cadena vacía.
      2. Conversión a texto; '7700010266.0' -> '7700010266'.
      3. Normalización Unicode NFKC (unifica dígitos/letras equivalentes).
      4. Eliminación de caracteres invisibles.
      5. Trim de espacios y conversión a MAYÚSCULAS.
    """
    s = serie.map(lambda x: "" if pd.isna(x) else str(x))
    s = s.str.replace(_SUFIJO_DECIMAL, "", regex=True)
    s = s.map(lambda x: unicodedata.normalize("NFKC", x))
    s = s.str.replace(_INVISIBLES, "", regex=True)
    s = s.str.strip().str.upper()
    return s.replace({"NAN": "", "NONE": "", "NAT": ""})


def llave_sufijo(serie_normalizada: pd.Series, n_digitos: int) -> pd.Series:
    """
    Llave de respaldo por últimos N dígitos (solo para valores numéricos).
    Se usa únicamente cuando la columna puente ('Codigo DN') no existe.
    """
    es_numerica = serie_normalizada.str.fullmatch(r"\d+", na=False)
    return serie_normalizada.where(~es_numerica, serie_normalizada.str[-n_digitos:])


def extraer_anio(serie: pd.Series) -> pd.Series:
    """
    Extrae el año de una columna que puede venir como fecha (UFA=2026-03-16),
    número (2026) o texto ('2026'). Devuelve Float64 con NaN si no aplica.
    """
    anios = pd.to_datetime(serie, errors="coerce").dt.year.astype("Float64")
    numerico = pd.to_numeric(serie, errors="coerce")
    return anios.fillna(numerico.where((numerico >= 1900) & (numerico <= 2100)))


def generar_codigo_puente(
    serie: pd.Series,
    prefijo: str | None = None,
    n_digitos: int = 7,
    prefijos_por_longitud: dict[str, str] | None = None,
    prefijo_defecto: str | None = None,
) -> pd.Series:
    """
    Construye una columna puente sin modificar la columna de origen.

    Admite dos estrategias configurables:
    - prefijo constante (ej. Nicaragua: ``160`` + últimos 7 dígitos);
    - prefijo por longitud (ej. Costa Rica: ``150`` si el código tiene
      9 dígitos y ``154`` en cualquier otro caso).

    Si no hay prefijo ni reglas por longitud, devuelve la llave normalizada.
    """
    norm = normalizar_llave(serie)
    sufijo = norm.str[-n_digitos:]
    if prefijos_por_longitud:
        reglas = {str(k).strip(): str(v).strip().upper()
                  for k, v in prefijos_por_longitud.items()}
        defecto = str(prefijo_defecto or "").strip().upper()
        prefijos = norm.str.len().astype(str).map(reglas).fillna(defecto)
        return prefijos + sufijo
    if prefijo:
        p = str(prefijo).strip().upper()
        return p + sufijo
    return norm



def normalizar_canal(serie: pd.Series) -> pd.Series:
    """
    Normaliza el canal mapeando alias conocidos:
    - CANAL ON: ON, ON PREMISE, PREMISE, RESTAURANTE -> 'ON'
    - CANAL OFF: OFF, HOME MARKET TRADICIONAL, BODEGA -> 'OFF'
    """
    s = normalizar_llave(serie)
    on_pattern = r"\b(ON|ON PREMISE|PREMISE|RESTAURANTE)\b"
    off_pattern = r"\b(OFF|HOME MARKET TRADICIONAL|HOME MARKET|BODEGA|TRADICIONAL)\b"
    
    res = pd.Series("OFF", index=s.index)
    m_on = s.str.contains(on_pattern, regex=True, na=False)
    res[m_on] = "ON"
    m_off = s.str.contains(off_pattern, regex=True, na=False)
    res[m_off] = "OFF"
    return res


def normalizar_fijo(serie: pd.Series) -> pd.Series:
    """
    Normaliza el estatus de Cliente Fijo:
    - FIJOS: SI, FIJO, YES, S, 1, TRUE -> 'FIJO'
    - VARIABLES: NO, VARIABLE, N, 0, FALSE -> 'VARIABLE'
    """
    s = normalizar_llave(serie)
    fijo_pattern = r"\b(SI|FIJO|YES|S|1|TRUE)\b"
    res = pd.Series("VARIABLE", index=s.index)
    m_fijo = s.str.contains(fijo_pattern, regex=True, na=False)
    res[m_fijo] = "FIJO"
    return res
