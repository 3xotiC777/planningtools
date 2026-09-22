# -*- coding: utf-8 -*-
"""Carga, cruce, edición y exportación de puntos y polígonos."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from optimizacion_rutas import ErrorOptimizacion, detectar_columnas_coordenadas, llave


def cargar_puntos_excel(ruta: str | Path) -> tuple[pd.DataFrame, str, str]:
    try:
        datos = pd.read_excel(ruta)
    except Exception as exc:
        raise ErrorOptimizacion(f"No fue posible leer el Excel de puntos: {exc}") from exc
    lat, lon = detectar_columnas_coordenadas(datos)
    return datos, lat, lon


def _columna_wkt(datos: pd.DataFrame) -> str | None:
    candidatas = {"WKT", "GEOMETRY", "GEOMETRIA", "GEOM", "POLYGON", "POLIGONO"}
    return next((str(col) for col in datos.columns if llave(col) in candidatas), None)


def cargar_poligonos(ruta: str | Path):
    import geopandas as gpd
    from shapely import wkt

    ruta = Path(ruta)
    try:
        if ruta.suffix.lower() in {".xlsx", ".xls"}:
            datos = pd.read_excel(ruta)
            col_wkt = _columna_wkt(datos)
            if not col_wkt:
                raise ErrorOptimizacion("El Excel de polígonos debe contener una columna WKT o GEOMETRIA.")
            geometria = datos[col_wkt].map(lambda valor: wkt.loads(str(valor)) if pd.notna(valor) else None)
            atributos = datos.drop(columns=[col_wkt])
            gdf = gpd.GeoDataFrame(atributos, geometry=geometria, crs="EPSG:4326")
        else:
            gdf = gpd.read_file(ruta)
        if gdf.empty:
            raise ErrorOptimizacion("La capa de polígonos no contiene elementos.")
        gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
        gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
        if gdf.empty:
            raise ErrorOptimizacion("El archivo no contiene geometrías Polygon o MultiPolygon.")
        if gdf.crs is None:
            gdf = gdf.set_crs("EPSG:4326")
        return gdf.to_crs("EPSG:4326")
    except ErrorOptimizacion:
        raise
    except Exception as exc:
        raise ErrorOptimizacion(f"No fue posible cargar los polígonos: {exc}") from exc


def crear_capa_poligonos(poligonos: list[dict[str, Any]]):
    import geopandas as gpd
    from shapely.geometry import Polygon

    filas = []
    for numero, item in enumerate(poligonos, start=1):
        coordenadas = item.get("coordenadas", [])
        if len(coordenadas) < 3:
            continue
        geometria = Polygon(coordenadas)
        if not geometria.is_valid:
            geometria = geometria.buffer(0)
        if geometria.is_empty:
            continue
        fila = {
            "ID_POLIGONO": item.get("id", numero),
            "DESCRIPCION": item.get("descripcion", f"Polígono {numero}"),
            "geometry": geometria,
        }
        fila.update(item.get("atributos", {}))
        filas.append(fila)
    if not filas:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(filas, geometry="geometry", crs="EPSG:4326")


def unir_capas_poligonos(cargados, generados: list[dict[str, Any]]):
    import geopandas as gpd

    capas = []
    if cargados is not None and not cargados.empty:
        capa = cargados.to_crs("EPSG:4326").copy()
        if "DESCRIPCION" not in capa.columns:
            capa["DESCRIPCION"] = [f"Polígono cargado {i + 1}" for i in range(len(capa))]
        capas.append(capa)
    nueva = crear_capa_poligonos(generados)
    if not nueva.empty:
        capas.append(nueva)
    if not capas:
        return gpd.GeoDataFrame(columns=["DESCRIPCION", "geometry"], geometry="geometry", crs="EPSG:4326")
    columnas = sorted(set().union(*(set(c.columns) for c in capas)) - {"geometry"})
    normalizadas = []
    for capa in capas:
        copia = capa.copy()
        for columna in columnas:
            if columna not in copia.columns:
                copia[columna] = None
        normalizadas.append(copia[columnas + ["geometry"]])
    return gpd.GeoDataFrame(pd.concat(normalizadas, ignore_index=True), geometry="geometry", crs="EPSG:4326")


def cruzar_puntos_poligonos(
    puntos: pd.DataFrame,
    col_lat: str,
    col_lon: str,
    poligonos,
) -> pd.DataFrame:
    """Agrega cobertura y atributos del primer polígono que cubre cada punto."""
    import geopandas as gpd

    if poligonos is None or poligonos.empty:
        raise ErrorOptimizacion("Cargue o dibuje al menos un polígono antes de cruzar.")
    salida = puntos.copy()
    lat = pd.to_numeric(salida[col_lat], errors="coerce")
    lon = pd.to_numeric(salida[col_lon], errors="coerce")
    validas = lat.between(-90, 90) & lon.between(-180, 180)
    geo = gpd.GeoDataFrame(
        salida.loc[validas].copy(),
        geometry=gpd.points_from_xy(lon.loc[validas], lat.loc[validas]),
        crs="EPSG:4326",
    )
    capa = poligonos.to_crs("EPSG:4326").reset_index(drop=True).copy()
    capa["_INDICE_POLIGONO"] = capa.index + 1
    atributos = [col for col in capa.columns if col != "geometry"]
    try:
        unido = gpd.sjoin(geo, capa, how="left", predicate="within")
    except Exception:
        # Respaldo sin índice espacial: es más lento, pero mantiene disponible
        # el cruce en instalaciones donde no esté el motor sindex.
        filas = []
        for indice, fila in geo.iterrows():
            coincidencia = next((p for _, p in capa.iterrows() if fila.geometry.within(p.geometry) or fila.geometry.touches(p.geometry)), None)
            registro = fila.drop(labels=["geometry"]).to_dict()
            if coincidencia is not None:
                registro.update({col: coincidencia[col] for col in atributos})
            registro["_INDICE_ORIGEN"] = indice
            filas.append(registro)
        unido = pd.DataFrame(filas).set_index("_INDICE_ORIGEN") if filas else pd.DataFrame()
    if not unido.empty:
        unido = unido[~unido.index.duplicated(keep="first")]
    salida["DENTRO_POLIGONO"] = "NO"
    if not unido.empty:
        cubiertos = unido.index[unido.get("_INDICE_POLIGONO").notna()] if "_INDICE_POLIGONO" in unido else []
        salida.loc[cubiertos, "DENTRO_POLIGONO"] = "SI"
        for columna in atributos:
            if columna == "_INDICE_POLIGONO":
                destino = "ID_POLIGONO_CRUCE"
            elif columna in salida.columns:
                destino = f"POLIGONO_{columna}"
            else:
                destino = columna
            salida[destino] = None
            if columna in unido:
                salida.loc[unido.index, destino] = unido[columna].values
    return salida


def contornos_desde_gdf(gdf) -> list[list[tuple[float, float]]]:
    if gdf is None or gdf.empty:
        return []
    capa = gdf.to_crs("EPSG:4326") if gdf.crs and str(gdf.crs).upper() != "EPSG:4326" else gdf
    contornos: list[list[tuple[float, float]]] = []
    for geometria in capa.geometry:
        partes = list(geometria.geoms) if geometria.geom_type == "MultiPolygon" else [geometria]
        for poligono in partes:
            contornos.append([(float(x), float(y)) for x, y in poligono.exterior.coords])
    return contornos


def exportar_poligonos(gdf, ruta: str | Path) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.suffix.lower() == ".gpkg":
        gdf.to_file(ruta, driver="GPKG", layer="poligonos")
    else:
        salida = pd.DataFrame(gdf.drop(columns=["geometry"]))
        salida["WKT"] = gdf.geometry.map(lambda geom: geom.wkt)
        salida.to_excel(ruta, index=False)
    return ruta
