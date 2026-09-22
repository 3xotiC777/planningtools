# -*- coding: utf-8 -*-
"""Pantallas de Optimización de rutas y Cruce/generación de polígonos."""

from __future__ import annotations

import math
import threading
import tkinter as tk
import unicodedata
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Callable

import customtkinter as ctk
import pandas as pd

from generador_mallas import FrameGeneradorMallas
from herramientas_geograficas import (
    cargar_poligonos,
    cargar_puntos_excel,
    contornos_desde_gdf,
    cruzar_puntos_poligonos,
    exportar_poligonos,
    unir_capas_poligonos,
)
from mapa_base import (
    MapaBaseOSM,
    ajustar_aspecto_mercator,
    desproyectar_mercator,
    desplazar_mercator,
    proyectar_mercator,
    zoom_mercator,
)
from optimizacion_rutas import (
    ErrorOptimizacion,
    ResultadoPlanificacion,
    ejecutar_qa_vial_mt,
    exportar_planificacion,
    mover_puntos,
    planificar_archivos,
    promedio_vecino_mas_cercano,
)


AZUL = "#0D5CAB"
AZUL_CLARO = "#33BDEE"
AZUL_OSCURO = "#1C293A"
VERDE = "#1E7D46"
ROJO = "#B3362B"
FONDO = "#F2F2F2"
BORDE = "#D5D8DC"
GRIS = "#69727D"
PALETA = ["#0D5CAB", "#1E7D46", "#F59E0B", "#7C3AED", "#D14343", "#0891B2", "#BE185D", "#4F46E5"]

VISTAS_POR_PAIS = {
    "CHILE": (-76.0, -66.0, -56.0, -17.0),
    "COSTA RICA": (-86.2, -82.3, 8.0, 11.3),
    "ECUADOR": (-92.2, -75.0, -5.3, 1.8),
    "EL SALVADOR": (-90.2, -87.6, 13.0, 14.6),
    "GUATEMALA": (-92.4, -88.0, 13.5, 18.0),
    "HONDURAS": (-89.5, -83.0, 12.8, 16.6),
    "NICARAGUA": (-88.0, -82.0, 10.5, 15.2),
    "PANAMA": (-83.1, -77.0, 7.0, 9.7),
    "REPUBLICA DOMINICANA": (-72.2, -68.2, 17.3, 20.2),
}


def vista_inicial_pais(nombre: str) -> tuple[float, float, float, float]:
    """Devuelve una vista útil aun cuando la configuración antigua no tenga límites."""
    limpio = "".join(
        caracter for caracter in unicodedata.normalize("NFD", str(nombre).upper())
        if unicodedata.category(caracter) != "Mn"
    )
    if limpio.startswith("GUATEMALA"):
        limpio = "GUATEMALA"
    return VISTAS_POR_PAIS.get(limpio, (-118.0, -33.0, -56.0, 33.0))


class MapaInteractivo(ctk.CTkFrame):
    """Lienzo vectorial con navegación, selección y dibujo de polígonos."""

    AYUDAS = {
        "mover": "Mover: arrastre el mapa sin seleccionar ni modificar elementos.",
        "seleccionar": "Seleccionar: arrastre un rectángulo para elegir los puntos del área.",
        "rectangulo": "Rectángulo: arrastre desde una esquina hasta la opuesta para crear un polígono.",
        "poligono": "Polígono: marque vértices con clic y pulse Terminar polígono cuando cierre el área.",
        "mano_alzada": "Mano alzada: mantenga presionado y dibuje libremente; al soltar se crea el polígono.",
        "editar": (
            "Editar figura: seleccione un polígono dibujado y arrástrelo para moverlo; "
            "arrastre los cuadros de las esquinas para cambiar su tamaño."
        ),
    }

    def __init__(
        self,
        master,
        permitir_dibujo: bool = False,
        al_seleccionar: Callable[[set[Any]], None] | None = None,
        al_dibujar: Callable[[list[tuple[float, float]]], None] | None = None,
        al_editar: Callable[[int, list[tuple[float, float]]], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        self.permitir_dibujo = permitir_dibujo
        self.al_seleccionar = al_seleccionar
        self.al_dibujar = al_dibujar
        self.al_editar = al_editar
        self.puntos: list[dict[str, Any]] = []
        self.contornos: list[list[tuple[float, float]]] = []
        self.contornos_editables: dict[int, int] = {}
        self.seleccionados: set[Any] = set()
        self.modo = "mover"
        self.radio = 4
        self.vista: tuple[float, float, float, float] | None = None
        self.vista_inicial: tuple[float, float, float, float] = (-118.0, -33.0, -56.0, 33.0)
        self._vista_render: tuple[float, float, float, float] | None = None
        self._inicio: tuple[float, float] | None = None
        self._vista_inicio: tuple[float, float, float, float] | None = None
        self._temporal: int | None = None
        self._vertices: list[tuple[float, float]] = []
        self._ultimo_pixel: tuple[float, float] | None = None
        self._contorno_editado: int | None = None
        self._edicion_accion: str | None = None
        self._edicion_pixeles: list[tuple[float, float]] = []
        self._edicion_centro: tuple[float, float] | None = None
        self._edicion_distancia: float = 1.0
        self._edicion_cambio = False

        barra = ctk.CTkFrame(self, fg_color="#F7F9FB", corner_radius=8)
        barra.pack(fill="x", padx=10, pady=(10, 6))
        self.botones: dict[str, ctk.CTkButton] = {}
        opciones = [("mover", "✋  Mover"), ("seleccionar", "▱  Seleccionar")]
        if permitir_dibujo:
            opciones += [
                ("rectangulo", "▭  Rectángulo"),
                ("poligono", "⬡  Por vértices"),
                ("mano_alzada", "✎  Mano alzada"),
                ("editar", "✥  Editar figura"),
            ]
        for modo, texto in opciones:
            boton = ctk.CTkButton(
                barra, text=texto, width=112, height=30, corner_radius=6,
                fg_color=AZUL if modo == "mover" else "transparent",
                text_color="white" if modo == "mover" else AZUL_OSCURO,
                hover_color=AZUL_CLARO, command=lambda m=modo: self.cambiar_modo(m),
            )
            boton.pack(side="left", padx=(5, 1), pady=5)
            boton.bind("<Enter>", lambda _e, m=modo: self._mostrar_ayuda(m))
            boton.bind("<Leave>", lambda _e: self._mostrar_ayuda(self.modo))
            self.botones[modo] = boton
        if permitir_dibujo:
            self.btn_terminar = ctk.CTkButton(
                barra, text="✓ Terminar polígono", width=132, height=30,
                fg_color=VERDE, hover_color="#27965A", state="disabled",
                command=self.terminar_poligono,
            )
            self.btn_terminar.pack(side="left", padx=(6, 1), pady=5)
            self.btn_terminar.bind("<Enter>", lambda _e: self.lbl_ayuda.configure(
                text="Terminar polígono: une el último vértice con el primero y guarda el área."
            ) if hasattr(self, "lbl_ayuda") else None)
        barra_vista = ctk.CTkFrame(self, fg_color="#F7F9FB")
        barra_vista.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(barra_vista, text="Tamaño de puntos", text_color=GRIS).pack(side="left", padx=(6, 3))
        self.slider = ctk.CTkSlider(barra_vista, from_=2, to=10, number_of_steps=8, width=150, command=self._cambiar_radio)
        self.slider.set(self.radio)
        self.slider.pack(side="left", padx=4)
        if permitir_dibujo:
            self.btn_achicar = ctk.CTkButton(
                barra_vista, text="−  Achicar", width=86, height=30,
                fg_color="transparent", border_width=1, border_color=BORDE,
                text_color=AZUL, hover_color="#E9F4FC", state="disabled",
                command=lambda: self._escalar_seleccionado(0.90),
            )
            self.btn_achicar.pack(side="left", padx=(16, 3), pady=5)
            self.btn_achicar.bind(
                "<Enter>",
                lambda _e: self.lbl_ayuda.configure(
                    text="Achicar: reduce 10% el polígono dibujado que está seleccionado."
                ) if hasattr(self, "lbl_ayuda") else None,
            )
            self.btn_agrandar = ctk.CTkButton(
                barra_vista, text="+  Agrandar", width=92, height=30,
                fg_color="transparent", border_width=1, border_color=BORDE,
                text_color=AZUL, hover_color="#E9F4FC", state="disabled",
                command=lambda: self._escalar_seleccionado(1.10),
            )
            self.btn_agrandar.pack(side="left", padx=3, pady=5)
            self.btn_agrandar.bind(
                "<Enter>",
                lambda _e: self.lbl_ayuda.configure(
                    text="Agrandar: aumenta 10% el polígono dibujado que está seleccionado."
                ) if hasattr(self, "lbl_ayuda") else None,
            )
        ctk.CTkButton(
            barra_vista, text="Ajustar", width=72, height=30, fg_color="transparent",
            border_width=1, border_color=BORDE, text_color=AZUL, hover_color="#E9F4FC",
            command=self.ajustar,
        ).pack(side="right", padx=5, pady=5)

        self.lbl_ayuda = ctk.CTkLabel(
            self, text=self.AYUDAS["mover"], anchor="w",
            text_color="#52606D", fg_color="#EEF5FA", corner_radius=6,
            font=ctk.CTkFont(size=11),
        )
        self.lbl_ayuda.pack(fill="x", padx=10, pady=(0, 6), ipady=3)

        self.canvas = tk.Canvas(self, bg="#F8FAFC", height=390, highlightthickness=0, cursor="hand2")
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas.bind("<Configure>", self._redibujar)
        self.canvas.bind("<ButtonPress-1>", self._presionar)
        self.canvas.bind("<B1-Motion>", self._arrastrar)
        self.canvas.bind("<ButtonRelease-1>", self._soltar)
        self.canvas.bind("<Double-Button-1>", self._doble_clic)
        self.canvas.bind("<MouseWheel>", self._rueda)
        self.canvas.bind("<Escape>", self._cancelar_poligono)
        self.canvas.focus_set()
        self._mapa_base = MapaBaseOSM(self.canvas, self._redibujar)
        self._mensaje("Cargue datos para visualizarlos")

    def cambiar_modo(self, modo: str) -> None:
        self.modo = modo
        self._vertices = []
        self._edicion_accion = None
        if modo != "editar":
            self._contorno_editado = None
        for nombre, boton in self.botones.items():
            activo = nombre == modo
            boton.configure(fg_color=AZUL if activo else "transparent", text_color="white" if activo else AZUL_OSCURO)
        cursores = {"mover": "hand2", "seleccionar": "crosshair", "rectangulo": "crosshair", "poligono": "tcross", "mano_alzada": "pencil", "editar": "fleur"}
        self.canvas.configure(cursor=cursores.get(modo, "arrow"))
        self._mostrar_ayuda(modo)
        self._actualizar_boton_terminar()
        self._actualizar_controles_edicion()
        self._redibujar()

    def _mostrar_ayuda(self, modo: str) -> None:
        if hasattr(self, "lbl_ayuda"):
            self.lbl_ayuda.configure(text=self.AYUDAS.get(modo, ""))

    def _actualizar_boton_terminar(self) -> None:
        if hasattr(self, "btn_terminar"):
            estado = "normal" if self.modo == "poligono" and len(self._vertices) >= 3 else "disabled"
            self.btn_terminar.configure(state=estado)

    def _actualizar_controles_edicion(self) -> None:
        if not hasattr(self, "btn_achicar"):
            return
        habilitado = (
            self.modo == "editar"
            and self._contorno_editado in self.contornos_editables
        )
        estado = "normal" if habilitado else "disabled"
        self.btn_achicar.configure(state=estado)
        self.btn_agrandar.configure(state=estado)

    def _cambiar_radio(self, valor: float) -> None:
        self.radio = int(round(valor))
        self._redibujar()

    def cargar(
        self,
        puntos: list[dict[str, Any]],
        contornos: list[list[tuple[float, float]]] | None = None,
        contornos_editables: dict[int, int] | None = None,
    ) -> None:
        self.puntos = puntos
        self.contornos = contornos or []
        self.contornos_editables = contornos_editables or {}
        if self._contorno_editado not in self.contornos_editables:
            self._contorno_editado = None
        ids = {p["id"] for p in puntos}
        self.seleccionados.intersection_update(ids)
        self._actualizar_controles_edicion()
        self.ajustar()

    def seleccionar_editable(self, referencia: int) -> None:
        """Selecciona en el mapa el polígono generado identificado por el padre."""
        self._contorno_editado = next(
            (indice for indice, valor in self.contornos_editables.items() if valor == referencia),
            None,
        )
        self._actualizar_controles_edicion()
        self._redibujar()

    def establecer_vista_inicial(self, limites: tuple[float, float, float, float]) -> None:
        self.vista_inicial = limites
        if not self.puntos and not self.contornos:
            self.vista = limites
            self._redibujar()

    def actualizar_contornos(self, contornos: list[list[tuple[float, float]]]) -> None:
        self.contornos = contornos
        self.ajustar()

    def ajustar(self) -> None:
        xs = [float(p["lon"]) for p in self.puntos]
        ys = [float(p["lat"]) for p in self.puntos]
        for contorno in self.contornos:
            xs.extend(x for x, _ in contorno)
            ys.extend(y for _, y in contorno)
        if not xs:
            self.vista = self.vista_inicial
            self._redibujar()
            return
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        if max_x - min_x < 0.01:
            min_x, max_x = min_x - 0.01, max_x + 0.01
        if max_y - min_y < 0.01:
            min_y, max_y = min_y - 0.01, max_y + 0.01
        pad_x, pad_y = (max_x - min_x) * 0.08, (max_y - min_y) * 0.08
        self.vista = (min_x - pad_x, max_x + pad_x, min_y - pad_y, max_y + pad_y)
        self._redibujar()

    def _dimensiones(self) -> tuple[int, int]:
        return max(300, self.canvas.winfo_width()), max(240, self.canvas.winfo_height())

    def _pantalla(self, lon: float, lat: float) -> tuple[float, float]:
        if not self.vista:
            return 0, 0
        ancho, alto = self._dimensiones()
        vista = self._vista_render or self.vista
        return proyectar_mercator(lon, lat, vista, (0, 0, ancho, alto))

    def _geografica(self, x: float, y: float) -> tuple[float, float]:
        if not self.vista:
            return 0, 0
        ancho, alto = self._dimensiones()
        vista = self._vista_render or self.vista
        return desproyectar_mercator(x, y, vista, (0, 0, ancho, alto))

    def _mensaje(self, texto: str) -> None:
        self.canvas.delete("all")
        ancho, alto = self._dimensiones()
        self.canvas.create_text(ancho / 2, alto / 2, text=texto, fill="#7A8490", font=("Segoe UI", 11))

    def _pixeles_contorno(self, indice: int) -> list[tuple[float, float]]:
        if indice < 0 or indice >= len(self.contornos):
            return []
        return [self._pantalla(*punto) for punto in self.contornos[indice]]

    @staticmethod
    def _centro_pixeles(puntos: list[tuple[float, float]]) -> tuple[float, float]:
        return (
            (min(x for x, _ in puntos) + max(x for x, _ in puntos)) / 2,
            (min(y for _, y in puntos) + max(y for _, y in puntos)) / 2,
        )

    @staticmethod
    def _punto_en_poligono(
        x: float, y: float, poligono: list[tuple[float, float]]
    ) -> bool:
        dentro = False
        if len(poligono) < 3:
            return dentro
        anterior = poligono[-1]
        for actual in poligono:
            x1, y1 = anterior
            x2, y2 = actual
            if (y1 > y) != (y2 > y):
                cruce_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
                if x < cruce_x:
                    dentro = not dentro
            anterior = actual
        return dentro

    def _esquinas_edicion(self, indice: int) -> list[tuple[float, float]]:
        puntos = self._pixeles_contorno(indice)
        if not puntos:
            return []
        min_x = min(x for x, _ in puntos)
        max_x = max(x for x, _ in puntos)
        min_y = min(y for _, y in puntos)
        max_y = max(y for _, y in puntos)
        return [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]

    def _buscar_contorno_editable(self, x: float, y: float) -> int | None:
        for indice in reversed(list(self.contornos_editables)):
            if self._punto_en_poligono(x, y, self._pixeles_contorno(indice)):
                return indice
        return None

    def _transformar_edicion(
        self,
        pixeles: list[tuple[float, float]],
        centro: tuple[float, float],
        *,
        factor: float = 1.0,
        delta_x: float = 0.0,
        delta_y: float = 0.0,
    ) -> list[tuple[float, float]]:
        centro_x, centro_y = centro
        return [
            self._geografica(
                centro_x + (x - centro_x) * factor + delta_x,
                centro_y + (y - centro_y) * factor + delta_y,
            )
            for x, y in pixeles
        ]

    def _notificar_edicion(self) -> None:
        indice = self._contorno_editado
        if indice is None or indice not in self.contornos_editables or not self.al_editar:
            return
        self.al_editar(
            self.contornos_editables[indice],
            list(self.contornos[indice]),
        )

    def _escalar_seleccionado(self, factor: float) -> None:
        indice = self._contorno_editado
        if indice is None or indice not in self.contornos_editables or not self.vista:
            return
        pixeles = self._pixeles_contorno(indice)
        if not pixeles:
            return
        centro = self._centro_pixeles(pixeles)
        self.contornos[indice] = self._transformar_edicion(
            pixeles, centro, factor=max(0.05, float(factor))
        )
        self._notificar_edicion()
        self._redibujar()

    def _presionar_edicion(self, evento) -> None:
        indice = self._contorno_editado
        sobre_esquina = False
        if indice is not None:
            sobre_esquina = any(
                math.hypot(evento.x - x, evento.y - y) <= 12
                for x, y in self._esquinas_edicion(indice)
            )
        if not sobre_esquina:
            indice = self._buscar_contorno_editable(evento.x, evento.y)
            self._contorno_editado = indice
        self._actualizar_controles_edicion()
        if indice is None:
            self._inicio = None
            self._redibujar()
            return
        self._inicio = (evento.x, evento.y)
        self._edicion_pixeles = self._pixeles_contorno(indice)
        self._edicion_centro = self._centro_pixeles(self._edicion_pixeles)
        self._edicion_accion = "escalar" if sobre_esquina else "mover"
        self._edicion_distancia = max(
            1.0,
            math.hypot(
                evento.x - self._edicion_centro[0],
                evento.y - self._edicion_centro[1],
            ),
        )
        self._edicion_cambio = False
        self._redibujar()

    def _arrastrar_edicion(self, evento) -> None:
        indice = self._contorno_editado
        if (
            indice is None
            or not self._inicio
            or not self._edicion_centro
            or not self._edicion_pixeles
        ):
            return
        if self._edicion_accion == "escalar":
            distancia = math.hypot(
                evento.x - self._edicion_centro[0],
                evento.y - self._edicion_centro[1],
            )
            factor = min(20.0, max(0.05, distancia / self._edicion_distancia))
            nuevos = self._transformar_edicion(
                self._edicion_pixeles, self._edicion_centro, factor=factor
            )
        else:
            nuevos = self._transformar_edicion(
                self._edicion_pixeles,
                self._edicion_centro,
                delta_x=evento.x - self._inicio[0],
                delta_y=evento.y - self._inicio[1],
            )
        self.contornos[indice] = nuevos
        self._edicion_cambio = True
        self._redibujar()

    def _redibujar(self, _evento=None) -> None:
        if not self.vista:
            return
        self.canvas.delete("all")
        ancho, alto = self._dimensiones()
        area = (0, 0, ancho, alto)
        self._vista_render = ajustar_aspecto_mercator(self.vista, area)
        self._mapa_base.dibujar(self._vista_render, area)
        for indice, contorno in enumerate(self.contornos):
            if len(contorno) < 2:
                continue
            coords = [valor for punto in contorno for valor in self._pantalla(*punto)]
            seleccionado = self.modo == "editar" and indice == self._contorno_editado
            self.canvas.create_polygon(
                coords,
                fill="#D7EAF7" if seleccionado else "#E7F2FA",
                outline=ROJO if seleccionado else AZUL,
                width=3 if seleccionado else 2,
                stipple="gray50",
            )
        if self.modo == "editar" and self._contorno_editado is not None:
            for x, y in self._esquinas_edicion(self._contorno_editado):
                self.canvas.create_rectangle(
                    x - 5, y - 5, x + 5, y + 5,
                    fill="white", outline=ROJO, width=2,
                )
        if len(self._vertices) >= 2:
            coords = [valor for punto in self._vertices for valor in self._pantalla(*punto)]
            self.canvas.create_line(coords, fill=ROJO, width=2)
        if self._vertices:
            for punto in self._vertices:
                x, y = self._pantalla(*punto)
                self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=ROJO, outline="white")
        maximos = 10_000
        paso = max(1, math.ceil(len(self.puntos) / maximos))
        for punto in self.puntos[::paso]:
            x, y = self._pantalla(float(punto["lon"]), float(punto["lat"]))
            seleccionado = punto["id"] in self.seleccionados
            radio = self.radio + (2 if seleccionado else 0)
            self.canvas.create_oval(
                x - radio, y - radio, x + radio, y + radio,
                fill=punto.get("color", AZUL), outline="white" if not seleccionado else AZUL_OSCURO,
                width=1 if not seleccionado else 3,
            )
        self._mapa_base.dibujar_atribucion(area)

    def _presionar(self, evento) -> None:
        self.canvas.focus_set()
        if not self.vista:
            return
        if self.modo == "editar":
            self._presionar_edicion(evento)
            return
        if self.modo == "poligono":
            self._vertices.append(self._geografica(evento.x, evento.y))
            self._actualizar_boton_terminar()
            self._redibujar()
            return
        if self.modo == "mano_alzada":
            self._inicio = (evento.x, evento.y)
            self._ultimo_pixel = (evento.x, evento.y)
            self._vertices = [self._geografica(evento.x, evento.y)]
            self._redibujar()
            return
        self._inicio = (evento.x, evento.y)
        self._vista_inicio = self._vista_render or self.vista
        if self.modo in {"seleccionar", "rectangulo"}:
            self._temporal = self.canvas.create_rectangle(evento.x, evento.y, evento.x, evento.y, outline=ROJO, width=2, dash=(4, 3))

    def _arrastrar(self, evento) -> None:
        if not self._inicio or not self.vista:
            return
        if self.modo == "editar":
            self._arrastrar_edicion(evento)
        elif self.modo == "mover" and self._vista_inicio:
            ancho, alto = self._dimensiones()
            dx, dy = evento.x - self._inicio[0], evento.y - self._inicio[1]
            self.vista = desplazar_mercator(self._vista_inicio, dx / ancho, dy / alto)
            self._redibujar()
        elif self.modo == "mano_alzada" and self._ultimo_pixel:
            if math.hypot(evento.x - self._ultimo_pixel[0], evento.y - self._ultimo_pixel[1]) >= 4:
                self._vertices.append(self._geografica(evento.x, evento.y))
                self._ultimo_pixel = (evento.x, evento.y)
                self._redibujar()
        elif self._temporal:
            self.canvas.coords(self._temporal, self._inicio[0], self._inicio[1], evento.x, evento.y)

    def _soltar(self, evento) -> None:
        if not self._inicio or not self.vista:
            return
        x1, y1 = self._inicio
        x2, y2 = evento.x, evento.y
        if self.modo == "editar":
            if self._edicion_cambio:
                self._notificar_edicion()
            self._edicion_accion = None
            self._edicion_pixeles = []
            self._edicion_centro = None
            self._redibujar()
        elif self.modo == "mano_alzada":
            if len(self._vertices) >= 3:
                self.terminar_poligono()
            else:
                self._cancelar_poligono()
        elif self.modo == "seleccionar":
            izquierda, derecha = sorted((x1, x2))
            arriba, abajo = sorted((y1, y2))
            encontrados = {
                p["id"] for p in self.puntos
                if izquierda <= self._pantalla(float(p["lon"]), float(p["lat"]))[0] <= derecha
                and arriba <= self._pantalla(float(p["lon"]), float(p["lat"]))[1] <= abajo
            }
            self.seleccionados = encontrados
            if self.al_seleccionar:
                self.al_seleccionar(encontrados)
            self._redibujar()
        elif self.modo == "rectangulo" and abs(x2 - x1) > 5 and abs(y2 - y1) > 5:
            a = self._geografica(x1, y1)
            b = self._geografica(x2, y2)
            coords = [(a[0], a[1]), (b[0], a[1]), (b[0], b[1]), (a[0], b[1]), (a[0], a[1])]
            if self.al_dibujar:
                self.al_dibujar(coords)
        self._inicio = None
        self._vista_inicio = None
        self._temporal = None

    def _doble_clic(self, _evento) -> None:
        if self.modo != "poligono" or len(self._vertices) < 3:
            return
        # Tk registra también el clic que originó el doble clic; elimina el
        # último vértice si quedó prácticamente repetido.
        if len(self._vertices) >= 2:
            a, b = self._vertices[-2:]
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1e-8:
                self._vertices.pop()
        self.terminar_poligono()

    def terminar_poligono(self) -> None:
        if len(self._vertices) < 3:
            return
        coords = list(self._vertices)
        if coords[-1] != coords[0]:
            coords.append(coords[0])
        self._vertices = []
        self._ultimo_pixel = None
        self._actualizar_boton_terminar()
        if self.al_dibujar:
            self.al_dibujar(coords)
        self._redibujar()

    def _cancelar_poligono(self, _evento=None) -> None:
        self._vertices = []
        self._ultimo_pixel = None
        self._actualizar_boton_terminar()
        self._redibujar()

    def _rueda(self, evento) -> None:
        if not self.vista:
            return
        factor = 0.82 if evento.delta > 0 else 1.22
        ancho, alto = self._dimensiones()
        rx, ry = evento.x / ancho, evento.y / alto
        self.vista = zoom_mercator(self._vista_render or self.vista, factor, rx, ry)
        self._redibujar()


def _titulo(master, titulo: str, subtitulo: str) -> None:
    ctk.CTkLabel(master, text=titulo, font=ctk.CTkFont(size=27, weight="bold"), text_color=AZUL).pack(anchor="w", padx=28, pady=(22, 2))
    ctk.CTkLabel(master, text=subtitulo, font=ctk.CTkFont(size=14), text_color=GRIS).pack(anchor="w", padx=28, pady=(0, 12))


def _boton_archivo(master, titulo: str, comando: Callable[[], None]) -> tuple[ctk.CTkFrame, ctk.CTkLabel]:
    tarjeta = ctk.CTkFrame(master, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
    ctk.CTkLabel(tarjeta, text=titulo, font=ctk.CTkFont(size=15, weight="bold"), text_color=AZUL_OSCURO).pack(anchor="w", padx=14, pady=(12, 2))
    etiqueta = ctk.CTkLabel(tarjeta, text="Ningún archivo seleccionado", text_color=GRIS, anchor="w")
    etiqueta.pack(fill="x", padx=14, pady=(0, 7))
    ctk.CTkButton(tarjeta, text="Seleccionar Excel", height=31, fg_color=AZUL, hover_color=AZUL_CLARO, command=comando).pack(anchor="w", padx=14, pady=(0, 12))
    return tarjeta, etiqueta


class FrameOptimizacionRutas(ctk.CTkFrame):
    def __init__(self, master, rutas: dict, _config: dict) -> None:
        super().__init__(master, fg_color=FONDO)
        self.rutas = rutas
        self.ruta_base: Path | None = None
        self.ruta_forecast: Path | None = None
        self.resultado: ResultadoPlanificacion | None = None
        self.datos_excel: pd.DataFrame | None = None
        self.lat_excel: str | None = None
        self.lon_excel: str | None = None
        self.ruta_excel_libre: Path | None = None
        self.seleccion_excel: set[Any] = set()
        _titulo(self, "Optimización de rutas", "Planifique jornadas geográficas o abra cualquier base de puntos para verla y editarla.")

        self.tabs = ctk.CTkSegmentedButton(
            self, values=["Nueva planificación", "Puntos en Excel"],
            command=self._cambiar_tab, selected_color=AZUL, selected_hover_color=AZUL_CLARO,
            unselected_color="white", unselected_hover_color="#E9F4FC", text_color=AZUL_OSCURO,
        )
        self.tabs.pack(anchor="w", padx=28, pady=(0, 10))
        self.contenedor = ctk.CTkFrame(self, fg_color="transparent")
        self.contenedor.pack(fill="both", expand=True)
        self.tab_plan = self._crear_tab_plan()
        self.tab_excel = self._crear_tab_excel()
        self.tabs.set("Nueva planificación")
        self._cambiar_tab("Nueva planificación")

    def _cambiar_tab(self, nombre: str) -> None:
        for frame in (self.tab_plan, self.tab_excel):
            frame.pack_forget()
        (self.tab_plan if nombre == "Nueva planificación" else self.tab_excel).pack(fill="both", expand=True)

    def _crear_tab_plan(self):
        marco = ctk.CTkScrollableFrame(self.contenedor, fg_color="transparent")
        archivos = ctk.CTkFrame(marco, fg_color="transparent")
        archivos.pack(fill="x", padx=20, pady=(0, 8))
        archivos.grid_columnconfigure((0, 1), weight=1)
        tarjeta_base, self.lbl_base = _boton_archivo(archivos, "Base de puntos · MT FINAL, SELECCION, LATITUD y LONGITUD", self._elegir_base)
        tarjeta_base.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        tarjeta_forecast, self.lbl_forecast = _boton_archivo(archivos, "Forecast mensual · MT FINAL en filas y días en columnas", self._elegir_forecast)
        tarjeta_forecast.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        acciones = ctk.CTkFrame(marco, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        acciones.pack(fill="x", padx=20, pady=5)
        self.btn_calcular = ctk.CTkButton(acciones, text="Calcular planificación", fg_color=AZUL, hover_color=AZUL_CLARO, height=38, command=self._calcular)
        self.btn_calcular.pack(side="left", padx=12, pady=12)
        self.qa_auto = ctk.CTkCheckBox(
            acciones, text="QA vial automático · OSRM", fg_color=AZUL,
            hover_color=AZUL_CLARO, text_color=AZUL_OSCURO,
        )
        self.qa_auto.select()
        self.qa_auto.pack(side="left", padx=(0, 8))
        self.progreso = ctk.CTkProgressBar(acciones, width=180, progress_color=AZUL)
        self.progreso.set(0)
        self.progreso.pack(side="left", padx=8)
        self.lbl_estado = ctk.CTkLabel(acciones, text="Seleccione los dos archivos.", text_color=GRIS, anchor="w")
        self.lbl_estado.pack(side="left", fill="x", expand=True, padx=8)
        self.btn_exportar = ctk.CTkButton(acciones, text="Exportar Excel", fg_color=VERDE, hover_color="#27965A", state="disabled", command=self._exportar_plan)
        self.btn_exportar.pack(side="right", padx=12, pady=12)
        self.mapa_plan = MapaInteractivo(marco, al_seleccionar=self._seleccion_plan)
        self.mapa_plan.pack(fill="both", expand=True, padx=20, pady=5)
        edicion = ctk.CTkFrame(marco, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        edicion.pack(fill="x", padx=20, pady=(5, 18))
        self.lbl_sel_plan = ctk.CTkLabel(edicion, text="0 puntos seleccionados", text_color=GRIS)
        self.lbl_sel_plan.pack(side="left", padx=12, pady=10)
        ctk.CTkLabel(edicion, text="Mover al día", text_color=AZUL_OSCURO).pack(side="left", padx=(20, 4))
        self.ent_dia = ctk.CTkEntry(edicion, width=65, placeholder_text="Día")
        self.ent_dia.pack(side="left", padx=4)
        ctk.CTkButton(edicion, text="Aplicar", width=82, fg_color=AZUL, command=self._mover_dia).pack(side="left", padx=6)
        self.combo_mt_qa = ctk.CTkComboBox(edicion, values=["MT FINAL"], width=135)
        self.combo_mt_qa.set("MT FINAL")
        self.combo_mt_qa.pack(side="right", padx=4)
        self.btn_qa = ctk.CTkButton(edicion, text="Repetir QA vial", width=112, fg_color="transparent", border_width=1, border_color=AZUL, text_color=AZUL, state="disabled", command=self._qa_vial)
        self.btn_qa.pack(side="right", padx=4)
        self.lbl_avisos = ctk.CTkLabel(edicion, text="", text_color="#8A5B00", anchor="w", justify="left", wraplength=650)
        self.lbl_avisos.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        return marco

    def _crear_tab_excel(self):
        marco = ctk.CTkScrollableFrame(self.contenedor, fg_color="transparent")
        superior = ctk.CTkFrame(marco, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        superior.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkButton(superior, text="Abrir puntos en Excel", fg_color=AZUL, hover_color=AZUL_CLARO, command=self._abrir_excel_libre).pack(side="left", padx=12, pady=12)
        self.lbl_excel = ctk.CTkLabel(superior, text="Solo LATITUD y LONGITUD son obligatorias.", text_color=GRIS, anchor="w")
        self.lbl_excel.pack(side="left", fill="x", expand=True, padx=8)
        self.btn_guardar_excel = ctk.CTkButton(
            superior, text="Guardar copia", fg_color=VERDE,
            state="disabled", command=self._guardar_excel_libre,
        )
        self.btn_guardar_excel.pack(side="right", padx=12)
        filtros = ctk.CTkFrame(marco, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        filtros.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(filtros, text="Color / filtro por", text_color=AZUL_OSCURO).pack(side="left", padx=(12, 4), pady=10)
        self.combo_columna = ctk.CTkComboBox(filtros, values=["(sin agrupar)"], width=190, command=lambda _v: self._refrescar_excel())
        self.combo_columna.pack(side="left", padx=4)
        self.combo_valor = ctk.CTkComboBox(filtros, values=["(todos)"], width=180, command=lambda _v: self._refrescar_excel())
        self.combo_valor.set("(todos)")
        self.combo_valor.pack(side="left", padx=4)
        self.lbl_vecino = ctk.CTkLabel(filtros, text="", text_color=GRIS)
        self.lbl_vecino.pack(side="right", padx=12)
        self.mapa_excel = MapaInteractivo(marco, al_seleccionar=self._seleccion_excel_cambio)
        self.mapa_excel.pack(fill="both", expand=True, padx=20, pady=5)
        edicion = ctk.CTkFrame(marco, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        edicion.pack(fill="x", padx=20, pady=(5, 18))
        self.lbl_sel_excel = ctk.CTkLabel(edicion, text="0 seleccionados", text_color=GRIS)
        self.lbl_sel_excel.pack(side="left", padx=12, pady=12)
        self.combo_editar_col = ctk.CTkComboBox(edicion, values=[""], width=180)
        self.combo_editar_col.pack(side="left", padx=5)
        self.ent_valor = ctk.CTkEntry(edicion, width=170, placeholder_text="Nuevo valor")
        self.ent_valor.pack(side="left", padx=5)
        ctk.CTkButton(edicion, text="Aplicar a selección", fg_color=AZUL, command=self._editar_excel).pack(side="left", padx=5)
        ctk.CTkButton(edicion, text="+ Columna", fg_color="transparent", border_width=1, border_color=AZUL, text_color=AZUL, command=self._agregar_columna).pack(side="left", padx=5)
        return marco

    def _elegir_base(self) -> None:
        ruta = filedialog.askopenfilename(title="Seleccione la base de puntos", filetypes=[("Excel", "*.xlsx *.xls")])
        if ruta:
            self.ruta_base = Path(ruta)
            self.lbl_base.configure(text=self.ruta_base.name)

    def _elegir_forecast(self) -> None:
        ruta = filedialog.askopenfilename(title="Seleccione el forecast mensual", filetypes=[("Excel", "*.xlsx *.xls")])
        if ruta:
            self.ruta_forecast = Path(ruta)
            self.lbl_forecast.configure(text=self.ruta_forecast.name)

    def _calcular(self) -> None:
        if not self.ruta_base or not self.ruta_forecast:
            messagebox.showwarning("Archivos requeridos", "Seleccione la base de puntos y el forecast mensual.")
            return
        self.btn_calcular.configure(state="disabled")
        self.btn_exportar.configure(state="disabled")
        self.progreso.set(0.03)
        self.lbl_estado.configure(text="Iniciando...")

        def progreso(valor: float, mensaje: str) -> None:
            self.after(0, lambda: (self.progreso.set(valor), self.lbl_estado.configure(text=mensaje)))

        def tarea() -> None:
            try:
                resultado = planificar_archivos(
                    self.ruta_base, self.ruta_forecast, progreso,
                    qa_vial_automatico=bool(self.qa_auto.get()),
                )
                self.after(0, lambda: self._plan_terminado(resultado))
            except Exception as exc:
                self.after(0, lambda e=exc: self._plan_error(e))

        threading.Thread(target=tarea, daemon=True).start()

    def _plan_terminado(self, resultado: ResultadoPlanificacion) -> None:
        self.resultado = resultado
        self.btn_calcular.configure(state="normal")
        self.btn_exportar.configure(state="normal")
        self.btn_qa.configure(state="normal")
        asignados = sum(p.dia is not None for p in resultado.puntos)
        self.lbl_estado.configure(text=f"{asignados:,} puntos asignados · modo {resultado.modo.replace('-', ' ')}")
        self.lbl_avisos.configure(text="\n".join(resultado.avisos[:3]))
        mts = sorted({p.mt_asignado for p in resultado.puntos if p.mt_asignado})
        self.combo_mt_qa.configure(values=mts or ["MT FINAL"])
        self.combo_mt_qa.set(mts[0] if mts else "MT FINAL")
        self._refrescar_plan()

    def _plan_error(self, exc: Exception) -> None:
        self.btn_calcular.configure(state="normal")
        self.lbl_estado.configure(text="No se pudo calcular.")
        messagebox.showerror("Error de planificación", str(exc))

    def _refrescar_plan(self) -> None:
        if not self.resultado:
            return
        dias = sorted({p.dia for p in self.resultado.puntos if p.dia})
        colores = {dia: PALETA[i % len(PALETA)] for i, dia in enumerate(dias)}
        puntos = [{
            "id": p.id, "lat": p.lat, "lon": p.lon,
            "color": colores.get(p.dia, "#A7AFB8") if p.tipo == "Titular" else "#34A853",
        } for p in self.resultado.puntos]
        self.mapa_plan.cargar(puntos)

    def _seleccion_plan(self, ids: set[Any]) -> None:
        self.lbl_sel_plan.configure(text=f"{len(ids):,} puntos seleccionados")

    def _mover_dia(self) -> None:
        if not self.resultado or not self.mapa_plan.seleccionados:
            messagebox.showwarning("Sin selección", "Use la herramienta Seleccionar para marcar puntos en el mapa.")
            return
        try:
            dia = int(self.ent_dia.get())
            if dia < 1 or dia > 31:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Día inválido", "Escriba un día entre 1 y 31.")
            return
        mover_puntos(self.resultado, {str(i) for i in self.mapa_plan.seleccionados}, dia)
        self._refrescar_plan()
        self.lbl_estado.configure(text=f"Selección movida al día {dia}; promedios recalculados.")

    def _exportar_plan(self) -> None:
        if not self.resultado or not self.ruta_base:
            return
        sugerido = f"{self.ruta_base.stem}_Planificado.xlsx"
        ruta = filedialog.asksaveasfilename(title="Guardar planificación", defaultextension=".xlsx", initialfile=sugerido, filetypes=[("Excel", "*.xlsx")])
        if ruta:
            try:
                exportar_planificacion(self.ruta_base, ruta, self.resultado)
                messagebox.showinfo("Planificación exportada", f"Se guardó el archivo en:\n{ruta}")
            except Exception as exc:
                messagebox.showerror("No se pudo exportar", str(exc))

    def _qa_vial(self) -> None:
        if not self.resultado:
            return
        mt = self.combo_mt_qa.get()
        if mt == "MT FINAL":
            return
        confirmar = messagebox.askyesno(
            "QA vial por carretera",
            "Esta revisión enviará las coordenadas de los titulares del MT seleccionado "
            "al servicio público OSRM. ¿Desea continuar?",
        )
        if not confirmar:
            return
        self.btn_qa.configure(state="disabled")
        self.lbl_estado.configure(text=f"Consultando carreteras para {mt}...")

        def tarea() -> None:
            try:
                aviso = ejecutar_qa_vial_mt(self.resultado, mt)
                self.after(0, lambda: self._qa_terminado(aviso))
            except Exception as exc:
                self.after(0, lambda e=exc: self._qa_error(e))

        threading.Thread(target=tarea, daemon=True).start()

    def _qa_terminado(self, aviso: str) -> None:
        self.btn_qa.configure(state="normal")
        self.lbl_estado.configure(text="QA vial terminado.")
        self.lbl_avisos.configure(text=aviso)
        self._refrescar_plan()

    def _qa_error(self, exc: Exception) -> None:
        self.btn_qa.configure(state="normal")
        self.lbl_estado.configure(text="QA vial no aplicado; la planificación no cambió.")
        messagebox.showerror("QA vial no aplicado", str(exc))

    def _abrir_excel_libre(self) -> None:
        ruta = filedialog.askopenfilename(title="Abrir puntos en Excel", filetypes=[("Excel", "*.xlsx *.xls")])
        if not ruta:
            return
        try:
            datos, lat, lon = cargar_puntos_excel(ruta)
        except Exception as exc:
            messagebox.showerror("No se pudo abrir", str(exc))
            return
        self.ruta_excel_libre = Path(ruta)
        self.datos_excel, self.lat_excel, self.lon_excel = datos, lat, lon
        columnas = [str(c) for c in datos.columns]
        self.combo_columna.configure(values=["(sin agrupar)"] + columnas)
        self.combo_columna.set("(sin agrupar)")
        self.combo_editar_col.configure(values=columnas)
        self.combo_editar_col.set(columnas[0])
        self.combo_valor.configure(values=["(todos)"])
        self.combo_valor.set("(todos)")
        promedio = promedio_vecino_mas_cercano(datos, lat, lon)
        self.lbl_vecino.configure(text=f"Vecino más cercano promedio: {promedio:,.0f} m" if promedio is not None else "")
        validos = pd.to_numeric(datos[lat], errors="coerce").notna() & pd.to_numeric(datos[lon], errors="coerce").notna()
        self.lbl_excel.configure(text=f"{self.ruta_excel_libre.name} · {int(validos.sum()):,} puntos válidos")
        self.btn_guardar_excel.configure(state="normal")
        self._refrescar_excel()

    def _refrescar_excel(self) -> None:
        if self.datos_excel is None or not self.lat_excel or not self.lon_excel:
            return
        datos = self.datos_excel
        col = self.combo_columna.get()
        if col != "(sin agrupar)" and col in datos.columns:
            valores = sorted(datos[col].fillna("(vacío)").astype(str).unique().tolist())
            actuales = ["(todos)"] + valores[:500]
            valor_anterior = self.combo_valor.get()
            self.combo_valor.configure(values=actuales)
            if valor_anterior not in actuales:
                self.combo_valor.set("(todos)")
            valor = self.combo_valor.get()
            vista = datos if valor == "(todos)" else datos.loc[datos[col].fillna("(vacío)").astype(str) == valor]
            mapa_colores = {v: PALETA[i % len(PALETA)] for i, v in enumerate(valores)}
        else:
            self.combo_valor.configure(values=["(todos)"])
            self.combo_valor.set("(todos)")
            vista, mapa_colores = datos, {}
        puntos = []
        for indice, fila in vista.iterrows():
            try:
                lat, lon = float(fila[self.lat_excel]), float(fila[self.lon_excel])
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            categoria = str(fila[col] if col in datos.columns and pd.notna(fila[col]) else "(vacío)")
            puntos.append({"id": indice, "lat": lat, "lon": lon, "color": mapa_colores.get(categoria, AZUL)})
        self.mapa_excel.cargar(puntos)

    def _seleccion_excel_cambio(self, ids: set[Any]) -> None:
        self.seleccion_excel = ids
        self.lbl_sel_excel.configure(text=f"{len(ids):,} seleccionados")

    def _editar_excel(self) -> None:
        if self.datos_excel is None or not self.seleccion_excel:
            messagebox.showwarning("Sin selección", "Seleccione un área del mapa antes de editar.")
            return
        columna = self.combo_editar_col.get()
        if columna not in self.datos_excel.columns:
            return
        self.datos_excel.loc[list(self.seleccion_excel), columna] = self.ent_valor.get()
        self._refrescar_excel()

    def _agregar_columna(self) -> None:
        if self.datos_excel is None:
            return
        nombre = simpledialog.askstring("Nueva columna", "Nombre de la columna:", parent=self)
        if not nombre or nombre in self.datos_excel.columns:
            return
        self.datos_excel[nombre] = ""
        columnas = [str(c) for c in self.datos_excel.columns]
        self.combo_columna.configure(values=["(sin agrupar)"] + columnas)
        self.combo_editar_col.configure(values=columnas)
        self.combo_editar_col.set(nombre)

    def _guardar_excel_libre(self) -> None:
        if self.datos_excel is None or not self.ruta_excel_libre:
            return
        ruta = filedialog.asksaveasfilename(title="Guardar puntos editados", defaultextension=".xlsx", initialfile=f"{self.ruta_excel_libre.stem}_Editado.xlsx", filetypes=[("Excel", "*.xlsx")])
        if ruta:
            try:
                self.datos_excel.to_excel(ruta, index=False)
                messagebox.showinfo("Archivo guardado", f"Se guardó la copia en:\n{ruta}")
            except Exception as exc:
                messagebox.showerror("No se pudo guardar", str(exc))


class FrameCrucePoligonosDibujo(ctk.CTkFrame):
    def __init__(self, master, rutas: dict, config: dict) -> None:
        super().__init__(master, fg_color=FONDO)
        self.rutas = rutas
        self.puntos: pd.DataFrame | None = None
        self.lat: str | None = None
        self.lon: str | None = None
        self.poligonos_cargados = None
        self.generados: list[dict[str, Any]] = []
        self.resultado_cruce: pd.DataFrame | None = None
        _titulo(self, "Cruce y generador de polígonos", "Cargue capas, dibuje áreas y cruce puntos sin salir de Planning Tools.")
        marco = ctk.CTkScrollableFrame(self, fg_color="transparent")
        marco.pack(fill="both", expand=True)
        archivos = ctk.CTkFrame(marco, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        archivos.pack(fill="x", padx=20, pady=(0, 7))
        ctk.CTkButton(archivos, text="Cargar puntos Excel", fg_color=AZUL, command=self._cargar_puntos).pack(side="left", padx=12, pady=12)
        ctk.CTkButton(archivos, text="Cargar polígonos (GPKG / Excel WKT)", fg_color=AZUL, command=self._cargar_poligonos).pack(side="left", padx=5)
        self.lbl_archivos = ctk.CTkLabel(archivos, text="El mapa está listo: dibuje por vértices, rectángulo o a mano alzada.", text_color=GRIS, anchor="w")
        self.lbl_archivos.pack(side="left", fill="x", expand=True, padx=10)
        self.mapa = MapaInteractivo(
            marco,
            permitir_dibujo=True,
            al_dibujar=self._poligono_dibujado,
            al_editar=self._poligono_editado,
        )
        self.mapa.pack(fill="both", expand=True, padx=20, pady=5)
        pais = config.get("pais_activo", "")
        cfg_sel = config.get("paises", {}).get(pais, {}).get("modulo_seleccion", {})
        limites = cfg_sel.get("limites_pais", {})
        rango_lat, rango_lon = limites.get("lat"), limites.get("lon")
        if (
            isinstance(rango_lat, (list, tuple)) and len(rango_lat) == 2
            and isinstance(rango_lon, (list, tuple)) and len(rango_lon) == 2
        ):
            vista_inicial = (float(rango_lon[0]), float(rango_lon[1]), float(rango_lat[0]), float(rango_lat[1]))
        else:
            vista_inicial = vista_inicial_pais(pais)
        self.mapa.establecer_vista_inicial(vista_inicial)
        acciones = ctk.CTkFrame(marco, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        acciones.pack(fill="x", padx=20, pady=(5, 18))
        self.lbl_poligonos = ctk.CTkLabel(acciones, text="0 polígonos generados", text_color=GRIS)
        self.lbl_poligonos.pack(side="left", padx=12, pady=12)
        ctk.CTkButton(acciones, text="Deshacer último", fg_color="transparent", border_width=1, border_color=BORDE, text_color=AZUL, command=self._deshacer).pack(side="left", padx=4)
        ctk.CTkButton(acciones, text="Cruzar puntos", fg_color=AZUL, command=self._cruzar).pack(side="left", padx=4)
        ctk.CTkButton(acciones, text="Exportar cruce", fg_color=VERDE, command=self._exportar_cruce).pack(side="right", padx=8)
        ctk.CTkButton(acciones, text="Exportar polígonos", fg_color=VERDE, command=self._exportar_poligonos).pack(side="right", padx=4)
        atributos = ctk.CTkFrame(marco, fg_color="white", border_width=1, border_color=BORDE, corner_radius=10)
        atributos.pack(fill="x", padx=20, pady=(0, 18))
        ctk.CTkLabel(atributos, text="Editar atributos", font=ctk.CTkFont(weight="bold"), text_color=AZUL_OSCURO).pack(side="left", padx=(12, 6), pady=12)
        self.combo_poligono = ctk.CTkComboBox(atributos, values=["Sin polígonos"], width=150)
        self.combo_poligono.set("Sin polígonos")
        self.combo_poligono.pack(side="left", padx=4)
        self.combo_atributo = ctk.CTkComboBox(atributos, values=["DESCRIPCION"], width=170)
        self.combo_atributo.set("DESCRIPCION")
        self.combo_atributo.pack(side="left", padx=4)
        self.ent_atributo = ctk.CTkEntry(atributos, width=160, placeholder_text="Nuevo valor")
        self.ent_atributo.pack(side="left", padx=4)
        ctk.CTkButton(atributos, text="Aplicar", width=75, fg_color=AZUL, command=self._editar_atributo).pack(side="left", padx=4)
        ctk.CTkButton(atributos, text="+ Campo", width=78, fg_color="transparent", border_width=1, border_color=AZUL, text_color=AZUL, command=self._agregar_campo_poligono).pack(side="left", padx=4)
        ctk.CTkButton(atributos, text="Quitar campo", width=94, fg_color="transparent", border_width=1, border_color=ROJO, text_color=ROJO, command=self._quitar_campo_poligono).pack(side="left", padx=4)

    def _puntos_mapa(self) -> list[dict[str, Any]]:
        if self.puntos is None or not self.lat or not self.lon:
            return []
        salida = []
        for indice, fila in self.puntos.iterrows():
            try:
                lat, lon = float(fila[self.lat]), float(fila[self.lon])
            except (TypeError, ValueError):
                continue
            if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
                color = VERDE if "DENTRO_POLIGONO" in self.puntos.columns and fila["DENTRO_POLIGONO"] == "SI" else AZUL
                salida.append({"id": indice, "lat": lat, "lon": lon, "color": color})
        return salida

    def _capa_completa(self):
        return unir_capas_poligonos(self.poligonos_cargados, self.generados)

    def _actualizar_mapa(self) -> None:
        capa = self._capa_completa()
        contornos_cargados = contornos_desde_gdf(self.poligonos_cargados)
        contornos = list(contornos_cargados)
        editables: dict[int, int] = {}
        for posicion, generado in enumerate(self.generados):
            coordenadas = list(generado.get("coordenadas", []))
            if len(coordenadas) < 3:
                continue
            editables[len(contornos)] = posicion
            contornos.append(coordenadas)
        self.mapa.cargar(self._puntos_mapa(), contornos, editables)
        cargados = 0 if self.poligonos_cargados is None else len(self.poligonos_cargados)
        self.lbl_poligonos.configure(text=f"{len(self.generados)} generados · {cargados} cargados")
        cantidad = len(capa)
        opciones = [f"Polígono {i + 1}" for i in range(cantidad)] or ["Sin polígonos"]
        self.combo_poligono.configure(values=opciones)
        if self.combo_poligono.get() not in opciones:
            self.combo_poligono.set(opciones[0])
        columnas = [str(col) for col in capa.columns if col != "geometry"] or ["DESCRIPCION"]
        self.combo_atributo.configure(values=columnas)
        if self.combo_atributo.get() not in columnas:
            self.combo_atributo.set(columnas[0])

    def _cargar_puntos(self) -> None:
        ruta = filedialog.askopenfilename(title="Cargar puntos", filetypes=[("Excel", "*.xlsx *.xls")])
        if not ruta:
            return
        try:
            self.puntos, self.lat, self.lon = cargar_puntos_excel(ruta)
            self.resultado_cruce = None
            self.lbl_archivos.configure(text=f"Puntos: {Path(ruta).name} · {len(self.puntos):,} filas")
            self._actualizar_mapa()
        except Exception as exc:
            messagebox.showerror("No se pudieron cargar los puntos", str(exc))

    def _cargar_poligonos(self) -> None:
        ruta = filedialog.askopenfilename(title="Cargar polígonos", filetypes=[("Polígonos", "*.gpkg *.shp *.geojson *.xlsx *.xls")])
        if not ruta:
            return
        try:
            self.poligonos_cargados = cargar_poligonos(ruta)
            self.lbl_archivos.configure(text=f"Polígonos: {Path(ruta).name} · {len(self.poligonos_cargados):,} elementos")
            self._actualizar_mapa()
        except Exception as exc:
            messagebox.showerror("No se pudieron cargar los polígonos", str(exc))

    def _poligono_dibujado(self, coordenadas: list[tuple[float, float]]) -> None:
        descripcion = simpledialog.askstring("Descripción", "Descripción del nuevo polígono:", parent=self)
        if descripcion is None:
            return
        self.generados.append({"id": len(self.generados) + 1, "descripcion": descripcion or f"Polígono {len(self.generados) + 1}", "coordenadas": coordenadas, "atributos": {}})
        self._actualizar_mapa()
        self.mapa.cambiar_modo("editar")
        self.mapa.seleccionar_editable(len(self.generados) - 1)

    def _poligono_editado(
        self, posicion: int, coordenadas: list[tuple[float, float]]
    ) -> None:
        if posicion < 0 or posicion >= len(self.generados):
            return
        self.generados[posicion]["coordenadas"] = coordenadas
        self.resultado_cruce = None
        self.lbl_archivos.configure(
            text=(
                f"Polígono {posicion + 1} ajustado. "
                "Si ya había realizado el cruce, ejecútelo nuevamente."
            )
        )

    def _deshacer(self) -> None:
        if self.generados:
            self.generados.pop()
            self._actualizar_mapa()

    def _cruzar(self) -> None:
        if self.puntos is None or not self.lat or not self.lon:
            messagebox.showwarning("Faltan puntos", "Cargue primero un Excel de puntos.")
            return
        try:
            self.resultado_cruce = cruzar_puntos_poligonos(self.puntos, self.lat, self.lon, self._capa_completa())
            self.puntos = self.resultado_cruce
            cubiertos = int((self.puntos["DENTRO_POLIGONO"] == "SI").sum())
            self.lbl_archivos.configure(text=f"Cruce terminado: {cubiertos:,} de {len(self.puntos):,} puntos dentro de polígonos.")
            self._actualizar_mapa()
        except Exception as exc:
            messagebox.showerror("No se pudo realizar el cruce", str(exc))

    def _indice_poligono(self) -> int | None:
        valor = self.combo_poligono.get()
        if not valor.startswith("Polígono "):
            return None
        try:
            return int(valor.split()[-1]) - 1
        except ValueError:
            return None

    def _editar_atributo(self) -> None:
        posicion = self._indice_poligono()
        if posicion is None:
            return
        columna, valor = self.combo_atributo.get(), self.ent_atributo.get()
        cargados = 0 if self.poligonos_cargados is None else len(self.poligonos_cargados)
        if posicion < cargados:
            if columna not in self.poligonos_cargados.columns:
                self.poligonos_cargados[columna] = None
            indice = self.poligonos_cargados.index[posicion]
            self.poligonos_cargados.at[indice, columna] = valor
        else:
            generado = self.generados[posicion - cargados]
            if columna == "DESCRIPCION":
                generado["descripcion"] = valor
            elif columna == "ID_POLIGONO":
                generado["id"] = valor
            else:
                generado.setdefault("atributos", {})[columna] = valor
        self._actualizar_mapa()

    def _agregar_campo_poligono(self) -> None:
        if self._capa_completa().empty:
            return
        nombre = simpledialog.askstring("Nuevo campo", "Nombre del campo de polígonos:", parent=self)
        if not nombre or nombre == "geometry":
            return
        if self.poligonos_cargados is not None and nombre not in self.poligonos_cargados.columns:
            self.poligonos_cargados[nombre] = None
        for generado in self.generados:
            generado.setdefault("atributos", {}).setdefault(nombre, None)
        self._actualizar_mapa()
        self.combo_atributo.set(nombre)

    def _quitar_campo_poligono(self) -> None:
        columna = self.combo_atributo.get()
        if columna in {"geometry", "ID_POLIGONO", "DESCRIPCION"}:
            messagebox.showwarning("Campo protegido", "ID_POLIGONO y DESCRIPCION no se pueden quitar.")
            return
        if self.poligonos_cargados is not None and columna in self.poligonos_cargados.columns:
            self.poligonos_cargados = self.poligonos_cargados.drop(columns=[columna])
        for generado in self.generados:
            generado.setdefault("atributos", {}).pop(columna, None)
        self._actualizar_mapa()

    def _exportar_cruce(self) -> None:
        if self.resultado_cruce is None:
            messagebox.showwarning("Sin cruce", "Ejecute primero Cruzar puntos.")
            return
        ruta = filedialog.asksaveasfilename(title="Exportar cruce", defaultextension=".xlsx", initialfile="Cruce_Puntos_Poligonos.xlsx", filetypes=[("Excel", "*.xlsx")])
        if ruta:
            try:
                self.resultado_cruce.to_excel(ruta, index=False)
                messagebox.showinfo("Cruce exportado", f"Se guardó en:\n{ruta}")
            except Exception as exc:
                messagebox.showerror("No se pudo exportar", str(exc))

    def _exportar_poligonos(self) -> None:
        capa = self._capa_completa()
        if capa.empty:
            messagebox.showwarning("Sin polígonos", "Cargue o dibuje al menos un polígono.")
            return
        ruta = filedialog.asksaveasfilename(title="Exportar polígonos", defaultextension=".gpkg", initialfile="Poligonos_Generados.gpkg", filetypes=[("GeoPackage", "*.gpkg"), ("Excel WKT", "*.xlsx")])
        if ruta:
            try:
                exportar_poligonos(capa, ruta)
                messagebox.showinfo("Polígonos exportados", f"Se guardaron en:\n{ruta}")
            except Exception as exc:
                messagebox.showerror("No se pudo exportar", str(exc))


class FrameCrucePoligonos(ctk.CTkFrame):
    """Contenedor de cruce/dibujo y del generador de mallas métricas."""

    def __init__(self, master, rutas: dict, config: dict) -> None:
        super().__init__(master, fg_color=FONDO)
        nombres = ["Cruce y dibujo", "Generador de mallas"]
        self.tabs = ctk.CTkSegmentedButton(
            self,
            values=nombres,
            command=self._cambiar_tab,
            selected_color=AZUL,
            selected_hover_color=AZUL_CLARO,
            unselected_color="white",
            unselected_hover_color="#E9F4FC",
            text_color=AZUL_OSCURO,
        )
        self.tabs.pack(anchor="w", padx=28, pady=(18, 6))
        self.contenedor = ctk.CTkFrame(self, fg_color="transparent")
        self.contenedor.pack(fill="both", expand=True)
        self.tab_cruce = FrameCrucePoligonosDibujo(self.contenedor, rutas, config)
        self.tab_malla = FrameGeneradorMallas(self.contenedor, rutas, config)
        self.tabs.set(nombres[0])
        self._cambiar_tab(nombres[0])

    def _cambiar_tab(self, nombre: str) -> None:
        for frame in (self.tab_cruce, self.tab_malla):
            frame.pack_forget()
        destino = {
            "Cruce y dibujo": self.tab_cruce,
            "Generador de mallas": self.tab_malla,
        }[nombre]
        destino.pack(fill="both", expand=True)
