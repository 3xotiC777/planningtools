# -*- coding: utf-8 -*-
"""
poligonos.py — Módulo de gestión e integración de polígonos GIS LATAM y Delimitación de Muestras.

Responsabilidad única:
1. Cargar la capa Shapefile de LATAM (LATAM DN.shp en Poligonos Muestras/LATAM) filtrando por NAME_0.
2. Cargar los polígonos de delimitación de muestras (en Poligonos Muestras/DELIMITACION PAISES).
3. Ofrecer validación geoespacial con reproyección de CRS para ambas capas.
"""

from __future__ import annotations

from pathlib import Path
import unicodedata
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

ARCHIVOS_FP_POR_PAIS = {
    "Costa Rica": "ICEKO_CR.gpkg",
    "Nicaragua": "ICEKO_NI.gpkg",
    "Guatemala ABVO": "ICEKO_ABVO.gpkg",
    "Guatemala EMBOCEN": "ICEKO_EMBOCEN.gpkg",
}


def _normalizar_pais(nombre: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", nombre)
    return " ".join("".join(c for c in sin_tildes if not unicodedata.combining(c)).casefold().split())


_ARCHIVOS_FP_NORMALIZADOS = {
    _normalizar_pais(pais): archivo for pais, archivo in ARCHIVOS_FP_POR_PAIS.items()
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
    Carga exclusivamente el polígono NO ELEGIBLE FP del país activo.

    Para los países con FP configurado, un archivo ausente o dañado es un error:
    continuar sin él clasificaría incorrectamente los puntos como elegibles.
    """
    log = obtener_logger()
    archivo = _ARCHIVOS_FP_NORMALIZADOS.get(_normalizar_pais(nombre_pais))
    if archivo is None:
        log.info("No hay filtro NO ELEGIBLE FP configurado para '%s'.", nombre_pais)
        return None
    fpath = carpeta_delim / archivo
    if not fpath.is_file():
        raise FileNotFoundError(f"Falta el polígono NO ELEGIBLE FP de {nombre_pais}: {fpath}")

    try:
        import pyogrio
        import geopandas as gpd

        polys = []
        for nombre_capa, tipo in pyogrio.list_layers(fpath):
            if "polygon" in tipo.lower():
                polys.append(gpd.read_file(fpath, layer=nombre_capa))
        if not polys:
            raise ValueError("El archivo no contiene geometrías de polígono")
        res_gdf = pd.concat(polys, ignore_index=True) if len(polys) > 1 else polys[0]
        if res_gdf.empty or res_gdf.crs is None:
            raise ValueError("El archivo está vacío o no tiene sistema de coordenadas")
        log.info("Polígono NO ELEGIBLE FP de %s: %s geometrías", nombre_pais, len(res_gdf))
        return res_gdf
    except Exception as exc:
        raise ValueError(f"No se pudo leer el polígono NO ELEGIBLE FP de {nombre_pais}: {fpath}") from exc


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
