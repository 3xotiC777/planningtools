# -*- coding: utf-8 -*-
"""Generación de mallas métricas para planificación de visitas.

La lógica geoespacial está separada de la interfaz para poder probarla y
reutilizarla desde otros procesos de Planning Tools.
"""

from __future__ import annotations

import math
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    from tkinter import filedialog, messagebox
    import customtkinter as ctk
except ImportError:  # El motor también se usa en servidores sin escritorio.
    filedialog = messagebox = None

    class _HeadlessCTk:
        CTkFrame = object

    ctk = _HeadlessCTk()
import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely
from pyproj import CRS
from shapely.geometry import Polygon, box

from lector_excel import leer_columnas, listar_hojas


AZUL = "#0D5CAB"
AZUL_CLARO = "#33BDEE"
AZUL_OSCURO = "#1C293A"
VERDE = "#1E7D46"
FONDO = "#F2F2F2"
BORDE = "#D5D8DC"
GRIS = "#69727D"


class ErrorGeneracionMalla(ValueError):
    """Error validado que se puede mostrar directamente al usuario."""


@dataclass(frozen=True)
class ResultadoMalla:
    ruta: Path
    celdas: int
    puntos_validos: int
    poligonos_urbanos_cercanos: int
    crs_metrico: str
    forma: str
    medida_m: float
    capa: gpd.GeoDataFrame


def _normalizar(texto: object) -> str:
    base = unicodedata.normalize("NFD", str(texto).strip().upper())
    return " ".join(
        "".join(c for c in base if unicodedata.category(c) != "Mn").split()
    )


def _buscar_columna(columnas: list[str], deseada: str) -> str | None:
    objetivo = _normalizar(deseada)
    return next((columna for columna in columnas if _normalizar(columna) == objetivo), None)


def _sugerir_coordenadas(columnas: list[str]) -> tuple[str, str]:
    normalizadas = {_normalizar(columna): columna for columna in columnas}
    latitud = next(
        (normalizadas[n] for n in ("LATITUD", "LATITUDE", "LAT", "Y") if n in normalizadas),
        columnas[0] if columnas else "",
    )
    longitud = next(
        (normalizadas[n] for n in ("LONGITUD", "LONGITUDE", "LONG", "LON", "X") if n in normalizadas),
        columnas[min(1, len(columnas) - 1)] if columnas else "",
    )
    return latitud, longitud


def listar_capas_gpkg(ruta: str | Path) -> list[str]:
    """Lista únicamente las capas espaciales disponibles en un GeoPackage."""
    try:
        capas = pyogrio.list_layers(ruta)
        return [str(nombre) for nombre, geometria in capas if geometria]
    except Exception as exc:
        raise ErrorGeneracionMalla(f"No se pudieron leer las capas del GeoPackage.\nDetalle: {exc}") from exc


def listar_campos_gpkg(ruta: str | Path, capa: str) -> list[str]:
    """Lee el esquema de una capa sin cargar todas sus geometrías."""
    try:
        info = pyogrio.read_info(ruta, layer=capa)
        return [str(campo) for campo in info.get("fields", [])]
    except Exception as exc:
        raise ErrorGeneracionMalla(f"No se pudieron leer los campos de la capa '{capa}'.\nDetalle: {exc}") from exc


def _leer_puntos(
    ruta_excel: str | Path,
    hoja: str | int,
    columna_latitud: str,
    columna_longitud: str,
) -> gpd.GeoDataFrame:
    try:
        datos = pd.read_excel(
            ruta_excel,
            sheet_name=hoja,
            usecols=[columna_latitud, columna_longitud],
            engine="openpyxl",
        )
    except Exception as exc:
        raise ErrorGeneracionMalla(f"No se pudo leer el Excel de puntos.\nDetalle: {exc}") from exc

    latitud = pd.to_numeric(
        datos[columna_latitud].astype(str).str.replace(",", ".", regex=False), errors="coerce"
    )
    longitud = pd.to_numeric(
        datos[columna_longitud].astype(str).str.replace(",", ".", regex=False), errors="coerce"
    )
    validos = (
        np.isfinite(latitud)
        & np.isfinite(longitud)
        & latitud.between(-90, 90)
        & longitud.between(-180, 180)
    )
    if not bool(validos.any()):
        raise ErrorGeneracionMalla(
            "El Excel no contiene coordenadas válidas. Revise las columnas de latitud y longitud."
        )
    return gpd.GeoDataFrame(
        {"LATITUD": latitud[validos].to_numpy(), "LONGITUD": longitud[validos].to_numpy()},
        geometry=gpd.points_from_xy(longitud[validos], latitud[validos]),
        crs="EPSG:4326",
    )


def _leer_poligonos_urbanos(
    ruta_gpkg: str | Path,
    capa: str,
    columna_zona: str,
    valor_zona: str,
) -> tuple[gpd.GeoDataFrame, str]:
    campos = listar_campos_gpkg(ruta_gpkg, capa)
    campo_real = _buscar_columna(campos, columna_zona)
    if campo_real is None:
        raise ErrorGeneracionMalla(
            f"La capa '{capa}' no contiene el campo '{columna_zona}'.\n"
            f"Campos disponibles: {', '.join(campos)}"
        )

    identificador = campo_real.replace('"', '""')
    literal = str(valor_zona).replace("'", "''")
    try:
        urbanos = pyogrio.read_dataframe(
            ruta_gpkg,
            layer=capa,
            columns=[campo_real],
            where=f'"{identificador}" = \'{literal}\'',
        )
    except Exception:
        urbanos = gpd.GeoDataFrame()

    # Algunos archivos guardan el valor con mayúsculas o espacios distintos.
    # Solo si el filtro SQL no encontró registros se carga el campo mínimo y se
    # aplica una comparación normalizada.
    if urbanos.empty:
        try:
            candidatos = pyogrio.read_dataframe(ruta_gpkg, layer=capa, columns=[campo_real])
        except Exception as exc:
            raise ErrorGeneracionMalla(f"No se pudo leer la capa urbana.\nDetalle: {exc}") from exc
        mascara = candidatos[campo_real].map(_normalizar) == _normalizar(valor_zona)
        urbanos = candidatos.loc[mascara].copy()

    if urbanos.crs is None:
        raise ErrorGeneracionMalla(f"La capa '{capa}' no tiene un sistema de coordenadas definido.")
    urbanos = urbanos.loc[urbanos.geometry.notna() & ~urbanos.geometry.is_empty].copy()
    if urbanos.empty:
        raise ErrorGeneracionMalla(
            f"No se encontraron polígonos con {campo_real} = '{valor_zona}'."
        )
    return urbanos, campo_real


def _resolver_crs_metrico(puntos: gpd.GeoDataFrame, crs_metrico: str | int | None) -> CRS:
    if crs_metrico is not None:
        try:
            crs = CRS.from_user_input(crs_metrico)
        except Exception as exc:
            raise ErrorGeneracionMalla(f"El CRS métrico indicado no es válido: {crs_metrico}") from exc
        if crs.is_geographic:
            raise ErrorGeneracionMalla("El CRS de trabajo debe usar unidades métricas, no grados.")
        return crs
    estimado = puntos.estimate_utm_crs()
    if estimado is None:
        raise ErrorGeneracionMalla("No fue posible determinar automáticamente la zona UTM de los puntos.")
    return CRS.from_user_input(estimado)


def _resolver_forma(forma: str) -> str:
    normalizada = _normalizar(forma)
    if normalizada in {"CUADRADO", "CUADRADOS", "CUADRO", "CUADROS"}:
        return "CUADRADO"
    if normalizada in {"CIRCULO", "CIRCULOS", "RADIO", "RADIOS"}:
        return "CIRCULO"
    if normalizada in {"HEXAGONO", "HEXAGONOS", "HEXAGONAL", "HEXAGONALES"}:
        return "HEXAGONO"
    raise ErrorGeneracionMalla(
        "La forma debe ser 'Cuadrados', 'Círculos' o 'Hexágonos'."
    )


def _claves_candidatas(
    poligonos: gpd.GeoDataFrame,
    puntos: gpd.GeoDataFrame,
    tamano: float,
    limite_candidatos: int,
) -> tuple[set[tuple[int, int]], Counter[tuple[int, int]], float, float]:
    limites = np.vstack((poligonos.total_bounds, puntos.total_bounds))
    origen_x = math.floor(float(np.nanmin(limites[:, 0])) / tamano) * tamano
    origen_y = math.floor(float(np.nanmin(limites[:, 1])) / tamano) * tamano

    claves: set[tuple[int, int]] = set()
    conteo_puntos: Counter[tuple[int, int]] = Counter()
    for punto in puntos.geometry:
        clave = (
            math.floor((punto.x - origen_x) / tamano),
            math.floor((punto.y - origen_y) / tamano),
        )
        claves.add(clave)
        conteo_puntos[clave] += 1

    for min_x, min_y, max_x, max_y in poligonos.geometry.bounds.itertuples(index=False, name=None):
        col_min = math.floor((min_x - origen_x) / tamano)
        fila_min = math.floor((min_y - origen_y) / tamano)
        col_max = math.ceil((max_x - origen_x) / tamano) - 1
        fila_max = math.ceil((max_y - origen_y) / tamano) - 1
        estimado = max(0, col_max - col_min + 1) * max(0, fila_max - fila_min + 1)
        if len(claves) + estimado > limite_candidatos:
            raise ErrorGeneracionMalla(
                "La extensión urbana produciría demasiadas celdas candidatas "
                f"(más de {limite_candidatos:,}). Revise el filtro, la capa o el CRS."
            )
        claves.update(
            (columna, fila)
            for fila in range(fila_min, fila_max + 1)
            for columna in range(col_min, col_max + 1)
        )
    return claves, conteo_puntos, origen_x, origen_y


def _claves_circulares(
    poligonos: gpd.GeoDataFrame,
    puntos: gpd.GeoDataFrame,
    radio: float,
    limite_candidatos: int,
) -> tuple[set[tuple[int, int]], Counter[tuple[int, int]], float, float, float]:
    """Crea una retícula de centros cuya separación cubre el plano sin huecos."""
    paso = radio * math.sqrt(2)
    limites = np.vstack((poligonos.total_bounds, puntos.total_bounds))
    origen_x = math.floor(float(np.nanmin(limites[:, 0])) / paso) * paso
    origen_y = math.floor(float(np.nanmin(limites[:, 1])) / paso) * paso

    claves: set[tuple[int, int]] = set()
    conteo_puntos: Counter[tuple[int, int]] = Counter()
    for punto in puntos.geometry:
        # El centro de cada círculo coincide con el centro de una celda de la
        # retícula. Con paso radio*sqrt(2), hasta las esquinas quedan cubiertas.
        clave = (
            math.floor((punto.x - origen_x) / paso),
            math.floor((punto.y - origen_y) / paso),
        )
        claves.add(clave)
        conteo_puntos[clave] += 1

    for min_x, min_y, max_x, max_y in poligonos.geometry.bounds.itertuples(index=False, name=None):
        col_min = math.ceil((min_x - radio - origen_x) / paso - 0.5)
        fila_min = math.ceil((min_y - radio - origen_y) / paso - 0.5)
        col_max = math.floor((max_x + radio - origen_x) / paso - 0.5)
        fila_max = math.floor((max_y + radio - origen_y) / paso - 0.5)
        estimado = max(0, col_max - col_min + 1) * max(0, fila_max - fila_min + 1)
        if len(claves) + estimado > limite_candidatos:
            raise ErrorGeneracionMalla(
                "La extensión urbana produciría demasiados círculos candidatos "
                f"(más de {limite_candidatos:,}). Revise el filtro, el radio o el CRS."
            )
        claves.update(
            (columna, fila)
            for fila in range(fila_min, fila_max + 1)
            for columna in range(col_min, col_max + 1)
        )
    return claves, conteo_puntos, origen_x, origen_y, paso


def _redondear_axial(q_decimal: float, r_decimal: float) -> tuple[int, int]:
    """Redondea coordenadas axiales al hexágono regular más cercano."""
    cubo_x = q_decimal
    cubo_z = r_decimal
    cubo_y = -cubo_x - cubo_z
    red_x, red_y, red_z = round(cubo_x), round(cubo_y), round(cubo_z)
    dif_x = abs(red_x - cubo_x)
    dif_y = abs(red_y - cubo_y)
    dif_z = abs(red_z - cubo_z)
    if dif_x > dif_y and dif_x > dif_z:
        red_x = -red_y - red_z
    elif dif_y > dif_z:
        red_y = -red_x - red_z
    else:
        red_z = -red_x - red_y
    return int(red_x), int(red_z)


def _clave_hexagonal(
    x: float,
    y: float,
    lado: float,
    origen_x: float,
    origen_y: float,
) -> tuple[int, int]:
    q_decimal = (2.0 / 3.0) * (x - origen_x) / lado
    r_decimal = (
        -(x - origen_x) / 3.0 + (math.sqrt(3) / 3.0) * (y - origen_y)
    ) / lado
    return _redondear_axial(q_decimal, r_decimal)


def _centro_hexagonal(
    clave: tuple[int, int], lado: float, origen_x: float, origen_y: float
) -> tuple[float, float]:
    columna, fila = clave
    return (
        origen_x + 1.5 * lado * columna,
        origen_y + math.sqrt(3) * lado * (fila + columna / 2.0),
    )


def _poligono_hexagonal(
    clave: tuple[int, int], lado: float, origen_x: float, origen_y: float
) -> Polygon:
    centro_x, centro_y = _centro_hexagonal(clave, lado, origen_x, origen_y)
    vertices = [
        (
            centro_x + lado * math.cos(math.radians(60 * vertice)),
            centro_y + lado * math.sin(math.radians(60 * vertice)),
        )
        for vertice in range(6)
    ]
    return Polygon(vertices)


def _claves_hexagonales(
    poligonos: gpd.GeoDataFrame,
    puntos: gpd.GeoDataFrame,
    lado: float,
    limite_candidatos: int,
) -> tuple[set[tuple[int, int]], Counter[tuple[int, int]], float, float]:
    """Crea las claves de una teselación de hexágonos regulares sin huecos."""
    paso_x = 1.5 * lado
    paso_y = math.sqrt(3) * lado
    limites = np.vstack((poligonos.total_bounds, puntos.total_bounds))
    origen_x = math.floor(float(np.nanmin(limites[:, 0])) / paso_x) * paso_x
    origen_y = math.floor(float(np.nanmin(limites[:, 1])) / paso_y) * paso_y

    claves: set[tuple[int, int]] = set()
    conteo_puntos: Counter[tuple[int, int]] = Counter()
    for punto in puntos.geometry:
        clave = _clave_hexagonal(punto.x, punto.y, lado, origen_x, origen_y)
        claves.add(clave)
        conteo_puntos[clave] += 1

    apotema = math.sqrt(3) * lado / 2.0
    for min_x, min_y, max_x, max_y in poligonos.geometry.bounds.itertuples(
        index=False, name=None
    ):
        columna_min = math.floor((min_x - lado - origen_x) / paso_x) - 1
        columna_max = math.ceil((max_x + lado - origen_x) / paso_x) + 1
        candidatas_poligono: set[tuple[int, int]] = set()
        for columna in range(columna_min, columna_max + 1):
            desplazamiento = columna / 2.0
            fila_min = math.floor(
                (min_y - apotema - origen_y) / paso_y - desplazamiento
            ) - 1
            fila_max = math.ceil(
                (max_y + apotema - origen_y) / paso_y - desplazamiento
            ) + 1
            candidatas_poligono.update(
                (columna, fila) for fila in range(fila_min, fila_max + 1)
            )
            if len(claves) + len(candidatas_poligono) > limite_candidatos:
                raise ErrorGeneracionMalla(
                    "La extensión urbana produciría demasiados hexágonos candidatos "
                    f"(más de {limite_candidatos:,}). Revise el filtro, el lado o el CRS."
                )
        claves.update(candidatas_poligono)
    return claves, conteo_puntos, origen_x, origen_y


def generar_malla_visitas(
    ruta_excel: str | Path,
    ruta_gpkg: str | Path,
    ruta_salida: str | Path,
    *,
    hoja: str | int = 0,
    columna_latitud: str = "Latitud",
    columna_longitud: str = "Longitud",
    capa: str | None = None,
    columna_zona: str = "Clasificación Por Zona",
    valor_zona: str = "Zona Urbana",
    tamano_celda_m: float = 300,
    forma: str = "Cuadrados",
    buffer_km: float = 15,
    crs_metrico: str | int | None = None,
    nombre_capa_salida: str | None = None,
    limite_candidatos: int = 2_000_000,
    informar: Callable[[str], None] | None = None,
) -> ResultadoMalla:
    """Genera y exporta la malla que cubre PDV u operación urbana cercana.

    En cuadrados y hexágonos, ``tamano_celda_m`` es la longitud del lado. En
    círculos es el radio. La alineación usa un único origen métrico. Los
    polígonos urbanos se conservan completos cuando intersectan el buffer de
    los PDV; no se recortan al límite exacto de 15 km.
    """
    ruta_excel = Path(ruta_excel)
    ruta_gpkg = Path(ruta_gpkg)
    ruta_salida = Path(ruta_salida)
    if not ruta_excel.is_file():
        raise ErrorGeneracionMalla(f"No se encontró el Excel de puntos:\n{ruta_excel}")
    if not ruta_gpkg.is_file():
        raise ErrorGeneracionMalla(f"No se encontró el GeoPackage:\n{ruta_gpkg}")
    if tamano_celda_m <= 0 or buffer_km < 0:
        raise ErrorGeneracionMalla("La medida debe ser positiva y el buffer no puede ser negativo.")
    forma_normalizada = _resolver_forma(forma)

    avisar = informar or (lambda _mensaje: None)
    avisar("Leyendo coordenadas del Excel...")
    puntos = _leer_puntos(ruta_excel, hoja, columna_latitud, columna_longitud)
    capas = listar_capas_gpkg(ruta_gpkg)
    if not capas:
        raise ErrorGeneracionMalla("El GeoPackage no contiene capas espaciales.")
    capa = capa or capas[0]
    if capa not in capas:
        raise ErrorGeneracionMalla(f"La capa '{capa}' no existe en el GeoPackage.")

    avisar("Filtrando los polígonos de Zona Urbana...")
    urbanos, _campo_real = _leer_poligonos_urbanos(
        ruta_gpkg, capa, columna_zona, valor_zona
    )
    crs_trabajo = _resolver_crs_metrico(puntos, crs_metrico)
    puntos_m = puntos.to_crs(crs_trabajo)
    urbanos_m = urbanos.to_crs(crs_trabajo)

    avisar(f"Buscando zonas urbanas a máximo {buffer_km:g} km de los PDV...")
    cobertura_operacion = puntos_m.geometry.buffer(buffer_km * 1000).union_all()
    urbanos_cercanos = urbanos_m.loc[urbanos_m.geometry.intersects(cobertura_operacion)].copy()

    if forma_normalizada == "CUADRADO":
        avisar(f"Construyendo cuadrados de {tamano_celda_m:g} × {tamano_celda_m:g} m...")
        claves, conteo_puntos, origen_x, origen_y = _claves_candidatas(
            urbanos_cercanos, puntos_m, tamano_celda_m, limite_candidatos
        )
        paso = tamano_celda_m
    elif forma_normalizada == "CIRCULO":
        avisar(f"Construyendo círculos con radio de {tamano_celda_m:g} m...")
        claves, conteo_puntos, origen_x, origen_y, paso = _claves_circulares(
            urbanos_cercanos, puntos_m, tamano_celda_m, limite_candidatos
        )
    else:
        avisar(f"Construyendo hexágonos regulares con lado de {tamano_celda_m:g} m...")
        claves, conteo_puntos, origen_x, origen_y = _claves_hexagonales(
            urbanos_cercanos, puntos_m, tamano_celda_m, limite_candidatos
        )
    claves_ordenadas = sorted(claves, key=lambda clave: (clave[1], clave[0]))
    if forma_normalizada == "CUADRADO":
        celdas = [
            box(
                origen_x + columna * tamano_celda_m,
                origen_y + fila * tamano_celda_m,
                origen_x + (columna + 1) * tamano_celda_m,
                origen_y + (fila + 1) * tamano_celda_m,
            )
            for columna, fila in claves_ordenadas
        ]
    elif forma_normalizada == "CIRCULO":
        celdas = [
            shapely.Point(
                origen_x + (columna + 0.5) * paso,
                origen_y + (fila + 0.5) * paso,
            ).buffer(tamano_celda_m, quad_segs=16)
            for columna, fila in claves_ordenadas
        ]
    else:
        celdas = [
            _poligono_hexagonal(clave, tamano_celda_m, origen_x, origen_y)
            for clave in claves_ordenadas
        ]

    if urbanos_cercanos.empty:
        intersecta_urbana = np.zeros(len(celdas), dtype=bool)
    else:
        union_urbana = urbanos_cercanos.geometry.union_all()
        intersecta_urbana = np.asarray(shapely.intersects(celdas, union_urbana), dtype=bool)
    tiene_pdv = np.fromiter((clave in conteo_puntos for clave in claves_ordenadas), dtype=bool)
    conservar = intersecta_urbana | tiene_pdv
    seleccion = [indice for indice, valor in enumerate(conservar) if valor]
    if not seleccion:
        raise ErrorGeneracionMalla("No se generaron celdas con los filtros seleccionados.")

    salida_m = gpd.GeoDataFrame(
        {
            "ID_CUADRO": [f"MALLA_{numero:07d}" for numero in range(1, len(seleccion) + 1)],
            "FILA_MALLA": [claves_ordenadas[i][1] for i in seleccion],
            "COLUMNA_MALLA": [claves_ordenadas[i][0] for i in seleccion],
            "PDV_EN_CELDA": [conteo_puntos.get(claves_ordenadas[i], 0) for i in seleccion],
            "INTERSECTA_URBANA": ["SI" if intersecta_urbana[i] else "NO" for i in seleccion],
            "FORMA": [forma_normalizada] * len(seleccion),
            "TAMANO_M": [float(tamano_celda_m)] * len(seleccion),
            "LADO_M": [float(tamano_celda_m) if forma_normalizada in {"CUADRADO", "HEXAGONO"} else None] * len(seleccion),
            "RADIO_M": [float(tamano_celda_m) if forma_normalizada == "CIRCULO" else None] * len(seleccion),
        },
        geometry=[celdas[i] for i in seleccion],
        crs=crs_trabajo,
    )
    salida = salida_m.to_crs("EPSG:4326")
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    avisar("Exportando la malla en EPSG:4326...")
    if not nombre_capa_salida:
        medida_nombre = f"{tamano_celda_m:g}".replace(".", "_")
        nombres_forma = {
            "CUADRADO": f"malla_cuadrados_{medida_nombre}m",
            "CIRCULO": f"malla_circulos_radio_{medida_nombre}m",
            "HEXAGONO": f"malla_hexagonos_lado_{medida_nombre}m",
        }
        nombre_capa_salida = nombres_forma[forma_normalizada]
    try:
        pyogrio.write_dataframe(
            salida,
            ruta_salida,
            layer=nombre_capa_salida,
            driver="GPKG",
        )
    except Exception as exc:
        raise ErrorGeneracionMalla(f"No se pudo exportar el GeoPackage.\nDetalle: {exc}") from exc

    avisar("Malla terminada.")
    return ResultadoMalla(
        ruta=ruta_salida,
        celdas=len(salida),
        puntos_validos=len(puntos),
        poligonos_urbanos_cercanos=len(urbanos_cercanos),
        crs_metrico=crs_trabajo.to_string(),
        forma=forma_normalizada,
        medida_m=float(tamano_celda_m),
        capa=salida,
    )


class FrameGeneradorMallas(ctk.CTkFrame):
    """Formulario de Planning Tools para generar la malla de visitas."""

    def __init__(self, master, rutas: dict, config: dict) -> None:
        super().__init__(master, fg_color=FONDO)
        self.rutas = rutas
        self.config = config
        self.ruta_excel: Path | None = None
        self.ruta_gpkg: Path | None = None

        ctk.CTkLabel(
            self, text="Generador de mallas",
            font=ctk.CTkFont(size=27, weight="bold"), text_color=AZUL,
        ).pack(anchor="w", padx=28, pady=(18, 2))
        ctk.CTkLabel(
            self,
            text="Crea cuadrados, círculos o hexágonos para los PDV y las zonas urbanas ubicadas a máximo 15 km.",
            text_color=GRIS, font=ctk.CTkFont(size=14),
        ).pack(anchor="w", padx=28, pady=(0, 10))

        contenido = ctk.CTkScrollableFrame(self, fg_color="transparent")
        contenido.pack(fill="both", expand=True)
        self.lbl_ayuda = ctk.CTkLabel(
            contenido,
            text="Seleccione los dos archivos. Pase el cursor por una opción para ver una breve definición.",
            text_color="#52606D", fg_color="#E9F4FC", corner_radius=7, anchor="w",
        )
        self.lbl_ayuda.pack(fill="x", padx=22, pady=(0, 8), ipady=5)

        self._crear_panel_puntos(contenido)
        self._crear_panel_poligonos(contenido)
        self._crear_panel_ejecucion(contenido)

    def _ayuda(self, widget, texto: str) -> None:
        widget.bind("<Enter>", lambda _evento, t=texto: self.lbl_ayuda.configure(text=t))

    def _crear_panel_puntos(self, master) -> None:
        panel = ctk.CTkFrame(master, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        panel.pack(fill="x", padx=22, pady=6)
        ctk.CTkLabel(panel, text="1. Puntos de venta", font=ctk.CTkFont(size=17, weight="bold"), text_color=AZUL_OSCURO).grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(13, 7))
        boton = ctk.CTkButton(panel, text="Seleccionar Excel", fg_color=AZUL, command=self._seleccionar_excel)
        boton.grid(row=1, column=0, padx=(16, 8), pady=8, sticky="w")
        self._ayuda(boton, "Excel de PDV: debe contener una fila por punto y columnas de latitud y longitud.")
        self.lbl_excel = ctk.CTkLabel(panel, text="Ningún archivo seleccionado", text_color=GRIS, anchor="w")
        self.lbl_excel.grid(row=1, column=1, columnspan=3, padx=8, sticky="ew")
        self.combo_hoja = self._combo(panel, "Hoja", 2, 0, self._cambiar_hoja, "Pestaña del Excel donde se encuentran los puntos.")
        self.combo_latitud = self._combo(panel, "Latitud", 2, 1, None, "Coordenada norte-sur en grados, entre -90 y 90.")
        self.combo_longitud = self._combo(panel, "Longitud", 2, 2, None, "Coordenada este-oeste en grados, entre -180 y 180.")
        panel.grid_columnconfigure(3, weight=1)

    def _crear_panel_poligonos(self, master) -> None:
        panel = ctk.CTkFrame(master, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        panel.pack(fill="x", padx=22, pady=6)
        ctk.CTkLabel(panel, text="2. Polígonos del país", font=ctk.CTkFont(size=17, weight="bold"), text_color=AZUL_OSCURO).grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(13, 7))
        boton = ctk.CTkButton(panel, text="Seleccionar GeoPackage", fg_color=AZUL, command=self._seleccionar_gpkg)
        boton.grid(row=1, column=0, padx=(16, 8), pady=8, sticky="w")
        self._ayuda(boton, "GeoPackage nacional que contiene los polígonos y su clasificación por zona.")
        self.lbl_gpkg = ctk.CTkLabel(panel, text="Ningún archivo seleccionado", text_color=GRIS, anchor="w")
        self.lbl_gpkg.grid(row=1, column=1, columnspan=3, padx=8, sticky="ew")
        self.combo_capa = self._combo(panel, "Capa", 2, 0, self._cambiar_capa, "Capa espacial del GeoPackage que contiene los polígonos.")
        self.combo_zona = self._combo(panel, "Campo de clasificación", 2, 1, None, "Columna donde se identifica si el polígono es una Zona Urbana.")
        ctk.CTkLabel(panel, text="Valor urbano").grid(row=2, column=2, padx=8, pady=(8, 2), sticky="w")
        self.ent_valor = ctk.CTkEntry(panel, width=190)
        self.ent_valor.insert(0, "Zona Urbana")
        self.ent_valor.grid(row=3, column=2, padx=8, pady=(0, 12), sticky="w")
        self._ayuda(self.ent_valor, "Valor que distingue los polígonos urbanos; por defecto: Zona Urbana.")
        panel.grid_columnconfigure(3, weight=1)

    def _crear_panel_ejecucion(self, master) -> None:
        panel = ctk.CTkFrame(master, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        panel.pack(fill="x", padx=22, pady=(6, 18))
        ctk.CTkLabel(panel, text="3. Forma y medida", font=ctk.CTkFont(size=17, weight="bold"), text_color=AZUL_OSCURO).grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(13, 7))
        ctk.CTkLabel(panel, text="Forma").grid(row=1, column=0, padx=16, pady=(3, 2), sticky="w")
        self.combo_forma = ctk.CTkComboBox(
            panel, values=["Cuadrados", "Círculos", "Hexágonos"], width=190,
            command=self._cambiar_forma
        )
        self.combo_forma.set("Cuadrados")
        self.combo_forma.grid(row=2, column=0, padx=16, pady=(0, 10), sticky="w")
        self._ayuda(
            self.combo_forma,
            "Cuadrados y hexágonos usan la medida como lado. Círculos la usa como radio.",
        )
        self.lbl_medida = ctk.CTkLabel(panel, text="Lado del cuadrado (m)")
        self.lbl_medida.grid(row=1, column=1, padx=8, pady=(3, 2), sticky="w")
        self.ent_tamano = ctk.CTkEntry(panel, width=190)
        self.ent_tamano.insert(0, "300")
        self.ent_tamano.grid(row=2, column=1, padx=8, pady=(0, 10), sticky="w")
        self.ent_tamano.bind(
            "<KeyRelease>", lambda _evento: self._cambiar_forma(self.combo_forma.get())
        )
        self._ayuda(self.ent_tamano, "Escriba cualquier medida positiva en metros, por ejemplo 200, 300 o 500.")
        self.lbl_config = ctk.CTkLabel(
            panel,
            text="Cada cuadrado tendrá 300 m por lado · buffer urbano 15 km · salida EPSG:4326",
            text_color=GRIS,
        )
        self.lbl_config.grid(row=3, column=0, columnspan=4, padx=16, pady=(0, 8), sticky="w")
        self.btn_generar = ctk.CTkButton(panel, text="Generar y exportar GeoPackage", fg_color=VERDE, command=self._generar)
        self.btn_generar.grid(row=4, column=0, columnspan=2, padx=16, pady=7, sticky="w")
        self._ayuda(self.btn_generar, "Ejecuta el filtro espacial, crea la malla métrica y solicita dónde guardar el GPKG final.")
        self.lbl_estado = ctk.CTkLabel(panel, text="Listo para configurar.", text_color=GRIS, anchor="w")
        self.lbl_estado.grid(row=5, column=0, columnspan=4, padx=16, pady=(3, 13), sticky="ew")
        panel.grid_columnconfigure(3, weight=1)

    def _cambiar_forma(self, forma: str) -> None:
        es_circulo = forma == "Círculos"
        es_hexagono = forma == "Hexágonos"
        etiqueta = (
            "Radio del círculo (m)"
            if es_circulo
            else "Lado del hexágono (m)" if es_hexagono else "Lado del cuadrado (m)"
        )
        self.lbl_medida.configure(text=etiqueta)
        medida = self.ent_tamano.get().strip() or "300"
        descripcion = (
            f"Cada círculo tendrá {medida} m de radio"
            if es_circulo
            else f"Cada hexágono tendrá {medida} m por lado"
            if es_hexagono
            else f"Cada cuadrado tendrá {medida} m por lado"
        )
        self.lbl_config.configure(text=f"{descripcion} · buffer urbano 15 km · salida EPSG:4326")

    def _combo(self, panel, titulo: str, fila: int, columna: int, comando, ayuda: str):
        ctk.CTkLabel(panel, text=titulo).grid(row=fila, column=columna, padx=(16 if columna == 0 else 8, 8), pady=(8, 2), sticky="w")
        combo = ctk.CTkComboBox(panel, values=["Seleccione..."], width=210, command=comando)
        combo.set("Seleccione...")
        combo.grid(row=fila + 1, column=columna, padx=(16 if columna == 0 else 8, 8), pady=(0, 12), sticky="w")
        self._ayuda(combo, ayuda)
        return combo

    def _seleccionar_excel(self) -> None:
        ruta = filedialog.askopenfilename(title="Seleccionar puntos de venta", filetypes=[("Excel", "*.xlsx *.xls")])
        if not ruta:
            return
        try:
            hojas = listar_hojas(Path(ruta))
            if not hojas:
                raise ErrorGeneracionMalla("El archivo no contiene hojas.")
            self.ruta_excel = Path(ruta)
            self.lbl_excel.configure(text=self.ruta_excel.name)
            self.combo_hoja.configure(values=hojas)
            self.combo_hoja.set(hojas[0])
            self._cambiar_hoja(hojas[0])
        except Exception as exc:
            messagebox.showerror("No se pudo abrir el Excel", str(exc))

    def _cambiar_hoja(self, hoja: str) -> None:
        if self.ruta_excel is None:
            return
        columnas = leer_columnas(self.ruta_excel, hoja)
        if not columnas:
            return
        latitud, longitud = _sugerir_coordenadas(columnas)
        for combo in (self.combo_latitud, self.combo_longitud):
            combo.configure(values=columnas)
        self.combo_latitud.set(latitud)
        self.combo_longitud.set(longitud)

    def _seleccionar_gpkg(self) -> None:
        ruta = filedialog.askopenfilename(title="Seleccionar polígonos del país", filetypes=[("GeoPackage", "*.gpkg")])
        if not ruta:
            return
        try:
            capas = listar_capas_gpkg(ruta)
            if not capas:
                raise ErrorGeneracionMalla("El GeoPackage no contiene capas espaciales.")
            self.ruta_gpkg = Path(ruta)
            self.lbl_gpkg.configure(text=self.ruta_gpkg.name)
            self.combo_capa.configure(values=capas)
            self.combo_capa.set(capas[0])
            self._cambiar_capa(capas[0])
        except Exception as exc:
            messagebox.showerror("No se pudo abrir el GeoPackage", str(exc))

    def _cambiar_capa(self, capa: str) -> None:
        if self.ruta_gpkg is None:
            return
        try:
            campos = listar_campos_gpkg(self.ruta_gpkg, capa)
            self.combo_zona.configure(values=campos or ["Seleccione..."])
            sugerido = _buscar_columna(campos, "Clasificación Por Zona")
            self.combo_zona.set(sugerido or (campos[0] if campos else "Seleccione..."))
        except Exception as exc:
            messagebox.showerror("No se pudo leer la capa", str(exc))

    def _generar(self) -> None:
        if self.ruta_excel is None or self.ruta_gpkg is None:
            messagebox.showwarning("Faltan archivos", "Seleccione el Excel de PDV y el GeoPackage de polígonos.")
            return
        try:
            medida = float(self.ent_tamano.get().strip().replace(",", "."))
            if not math.isfinite(medida) or medida <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Medida no válida", "Escriba una medida positiva en metros, por ejemplo 200 o 300.")
            return
        forma = self.combo_forma.get()
        medida_nombre = f"{medida:g}".replace(".", "_")
        nombres_archivo = {
            "Cuadrados": f"Malla_Cuadrados_{medida_nombre}m.gpkg",
            "Círculos": f"Malla_Circulos_Radio_{medida_nombre}m.gpkg",
            "Hexágonos": f"Malla_Hexagonos_Lado_{medida_nombre}m.gpkg",
        }
        archivo_inicial = nombres_archivo.get(
            forma, f"Malla_Cuadrados_{medida_nombre}m.gpkg"
        )
        ruta = filedialog.asksaveasfilename(
            title="Guardar malla", defaultextension=".gpkg", initialfile=archivo_inicial,
            filetypes=[("GeoPackage", "*.gpkg")],
        )
        if not ruta:
            return
        parametros = {
            "ruta_excel": self.ruta_excel,
            "ruta_gpkg": self.ruta_gpkg,
            "ruta_salida": ruta,
            "hoja": self.combo_hoja.get(),
            "columna_latitud": self.combo_latitud.get(),
            "columna_longitud": self.combo_longitud.get(),
            "capa": self.combo_capa.get(),
            "columna_zona": self.combo_zona.get(),
            "valor_zona": self.ent_valor.get().strip() or "Zona Urbana",
            "tamano_celda_m": medida,
            "forma": forma,
            "informar": lambda texto: self.after(0, lambda t=texto: self.lbl_estado.configure(text=t)),
        }
        self.btn_generar.configure(state="disabled", text="Generando malla...")

        def tarea() -> None:
            try:
                resultado = generar_malla_visitas(**parametros)
                self.after(0, lambda r=resultado: self._terminado(r))
            except Exception as exc:
                self.after(0, lambda e=exc: self._error(e))

        threading.Thread(target=tarea, daemon=True).start()

    def _terminado(self, resultado: ResultadoMalla) -> None:
        self.btn_generar.configure(state="normal", text="Generar y exportar GeoPackage")
        self.lbl_estado.configure(
            text=f"{resultado.celdas:,} celdas · {resultado.puntos_validos:,} PDV válidos · "
                 f"{resultado.poligonos_urbanos_cercanos:,} polígonos urbanos cercanos"
        )
        descripciones = {
            "CIRCULO": f"círculos con radio de {resultado.medida_m:g} m",
            "HEXAGONO": f"hexágonos regulares con lado de {resultado.medida_m:g} m",
            "CUADRADO": (
                f"cuadrados de {resultado.medida_m:g} × {resultado.medida_m:g} m"
            ),
        }
        descripcion = descripciones.get(resultado.forma, resultado.forma.lower())
        messagebox.showinfo(
            "Malla generada",
            f"Se crearon {resultado.celdas:,} elementos: {descripcion}.\n\n"
            f"Archivo: {resultado.ruta}\nCRS de cálculo: {resultado.crs_metrico}\n"
            "Salida: EPSG:4326",
        )

    def _error(self, exc: Exception) -> None:
        self.btn_generar.configure(state="normal", text="Generar y exportar GeoPackage")
        self.lbl_estado.configure(text="No se pudo generar la malla.")
        messagebox.showerror("No se pudo generar la malla", str(exc))
