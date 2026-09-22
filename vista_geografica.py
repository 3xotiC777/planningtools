# -*- coding: utf-8 -*-
"""Editor geográfico interactivo para revisar la elegibilidad de los PDV."""

from __future__ import annotations

import colorsys
import math
import tkinter as tk
from pathlib import Path
from typing import Callable

import customtkinter as ctk
import pandas as pd
from PIL import Image

from mapa_base import MapaBaseOSM, ajustar_aspecto_mercator, desplazar_mercator, proyectar_mercator, zoom_mercator
from normalizacion import normalizar_llave
from seleccion import normalizar_coordenadas


_AZUL = "#0D5CAB"
_AZUL_CLARO = "#33BDEE"
_AZUL_OSCURO = "#1C293A"
_ROJO = "#B3362B"
_VERDE = "#1E7D46"
_BORDE = "#D5D8DC"


class MapaSeleccionGeografica(ctk.CTkFrame):
    """Mapa de puntos elegibles con selección individual y por rectángulo."""

    ESTADOS_SUGERIDOS = [
        "NO ELEGIBLE EG",
        "NO ELEGIBLE INC",
        "NO ELEGIBLE REP",
        "NO ELEGIBLE FP",
    ]

    def __init__(
        self,
        master,
        on_toggle_pantalla: Callable[[], None] | None = None,
        on_cambio: Callable[[int], None] | None = None,
        icono_pantalla: str | Path | None = None,
    ) -> None:
        super().__init__(
            master, fg_color="#FFFFFF", corner_radius=10,
            border_width=1, border_color=_BORDE,
        )
        self._on_toggle_pantalla = on_toggle_pantalla
        self._on_cambio = on_cambio
        self._datos = pd.DataFrame()
        self._col_lat = ""
        self._col_lon = ""
        self._limites = {"lat": [-60.0, 35.0], "lon": [-120.0, -30.0]}
        self._puntos: list[tuple[int, float, float, str]] = []
        self._coords_pantalla: dict[int, tuple[float, float]] = {}
        self._seleccionados: set[int] = set()
        self._colores: dict[str, str] = {}
        self._contornos_pais: list[list[tuple[float, float]]] = []
        self._contornos_muestra: list[list[tuple[float, float]]] = []
        self._ocultas_por_columna: dict[str, set[str]] = {}
        self._vista_total: tuple[float, float, float, float] | None = None
        self._vista_actual: tuple[float, float, float, float] | None = None
        self._vista_render: tuple[float, float, float, float] | None = None
        self._inicio_arrastre: tuple[float, float] | None = None
        self._rectangulo_arrastre: int | None = None
        self._inicio_pan: tuple[float, float] | None = None
        self._vista_inicio_pan: tuple[float, float, float, float] | None = None
        self._cambios = 0
        self._pantalla_completa = False
        self._modo_interaccion = "seleccionar"
        self._radio_punto = 5.0

        encabezado = ctk.CTkFrame(self, fg_color="transparent")
        encabezado.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(
            encabezado, text="Revisión geográfica de elegibles",
            font=ctk.CTkFont(size=17, weight="bold"), text_color=_AZUL_OSCURO,
        ).pack(side="left")

        self._imagen_pantalla = None
        if icono_pantalla and Path(icono_pantalla).exists():
            try:
                imagen = Image.open(icono_pantalla).convert("RGBA")
                self._imagen_pantalla = ctk.CTkImage(
                    light_image=imagen, dark_image=imagen, size=(20, 20),
                )
            except OSError:
                self._imagen_pantalla = None

        self.btn_pantalla = ctk.CTkButton(
            encabezado,
            text="" if self._imagen_pantalla else "⛶",
            image=self._imagen_pantalla,
            width=38,
            height=32,
            fg_color="#E9F2FA",
            hover_color="#D7E8F6",
            text_color=_AZUL,
            command=self._alternar_pantalla,
        )
        self.btn_pantalla.pack(side="right")

        controles = ctk.CTkFrame(self, fg_color="#F5F8FB", corner_radius=8)
        controles.pack(fill="x", padx=16, pady=(0, 8))

        fila_columna = ctk.CTkFrame(controles, fg_color="transparent")
        fila_columna.pack(fill="x", padx=12, pady=(9, 4))
        ctk.CTkLabel(
            fila_columna, text="Categorizar por", width=112, anchor="w",
            text_color="#34414E",
        ).pack(side="left")
        self.cmb_columna = ctk.CTkComboBox(
            fila_columna, values=[""], command=self._cambiar_columna,
            height=30, border_color="#B9C4CE", fg_color="#FFFFFF",
        )
        self.cmb_columna.pack(side="left", fill="x", expand=True, padx=(0, 12))
        ctk.CTkButton(
            fila_columna, text="−", width=32, height=30,
            fg_color="transparent", border_width=1, border_color="#B9C4CE",
            text_color=_AZUL, command=lambda: self._aplicar_zoom(1.35),
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            fila_columna, text="+", width=32, height=30,
            fg_color="transparent", border_width=1, border_color="#B9C4CE",
            text_color=_AZUL, command=lambda: self._aplicar_zoom(0.72),
        ).pack(side="left", padx=(0, 4))
        ctk.CTkButton(
            fila_columna, text="Restablecer", width=82, height=30,
            fg_color="transparent", border_width=1, border_color="#B9C4CE",
            text_color=_AZUL, command=self._restablecer_zoom,
        ).pack(side="left", padx=(0, 6))
        self.lbl_visibles = ctk.CTkLabel(
            fila_columna, text="0 elegibles", width=132, anchor="e",
            text_color=_AZUL,
        )
        self.lbl_visibles.pack(side="right")

        fila_mapa = ctk.CTkFrame(controles, fg_color="transparent")
        fila_mapa.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(
            fila_mapa, text="Herramienta", width=112, anchor="w",
            text_color="#34414E",
        ).pack(side="left")
        self.selector_modo = ctk.CTkSegmentedButton(
            fila_mapa,
            values=["✋  Mover mapa", "▭  Seleccionar área"],
            command=self._cambiar_modo,
            height=30,
            selected_color=_AZUL,
            selected_hover_color=_AZUL_OSCURO,
            unselected_color="#FFFFFF",
            unselected_hover_color="#E5EEF6",
            text_color="#243444",
        )
        self.selector_modo.set("▭  Seleccionar área")
        self.selector_modo.pack(side="left", padx=(0, 18))
        ctk.CTkLabel(
            fila_mapa, text="Tamaño de puntos", anchor="w",
            text_color="#34414E",
        ).pack(side="left", padx=(0, 8))
        self.sld_tamano_puntos = ctk.CTkSlider(
            fila_mapa, from_=3, to=9, number_of_steps=6, width=120,
            command=self._cambiar_tamano_puntos,
        )
        self.sld_tamano_puntos.set(self._radio_punto)
        self.sld_tamano_puntos.pack(side="left", padx=(0, 8))
        self.lbl_tamano_puntos = ctk.CTkLabel(
            fila_mapa, text="5 px", width=40, anchor="e", text_color=_AZUL,
        )
        self.lbl_tamano_puntos.pack(side="left")

        fila_estado = ctk.CTkFrame(controles, fg_color="transparent")
        fila_estado.pack(fill="x", padx=12, pady=(4, 9))
        ctk.CTkLabel(
            fila_estado, text="Asignar estado", width=112, anchor="w",
            text_color="#34414E",
        ).pack(side="left")
        self.cmb_estado = ctk.CTkComboBox(
            fila_estado, values=self.ESTADOS_SUGERIDOS,
            height=30, border_color="#B9C4CE", fg_color="#FFFFFF",
        )
        self.cmb_estado.set("NO ELEGIBLE EG")
        self.cmb_estado.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.btn_aplicar = ctk.CTkButton(
            fila_estado, text="Aplicar a seleccionados", height=30,
            width=176, fg_color=_ROJO, hover_color="#8F2C24",
            command=self._aplicar_estado,
        )
        self.btn_aplicar.pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            fila_estado, text="Limpiar selección", height=30, width=132,
            fg_color="transparent", border_width=1, border_color=_AZUL,
            text_color=_AZUL, command=self._limpiar_seleccion,
        ).pack(side="left")

        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        cuerpo.grid_columnconfigure(0, weight=5)
        cuerpo.grid_columnconfigure(1, weight=1)
        cuerpo.grid_rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            cuerpo, height=390, bg="#F8FAFC", highlightthickness=1,
            highlightbackground="#DDE3E9", cursor="crosshair",
        )
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.canvas.bind("<Configure>", self._redibujar)
        self.canvas.bind("<ButtonPress-1>", self._iniciar_accion)
        self.canvas.bind("<B1-Motion>", self._mover_accion)
        self.canvas.bind("<ButtonRelease-1>", self._terminar_accion)
        self.canvas.bind("<MouseWheel>", self._zoom_rueda)
        self.canvas.bind("<Button-4>", self._zoom_rueda)
        self.canvas.bind("<Button-5>", self._zoom_rueda)
        self.canvas.bind("<ButtonPress-3>", self._iniciar_pan)
        self.canvas.bind("<B3-Motion>", self._mover_pan)
        self.canvas.bind("<ButtonRelease-3>", self._terminar_pan)
        self._mapa_base = MapaBaseOSM(self.canvas, self._redibujar)

        lateral = ctk.CTkFrame(
            cuerpo, fg_color="#F8FAFC", corner_radius=8,
            border_width=1, border_color="#DDE3E9",
        )
        lateral.grid(row=0, column=1, sticky="nsew")
        ctk.CTkLabel(
            lateral, text="Leyenda", anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"), text_color=_AZUL_OSCURO,
        ).pack(fill="x", padx=10, pady=(10, 4))
        self.leyenda = ctk.CTkScrollableFrame(
            lateral, fg_color="transparent", width=210,
            scrollbar_button_color="#AAB5C0",
            scrollbar_button_hover_color="#8795A3",
        )
        self.leyenda.pack(fill="both", expand=True, padx=2, pady=(0, 6))

        self.lbl_estado = ctk.CTkLabel(
            self,
            text="Modo Seleccionar área · Clic o arrastre para seleccionar · Rueda: zoom.",
            anchor="w", text_color="#5D6874", font=ctk.CTkFont(size=12),
        )
        self.lbl_estado.pack(fill="x", padx=16, pady=(0, 10))
        self._mostrar_mensaje("La vista aparecerá al preparar el universo")

    @property
    def cambios(self) -> int:
        return self._cambios

    def cargar(
        self,
        datos: pd.DataFrame,
        columna_lat: str,
        columna_lon: str,
        limites: dict | None = None,
        categoria_inicial: str | None = None,
        contornos_pais: list[list[tuple[float, float]]] | None = None,
        contornos_muestra: list[list[tuple[float, float]]] | None = None,
    ) -> None:
        self._datos = datos.copy().reset_index(drop=True)
        self._col_lat = columna_lat
        self._col_lon = columna_lon
        self._limites = limites or self._limites
        self._contornos_pais = contornos_pais or []
        self._contornos_muestra = contornos_muestra or []
        self._seleccionados.clear()
        self._ocultas_por_columna.clear()
        self._vista_total = None
        self._vista_actual = None
        self._cambios = 0
        if "ELEGIBLE" not in self._datos.columns:
            self._datos["ELEGIBLE"] = "ELEGIBLE"

        columnas = [
            str(c) for c in self._datos.columns
            if not str(c).startswith("__MAP_")
        ]
        self.cmb_columna.configure(values=columnas or ["ELEGIBLE"])
        elegida = categoria_inicial if categoria_inicial in columnas else (
            "ELEGIBLE" if "ELEGIBLE" in columnas else columnas[0]
        )
        self.cmb_columna.set(elegida)
        self._reconstruir_puntos()

    def obtener_datos(self) -> pd.DataFrame:
        return self._datos.drop(
            columns=[c for c in self._datos.columns if str(c).startswith("__MAP_")],
            errors="ignore",
        ).copy()

    def establecer_pantalla_completa(self, activa: bool) -> None:
        self._pantalla_completa = activa
        ayuda = "Volver al tamaño normal" if activa else "Ampliar mapa"
        self.btn_pantalla.configure(hover_color="#D7E8F6")
        self.btn_pantalla._tooltip_text = ayuda
        self.after_idle(self._redibujar)

    def _alternar_pantalla(self) -> None:
        if self._on_toggle_pantalla:
            self._on_toggle_pantalla()

    def _es_elegible(self) -> pd.Series:
        return normalizar_llave(self._datos["ELEGIBLE"]) == "ELEGIBLE"

    def _reconstruir_puntos(self) -> None:
        if self._datos.empty:
            self._puntos = []
            self._mostrar_mensaje("No hay datos para mostrar")
            return
        if self._col_lat not in self._datos.columns or self._col_lon not in self._datos.columns:
            self._puntos = []
            self._mostrar_mensaje("Revise las columnas de Latitud y Longitud")
            return

        lat, lon = normalizar_coordenadas(
            self._datos[self._col_lat], self._datos[self._col_lon], self._limites,
        )
        self._datos["__MAP_LAT"] = lat
        self._datos["__MAP_LON"] = lon
        mascara = self._es_elegible() & lat.notna() & lon.notna()
        visibles = self._datos.loc[mascara]
        columna = self.cmb_columna.get()
        if columna not in visibles.columns:
            columna = "ELEGIBLE"
        categorias = visibles[columna].map(
            lambda v: "(Sin valor)" if pd.isna(v) or not str(v).strip() else str(v).strip()
        )
        self._puntos = [
            (int(idx), float(row["__MAP_LON"]), float(row["__MAP_LAT"]), str(categoria))
            for (idx, row), categoria in zip(visibles.iterrows(), categorias)
        ]
        valores = sorted({p[3] for p in self._puntos}, key=lambda x: x.casefold())
        self._colores = {valor: self._color_categoria(i) for i, valor in enumerate(valores)}
        ocultas = self._categorias_ocultas()
        ocultas.intersection_update(valores)
        self._actualizar_limites_vista()
        self._actualizar_contador_visibles()
        self._actualizar_leyenda(valores)
        self._actualizar_estado()
        self.after_idle(self._redibujar)

    @staticmethod
    def _color_categoria(indice: int) -> str:
        tono = (0.60 + indice * 0.61803398875) % 1.0
        r, g, b = colorsys.hsv_to_rgb(tono, 0.68, 0.82)
        return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"

    def _actualizar_leyenda(self, valores: list[str]) -> None:
        for widget in self.leyenda.winfo_children():
            widget.destroy()
        if not valores:
            ctk.CTkLabel(
                self.leyenda, text="Sin categorías", text_color="#69727D",
            ).pack(anchor="w", padx=6, pady=4)
            return
        for valor in valores:
            oculta = valor in self._categorias_ocultas()
            fila = ctk.CTkFrame(self.leyenda, fg_color="transparent")
            fila.pack(fill="x", padx=4, pady=2)
            ctk.CTkLabel(
                fila, text="", width=12, height=12, corner_radius=6,
                fg_color="#B7C0C9" if oculta else self._colores[valor],
            ).pack(side="left", padx=(0, 6))
            ctk.CTkLabel(
                fila, text=valor, anchor="w",
                text_color="#89939D" if oculta else "#3C4650",
                font=ctk.CTkFont(size=11), wraplength=135,
            ).pack(side="left", fill="x", expand=True)
            ctk.CTkButton(
                fila, text="Mostrar" if oculta else "Ocultar",
                width=54, height=24, corner_radius=5,
                fg_color="transparent", border_width=1,
                border_color="#AEB8C2", text_color=_AZUL,
                font=ctk.CTkFont(size=10),
                command=lambda categoria=valor: self._alternar_categoria(categoria),
            ).pack(side="right", padx=(4, 0))

    def _categorias_ocultas(self) -> set[str]:
        columna = self.cmb_columna.get()
        return self._ocultas_por_columna.setdefault(columna, set())

    def _alternar_categoria(self, categoria: str) -> None:
        ocultas = self._categorias_ocultas()
        if categoria in ocultas:
            ocultas.remove(categoria)
        else:
            ocultas.add(categoria)
            indices_ocultos = {p[0] for p in self._puntos if p[3] == categoria}
            self._seleccionados.difference_update(indices_ocultos)
        self._actualizar_leyenda(list(self._colores))
        self._actualizar_contador_visibles()
        self._actualizar_estado()
        self._redibujar()

    def _actualizar_contador_visibles(self) -> None:
        ocultas = self._categorias_ocultas()
        cantidad = sum(1 for punto in self._puntos if punto[3] not in ocultas)
        if cantidad == len(self._puntos):
            texto = f"{cantidad:,} elegibles"
        else:
            texto = f"{cantidad:,} de {len(self._puntos):,} visibles"
        self.lbl_visibles.configure(text=texto)

    def _cambiar_columna(self, _valor=None) -> None:
        self._seleccionados.clear()
        self._reconstruir_puntos()

    def _mostrar_mensaje(self, texto: str) -> None:
        self.canvas.delete("all")
        ancho = max(self.canvas.winfo_width(), 360)
        alto = max(self.canvas.winfo_height(), 260)
        self.canvas.create_text(
            ancho / 2, alto / 2, text=texto, fill="#7A8490",
            font=("Segoe UI", 11),
        )

    def _actualizar_limites_vista(self) -> None:
        if not self._puntos:
            self._vista_total = None
            self._vista_actual = None
            return
        xs = [p[1] for p in self._puntos]
        ys = [p[2] for p in self._puntos]
        for contorno in self._contornos_pais + self._contornos_muestra:
            xs.extend(p[0] for p in contorno)
            ys.extend(p[1] for p in contorno)
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        if min_x == max_x:
            min_x, max_x = min_x - 0.5, max_x + 0.5
        if min_y == max_y:
            min_y, max_y = min_y - 0.5, max_y + 0.5
        pad_x = (max_x - min_x) * 0.04
        pad_y = (max_y - min_y) * 0.04
        total = (min_x - pad_x, max_x + pad_x, min_y - pad_y, max_y + pad_y)
        self._vista_total = total
        if self._vista_actual is None:
            self._vista_actual = total
        else:
            self._vista_actual = self._limitar_vista(self._vista_actual)

    def _limitar_vista(
        self, vista: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        if self._vista_total is None:
            return vista

        def limitar_intervalo(inicio, fin, total_inicio, total_fin):
            total_span = total_fin - total_inicio
            span = min(fin - inicio, total_span)
            if span >= total_span:
                return total_inicio, total_fin
            if inicio < total_inicio:
                inicio = total_inicio
                fin = inicio + span
            if fin > total_fin:
                fin = total_fin
                inicio = fin - span
            return inicio, fin

        min_x, max_x, min_y, max_y = vista
        tmin_x, tmax_x, tmin_y, tmax_y = self._vista_total
        min_x, max_x = limitar_intervalo(min_x, max_x, tmin_x, tmax_x)
        min_y, max_y = limitar_intervalo(min_y, max_y, tmin_y, tmax_y)
        return min_x, max_x, min_y, max_y

    def _aplicar_zoom(self, factor: float, x: float | None = None, y: float | None = None) -> None:
        if self._vista_actual is None or self._vista_total is None:
            return
        ancho = max(self.canvas.winfo_width(), 420)
        alto = max(self.canvas.winfo_height(), 300)
        margen = 28
        x = ancho / 2 if x is None else x
        y = alto / 2 if y is None else y
        proporcion_x = min(1.0, max(0.0, (x - margen) / max(1, ancho - 2 * margen)))
        proporcion_y = min(1.0, max(0.0, (y - margen) / max(1, alto - 2 * margen)))
        vista = zoom_mercator(self._vista_render or self._vista_actual, factor, proporcion_x, proporcion_y)
        self._vista_actual = self._limitar_vista(vista)
        self._actualizar_estado()
        self._redibujar()

    def _zoom_rueda(self, evento):
        acercar = getattr(evento, "delta", 0) > 0 or getattr(evento, "num", 0) == 4
        self._aplicar_zoom(0.78 if acercar else 1.28, evento.x, evento.y)
        return "break"

    def _restablecer_zoom(self) -> None:
        if self._vista_total is not None:
            self._vista_actual = self._vista_total
            self._actualizar_estado()
            self._redibujar()

    def _cambiar_modo(self, valor: str) -> None:
        self._modo_interaccion = "mover" if valor.startswith("✋") else "seleccionar"
        if self._rectangulo_arrastre is not None:
            self.canvas.delete(self._rectangulo_arrastre)
        self._inicio_arrastre = None
        self._rectangulo_arrastre = None
        self._inicio_pan = None
        self._vista_inicio_pan = None
        self._actualizar_cursor_modo()
        self._actualizar_estado()

    def _cambiar_tamano_puntos(self, valor: float) -> None:
        self._radio_punto = float(valor)
        self.lbl_tamano_puntos.configure(text=f"{round(self._radio_punto)} px")
        self._redibujar()

    def _actualizar_cursor_modo(self) -> None:
        self.canvas.configure(
            cursor="fleur" if self._modo_interaccion == "mover" else "crosshair",
        )

    def _iniciar_accion(self, evento) -> None:
        if self._modo_interaccion == "mover":
            self._iniciar_pan(evento)
        else:
            self._iniciar_seleccion(evento)

    def _mover_accion(self, evento) -> None:
        if self._modo_interaccion == "mover":
            self._mover_pan(evento)
        else:
            self._mover_seleccion(evento)

    def _terminar_accion(self, evento) -> None:
        if self._modo_interaccion == "mover":
            self._terminar_pan(evento)
        else:
            self._terminar_seleccion(evento)

    def _iniciar_pan(self, evento) -> None:
        if self._vista_actual is None:
            return
        self._inicio_pan = (evento.x, evento.y)
        self._vista_inicio_pan = self._vista_render or self._vista_actual
        self.canvas.configure(cursor="fleur")

    def _mover_pan(self, evento) -> None:
        if self._inicio_pan is None or self._vista_inicio_pan is None:
            return
        ancho = max(self.canvas.winfo_width(), 420)
        alto = max(self.canvas.winfo_height(), 300)
        margen = 28
        x0, y0 = self._inicio_pan
        dx = (evento.x - x0) / max(1, ancho - 2 * margen)
        dy = (evento.y - y0) / max(1, alto - 2 * margen)
        self._vista_actual = self._limitar_vista(desplazar_mercator(self._vista_inicio_pan, dx, dy))
        self._redibujar()

    def _terminar_pan(self, _evento=None) -> None:
        self._inicio_pan = None
        self._vista_inicio_pan = None
        self._actualizar_cursor_modo()
        self._actualizar_estado()

    def _redibujar(self, _evento=None) -> None:
        self._coords_pantalla.clear()
        if not self._puntos:
            self._mostrar_mensaje("No hay puntos elegibles con GPS válido")
            return
        self.canvas.delete("all")
        puntos_visibles = [
            punto for punto in self._puntos
            if punto[3] not in self._categorias_ocultas()
        ]
        if not puntos_visibles:
            self._mostrar_mensaje("Todas las categorías están ocultas")
            return
        ancho = max(self.canvas.winfo_width(), 420)
        alto = max(self.canvas.winfo_height(), 300)
        margen = 28
        if self._vista_actual is None:
            self._actualizar_limites_vista()
        if self._vista_actual is None:
            return
        area = (margen, margen, ancho - margen, alto - margen)
        self._vista_render = ajustar_aspecto_mercator(self._vista_actual, area)

        def proyectar(lon: float, lat: float) -> tuple[float, float]:
            return proyectar_mercator(lon, lat, self._vista_render, area)

        self._mapa_base.dibujar(self._vista_render, area)

        # El límite nacional funciona como base política y permite reconocer el país.
        for contorno in self._contornos_pais:
            coords = [valor for punto in contorno for valor in proyectar(*punto)]
            if len(coords) >= 6:
                self.canvas.create_polygon(
                    *coords, fill="", outline="#52697D", width=2,
                )
        for contorno in self._contornos_muestra:
            coords = [valor for punto in contorno for valor in proyectar(*punto)]
            if len(coords) >= 4:
                self.canvas.create_line(
                    *coords, fill=_AZUL_CLARO, width=1.2, dash=(4, 3),
                )

        for indice, lon, lat, categoria in puntos_visibles:
            x, y = proyectar(lon, lat)
            if not (margen - 6 <= x <= ancho - margen + 6 and margen - 6 <= y <= alto - margen + 6):
                continue
            self._coords_pantalla[indice] = (x, y)
            seleccionado = indice in self._seleccionados
            radio = self._radio_punto + 2.0 if seleccionado else self._radio_punto
            self.canvas.create_oval(
                x - radio, y - radio, x + radio, y + radio,
                fill=self._colores[categoria],
                outline="#17202A" if seleccionado else "",
                width=2 if seleccionado else 0,
            )
        self._mapa_base.dibujar_atribucion(area)

    def _iniciar_seleccion(self, evento) -> None:
        self._inicio_arrastre = (self.canvas.canvasx(evento.x), self.canvas.canvasy(evento.y))
        x, y = self._inicio_arrastre
        self._rectangulo_arrastre = self.canvas.create_rectangle(
            x, y, x, y, outline=_AZUL, width=1, dash=(4, 3), fill="#DCECF8",
            stipple="gray25",
        )

    def _mover_seleccion(self, evento) -> None:
        if self._inicio_arrastre is None or self._rectangulo_arrastre is None:
            return
        x0, y0 = self._inicio_arrastre
        self.canvas.coords(
            self._rectangulo_arrastre, x0, y0,
            self.canvas.canvasx(evento.x), self.canvas.canvasy(evento.y),
        )

    def _terminar_seleccion(self, evento) -> None:
        if self._inicio_arrastre is None:
            return
        x0, y0 = self._inicio_arrastre
        x1, y1 = self.canvas.canvasx(evento.x), self.canvas.canvasy(evento.y)
        if self._rectangulo_arrastre is not None:
            self.canvas.delete(self._rectangulo_arrastre)
        self._inicio_arrastre = None
        self._rectangulo_arrastre = None
        acumular = bool(evento.state & 0x0004) or bool(evento.state & 0x0001)
        if not acumular:
            self._seleccionados.clear()

        if abs(x1 - x0) <= 5 and abs(y1 - y0) <= 5:
            if self._coords_pantalla:
                indice, distancia = min(
                    (
                        (idx, math.hypot(px - x1, py - y1))
                        for idx, (px, py) in self._coords_pantalla.items()
                    ),
                    key=lambda item: item[1],
                )
                if distancia <= 12:
                    if acumular and indice in self._seleccionados:
                        self._seleccionados.remove(indice)
                    else:
                        self._seleccionados.add(indice)
        else:
            izquierda, derecha = sorted((x0, x1))
            arriba, abajo = sorted((y0, y1))
            self._seleccionados.update(
                idx for idx, (px, py) in self._coords_pantalla.items()
                if izquierda <= px <= derecha and arriba <= py <= abajo
            )
        self._actualizar_estado()
        self._redibujar()

    def _limpiar_seleccion(self) -> None:
        self._seleccionados.clear()
        self._actualizar_estado()
        self._redibujar()

    def _aplicar_estado(self) -> None:
        estado = self.cmb_estado.get().strip()
        if not self._seleccionados:
            self.lbl_estado.configure(
                text="Seleccione al menos un punto antes de aplicar un estado.",
                text_color=_ROJO,
            )
            return
        if not estado:
            self.lbl_estado.configure(text="Escriba el estado que desea asignar.", text_color=_ROJO)
            return
        indices = sorted(self._seleccionados)
        self._datos.loc[indices, "ELEGIBLE"] = estado
        self._cambios += len(indices)
        self._seleccionados.clear()
        if self._on_cambio:
            self._on_cambio(len(indices))
        self._reconstruir_puntos()

    def _actualizar_estado(self) -> None:
        nivel_zoom = 1.0
        if self._vista_total is not None and self._vista_actual is not None:
            ancho_total = self._vista_total[1] - self._vista_total[0]
            ancho_actual = self._vista_actual[1] - self._vista_actual[0]
            if ancho_actual > 0:
                nivel_zoom = ancho_total / ancho_actual
        modo = "Mover mapa" if self._modo_interaccion == "mover" else "Seleccionar área"
        self.lbl_estado.configure(
            text=(
                f"{len(self._seleccionados):,} seleccionados · "
                f"{self._cambios:,} cambios en esta revisión · "
                f"Zoom {nivel_zoom:.1f}× · Modo: {modo} · Rueda: zoom."
            ),
            text_color="#5D6874",
        )
