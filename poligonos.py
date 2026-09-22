# -*- coding: utf-8 -*-
"""
poligonos.py — Módulo de gestión e integración de polígonos GIS LATAM y Delimitación de Muestras.

Responsabilidad única:
1. Cargar la capa Shapefile de LATAM (LATAM DN.shp en Poligonos Muestras/LATAM) filtrando por NAME_0.
2. Cargar los polígonos de delimitación de muestras (en Poligonos Muestras/DELIMITACION PAISES).
3. Ofrecer validación geoespacial con reproyección de CRS para ambas capas.
"""

from __future__ import annotations

import os
from pathlib import Path
import pandas as pd

from logs import obtener_logger

MAPA_NAME_0 = {
    "Nicaragua": "Nicaragua",
    "El Salvador": "El Salvador",
    "Costa Rica": "Costa Rica",
    "Honduras": "Honduras",
    "Panama": "Panama",
    "Panamá": "Panama",
    "Chile": "Chile",
    "Ecuador": "Ecuador",
    "Guatemala ABVO": "Guatemala",
    "Guatemala EMBOCEN": "Guatemala",
    "Republica Dominicana": "Dominican Republic",
    "República Dominicana": "Dominican Republic",
}


def obtener_poligono_pais_latam(carpeta_latam: Path, nombre_pais: str):
    """
    Carga LATAM DN.shp desde Poligonos Muestras/LATAM y filtra el país por NAME_0.
    """
    log = obtener_logger()
    shape_path = carpeta_latam / "LATAM DN.shp"
    if not shape_path.exists():
        log.warning("No se encontró el archivo de polígonos LATAM: %s", shape_path)
        return None

    try:
        import geopandas as gpd
        gdf = gpd.read_file(shape_path)
        name_0_target = MAPA_NAME_0.get(nombre_pais, nombre_pais)
        
        poligono_pais = gdf[gdf["NAME_0"].str.upper() == name_0_target.upper()].copy()
        if len(poligono_pais) == 0:
            log.warning("No se encontró geometría LATAM en NAME_0 para: %s (buscado: %s)", nombre_pais, name_0_target)
            return None
            
        log.info("Polígono GIS LATAM cargado para '%s' (NAME_0='%s'): %s geometrías", nombre_pais, name_0_target, len(poligono_pais))
        return poligono_pais
    except Exception as exc:
        log.error("Error al cargar polígonos LATAM para '%s': %s", nombre_pais, exc)
        return None


def obtener_poligono_delimitacion_muestra(carpeta_delim: Path, nombre_pais: str):
    """
    Busca dinámicamente el archivo de delimitación en Poligonos Muestras/DELIMITACION PAISES
    que corresponda al país activo y extrae sus polígonos.
    """
    log = obtener_logger()
    if not carpeta_delim.exists():
        log.warning("Carpeta de delimitación no existe: %s", carpeta_delim)
        return None

    pais_lower = nombre_pais.lower()
    archivos = [f for f in os.listdir(carpeta_delim) if f.endswith((".gpkg", ".shp"))]

    for fname in archivos:
        fn_lower = fname.lower()
        if (
            ("ni" in fn_lower and "nicaragua" in pais_lower)
            or ("cr" in fn_lower and "costa rica" in pais_lower)
            or ("ec" in fn_lower and "ecuador" in pais_lower)
            or ("rd" in fn_lower and "dominicana" in pais_lower)
            or ("abvo" in fn_lower and "abvo" in pais_lower)
            or ("embocen" in fn_lower and "embocen" in pais_lower)
            or ("hn" in fn_lower and "honduras" in pais_lower)
            or ("es" in fn_lower and "salvador" in pais_lower)
            or ("pa" in fn_lower and ("panama" in pais_lower or "panamá" in pais_lower))
            or ("ch" in fn_lower and "chile" in pais_lower)
        ):
            fpath = carpeta_delim / fname
            try:
                import pyogrio
                import geopandas as gpd
                layers_info = pyogrio.list_layers(fpath)
                polys = []
                for l_name, l_type in layers_info:
                    if "polygon" in l_type.lower():
                        gdf = gpd.read_file(fpath, layer=l_name)
                        polys.append(gdf)
                if polys:
                    res_gdf = pd.concat(polys, ignore_index=True) if len(polys) > 1 else polys[0]
                    log.info("Cargado polígono de delimitación muestra desde '%s': %s elementos", fname, len(res_gdf))
                    return res_gdf
            except Exception as exc:
                log.warning("Error al leer delimitación muestra '%s': %s", fname, exc)
    
    log.info("No hay archivo de delimitación muestra específico para '%s' en DELIMITACION PAISES (se omite filtro FP).", nombre_pais)
    return None


def validar_coordenadas_en_poligono(
    df: pd.DataFrame,
    col_lat: str,
    col_lon: str,
    poligono_gdf
) -> pd.Series:
    """
    Verifica si las coordenadas (Latitud, Longitud) de cada punto están dentro
    del polígono especificado (Point-in-Polygon) aplicando la reproyección de CRS adecuada.
    """
    if poligono_gdf is None or df.empty or col_lat not in df.columns or col_lon not in df.columns:
        return pd.Series(True, index=df.index)

    try:
        import geopandas as gpd
        from shapely.geometry import Point

        lats = pd.to_numeric(df[col_lat], errors="coerce").fillna(0)
        lons = pd.to_numeric(df[col_lon], errors="coerce").fillna(0)

        # Crear GeoSeries de puntos en EPSG:4326 (WGS84)
        puntos_wgs84 = gpd.GeoSeries([Point(xy) for xy in zip(lons, lats)], crs="EPSG:4326")

        # Reproyectar los puntos al CRS del polígono objetivo si es diferente
        if poligono_gdf.crs and str(poligono_gdf.crs).upper() != "EPSG:4326":
            puntos_reproj = puntos_wgs84.to_crs(poligono_gdf.crs)
        else:
            puntos_reproj = puntos_wgs84

        union_geom = poligono_gdf.geometry.union_all() if hasattr(poligono_gdf.geometry, "union_all") else poligono_gdf.unary_union
        dentro = puntos_reproj.within(union_geom)
        return dentro
    except Exception as exc:
        obtener_logger().warning("Error en validación punto en polígono: %s", exc)
        return pd.Series(True, index=df.index)


def extraer_contornos_mapa(poligono_gdf, tolerancia: float = 0.0005) -> list[list[tuple[float, float]]]:
    """Convierte polígonos GIS a contornos WGS84 ligeros para dibujarlos en Tk."""
    if poligono_gdf is None or len(poligono_gdf) == 0:
        return []

    try:
        gdf = poligono_gdf
        if gdf.crs and str(gdf.crs).upper() != "EPSG:4326":
            gdf = gdf.to_crs("EPSG:4326")

        contornos: list[list[tuple[float, float]]] = []
        for geometria in gdf.geometry:
            if geometria is None or geometria.is_empty:
                continue
            simplificada = geometria.simplify(tolerancia, preserve_topology=True)
            poligonos = list(simplificada.geoms) if simplificada.geom_type == "MultiPolygon" else [simplificada]
            for poligono in poligonos:
                if poligono.geom_type != "Polygon":
                    continue
                contornos.append([(float(x), float(y)) for x, y in poligono.exterior.coords])
        return contornos
    except Exception as exc:
        obtener_logger().warning("No fue posible preparar el contorno para el mapa: %s", exc)
        return []
