# -*- coding: utf-8 -*-
"""Cruce escalable de dos archivos y cálculo de distancia GPS en metros."""

from __future__ import annotations

import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

import customtkinter as ctk
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill

from normalizacion import normalizar_llave
from optimizacion_rutas import ErrorOptimizacion, detectar_columnas_coordenadas, llave


AZUL = "#0D5CAB"
AZUL_CLARO = "#33BDEE"
AZUL_OSCURO = "#1C293A"
VERDE = "#1E7D46"
FONDO = "#F2F2F2"
BORDE = "#D5D8DC"
GRIS = "#69727D"
NOMBRE_DISTANCIA = "Distancia GPS (m)"
NOMBRE_RANGO = "Rango distancia"
MAX_FILAS_EXCEL = 1_048_575  # Una fila se reserva para los encabezados.
RANGOS_DISTANCIA = [
    "0-50 m",
    "50-100 m",
    "100-150 m",
    "150-300 m",
    "300-500 m",
    "500-1000 m",
    ">1000 m",
    "GPS inválido",
]


def listar_hojas_excel(ruta: str | Path) -> list[str]:
    try:
        with pd.ExcelFile(ruta) as libro:
            return list(libro.sheet_names)
    except Exception as exc:
        raise ErrorOptimizacion(f"No fue posible leer las hojas de {Path(ruta).name}: {exc}") from exc


def _sugerir_hoja(hojas: list[str]) -> str:
    prioridades = ("BD", "BASE", "DATOS", "DATA", "PUNTOS", "UNIVERSO")
    mapa = {llave(hoja): hoja for hoja in hojas}
    return next((mapa[nombre] for nombre in prioridades if nombre in mapa), hojas[0])


def leer_columnas_excel(ruta: str | Path, hoja: str | int = 0) -> list[str]:
    """Lee solo los encabezados para configurar el cruce sin cargar toda la base."""
    try:
        columnas = [str(col) for col in pd.read_excel(ruta, sheet_name=hoja, nrows=0).columns]
    except Exception as exc:
        raise ErrorOptimizacion(f"No fue posible leer los encabezados de {Path(ruta).name}: {exc}") from exc
    if not columnas:
        raise ErrorOptimizacion(f"El archivo {Path(ruta).name} no contiene columnas.")
    return columnas


def _sugerir_puente(columnas: list[str]) -> str:
    mapa = {llave(col): col for col in columnas}
    prioridades = (
        "REFID", "TIENDAID", "IDPDV", "CODIGODN", "CODIGO", "COD", "ID",
        "RUTAVENTA", "RUTA", "MTFINAL",
    )
    return next((mapa[nombre] for nombre in prioridades if nombre in mapa), columnas[0])


def _sugerir_coordenadas(columnas: list[str]) -> tuple[str, str]:
    try:
        return detectar_columnas_coordenadas(pd.DataFrame(columns=columnas))
    except ErrorOptimizacion:
        primero = columnas[0]
        segundo = columnas[1] if len(columnas) > 1 else columnas[0]
        return primero, segundo


def _numero(serie: pd.Series) -> pd.Series:
    directa = pd.to_numeric(serie, errors="coerce")
    texto = serie.map(lambda valor: "" if pd.isna(valor) else str(valor).strip())
    alternativa = pd.to_numeric(texto.str.replace(",", ".", regex=False), errors="coerce")
    return directa.fillna(alternativa)


def _columnas_unicas(columnas: list[str]) -> list[str]:
    return list(dict.fromkeys(columnas))


def _leer_seleccion(ruta: str | Path, hoja: str | int, columnas: list[str]) -> pd.DataFrame:
    try:
        return pd.read_excel(ruta, sheet_name=hoja, usecols=_columnas_unicas(columnas))
    except Exception as exc:
        raise ErrorOptimizacion(f"No fue posible leer {Path(ruta).name}: {exc}") from exc


def _nombres_salida(
    columnas_origen: list[str],
    columnas_destino: list[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Conserva encabezados originales y diferencia únicamente las colisiones."""
    repetidas = {col.casefold() for col in columnas_origen} & {col.casefold() for col in columnas_destino}
    reservadas = {NOMBRE_DISTANCIA.casefold(), NOMBRE_RANGO.casefold()}
    usados: set[str] = set()

    def crear(columnas: list[str], etiqueta: str) -> dict[str, str]:
        mapa: dict[str, str] = {}
        for columna in columnas:
            base = f"{columna} ({etiqueta})" if columna.casefold() in repetidas | reservadas else columna
            candidato = base
            numero = 2
            while candidato.casefold() in usados:
                candidato = f"{base} {numero}"
                numero += 1
            usados.add(candidato.casefold())
            mapa[columna] = candidato
        return mapa

    return crear(columnas_origen, "Archivo 1"), crear(columnas_destino, "Archivo 2")


def _estimar_coincidencias(llaves_origen: pd.Series, llaves_destino: pd.Series) -> int:
    cantidades_o = llaves_origen[llaves_origen != ""].value_counts()
    cantidades_d = llaves_destino[llaves_destino != ""].value_counts()
    comunes = cantidades_o.index.intersection(cantidades_d.index)
    return int(np.minimum(
        cantidades_o.loc[comunes].to_numpy(dtype="int64"),
        cantidades_d.loc[comunes].to_numpy(dtype="int64"),
    ).sum())


def _rango_distancia(distancias: pd.Series) -> pd.Series:
    valores = distancias.to_numpy(float)
    condiciones = [
        valores <= 50,
        valores <= 100,
        valores <= 150,
        valores <= 300,
        valores <= 500,
        valores <= 1_000,
        valores > 1_000,
    ]
    return pd.Series(
        np.select(condiciones, RANGOS_DISTANCIA[:-1], default="GPS inválido"),
        index=distancias.index,
    )


def _distancia_haversine(
    latitud_origen: np.ndarray,
    longitud_origen: np.ndarray,
    latitud_destino: np.ndarray,
    longitud_destino: np.ndarray,
) -> np.ndarray:
    lat1, lon1 = np.radians(latitud_origen), np.radians(longitud_origen)
    lat2, lon2 = np.radians(latitud_destino), np.radians(longitud_destino)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    hav = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * 6_371_000.0 * np.arcsin(np.sqrt(np.clip(hav, 0, 1)))


def _coordenadas_esfera(latitudes: np.ndarray, longitudes: np.ndarray) -> np.ndarray:
    lat, lon = np.radians(latitudes), np.radians(longitudes)
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def _indices_mas_cercanos(origen: np.ndarray, destino: np.ndarray) -> np.ndarray:
    """Vecino exacto sobre la esfera; usa KD-tree y conserva un respaldo NumPy."""
    try:
        from scipy.spatial import cKDTree

        arbol = cKDTree(destino)
        _distancia, indices = arbol.query(origen, k=1, workers=-1)
        return np.asarray(indices, dtype="int64")
    except ImportError:
        # Respaldo para instalaciones antiguas: procesa por bloques para no
        # construir una matriz completa en memoria.
        indices = np.empty(len(origen), dtype="int64")
        tamano = max(1, min(1_000, 20_000_000 // max(1, len(destino))))
        for inicio in range(0, len(origen), tamano):
            fin = min(len(origen), inicio + tamano)
            productos = origen[inicio:fin] @ destino.T
            indices[inicio:fin] = np.argmax(productos, axis=1)
        return indices


def calcular_distancias_por_cercania(
    ruta_analisis: str | Path,
    ruta_referencia: str | Path,
    hoja_analisis: str | int = 0,
    hoja_referencia: str | int = 0,
    latitud_analisis: str | None = None,
    longitud_analisis: str | None = None,
    latitud_referencia: str | None = None,
    longitud_referencia: str | None = None,
    columnas_analisis: list[str] | None = None,
    columnas_referencia: list[str] | None = None,
) -> pd.DataFrame:
    """Asigna a cada fila de la primera base el punto más cercano de la segunda."""
    disponibles_o = leer_columnas_excel(ruta_analisis, hoja_analisis)
    disponibles_d = leer_columnas_excel(ruta_referencia, hoja_referencia)
    sugerida_lat_o, sugerida_lon_o = _sugerir_coordenadas(disponibles_o)
    sugerida_lat_d, sugerida_lon_d = _sugerir_coordenadas(disponibles_d)
    latitud_analisis = latitud_analisis or sugerida_lat_o
    longitud_analisis = longitud_analisis or sugerida_lon_o
    latitud_referencia = latitud_referencia or sugerida_lat_d
    longitud_referencia = longitud_referencia or sugerida_lon_d
    columnas_analisis = _columnas_unicas(
        [latitud_analisis, longitud_analisis] if columnas_analisis is None else columnas_analisis
    )
    columnas_referencia = _columnas_unicas(
        [latitud_referencia, longitud_referencia] if columnas_referencia is None else columnas_referencia
    )
    requeridas_o = [latitud_analisis, longitud_analisis, *columnas_analisis]
    requeridas_d = [latitud_referencia, longitud_referencia, *columnas_referencia]
    origen = _leer_seleccion(ruta_analisis, hoja_analisis, requeridas_o)
    destino = _leer_seleccion(ruta_referencia, hoja_referencia, requeridas_d)

    lat_o, lon_o = _numero(origen[latitud_analisis]), _numero(origen[longitud_analisis])
    lat_d, lon_d = _numero(destino[latitud_referencia]), _numero(destino[longitud_referencia])
    validas_o = lat_o.between(-90, 90) & lon_o.between(-180, 180)
    validas_d = lat_d.between(-90, 90) & lon_d.between(-180, 180)
    if not validas_o.any():
        raise ErrorOptimizacion("El archivo que se analiza no contiene coordenadas válidas.")
    if not validas_d.any():
        raise ErrorOptimizacion("El archivo de referencia no contiene coordenadas válidas.")

    posiciones_d = np.flatnonzero(validas_d.to_numpy())
    esfera_o = _coordenadas_esfera(lat_o.loc[validas_o].to_numpy(float), lon_o.loc[validas_o].to_numpy(float))
    esfera_d = _coordenadas_esfera(lat_d.loc[validas_d].to_numpy(float), lon_d.loc[validas_d].to_numpy(float))
    indices_locales = _indices_mas_cercanos(esfera_o, esfera_d)
    indices_destino = posiciones_d[indices_locales]
    posiciones_o = np.flatnonzero(validas_o.to_numpy())

    mapa_o, mapa_d = _nombres_salida(columnas_analisis, columnas_referencia)
    salida = origen[columnas_analisis].rename(columns=mapa_o).reset_index(drop=True)
    for columna, nombre_salida in mapa_d.items():
        valores = np.full(len(origen), None, dtype=object)
        valores[posiciones_o] = destino.iloc[indices_destino][columna].to_numpy()
        salida[nombre_salida] = valores

    metros = np.full(len(origen), np.nan, dtype=float)
    metros[posiciones_o] = _distancia_haversine(
        lat_o.iloc[posiciones_o].to_numpy(float),
        lon_o.iloc[posiciones_o].to_numpy(float),
        lat_d.iloc[indices_destino].to_numpy(float),
        lon_d.iloc[indices_destino].to_numpy(float),
    )
    salida[NOMBRE_DISTANCIA] = np.round(metros, 3)
    salida[NOMBRE_RANGO] = _rango_distancia(salida[NOMBRE_DISTANCIA])
    salida.attrs.update({
        "filas_analizadas": len(origen),
        "puntos_referencia": int(validas_d.sum()),
        "gps_invalidos": int((~validas_o).sum()),
    })
    return salida


def _orden_greedy(latitudes: np.ndarray, longitudes: np.ndarray) -> tuple[list[int], list[float]]:
    if not len(latitudes):
        return [], []
    pendientes = np.ones(len(latitudes), dtype=bool)
    actual = 0
    orden = [actual]
    distancias = [0.0]
    pendientes[actual] = False
    while pendientes.any():
        candidatos = np.flatnonzero(pendientes)
        lat_o = np.full(len(candidatos), latitudes[actual])
        lon_o = np.full(len(candidatos), longitudes[actual])
        metros = _distancia_haversine(lat_o, lon_o, latitudes[candidatos], longitudes[candidatos])
        posicion = int(np.argmin(metros))
        actual = int(candidatos[posicion])
        pendientes[actual] = False
        orden.append(actual)
        distancias.append(float(metros[posicion]))
    return orden, distancias


def ordenar_ruta_por_cercania(
    ruta: str | Path,
    hoja: str | int = 0,
    columna_reinicio: str | None = None,
    columna_latitud: str | None = None,
    columna_longitud: str | None = None,
    columnas_salida: list[str] | None = None,
) -> pd.DataFrame:
    """Ordena cada grupo visitando iterativamente el punto más cercano."""
    disponibles = leer_columnas_excel(ruta, hoja)
    sugerida_lat, sugerida_lon = _sugerir_coordenadas(disponibles)
    columna_latitud = columna_latitud or sugerida_lat
    columna_longitud = columna_longitud or sugerida_lon
    if columna_reinicio is None:
        mapa = {llave(col): col for col in disponibles}
        columna_reinicio = next(
            (mapa[nombre] for nombre in ("COMUNA", "CLUSTER", "RUTA", "ZONA", "AGENCIA") if nombre in mapa),
            disponibles[0],
        )
    columnas_salida = _columnas_unicas(columnas_salida or disponibles)
    if columna_reinicio not in columnas_salida:
        columnas_salida.insert(0, columna_reinicio)
    datos = _leer_seleccion(
        ruta, hoja,
        [columna_reinicio, columna_latitud, columna_longitud, *columnas_salida],
    )
    latitudes = _numero(datos[columna_latitud])
    longitudes = _numero(datos[columna_longitud])
    grupos = normalizar_llave(datos[columna_reinicio])
    posiciones_salida: list[int] = []
    ordenes: list[int] = []
    distancias_salida: list[float] = []

    for _grupo, indices in grupos.groupby(grupos, sort=True).groups.items():
        posiciones = np.asarray(list(indices), dtype="int64")
        validas = (
            latitudes.iloc[posiciones].between(-90, 90)
            & longitudes.iloc[posiciones].between(-180, 180)
        ).to_numpy()
        posiciones_validas = posiciones[validas]
        posiciones_invalidas = posiciones[~validas]
        orden_local, distancias = _orden_greedy(
            latitudes.iloc[posiciones_validas].to_numpy(float),
            longitudes.iloc[posiciones_validas].to_numpy(float),
        )
        ordenadas = posiciones_validas[orden_local].tolist() if orden_local else []
        ordenadas.extend(posiciones_invalidas.tolist())
        posiciones_salida.extend(ordenadas)
        ordenes.extend(range(1, len(ordenadas) + 1))
        distancias_salida.extend(distancias)
        distancias_salida.extend([float("nan")] * len(posiciones_invalidas))

    salida = datos.iloc[posiciones_salida][columnas_salida].reset_index(drop=True)
    salida["Orden_Ruta"] = ordenes
    salida["Distancia_Punto_Anterior_Metros"] = np.round(distancias_salida, 3)
    salida.attrs["columna_reinicio"] = columna_reinicio
    salida.attrs["grupos"] = int(grupos.nunique())
    salida.attrs["gps_invalidos"] = int((~(latitudes.between(-90, 90) & longitudes.between(-180, 180))).sum())
    return salida


def calcular_matriz_distancias(
    ruta_origen: str | Path,
    ruta_destino: str | Path,
    hoja_origen: str | int = 0,
    hoja_destino: str | int = 0,
    puente_origen: str | None = None,
    puente_destino: str | None = None,
    latitud_origen: str | None = None,
    longitud_origen: str | None = None,
    latitud_destino: str | None = None,
    longitud_destino: str | None = None,
    columnas_origen: list[str] | None = None,
    columnas_destino: list[str] | None = None,
) -> pd.DataFrame:
    """Cruza por la llave elegida y calcula una distancia por coincidencia.

    El resultado es largo (una fila por coincidencia), no una matriz cuadrada.
    Así, el costo depende de las coincidencias y no de todos los pares posibles.
    """
    disponibles_o = leer_columnas_excel(ruta_origen, hoja_origen)
    disponibles_d = leer_columnas_excel(ruta_destino, hoja_destino)
    puente_origen = puente_origen or _sugerir_puente(disponibles_o)
    puente_destino = puente_destino or _sugerir_puente(disponibles_d)
    sugerida_lat_o, sugerida_lon_o = _sugerir_coordenadas(disponibles_o)
    sugerida_lat_d, sugerida_lon_d = _sugerir_coordenadas(disponibles_d)
    latitud_origen = latitud_origen or sugerida_lat_o
    longitud_origen = longitud_origen or sugerida_lon_o
    latitud_destino = latitud_destino or sugerida_lat_d
    longitud_destino = longitud_destino or sugerida_lon_d
    columnas_origen = _columnas_unicas(
        [puente_origen, latitud_origen, longitud_origen] if columnas_origen is None else columnas_origen
    )
    columnas_destino = _columnas_unicas(
        [puente_destino, latitud_destino, longitud_destino] if columnas_destino is None else columnas_destino
    )

    requeridas_o = [puente_origen, latitud_origen, longitud_origen, *columnas_origen]
    requeridas_d = [puente_destino, latitud_destino, longitud_destino, *columnas_destino]
    faltantes_o = [col for col in _columnas_unicas(requeridas_o) if col not in disponibles_o]
    faltantes_d = [col for col in _columnas_unicas(requeridas_d) if col not in disponibles_d]
    if faltantes_o or faltantes_d:
        detalle = []
        if faltantes_o:
            detalle.append(f"Archivo 1: {', '.join(faltantes_o)}")
        if faltantes_d:
            detalle.append(f"Archivo 2: {', '.join(faltantes_d)}")
        raise ErrorOptimizacion("No se encontraron las columnas seleccionadas. " + " | ".join(detalle))

    origen = _leer_seleccion(ruta_origen, hoja_origen, requeridas_o)
    destino = _leer_seleccion(ruta_destino, hoja_destino, requeridas_d)
    origen["__MD_LLAVE"] = normalizar_llave(origen[puente_origen])
    destino["__MD_LLAVE"] = normalizar_llave(destino[puente_destino])
    # Las llaves repetidas se emparejan por orden de aparición. Esto evita un
    # producto cartesiano y reproduce el comportamiento fila a fila del modelo.
    origen["__MD_ORDEN"] = origen.groupby("__MD_LLAVE", sort=False).cumcount()
    destino["__MD_ORDEN"] = destino.groupby("__MD_LLAVE", sort=False).cumcount()
    estimadas = _estimar_coincidencias(origen["__MD_LLAVE"], destino["__MD_LLAVE"])
    if estimadas == 0:
        raise ErrorOptimizacion("No hay coincidencias entre las dos columnas puente seleccionadas.")
    if estimadas > MAX_FILAS_EXCEL:
        raise ErrorOptimizacion(
            f"El cruce produciría {estimadas:,} filas y Excel admite {MAX_FILAS_EXCEL:,}. "
            "Elija una llave más específica o elimine duplicados."
        )

    mapa_o, mapa_d = _nombres_salida(columnas_origen, columnas_destino)
    indices_o = origen["__MD_LLAVE"] != ""
    indices_d = destino["__MD_LLAVE"] != ""
    izquierda = origen.loc[indices_o, [*columnas_origen, "__MD_LLAVE", "__MD_ORDEN"]].rename(columns=mapa_o)
    derecha = destino.loc[indices_d, [*columnas_destino, "__MD_LLAVE", "__MD_ORDEN"]].rename(columns=mapa_d)
    izquierda["__MD_LAT_O"] = _numero(origen.loc[indices_o, latitud_origen])
    izquierda["__MD_LON_O"] = _numero(origen.loc[indices_o, longitud_origen])
    derecha["__MD_LAT_D"] = _numero(destino.loc[indices_d, latitud_destino])
    derecha["__MD_LON_D"] = _numero(destino.loc[indices_d, longitud_destino])

    combinado = izquierda.merge(derecha, on=["__MD_LLAVE", "__MD_ORDEN"], how="inner", sort=False)
    lat_o = combinado["__MD_LAT_O"].to_numpy(float)
    lon_o = combinado["__MD_LON_O"].to_numpy(float)
    lat_d = combinado["__MD_LAT_D"].to_numpy(float)
    lon_d = combinado["__MD_LON_D"].to_numpy(float)
    validas = (
        np.isfinite(lat_o) & np.isfinite(lon_o) & np.isfinite(lat_d) & np.isfinite(lon_d)
        & (np.abs(lat_o) <= 90) & (np.abs(lat_d) <= 90)
        & (np.abs(lon_o) <= 180) & (np.abs(lon_d) <= 180)
    )
    metros = np.full(len(combinado), np.nan, dtype=float)
    if validas.any():
        lat1, lon1 = np.radians(lat_o[validas]), np.radians(lon_o[validas])
        lat2, lon2 = np.radians(lat_d[validas]), np.radians(lon_d[validas])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        hav = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
        metros[validas] = 2 * 6_371_000.0 * np.arcsin(np.sqrt(np.clip(hav, 0, 1)))

    salida = combinado[[*mapa_o.values(), *mapa_d.values()]].copy()
    salida[NOMBRE_DISTANCIA] = np.round(metros, 3)
    salida[NOMBRE_RANGO] = _rango_distancia(salida[NOMBRE_DISTANCIA])
    salida.attrs.update({
        "filas_archivo_1": len(origen),
        "filas_archivo_2": len(destino),
        "coincidencias": len(salida),
        "llaves_sin_coincidencia_archivo_1": len(origen) - estimadas,
        "llaves_sin_coincidencia_archivo_2": len(destino) - estimadas,
    })
    return salida


def resumen_distancias(datos: pd.DataFrame) -> pd.DataFrame:
    conteos = datos[NOMBRE_RANGO].value_counts()
    resumen = pd.DataFrame({
        "Rango de distancia": RANGOS_DISTANCIA,
        "Cantidad": [int(conteos.get(rango, 0)) for rango in RANGOS_DISTANCIA],
    })
    return resumen.loc[resumen["Cantidad"] > 0].reset_index(drop=True)


def _valor_excel(valor: Any) -> Any:
    if valor is None or pd.isna(valor):
        return None
    if isinstance(valor, np.generic):
        return valor.item()
    return valor


def _encabezado(celda: WriteOnlyCell) -> WriteOnlyCell:
    celda.fill = PatternFill("solid", fgColor="0D5CAB")
    celda.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    return celda


def _columna_excel(numero: int) -> str:
    letras = ""
    while numero:
        numero, resto = divmod(numero - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def exportar_matriz_distancias(ruta: str | Path, datos: pd.DataFrame) -> Path:
    """Exporta RESUMEN y BD en escritura continua para contener el uso de memoria."""
    destino = Path(ruta)
    destino.parent.mkdir(parents=True, exist_ok=True)
    libro = Workbook(write_only=True)

    hoja_resumen = libro.create_sheet("RESUMEN")
    hoja_resumen.freeze_panes = "A2"
    resumen = resumen_distancias(datos)
    hoja_resumen.append([_encabezado(WriteOnlyCell(hoja_resumen, value=col)) for col in resumen.columns])
    for fila in resumen.itertuples(index=False, name=None):
        hoja_resumen.append([_valor_excel(valor) for valor in fila])
    hoja_resumen.auto_filter.ref = f"A1:B{len(resumen) + 1}"
    hoja_resumen.column_dimensions["A"].width = 24
    hoja_resumen.column_dimensions["B"].width = 14

    hoja_bd = libro.create_sheet("BD")
    hoja_bd.freeze_panes = "A2"
    hoja_bd.append([_encabezado(WriteOnlyCell(hoja_bd, value=col)) for col in datos.columns])
    indice_distancia = datos.columns.get_loc(NOMBRE_DISTANCIA)
    for fila in datos.itertuples(index=False, name=None):
        celdas = []
        for indice, valor in enumerate(fila):
            celda = WriteOnlyCell(hoja_bd, value=_valor_excel(valor))
            if indice == indice_distancia:
                celda.number_format = "#,##0.00"
            celdas.append(celda)
        hoja_bd.append(celdas)
    ultima_columna = _columna_excel(len(datos.columns))
    hoja_bd.auto_filter.ref = f"A1:{ultima_columna}{len(datos) + 1}"
    libro.save(destino)
    return destino


def resumen_rutas(datos: pd.DataFrame, columna_reinicio: str) -> pd.DataFrame:
    resumen = (
        datos.groupby(columna_reinicio, dropna=False, sort=True)
        .agg(
            Puntos_Visitados=("Orden_Ruta", "size"),
            Distancia_Total_Metros=("Distancia_Punto_Anterior_Metros", "sum"),
        )
        .reset_index()
    )
    return resumen


def exportar_ruta_optima(ruta: str | Path, datos: pd.DataFrame) -> Path:
    destino = Path(ruta)
    destino.parent.mkdir(parents=True, exist_ok=True)
    columna_reinicio = str(datos.attrs.get("columna_reinicio", datos.columns[0]))
    resumen = resumen_rutas(datos, columna_reinicio)
    libro = Workbook(write_only=True)

    hoja_datos = libro.create_sheet("Datos_Ordenados_Ruta")
    hoja_datos.freeze_panes = "A2"
    hoja_datos.append([_encabezado(WriteOnlyCell(hoja_datos, value=col)) for col in datos.columns])
    indices_metros = {
        datos.columns.get_loc("Distancia_Punto_Anterior_Metros"),
    }
    for fila in datos.itertuples(index=False, name=None):
        celdas = []
        for indice, valor in enumerate(fila):
            celda = WriteOnlyCell(hoja_datos, value=_valor_excel(valor))
            if indice in indices_metros:
                celda.number_format = "#,##0.00"
            celdas.append(celda)
        hoja_datos.append(celdas)
    hoja_datos.auto_filter.ref = f"A1:{_columna_excel(len(datos.columns))}{len(datos) + 1}"

    etiqueta_grupo = columna_reinicio.title() if columna_reinicio.isupper() else columna_reinicio
    nombre_resumen = f"Total_Por_{etiqueta_grupo}".replace(" ", "_")[:31]
    hoja_resumen = libro.create_sheet(nombre_resumen)
    hoja_resumen.freeze_panes = "A2"
    hoja_resumen.append([_encabezado(WriteOnlyCell(hoja_resumen, value=col)) for col in resumen.columns])
    for fila in resumen.itertuples(index=False, name=None):
        hoja_resumen.append([_valor_excel(valor) for valor in fila])
    hoja_resumen.auto_filter.ref = f"A1:{_columna_excel(len(resumen.columns))}{len(resumen) + 1}"
    libro.save(destino)
    return destino


class FrameComparacionDistancias(ctk.CTkFrame):
    """Pantalla para configurar un cruce GPS sin exigir nombres fijos."""

    def __init__(self, master, rutas: dict, _config: dict, modo: str = "puente") -> None:
        super().__init__(master, fg_color=FONDO)
        self.rutas = rutas
        self.modo = modo
        self.ruta_origen: Path | None = None
        self.ruta_destino: Path | None = None
        self.matriz: pd.DataFrame | None = None
        self.columnas_origen: list[str] = []
        self.columnas_destino: list[str] = []
        self.hojas_origen: list[str] = []
        self.hojas_destino: list[str] = []
        self.vars_origen: dict[str, ctk.BooleanVar] = {}
        self.vars_destino: dict[str, ctk.BooleanVar] = {}

        descripcion = (
            "Cruce dos archivos por una columna puente seleccionable."
            if modo == "puente"
            else "Para cada punto del archivo 1, encuentre el punto geográfico más cercano del archivo 2."
        )
        ctk.CTkLabel(self, text=descripcion, text_color=GRIS, font=ctk.CTkFont(size=14)).pack(anchor="w", padx=22, pady=(2, 8))
        contenido = ctk.CTkScrollableFrame(self, fg_color="transparent")
        contenido.pack(fill="both", expand=True)

        tarjetas = ctk.CTkFrame(contenido, fg_color="transparent")
        tarjetas.pack(fill="x", padx=22, pady=4)
        tarjetas.grid_columnconfigure((0, 1), weight=1)
        self._crear_tarjeta(tarjetas, "origen", "Archivo 1", 0)
        self._crear_tarjeta(tarjetas, "destino", "Archivo 2", 1)

        acciones = ctk.CTkFrame(contenido, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        acciones.pack(fill="x", padx=22, pady=8)
        texto_calcular = "Cruzar por puente" if modo == "puente" else "Buscar más cercanos"
        self.btn_calcular = ctk.CTkButton(acciones, text=texto_calcular, fg_color=AZUL, hover_color=AZUL_CLARO, command=self._calcular)
        self.btn_calcular.pack(side="left", padx=12, pady=12)
        self.btn_exportar = ctk.CTkButton(acciones, text="Exportar Excel", fg_color=VERDE, state="disabled", command=self._exportar)
        self.btn_exportar.pack(side="right", padx=12, pady=12)
        self.lbl_estado = ctk.CTkLabel(acciones, text="Seleccione los dos archivos y configure las columnas.", text_color=GRIS, anchor="w")
        self.lbl_estado.pack(side="left", fill="x", expand=True, padx=10)

        ayuda_inicial = (
            "Puente: columna que identifica el mismo registro en ambos archivos. GPS: coordenadas que se compararán."
            if modo == "puente"
            else "El archivo 1 es la base analizada. Para cada fila se anexan los datos del punto más cercano del archivo 2."
        )
        self.lbl_ayuda = ctk.CTkLabel(contenido, text=ayuda_inicial, text_color="#52606D", fg_color="#E9F4FC", corner_radius=7, anchor="w")
        self.lbl_ayuda.pack(fill="x", padx=22, pady=(0, 8), ipady=5)
        self.vista = ctk.CTkTextbox(contenido, height=210, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10, font=("Consolas", 11))
        self.vista.pack(fill="both", expand=True, padx=22, pady=(0, 22))
        self.vista.insert("1.0", "La vista previa de la hoja BD aparecerá aquí.")
        self.vista.configure(state="disabled")

    def _crear_tarjeta(self, master, lado: str, titulo: str, columna: int) -> None:
        tarjeta = ctk.CTkFrame(master, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        tarjeta.grid(row=0, column=columna, sticky="nsew", padx=(0, 5) if columna == 0 else (5, 0))
        ctk.CTkLabel(tarjeta, text=titulo, font=ctk.CTkFont(size=16, weight="bold"), text_color=AZUL_OSCURO).pack(anchor="w", padx=12, pady=(10, 1))
        etiqueta = ctk.CTkLabel(tarjeta, text="Ningún archivo seleccionado", text_color=GRIS, anchor="w")
        etiqueta.pack(fill="x", padx=12)
        boton = ctk.CTkButton(tarjeta, text="Seleccionar Excel", fg_color=AZUL, hover_color=AZUL_CLARO, command=lambda l=lado: self._elegir_archivo(l))
        boton.pack(anchor="w", padx=12, pady=(5, 8))
        setattr(self, f"lbl_{lado}", etiqueta)
        boton.bind("<Enter>", lambda _e: self._ayuda("Seleccione un Excel. Solo se leen sus encabezados hasta que pulse Crear comparación."))

        opciones = ["Seleccione un archivo"]
        fila_hoja = ctk.CTkFrame(tarjeta, fg_color="transparent")
        fila_hoja.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(fila_hoja, text="Hoja", width=115, anchor="w", text_color=GRIS).pack(side="left")
        combo_hoja = ctk.CTkComboBox(
            fila_hoja, values=opciones, state="readonly", width=190,
            command=lambda valor, l=lado: self._cargar_hoja(l, valor),
        )
        combo_hoja.set(opciones[0])
        combo_hoja.pack(side="left", fill="x", expand=True)
        combo_hoja.bind("<Enter>", lambda _e: self._ayuda("Hoja: pestaña del Excel que contiene la base que desea comparar."))
        setattr(self, f"cmb_hoja_{lado}", combo_hoja)

        ayudas = {
            "puente": "Puente: código, ruta o ID utilizado para encontrar el mismo registro en los dos archivos.",
            "latitud": "Latitud GPS: coordenada vertical utilizada para calcular la distancia.",
            "longitud": "Longitud GPS: coordenada horizontal utilizada para calcular la distancia.",
        }
        campos = [("latitud", "Latitud GPS"), ("longitud", "Longitud GPS")]
        if self.modo == "puente":
            campos.insert(0, ("puente", "Columna puente"))
        for nombre, texto in campos:
            fila = ctk.CTkFrame(tarjeta, fg_color="transparent")
            fila.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(fila, text=texto, width=115, anchor="w", text_color=GRIS).pack(side="left")
            combo = ctk.CTkComboBox(fila, values=opciones, state="readonly", width=190)
            combo.set(opciones[0])
            combo.pack(side="left", fill="x", expand=True)
            combo.bind("<Enter>", lambda _e, n=nombre: self._ayuda(ayudas[n]))
            setattr(self, f"cmb_{nombre}_{lado}", combo)

        fila_salida = ctk.CTkFrame(tarjeta, fg_color="transparent")
        fila_salida.pack(fill="x", padx=12, pady=(7, 1))
        ctk.CTkLabel(fila_salida, text="Columnas en el archivo final", font=ctk.CTkFont(weight="bold"), text_color=AZUL_OSCURO).pack(side="left")
        ctk.CTkButton(fila_salida, text="Todas", width=52, height=24, fg_color="transparent", border_width=1, border_color=BORDE, text_color=AZUL, command=lambda l=lado: self._marcar_columnas(l, True)).pack(side="right", padx=(3, 0))
        ctk.CTkButton(fila_salida, text="Ninguna", width=62, height=24, fg_color="transparent", border_width=1, border_color=BORDE, text_color=AZUL, command=lambda l=lado: self._marcar_columnas(l, False)).pack(side="right")
        marco_columnas = ctk.CTkScrollableFrame(tarjeta, height=118, fg_color="#F7F9FB", corner_radius=7)
        marco_columnas.pack(fill="x", padx=12, pady=(2, 10))
        ctk.CTkLabel(marco_columnas, text="Cargue el archivo para elegir columnas", text_color=GRIS).pack(anchor="w", padx=4, pady=4)
        setattr(self, f"marco_columnas_{lado}", marco_columnas)

    def _ayuda(self, texto: str) -> None:
        self.lbl_ayuda.configure(text=texto)

    def _elegir_archivo(self, lado: str) -> None:
        ruta = filedialog.askopenfilename(title=f"Seleccione el {lado}", filetypes=[("Excel", "*.xlsx *.xls")])
        if not ruta:
            return
        try:
            hojas = listar_hojas_excel(ruta)
        except ErrorOptimizacion as exc:
            messagebox.showerror("No se pudo cargar", str(exc))
            return
        if not hojas:
            messagebox.showerror("No se pudo cargar", "El archivo no contiene hojas.")
            return
        setattr(self, f"ruta_{lado}", Path(ruta))
        setattr(self, f"hojas_{lado}", hojas)
        getattr(self, f"lbl_{lado}").configure(text=Path(ruta).name)
        hoja = _sugerir_hoja(hojas)
        combo_hoja = getattr(self, f"cmb_hoja_{lado}")
        combo_hoja.configure(values=hojas)
        combo_hoja.set(hoja)
        self._cargar_hoja(lado, hoja)

    def _cargar_hoja(self, lado: str, hoja: str) -> None:
        ruta = getattr(self, f"ruta_{lado}")
        if not ruta or hoja not in getattr(self, f"hojas_{lado}"):
            return
        try:
            columnas = leer_columnas_excel(ruta, hoja)
        except ErrorOptimizacion as exc:
            messagebox.showerror("No se pudo cargar la hoja", str(exc))
            return
        setattr(self, f"columnas_{lado}", columnas)
        puente = _sugerir_puente(columnas)
        latitud, longitud = _sugerir_coordenadas(columnas)
        configuraciones = [("latitud", latitud), ("longitud", longitud)]
        predeterminadas = {latitud, longitud}
        if self.modo == "puente":
            configuraciones.insert(0, ("puente", puente))
            predeterminadas.add(puente)
        for nombre, valor in configuraciones:
            combo = getattr(self, f"cmb_{nombre}_{lado}")
            combo.configure(values=columnas)
            combo.set(valor)
        self._crear_checks_columnas(lado, predeterminadas)
        self.matriz = None
        self.btn_exportar.configure(state="disabled")
        self.lbl_estado.configure(text="Configuración actualizada. Pulse Crear comparación cuando esté lista.")

    def _crear_checks_columnas(self, lado: str, predeterminadas: set[str]) -> None:
        marco = getattr(self, f"marco_columnas_{lado}")
        for widget in marco.winfo_children():
            widget.destroy()
        variables: dict[str, ctk.BooleanVar] = {}
        for columna in getattr(self, f"columnas_{lado}"):
            variable = ctk.BooleanVar(value=columna in predeterminadas)
            ctk.CTkCheckBox(marco, text=columna, variable=variable, height=24, text_color=AZUL_OSCURO).pack(anchor="w", padx=4, pady=1)
            variables[columna] = variable
        setattr(self, f"vars_{lado}", variables)

    def _marcar_columnas(self, lado: str, valor: bool) -> None:
        for variable in getattr(self, f"vars_{lado}").values():
            variable.set(valor)

    def _seleccionadas(self, lado: str) -> list[str]:
        return [col for col, variable in getattr(self, f"vars_{lado}").items() if variable.get()]

    def _calcular(self) -> None:
        if not self.ruta_origen or not self.ruta_destino:
            messagebox.showwarning("Archivos requeridos", "Seleccione los dos archivos Excel.")
            return
        columnas_o, columnas_d = self._seleccionadas("origen"), self._seleccionadas("destino")
        if not columnas_o and not columnas_d:
            messagebox.showwarning("Columnas requeridas", "Seleccione al menos una columna para el archivo final.")
            return
        if self.modo == "puente":
            funcion = calcular_matriz_distancias
            parametros = {
                "ruta_origen": self.ruta_origen, "ruta_destino": self.ruta_destino,
                "hoja_origen": self.cmb_hoja_origen.get(), "hoja_destino": self.cmb_hoja_destino.get(),
                "puente_origen": self.cmb_puente_origen.get(), "puente_destino": self.cmb_puente_destino.get(),
                "latitud_origen": self.cmb_latitud_origen.get(), "longitud_origen": self.cmb_longitud_origen.get(),
                "latitud_destino": self.cmb_latitud_destino.get(), "longitud_destino": self.cmb_longitud_destino.get(),
                "columnas_origen": columnas_o, "columnas_destino": columnas_d,
            }
        else:
            funcion = calcular_distancias_por_cercania
            parametros = {
                "ruta_analisis": self.ruta_origen, "ruta_referencia": self.ruta_destino,
                "hoja_analisis": self.cmb_hoja_origen.get(), "hoja_referencia": self.cmb_hoja_destino.get(),
                "latitud_analisis": self.cmb_latitud_origen.get(), "longitud_analisis": self.cmb_longitud_origen.get(),
                "latitud_referencia": self.cmb_latitud_destino.get(), "longitud_referencia": self.cmb_longitud_destino.get(),
                "columnas_analisis": columnas_o, "columnas_referencia": columnas_d,
            }
        self.btn_calcular.configure(state="disabled")
        self.btn_exportar.configure(state="disabled")
        self.lbl_estado.configure(text="Leyendo, cruzando y calculando distancias en metros...")

        def tarea() -> None:
            try:
                matriz = funcion(**parametros)
                self.after(0, lambda: self._terminado(matriz))
            except Exception as exc:
                self.after(0, lambda e=exc: self._error(e))

        threading.Thread(target=tarea, daemon=True).start()

    def _terminado(self, matriz: pd.DataFrame) -> None:
        self.matriz = matriz
        self.btn_calcular.configure(state="normal")
        self.btn_exportar.configure(state="normal")
        if self.modo == "puente":
            sin_o = matriz.attrs.get("llaves_sin_coincidencia_archivo_1", 0)
            sin_d = matriz.attrs.get("llaves_sin_coincidencia_archivo_2", 0)
            estado = f"Comparación lista: {len(matriz):,} coincidencias · Sin cruce: {sin_o:,} del archivo 1 y {sin_d:,} del archivo 2."
        else:
            invalidos = matriz.attrs.get("gps_invalidos", 0)
            estado = f"Cercanía lista: {len(matriz):,} puntos analizados · GPS inválidos: {invalidos:,}."
        self.lbl_estado.configure(text=estado)
        previa = matriz.iloc[:12, :10].to_string(index=False)
        if len(matriz) > 12 or len(matriz.columns) > 10:
            previa += "\n\nVista previa parcial. El Excel conserva todas las filas y columnas seleccionadas."
        self.vista.configure(state="normal")
        self.vista.delete("1.0", "end")
        self.vista.insert("1.0", previa)
        self.vista.configure(state="disabled")

    def _error(self, exc: Exception) -> None:
        self.btn_calcular.configure(state="normal")
        self.lbl_estado.configure(text="No se pudo crear la comparación.")
        messagebox.showerror("Matriz de distancias", str(exc))

    def _exportar(self) -> None:
        if self.matriz is None:
            return
        carpeta = self.rutas.get("salida_matriz_distancias")
        ruta = filedialog.asksaveasfilename(
            title="Exportar comparación de distancias",
            defaultextension=".xlsx",
            initialfile=("Comparativa_Distancias_GPS.xlsx" if self.modo == "puente" else "Puntos_Mas_Cercanos.xlsx"),
            initialdir=str(carpeta) if carpeta else None,
            filetypes=[("Excel", "*.xlsx")],
        )
        if not ruta:
            return
        self.btn_exportar.configure(state="disabled", text="Exportando...")
        self.lbl_estado.configure(text="Escribiendo RESUMEN y BD sin duplicar la memoria...")

        def tarea() -> None:
            try:
                salida = exportar_matriz_distancias(ruta, self.matriz)
                self.after(0, lambda: self._exportado(salida))
            except Exception as exc:
                self.after(0, lambda e=exc: self._error_exportacion(e))

        threading.Thread(target=tarea, daemon=True).start()

    def _exportado(self, ruta: Path) -> None:
        self.btn_exportar.configure(state="normal", text="Exportar Excel")
        self.lbl_estado.configure(text=f"Archivo exportado: {ruta.name}")
        messagebox.showinfo("Matriz exportada", f"Se guardó en:\n{ruta}")

    def _error_exportacion(self, exc: Exception) -> None:
        self.btn_exportar.configure(state="normal", text="Exportar Excel")
        self.lbl_estado.configure(text="No se pudo exportar el archivo.")
        messagebox.showerror("No se pudo exportar", str(exc))


class FrameRutaOptima(ctk.CTkFrame):
    """Ordena visitas por cercanía y reinicia el recorrido por un grupo elegido."""

    def __init__(self, master, rutas: dict, _config: dict) -> None:
        super().__init__(master, fg_color=FONDO)
        self.rutas = rutas
        self.ruta_archivo: Path | None = None
        self.hojas: list[str] = []
        self.columnas: list[str] = []
        self.vars_columnas: dict[str, ctk.BooleanVar] = {}
        self.resultado: pd.DataFrame | None = None

        ctk.CTkLabel(
            self,
            text="Ordene cada grupo visitando siempre el punto pendiente más cercano. La distancia se expresa en metros.",
            text_color=GRIS, font=ctk.CTkFont(size=14),
        ).pack(anchor="w", padx=22, pady=(2, 8))
        contenido = ctk.CTkScrollableFrame(self, fg_color="transparent")
        contenido.pack(fill="both", expand=True)

        tarjeta = ctk.CTkFrame(contenido, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        tarjeta.pack(fill="x", padx=22, pady=4)
        superior = ctk.CTkFrame(tarjeta, fg_color="transparent")
        superior.pack(fill="x", padx=12, pady=(10, 5))
        self.lbl_archivo = ctk.CTkLabel(superior, text="Ningún archivo seleccionado", text_color=GRIS, anchor="w")
        self.lbl_archivo.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(superior, text="Seleccionar Excel", fg_color=AZUL, hover_color=AZUL_CLARO, command=self._elegir_archivo).pack(side="right")

        selectores = ctk.CTkFrame(tarjeta, fg_color="transparent")
        selectores.pack(fill="x", padx=12, pady=4)
        selectores.grid_columnconfigure((1, 3), weight=1)
        opciones = ["Seleccione un archivo"]
        campos = (
            ("hoja", "Hoja", 0, 0),
            ("reinicio", "Reiniciar ruta por", 0, 2),
            ("latitud", "Latitud GPS", 1, 0),
            ("longitud", "Longitud GPS", 1, 2),
        )
        ayudas = {
            "hoja": "Hoja: pestaña del Excel que contiene los puntos.",
            "reinicio": "Reiniciar ruta por: columna que define cada grupo o clúster, por ejemplo COMUNA, RUTA o AGENCIA.",
            "latitud": "Latitud GPS utilizada para ordenar los puntos.",
            "longitud": "Longitud GPS utilizada para ordenar los puntos.",
        }
        for nombre, texto, fila, columna in campos:
            ctk.CTkLabel(selectores, text=texto, width=118, anchor="w", text_color=GRIS).grid(row=fila, column=columna, sticky="w", padx=(0, 4), pady=3)
            comando = self._cargar_hoja if nombre == "hoja" else None
            combo = ctk.CTkComboBox(selectores, values=opciones, state="readonly", command=comando)
            combo.set(opciones[0])
            combo.grid(row=fila, column=columna + 1, sticky="ew", padx=(0, 14), pady=3)
            combo.bind("<Enter>", lambda _e, n=nombre: self._ayuda(ayudas[n]))
            setattr(self, f"cmb_{nombre}", combo)

        fila_columnas = ctk.CTkFrame(tarjeta, fg_color="transparent")
        fila_columnas.pack(fill="x", padx=12, pady=(7, 1))
        ctk.CTkLabel(fila_columnas, text="Columnas en el archivo final", font=ctk.CTkFont(weight="bold"), text_color=AZUL_OSCURO).pack(side="left")
        ctk.CTkButton(fila_columnas, text="Todas", width=52, height=24, fg_color="transparent", border_width=1, border_color=BORDE, text_color=AZUL, command=lambda: self._marcar_columnas(True)).pack(side="right", padx=(3, 0))
        ctk.CTkButton(fila_columnas, text="Ninguna", width=62, height=24, fg_color="transparent", border_width=1, border_color=BORDE, text_color=AZUL, command=lambda: self._marcar_columnas(False)).pack(side="right")
        self.marco_columnas = ctk.CTkScrollableFrame(tarjeta, height=150, fg_color="#F7F9FB", corner_radius=7)
        self.marco_columnas.pack(fill="x", padx=12, pady=(2, 10))
        ctk.CTkLabel(self.marco_columnas, text="Cargue el archivo para elegir columnas", text_color=GRIS).pack(anchor="w", padx=4, pady=4)

        acciones = ctk.CTkFrame(contenido, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        acciones.pack(fill="x", padx=22, pady=8)
        self.btn_calcular = ctk.CTkButton(acciones, text="Crear ruta ordenada", fg_color=AZUL, hover_color=AZUL_CLARO, command=self._calcular)
        self.btn_calcular.pack(side="left", padx=12, pady=12)
        self.btn_exportar = ctk.CTkButton(acciones, text="Exportar Excel", fg_color=VERDE, state="disabled", command=self._exportar)
        self.btn_exportar.pack(side="right", padx=12, pady=12)
        self.lbl_estado = ctk.CTkLabel(acciones, text="Seleccione un archivo y configure el grupo de reinicio.", text_color=GRIS, anchor="w")
        self.lbl_estado.pack(side="left", fill="x", expand=True, padx=10)
        self.lbl_ayuda = ctk.CTkLabel(contenido, text="El primer registro válido inicia cada grupo; los siguientes se ordenan por el vecino más cercano.", text_color="#52606D", fg_color="#E9F4FC", corner_radius=7, anchor="w")
        self.lbl_ayuda.pack(fill="x", padx=22, pady=(0, 8), ipady=5)
        self.vista = ctk.CTkTextbox(contenido, height=210, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10, font=("Consolas", 11))
        self.vista.pack(fill="both", expand=True, padx=22, pady=(0, 22))
        self.vista.insert("1.0", "La vista previa de Datos_Ordenados_Ruta aparecerá aquí.")
        self.vista.configure(state="disabled")

    def _ayuda(self, texto: str) -> None:
        self.lbl_ayuda.configure(text=texto)

    def _elegir_archivo(self) -> None:
        ruta = filedialog.askopenfilename(title="Seleccione la base para ordenar", filetypes=[("Excel", "*.xlsx *.xls")])
        if not ruta:
            return
        try:
            hojas = listar_hojas_excel(ruta)
        except ErrorOptimizacion as exc:
            messagebox.showerror("No se pudo cargar", str(exc))
            return
        self.ruta_archivo = Path(ruta)
        self.hojas = hojas
        self.lbl_archivo.configure(text=self.ruta_archivo.name)
        hoja = _sugerir_hoja(hojas)
        self.cmb_hoja.configure(values=hojas)
        self.cmb_hoja.set(hoja)
        self._cargar_hoja(hoja)

    def _cargar_hoja(self, hoja: str) -> None:
        if not self.ruta_archivo or hoja not in self.hojas:
            return
        try:
            self.columnas = leer_columnas_excel(self.ruta_archivo, hoja)
        except ErrorOptimizacion as exc:
            messagebox.showerror("No se pudo cargar la hoja", str(exc))
            return
        latitud, longitud = _sugerir_coordenadas(self.columnas)
        mapa = {llave(col): col for col in self.columnas}
        reinicio = next((mapa[n] for n in ("COMUNA", "CLUSTER", "RUTA", "ZONA", "AGENCIA") if n in mapa), self.columnas[0])
        for combo, valor in ((self.cmb_reinicio, reinicio), (self.cmb_latitud, latitud), (self.cmb_longitud, longitud)):
            combo.configure(values=self.columnas)
            combo.set(valor)
        for widget in self.marco_columnas.winfo_children():
            widget.destroy()
        self.vars_columnas = {}
        for columna in self.columnas:
            variable = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(self.marco_columnas, text=columna, variable=variable, height=24, text_color=AZUL_OSCURO).pack(anchor="w", padx=4, pady=1)
            self.vars_columnas[columna] = variable
        self.resultado = None
        self.btn_exportar.configure(state="disabled")
        self.lbl_estado.configure(text="Configuración actualizada. Pulse Crear ruta ordenada.")

    def _marcar_columnas(self, valor: bool) -> None:
        for variable in self.vars_columnas.values():
            variable.set(valor)

    def _calcular(self) -> None:
        if not self.ruta_archivo:
            messagebox.showwarning("Archivo requerido", "Seleccione el archivo Excel.")
            return
        seleccionadas = [col for col, variable in self.vars_columnas.items() if variable.get()]
        if not seleccionadas:
            messagebox.showwarning("Columnas requeridas", "Seleccione al menos una columna para el archivo final.")
            return
        parametros = {
            "ruta": self.ruta_archivo,
            "hoja": self.cmb_hoja.get(),
            "columna_reinicio": self.cmb_reinicio.get(),
            "columna_latitud": self.cmb_latitud.get(),
            "columna_longitud": self.cmb_longitud.get(),
            "columnas_salida": seleccionadas,
        }
        self.btn_calcular.configure(state="disabled")
        self.btn_exportar.configure(state="disabled")
        self.lbl_estado.configure(text="Agrupando y ordenando cada ruta por cercanía...")

        def tarea() -> None:
            try:
                resultado = ordenar_ruta_por_cercania(**parametros)
                self.after(0, lambda: self._terminado(resultado))
            except Exception as exc:
                self.after(0, lambda e=exc: self._error(e))

        threading.Thread(target=tarea, daemon=True).start()

    def _terminado(self, resultado: pd.DataFrame) -> None:
        self.resultado = resultado
        self.btn_calcular.configure(state="normal")
        self.btn_exportar.configure(state="normal")
        self.lbl_estado.configure(text=f"Ruta lista: {len(resultado):,} puntos · {resultado.attrs.get('grupos', 0):,} grupos · GPS inválidos: {resultado.attrs.get('gps_invalidos', 0):,}.")
        previa = resultado.iloc[:14, :10].to_string(index=False)
        if len(resultado) > 14 or len(resultado.columns) > 10:
            previa += "\n\nVista previa parcial. El Excel conserva todas las filas y columnas seleccionadas."
        self.vista.configure(state="normal")
        self.vista.delete("1.0", "end")
        self.vista.insert("1.0", previa)
        self.vista.configure(state="disabled")

    def _error(self, exc: Exception) -> None:
        self.btn_calcular.configure(state="normal")
        self.lbl_estado.configure(text="No se pudo ordenar la ruta.")
        messagebox.showerror("Ruta ordenada", str(exc))

    def _exportar(self) -> None:
        if self.resultado is None:
            return
        carpeta = self.rutas.get("salida_matriz_distancias")
        ruta = filedialog.asksaveasfilename(
            title="Exportar ruta ordenada", defaultextension=".xlsx",
            initialfile="Distancias_Ruta_Optima.xlsx",
            initialdir=str(carpeta) if carpeta else None,
            filetypes=[("Excel", "*.xlsx")],
        )
        if not ruta:
            return
        self.btn_exportar.configure(state="disabled", text="Exportando...")

        def tarea() -> None:
            try:
                salida = exportar_ruta_optima(ruta, self.resultado)
                self.after(0, lambda: self._exportado(salida))
            except Exception as exc:
                self.after(0, lambda e=exc: self._error_exportacion(e))

        threading.Thread(target=tarea, daemon=True).start()

    def _exportado(self, ruta: Path) -> None:
        self.btn_exportar.configure(state="normal", text="Exportar Excel")
        self.lbl_estado.configure(text=f"Archivo exportado: {ruta.name}")
        messagebox.showinfo("Ruta exportada", f"Se guardó en:\n{ruta}")

    def _error_exportacion(self, exc: Exception) -> None:
        self.btn_exportar.configure(state="normal", text="Exportar Excel")
        self.lbl_estado.configure(text="No se pudo exportar el archivo.")
        messagebox.showerror("No se pudo exportar", str(exc))


class FrameMatrizDistancias(ctk.CTkFrame):
    """Contenedor de las tres herramientas de distancia."""

    def __init__(self, master, rutas: dict, config: dict) -> None:
        super().__init__(master, fg_color=FONDO)
        ctk.CTkLabel(self, text="Matriz de distancias", font=ctk.CTkFont(size=27, weight="bold"), text_color=AZUL).pack(anchor="w", padx=28, pady=(22, 2))
        ctk.CTkLabel(self, text="Compare coordenadas, encuentre vecinos cercanos u ordene recorridos.", text_color=GRIS, font=ctk.CTkFont(size=14)).pack(anchor="w", padx=28, pady=(0, 10))
        nombres = ["Por columna puente", "Por cercanía", "Ruta ordenada"]
        self.tabs = ctk.CTkSegmentedButton(
            self, values=nombres, command=self._cambiar_tab,
            selected_color=AZUL, selected_hover_color=AZUL_CLARO,
            unselected_color="white", unselected_hover_color="#E9F4FC", text_color=AZUL_OSCURO,
        )
        self.tabs.pack(anchor="w", padx=28, pady=(0, 10))
        self.contenedor = ctk.CTkFrame(self, fg_color="transparent")
        self.contenedor.pack(fill="both", expand=True)
        self.tab_puente = FrameComparacionDistancias(self.contenedor, rutas, config, modo="puente")
        self.tab_cercania = FrameComparacionDistancias(self.contenedor, rutas, config, modo="cercania")
        self.tab_ruta = FrameRutaOptima(self.contenedor, rutas, config)
        self.tabs.set(nombres[0])
        self._cambiar_tab(nombres[0])

    def _cambiar_tab(self, nombre: str) -> None:
        for frame in (self.tab_puente, self.tab_cercania, self.tab_ruta):
            frame.pack_forget()
        destino = {
            "Por columna puente": self.tab_puente,
            "Por cercanía": self.tab_cercania,
            "Ruta ordenada": self.tab_ruta,
        }[nombre]
        destino.pack(fill="both", expand=True)
