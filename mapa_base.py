# -*- coding: utf-8 -*-
"""Capa cartográfica común para todos los mapas de Planning Tools.

Usa las teselas estándar de OpenStreetMap con caché local. Los puntos y
polígonos se proyectan con Web Mercator para coincidir exactamente con las
calles, ciudades y fronteras de la imagen base.
"""

from __future__ import annotations

import io
import math
import os
import threading
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageTk


MAX_LATITUD_MERCATOR = 85.05112878
URL_TESELA = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ATRIBUCION = "© OpenStreetMap contributors"
_EJECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mapa-osm")
_MEMORIA: dict[tuple[int, int, int], Image.Image] = {}
_BLOQUEO = threading.Lock()
_CERRANDO = False


def cerrar_descargas_mapas() -> None:
    """Cancela teselas pendientes al cerrar Planning Tools."""
    global _CERRANDO
    _CERRANDO = True
    _EJECUTOR.shutdown(wait=False, cancel_futures=True)


def _latitud_segura(latitud: float) -> float:
    return max(-MAX_LATITUD_MERCATOR, min(MAX_LATITUD_MERCATOR, float(latitud)))


def mundo_mercator(longitud: float, latitud: float) -> tuple[float, float]:
    """Convierte longitud/latitud a coordenadas Web Mercator normalizadas."""
    x = (float(longitud) + 180.0) / 360.0
    radianes = math.radians(_latitud_segura(latitud))
    y = (1.0 - math.asinh(math.tan(radianes)) / math.pi) / 2.0
    return x, y


def geografia_mercator(x: float, y: float) -> tuple[float, float]:
    longitud = float(x) * 360.0 - 180.0
    latitud = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * float(y)))))
    return longitud, _latitud_segura(latitud)


def _mundo_limites(limites: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    min_lon, max_lon, min_lat, max_lat = limites
    x_izq, y_inf = mundo_mercator(min_lon, min_lat)
    x_der, y_sup = mundo_mercator(max_lon, max_lat)
    return x_izq, x_der, y_sup, y_inf


def ajustar_aspecto_mercator(
    limites: tuple[float, float, float, float],
    area: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Expande la vista para que el mapa no se estire ni se comprima."""
    izquierda, arriba, derecha, abajo = area
    proporcion = max(1e-6, derecha - izquierda) / max(1e-6, abajo - arriba)
    x_izq, x_der, y_sup, y_inf = _mundo_limites(limites)
    ancho, alto = max(1e-12, x_der - x_izq), max(1e-12, y_inf - y_sup)
    centro_x, centro_y = (x_izq + x_der) / 2, (y_sup + y_inf) / 2
    if ancho / alto < proporcion:
        ancho = alto * proporcion
    else:
        alto = ancho / proporcion
    x_izq, x_der = centro_x - ancho / 2, centro_x + ancho / 2
    y_sup, y_inf = centro_y - alto / 2, centro_y + alto / 2
    min_lon, max_lat = geografia_mercator(x_izq, y_sup)
    max_lon, min_lat = geografia_mercator(x_der, y_inf)
    return min_lon, max_lon, min_lat, max_lat


def proyectar_mercator(
    longitud: float,
    latitud: float,
    limites: tuple[float, float, float, float],
    area: tuple[float, float, float, float],
) -> tuple[float, float]:
    izquierda, arriba, derecha, abajo = area
    x_izq, x_der, y_sup, y_inf = _mundo_limites(limites)
    x, y = mundo_mercator(longitud, latitud)
    px = izquierda + (x - x_izq) / max(1e-12, x_der - x_izq) * (derecha - izquierda)
    py = arriba + (y - y_sup) / max(1e-12, y_inf - y_sup) * (abajo - arriba)
    return px, py


def desproyectar_mercator(
    px: float,
    py: float,
    limites: tuple[float, float, float, float],
    area: tuple[float, float, float, float],
) -> tuple[float, float]:
    izquierda, arriba, derecha, abajo = area
    x_izq, x_der, y_sup, y_inf = _mundo_limites(limites)
    x = x_izq + (px - izquierda) / max(1e-12, derecha - izquierda) * (x_der - x_izq)
    y = y_sup + (py - arriba) / max(1e-12, abajo - arriba) * (y_inf - y_sup)
    return geografia_mercator(x, y)


def zoom_mercator(
    limites: tuple[float, float, float, float],
    factor: float,
    proporcion_x: float = 0.5,
    proporcion_y: float = 0.5,
) -> tuple[float, float, float, float]:
    x_izq, x_der, y_sup, y_inf = _mundo_limites(limites)
    proporcion_x = min(1.0, max(0.0, proporcion_x))
    proporcion_y = min(1.0, max(0.0, proporcion_y))
    ancla_x = x_izq + proporcion_x * (x_der - x_izq)
    ancla_y = y_sup + proporcion_y * (y_inf - y_sup)
    ancho = (x_der - x_izq) * factor
    alto = (y_inf - y_sup) * factor
    nuevo_x_izq = ancla_x - proporcion_x * ancho
    nuevo_y_sup = ancla_y - proporcion_y * alto
    min_lon, max_lat = geografia_mercator(nuevo_x_izq, nuevo_y_sup)
    max_lon, min_lat = geografia_mercator(nuevo_x_izq + ancho, nuevo_y_sup + alto)
    return min_lon, max_lon, min_lat, max_lat


def desplazar_mercator(
    limites: tuple[float, float, float, float],
    proporcion_x: float,
    proporcion_y: float,
) -> tuple[float, float, float, float]:
    """Desplaza la vista según píxeles normalizados del gesto de arrastre."""
    x_izq, x_der, y_sup, y_inf = _mundo_limites(limites)
    dx = -proporcion_x * (x_der - x_izq)
    dy = -proporcion_y * (y_inf - y_sup)
    min_lon, max_lat = geografia_mercator(x_izq + dx, y_sup + dy)
    max_lon, min_lat = geografia_mercator(x_der + dx, y_inf + dy)
    return min_lon, max_lon, min_lat, max_lat


class MapaBaseOSM:
    """Dibuja un fondo OSM no bloqueante dentro de un Canvas de Tk."""

    def __init__(self, canvas: tk.Canvas, redibujar) -> None:
        self.canvas = canvas
        self.redibujar = redibujar
        self._pendientes: set[tuple[int, int, int]] = set()
        self._foto: ImageTk.PhotoImage | None = None
        self._version_descargas = 0
        self._version_dibujada = 0
        raiz = Path(os.environ.get("LOCALAPPDATA", Path(__file__).resolve().parent))
        self.carpeta_cache = raiz / "Planning Tools" / "Cache Mapas"
        self.canvas.after(250, self._vigilar_descargas)

    def _zoom(self, limites, ancho: int, alto: int) -> int:
        x_izq, x_der, y_sup, y_inf = _mundo_limites(limites)
        span_x = max(1e-10, x_der - x_izq)
        span_y = max(1e-10, y_inf - y_sup)
        por_x = math.log2(max(1.0, ancho) / (256.0 * span_x))
        por_y = math.log2(max(1.0, alto) / (256.0 * span_y))
        return max(1, min(19, int(math.ceil(min(por_x, por_y)))))

    def _ruta_cache(self, clave: tuple[int, int, int]) -> Path:
        z, x, y = clave
        return self.carpeta_cache / str(z) / str(x) / f"{y}.png"

    def _imagen_memoria_o_disco(self, clave: tuple[int, int, int]) -> Image.Image | None:
        with _BLOQUEO:
            imagen = _MEMORIA.get(clave)
        if imagen is not None:
            return imagen
        ruta = self._ruta_cache(clave)
        if not ruta.exists():
            return None
        try:
            with Image.open(ruta) as archivo:
                imagen = archivo.convert("RGB").copy()
            with _BLOQUEO:
                _MEMORIA[clave] = imagen
            return imagen
        except OSError:
            return None

    def _solicitar(self, clave: tuple[int, int, int]) -> None:
        if _CERRANDO or clave in self._pendientes:
            return
        self._pendientes.add(clave)
        _EJECUTOR.submit(self._descargar, clave)

    def _descargar(self, clave: tuple[int, int, int]) -> None:
        z, x, y = clave
        try:
            peticion = Request(
                URL_TESELA.format(z=z, x=x, y=y),
                headers={"User-Agent": "PlanningTools/1.2 (desktop map viewer)"},
            )
            with urlopen(peticion, timeout=10) as respuesta:  # nosec B310 - HTTPS fijo
                contenido = respuesta.read()
            with Image.open(io.BytesIO(contenido)) as archivo:
                imagen = archivo.convert("RGB").copy()
            with _BLOQUEO:
                _MEMORIA[clave] = imagen
            try:
                ruta = self._ruta_cache(clave)
                ruta.parent.mkdir(parents=True, exist_ok=True)
                ruta.write_bytes(contenido)
            except OSError:
                # El mapa sigue funcionando en memoria si Windows impide
                # escribir la caché local.
                pass
        except Exception:
            pass
        finally:
            self._pendientes.discard(clave)
            self._version_descargas += 1

    def _vigilar_descargas(self) -> None:
        """Actualiza Tk únicamente desde su hilo principal."""
        try:
            if not self.canvas.winfo_exists():
                return
            if self._version_descargas != self._version_dibujada:
                self._version_dibujada = self._version_descargas
                self.redibujar()
            self.canvas.after(250, self._vigilar_descargas)
        except tk.TclError:
            pass

    def dibujar(
        self,
        limites: tuple[float, float, float, float],
        area: tuple[float, float, float, float],
    ) -> None:
        izquierda, arriba, derecha, abajo = area
        ancho = max(1, int(round(derecha - izquierda)))
        alto = max(1, int(round(abajo - arriba)))
        fondo = Image.new("RGB", (ancho, alto), "#DCECF3")
        x_izq, x_der, y_sup, y_inf = _mundo_limites(limites)
        zoom = self._zoom(limites, ancho, alto)
        escala = 2 ** zoom
        min_tx = math.floor(x_izq * escala)
        max_tx = math.floor(x_der * escala)
        min_ty = math.floor(y_sup * escala)
        max_ty = math.floor(y_inf * escala)
        max_indice = escala - 1

        for tx in range(min_tx, max_tx + 1):
            for ty in range(min_ty, max_ty + 1):
                if ty < 0 or ty > max_indice:
                    continue
                clave = (zoom, tx % escala, ty)
                imagen = self._imagen_memoria_o_disco(clave)
                if imagen is None:
                    self._solicitar(clave)
                    continue
                tile_x0, tile_x1 = tx / escala, (tx + 1) / escala
                tile_y0, tile_y1 = ty / escala, (ty + 1) / escala
                px0 = int(round((tile_x0 - x_izq) / max(1e-12, x_der - x_izq) * ancho))
                px1 = int(round((tile_x1 - x_izq) / max(1e-12, x_der - x_izq) * ancho))
                py0 = int(round((tile_y0 - y_sup) / max(1e-12, y_inf - y_sup) * alto))
                py1 = int(round((tile_y1 - y_sup) / max(1e-12, y_inf - y_sup) * alto))
                destino_ancho, destino_alto = max(1, px1 - px0), max(1, py1 - py0)
                tesela = imagen.resize((destino_ancho, destino_alto), Image.Resampling.LANCZOS)
                recorte_izq, recorte_sup = max(0, -px0), max(0, -py0)
                recorte_der = destino_ancho - max(0, px1 - ancho)
                recorte_inf = destino_alto - max(0, py1 - alto)
                if recorte_der > recorte_izq and recorte_inf > recorte_sup:
                    fondo.paste(tesela.crop((recorte_izq, recorte_sup, recorte_der, recorte_inf)), (max(0, px0), max(0, py0)))

        self._foto = ImageTk.PhotoImage(fondo)
        self.canvas.create_image(izquierda, arriba, image=self._foto, anchor="nw")

    def dibujar_atribucion(self, area: tuple[float, float, float, float]) -> None:
        izquierda, arriba, derecha, abajo = area
        self.canvas.create_rectangle(
            derecha - 168, abajo - 18, derecha, abajo,
            fill="#FFFFFF", outline="", stipple="gray25",
        )
        self.canvas.create_text(
            derecha - 4, abajo - 4, text=ATRIBUCION,
            anchor="se", fill="#34414E", font=("Segoe UI", 8),
        )
