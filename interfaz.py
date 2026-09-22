# -*- coding: utf-8 -*-
"""
interfaz.py — Interfaz gráfica de Planning Tools (CustomTkinter).

Responsabilidad única: presentación e interacción. El procesamiento corre en
un hilo secundario (threading) para no congelar la ventana; la comunicación
hilo -> UI se hace con una cola y ctk.after (patrón seguro en Tkinter).

Arquitectura escalable: la barra lateral se construye desde REGISTRO_MODULOS,
por lo que agregar un módulo futuro (Selección de muestra, Rutas, etc.) solo
requiere registrar su nombre y su frame, sin tocar el resto de la app.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import pandas as pd
from PIL import Image

from depuracion import DepuradorUniverso
from lector_excel import (
    ErrorLectura,
    leer_columnas,
    leer_valores_columna,
    listar_archivos_excel,
    listar_hojas,
)
from logs import obtener_logger
from mapa_base import MapaBaseOSM, ajustar_aspecto_mercator, cerrar_descargas_mapas, proyectar_mercator
from matriz_distancias import FrameMatrizDistancias
from modulos_geograficos import FrameCrucePoligonos, FrameOptimizacionRutas
from reportes import (
    generar_salidas,
    generar_salidas_muestra,
    guardar_revision_geografica,
)
from seleccion import ErrorSeleccion, SelectorMuestra
from utilidades import (
    NOMBRE_APP,
    VERSION,
    abrir_carpeta,
    buscar_archivo_universo_seleccion,
    cargar_config,
    formatear_duracion,
    guardar_config,
    ruta_recurso,
)
from validaciones import ErrorValidacion
from vista_geografica import MapaSeleccionGeografica

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

_AZUL = "#0D5CAB"
_AZUL_CLARO = "#33BDEE"
_AZUL_OSCURO = "#1C293A"
_VERDE = "#1E7D46"
_ROJO = "#B3362B"
_FONDO = "#F2F2F2"
_BORDE = "#D5D8DC"


class MapaPuntos(ctk.CTkFrame):
    """Mapa vectorial liviano para inspeccionar la distribución de puntos."""

    COLORES = {
        "ELEGIBLE": _AZUL,
        "REP": "#F59E0B",
        "INC": "#D14343",
        "FUERA": "#7C6FB0",
    }

    def __init__(self, master) -> None:
        super().__init__(master, fg_color="#FFFFFF", corner_radius=10,
                         border_width=1, border_color=_BORDE)
        self._puntos: list[tuple[float, float, str]] = []
        self._contornos_pais: list[list[tuple[float, float]]] = []
        self._contornos_muestra: list[list[tuple[float, float]]] = []

        ctk.CTkLabel(
            self, text="Vista geográfica de puntos",
            font=ctk.CTkFont(size=15, weight="bold"), text_color=_AZUL_OSCURO,
        ).pack(anchor="w", padx=16, pady=(12, 0))
        self.lbl_info = ctk.CTkLabel(
            self, text="Ejecute la depuración para visualizar el universo.",
            font=ctk.CTkFont(size=12), text_color="#69727D", anchor="w",
        )
        self.lbl_info.pack(fill="x", padx=16, pady=(2, 8))
        self.canvas = tk.Canvas(
            self, height=300, bg="#F8FAFC", highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.canvas.bind("<Configure>", self._redibujar)
        self._mapa_base = MapaBaseOSM(self.canvas, self._redibujar)
        self._mostrar_mensaje("Sin datos para mostrar")

    def actualizar(
        self,
        datos,
        columna_lat: str,
        columna_lon: str,
        contornos_pais=None,
        contornos_muestra=None,
    ) -> None:
        self._contornos_pais = contornos_pais or []
        self._contornos_muestra = contornos_muestra or []
        if datos is None or datos.empty or columna_lat not in datos.columns or columna_lon not in datos.columns:
            self._puntos = []
            self.lbl_info.configure(text="No se encontraron columnas de coordenadas válidas.")
            self._mostrar_mensaje("Revise el mapeo de Latitud y Longitud")
            return

        lat = pd.to_numeric(datos[columna_lat], errors="coerce")
        lon = pd.to_numeric(datos[columna_lon], errors="coerce")
        mascara = lat.between(-90, 90) & lon.between(-180, 180) & lat.notna() & lon.notna()
        validos = datos.loc[mascara]
        if validos.empty:
            self._puntos = []
            self.lbl_info.configure(text="No hay coordenadas geográficas utilizables.")
            self._mostrar_mensaje("No hay puntos válidos para dibujar")
            return

        max_puntos = 6000
        paso = max(1, len(validos) // max_puntos)
        muestra = validos.iloc[::paso].head(max_puntos)
        estados = muestra["ELEGIBLE"].fillna("ELEGIBLE").astype(str) if "ELEGIBLE" in muestra.columns else pd.Series("ELEGIBLE", index=muestra.index)
        self._puntos = list(zip(
            pd.to_numeric(muestra[columna_lon], errors="coerce"),
            pd.to_numeric(muestra[columna_lat], errors="coerce"),
            estados,
        ))
        texto_muestra = "" if len(muestra) == len(validos) else f" · vista optimizada: {len(muestra):,}"
        self.lbl_info.configure(text=f"{len(validos):,} puntos con coordenadas válidas{texto_muestra}")
        self.after_idle(self._redibujar)

    def _categoria(self, estado: str) -> str:
        valor = str(estado).upper()
        if "REP" in valor:
            return "REP"
        if "INC" in valor:
            return "INC"
        if "NO ELEGIBLE EG" in valor or "NO ELEGIBLE FP" in valor:
            return "FUERA"
        return "ELEGIBLE"

    def _mostrar_mensaje(self, texto: str) -> None:
        self.canvas.delete("all")
        ancho = max(self.canvas.winfo_width(), 300)
        alto = max(self.canvas.winfo_height(), 220)
        self.canvas.create_text(
            ancho / 2, alto / 2, text=texto, fill="#7A8490",
            font=("Segoe UI", 11),
        )

    def _redibujar(self, _evento=None) -> None:
        if not self._puntos:
            return
        self.canvas.delete("all")
        ancho = max(self.canvas.winfo_width(), 360)
        alto = max(self.canvas.winfo_height(), 260)
        margen_x, margen_sup, margen_inf = 36, 44, 28

        xs = [p[0] for p in self._puntos]
        ys = [p[1] for p in self._puntos]
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
        min_x, max_x = min_x - pad_x, max_x + pad_x
        min_y, max_y = min_y - pad_y, max_y + pad_y
        area = (margen_x, margen_sup, ancho - margen_x, alto - margen_inf)
        limites = ajustar_aspecto_mercator((min_x, max_x, min_y, max_y), area)

        def proyectar(lon: float, lat: float) -> tuple[float, float]:
            return proyectar_mercator(lon, lat, limites, area)

        self._mapa_base.dibujar(limites, area)

        for contorno in self._contornos_pais:
            coords = [v for punto in contorno for v in proyectar(*punto)]
            if len(coords) >= 4:
                self.canvas.create_line(*coords, fill="#8793A1", width=1.2)
        for contorno in self._contornos_muestra:
            coords = [v for punto in contorno for v in proyectar(*punto)]
            if len(coords) >= 4:
                self.canvas.create_line(*coords, fill=_AZUL_CLARO, width=1.5, dash=(4, 3))

        for lon, lat, estado in self._puntos:
            x, y = proyectar(float(lon), float(lat))
            color = self.COLORES[self._categoria(estado)]
            self.canvas.create_oval(x - 1.8, y - 1.8, x + 1.8, y + 1.8,
                                    fill=color, outline="")
        self._mapa_base.dibujar_atribucion(area)

        leyenda = [
            ("Elegible", self.COLORES["ELEGIBLE"]),
            ("REP", self.COLORES["REP"]),
            ("Incidencia", self.COLORES["INC"]),
            ("Fuera área", self.COLORES["FUERA"]),
        ]
        x_leyenda = margen_x
        for etiqueta, color in leyenda:
            self.canvas.create_oval(x_leyenda, 16, x_leyenda + 8, 24, fill=color, outline="")
            self.canvas.create_text(x_leyenda + 13, 20, text=etiqueta, anchor="w",
                                    fill="#4D5864", font=("Segoe UI", 9))
            x_leyenda += 72 if etiqueta == "REP" else 96


# =============================================================================
# MÓDULO: Depuración de Universo
# =============================================================================
class FrameDepuracion(ctk.CTkFrame):
    """Pantalla del módulo 'Depuración de Universo'."""

    def __init__(self, master, rutas: dict, config: dict) -> None:
        super().__init__(master, fg_color="transparent")
        self.rutas = rutas
        self.config_app = config
        self.cola: queue.Queue = queue.Queue()
        self.t_inicio: float = 0.0
        self.procesando = False
        self._revision_activa = False
        self._mapa_depuracion_ampliado = False
        self._resultado_pendiente = None
        self._construir()
        self.refrescar_archivos()
        
    @property
    def cfg(self):
        pais = self.config_app.get("pais_activo", "Costa Rica")
        return self.config_app["paises"][pais]["modulo_depuracion"]

    def _construir(self) -> None:
        ctk.CTkLabel(
            self, text="Depuración de Universo",
            font=ctk.CTkFont(size=26, weight="bold"), text_color=_AZUL,
        ).pack(anchor="w", padx=30, pady=(25, 4))
        ctk.CTkLabel(
            self, text="Excluye del universo las tiendas con incidencias no "
                       "elegibles antes de la selección de muestra.",
            font=ctk.CTkFont(size=14), text_color="#555555",
        ).pack(anchor="w", padx=30, pady=(0, 15))

        # --- Estado de archivos ---------------------------------------------
        self.marco_archivos = ctk.CTkFrame(self, corner_radius=12)
        self.marco_archivos.pack(fill="x", padx=30, pady=(0, 15))
        ctk.CTkLabel(
            self.marco_archivos, text="Archivos de entrada  (carpeta 'Entrada Depuración')",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(12, 6))
        self.lbl_universo = ctk.CTkLabel(self.marco_archivos, text="", anchor="w",
                                         font=ctk.CTkFont(size=14))
        self.lbl_universo.pack(fill="x", padx=20)
        self.lbl_incidencia = ctk.CTkLabel(self.marco_archivos, text="", anchor="w",
                                           font=ctk.CTkFont(size=14))
        self.lbl_incidencia.pack(fill="x", padx=20, pady=(0, 6))
        ctk.CTkButton(
            self.marco_archivos, text="Actualizar estado", width=140, height=28,
            fg_color="transparent", border_width=1, text_color=_AZUL,
            command=self.refrescar_archivos,
        ).pack(anchor="e", padx=20, pady=(0, 12))

        # --- Ejecución -------------------------------------------------------
        self.marco_run = ctk.CTkFrame(self, corner_radius=12)
        self.marco_run.pack(fill="x", padx=30, pady=(0, 15))
        self.btn_ejecutar = ctk.CTkButton(
            self.marco_run, text="▶  Ejecutar proceso", height=44,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=_AZUL, hover_color=_AZUL_CLARO, command=self.ejecutar,
        )
        self.btn_ejecutar.pack(fill="x", padx=20, pady=(18, 10))

        self.barra = ctk.CTkProgressBar(self.marco_run, height=14, progress_color=_AZUL)
        self.barra.set(0)
        self.barra.pack(fill="x", padx=20, pady=(0, 6))

        fila = ctk.CTkFrame(self.marco_run, fg_color="transparent")
        fila.pack(fill="x", padx=20, pady=(0, 14))
        self.lbl_estado = ctk.CTkLabel(fila, text="Listo para ejecutar.",
                                       font=ctk.CTkFont(size=14), anchor="w")
        self.lbl_estado.pack(side="left")
        self.lbl_tiempo = ctk.CTkLabel(fila, text="Tiempo: 00:00:00",
                                       font=ctk.CTkFont(size=14), anchor="e")
        self.lbl_tiempo.pack(side="right")

        # --- Resumen y mapa --------------------------------------------------
        self.contenedor_resultados = ctk.CTkFrame(self, fg_color="transparent")
        self.contenedor_resultados.pack(fill="both", expand=True, padx=30, pady=(0, 12))
        self.contenedor_resultados.grid_columnconfigure(0, weight=2)
        self.contenedor_resultados.grid_columnconfigure(1, weight=3)
        self.contenedor_resultados.grid_rowconfigure(0, weight=1)

        marco_res = ctk.CTkFrame(
            self.contenedor_resultados, corner_radius=10, fg_color="#FFFFFF",
            border_width=1, border_color=_BORDE,
        )
        marco_res.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ctk.CTkLabel(marco_res, text="Resumen del proceso",
                     font=ctk.CTkFont(size=15, weight="bold"), text_color=_AZUL_OSCURO,
                     ).pack(anchor="w", padx=20, pady=(12, 4))
        self.txt_resumen = ctk.CTkTextbox(marco_res, font=ctk.CTkFont(size=13),
                                          fg_color="#F7F8FA", text_color="#222222")
        self.txt_resumen.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        self.txt_resumen.insert("1.0", "Aún no se ha ejecutado el proceso.")
        self.txt_resumen.configure(state="disabled")

        self.mapa = MapaPuntos(self.contenedor_resultados)
        self.mapa.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self.marco_revision = ctk.CTkFrame(self, fg_color="transparent")
        self.mapa_revision = MapaSeleccionGeografica(
            self.marco_revision,
            on_toggle_pantalla=self._alternar_mapa_depuracion,
            on_cambio=self._registrar_cambio_depuracion,
            icono_pantalla=ruta_recurso("Config/ampliar-pantalla.png"),
        )
        self.mapa_revision.pack(fill="both", expand=True)
        acciones_revision = ctk.CTkFrame(self.marco_revision, fg_color="transparent")
        acciones_revision.pack(fill="x", pady=(8, 0))
        self.lbl_revision = ctk.CTkLabel(
            acciones_revision, text="", anchor="w", text_color="#5D6874",
        )
        self.lbl_revision.pack(side="left", fill="x", expand=True)
        self.btn_guardar_revision = ctk.CTkButton(
            acciones_revision, text="Guardar cambios", width=150, height=38,
            fg_color="transparent", border_width=1, border_color=_AZUL,
            text_color=_AZUL, command=self._guardar_revision_depuracion,
        )
        self.btn_guardar_revision.pack(side="right", padx=(8, 0))
        self.btn_continuar_revision = ctk.CTkButton(
            acciones_revision, text="Continuar y exportar", width=190, height=38,
            fg_color=_VERDE, hover_color="#166237",
            command=self._continuar_depuracion,
        )
        self.btn_continuar_revision.pack(side="right", padx=(8, 0))

        self.btn_salida = ctk.CTkButton(
            self, text="📂  Abrir carpeta Salida", height=38, fg_color=_VERDE,
            hover_color="#166237",
            command=lambda: abrir_carpeta(self.rutas["salida"]),
        )
        self.btn_salida.pack(fill="x", padx=30, pady=(0, 20))

    # ------------------------------------------------------------- acciones --
    def refrescar_archivos(self) -> None:
        """Muestra la configuración actual."""

        self.lbl_universo.configure(
            text=f"📄 Universo: {self.cfg['archivo_universo']}\n" 
                 f"📑 Hoja: {self.cfg['hoja_universo']}", 
        text_color=_AZUL,)

        self.lbl_incidencia.configure(
            text=f"📄 Incidencias: {self.cfg['archivo_incidencias']}\n" 
                 f"📑 Hoja: {self.cfg['hoja_incidencias']}",
        text_color=_AZUL,)

    def cargar_archivos(self) -> None:
        """
        Carga todos los archivos Excel encontrados en la carpeta Entrada
        dentro de los ComboBox.
        """

        archivos = listar_archivos_excel(self.rutas["entrada"])

        if not archivos:
            archivos = ["No se encontraron archivos"]

        self.cmb_universo.configure(values=archivos)
        self.cmb_incidencias.configure(values=archivos)

        # Intentar detectar automáticamente el universo
        universo = next((a for a in archivos if "universo" in a.lower()),archivos[0])

        # Intentar detectar automáticamente las incidencias
        incidencias = next((a for a in archivos if any(p in a.lower() for p in ["incidencia", "incidencias", "export"])),archivos[0])
        self.cmb_universo.set(universo)
        self.cmb_incidencias.set(incidencias)
        self.cargar_hojas("universo")
        self.cargar_hojas("incidencias")

    def cargar_hojas(self, tipo: str) -> None:
        """
        Carga las hojas del archivo seleccionado.
        """

        if tipo == "universo":
            archivo = self.cmb_universo.get()
            ruta = self.rutas["entrada"] / archivo
            hojas = listar_hojas(ruta)
            self.cmb_hoja_universo.configure(values=hojas)

            if hojas:
                self.cmb_hoja_universo.set(hojas[0])
        else:
            archivo = self.cmb_incidencias.get()
            ruta = self.rutas["entrada"] / archivo
            hojas = listar_hojas(ruta)
            self.cmb_hoja_incidencias.configure(values=hojas)

            if hojas:
                self.cmb_hoja_incidencias.set(hojas[0])

    def ejecutar(self) -> None:
        """Lanza el proceso en un hilo secundario."""
        if self.procesando:
            return
        self.procesando = True
        self.btn_ejecutar.configure(state="disabled", text="Procesando...")
        self.barra.set(0)
        self.t_inicio = time.perf_counter()
        threading.Thread(target=self._trabajo, daemon=True).start()
        self.after(120, self._bombear_cola)
        self._tic_reloj()

    # -------------------------------------------------- hilo de procesamiento
    def _trabajo(self) -> None:
        """Corre en hilo secundario: nunca toca widgets directamente."""
        try:
            self.cfg["pais_activo"] = self.config_app.get("pais_activo", "Nicaragua")
            depurador = DepuradorUniverso(self.rutas["entrada"], self.cfg, self.config_app)
            resultado = depurador.ejecutar(
                progreso=lambda p, m: self.cola.put(("progreso", p, m)))
            self.cola.put(("revision", resultado, None))
        except (ErrorValidacion, ErrorLectura, PermissionError) as exc:
            obtener_logger().error("Proceso detenido: %s", exc)
            self.cola.put(("error", str(exc), None))
        except Exception:
            obtener_logger().error("Error inesperado:\n%s", traceback.format_exc())
            self.cola.put((
                "error",
                "Ocurrió un error inesperado. Revise el archivo de log en la "
                "carpeta 'Logs' para más detalle.", None))

    # ------------------------------------------------------- UI en vivo ------
    def _bombear_cola(self) -> None:
        """Procesa mensajes del hilo de trabajo (patrón seguro Tkinter)."""
        try:
            while True:
                tipo, a, b = self.cola.get_nowait()
                if tipo == "progreso":
                    self.barra.set(a)
                    self.lbl_estado.configure(text=b)
                    if self._revision_activa:
                        self.lbl_revision.configure(text=b, text_color=_AZUL)
                elif tipo == "revision":
                    self._mostrar_revision_depuracion(a)
                    return
                elif tipo == "guardado_revision":
                    self._revision_guardada(a)
                    return
                elif tipo == "fin":
                    self._finalizar_ok(a)
                    return
                elif tipo == "error":
                    self._finalizar_error(a)
                    return
        except queue.Empty:
            pass
        if self.procesando:
            self.after(120, self._bombear_cola)

    def _tic_reloj(self) -> None:
        if self.procesando:
            self.lbl_tiempo.configure(
                text=f"Tiempo: {formatear_duracion(time.perf_counter() - self.t_inicio)}")
            self.after(500, self._tic_reloj)

    def _mostrar_revision_depuracion(self, resultado) -> None:
        self.procesando = False
        self._revision_activa = True
        self._resultado_pendiente = resultado
        for widget in (
            self.marco_archivos, self.marco_run,
            self.contenedor_resultados, self.btn_salida,
        ):
            widget.pack_forget()
        self.marco_revision.pack(fill="both", expand=True, padx=30, pady=(0, 20))
        cfg_sel = self.config_app["paises"][
            self.config_app.get("pais_activo", "Costa Rica")
        ]["modulo_seleccion"]
        self.mapa_revision.cargar(
            resultado.elegibles,
            self.cfg.get("columna_lat", "Latitud"),
            self.cfg.get("columna_lon", "Longitud"),
            limites=cfg_sel.get("limites_pais"),
            categoria_inicial=cfg_sel.get("columna_gec", self.cfg.get("columna_gec")),
            contornos_pais=resultado.contornos_pais,
            contornos_muestra=resultado.contornos_muestra,
        )
        self.lbl_revision.configure(
            text=(
                "Revise únicamente los elegibles. Al continuar se calculará PXR "
                f"con mínimo {cfg_sel.get('pxr_minimo', 10)}."
            ),
            text_color="#5D6874",
        )
        self.btn_guardar_revision.configure(state="normal", text="Guardar cambios")
        self.btn_continuar_revision.configure(state="normal", text="Continuar y exportar")

    def _registrar_cambio_depuracion(self, cantidad: int) -> None:
        self.lbl_revision.configure(
            text=f"{cantidad:,} puntos reclasificados. Hay cambios pendientes de guardar.",
            text_color="#A65E00",
        )

    def _guardar_revision_depuracion(self) -> None:
        if self.procesando or not self._revision_activa:
            return
        datos = self.mapa_revision.obtener_datos()
        self.procesando = True
        self.btn_guardar_revision.configure(state="disabled", text="Guardando...")
        self.btn_continuar_revision.configure(state="disabled")
        threading.Thread(
            target=self._trabajo_guardar_depuracion,
            args=(datos,), daemon=True,
        ).start()
        self.after(120, self._bombear_cola)

    def _trabajo_guardar_depuracion(self, datos: pd.DataFrame) -> None:
        try:
            pais = self.config_app.get("pais_activo", "Costa Rica")
            ruta = guardar_revision_geografica(datos, self.rutas["salida"], pais)
            self.cola.put(("guardado_revision", ruta, None))
        except PermissionError as exc:
            self.cola.put(("error", str(exc), None))
        except Exception:
            obtener_logger().error("Error guardando revisión:\n%s", traceback.format_exc())
            self.cola.put(("error", "No se pudo guardar la revisión geográfica.", None))

    def _revision_guardada(self, ruta) -> None:
        self.procesando = False
        self.btn_guardar_revision.configure(state="normal", text="Guardar cambios")
        self.btn_continuar_revision.configure(state="normal")
        self.lbl_revision.configure(
            text=f"Cambios guardados en {ruta.name}.", text_color=_VERDE,
        )

    def _continuar_depuracion(self) -> None:
        if self.procesando or not self._revision_activa or self._resultado_pendiente is None:
            return
        datos = self.mapa_revision.obtener_datos()
        self.procesando = True
        self.t_inicio = time.perf_counter()
        self.btn_guardar_revision.configure(state="disabled")
        self.btn_continuar_revision.configure(state="disabled", text="Calculando PXR...")
        self.lbl_revision.configure(
            text="Aplicando cambios, calculando PXR y exportando el universo...",
            text_color=_AZUL,
        )
        threading.Thread(
            target=self._trabajo_exportar_depuracion,
            args=(self._resultado_pendiente, datos), daemon=True,
        ).start()
        self.after(120, self._bombear_cola)
        self._tic_reloj()

    def _trabajo_exportar_depuracion(self, resultado, datos: pd.DataFrame) -> None:
        try:
            depurador = DepuradorUniverso(
                self.rutas["entrada"], self.cfg, self.config_app,
            )
            resultado = depurador.aplicar_revision_geografica(resultado, datos)
            pais = self.config_app.get("pais_activo", "Costa Rica")
            guardar_revision_geografica(resultado.elegibles, self.rutas["salida"], pais)
            generar_salidas(resultado, self.rutas["salida"])
            self.cola.put(("fin", resultado, None))
        except (ErrorValidacion, ErrorLectura, PermissionError) as exc:
            obtener_logger().error("Exportación detenida: %s", exc)
            self.cola.put(("error", str(exc), None))
        except Exception:
            obtener_logger().error("Error exportando revisión:\n%s", traceback.format_exc())
            self.cola.put((
                "error", "No se pudo aplicar PXR o exportar el universo. Revise el log.", None,
            ))

    def _alternar_mapa_depuracion(self) -> None:
        if not self._revision_activa:
            return
        if not self._mapa_depuracion_ampliado:
            self.marco_revision.pack_forget()
            self.marco_revision.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.marco_revision.lift()
            self._mapa_depuracion_ampliado = True
        else:
            self.marco_revision.place_forget()
            self.marco_revision.pack(fill="both", expand=True, padx=30, pady=(0, 20))
            self._mapa_depuracion_ampliado = False
        self.mapa_revision.establecer_pantalla_completa(
            self._mapa_depuracion_ampliado,
        )

    def _ocultar_revision_depuracion(self) -> None:
        if self._mapa_depuracion_ampliado:
            self.marco_revision.place_forget()
            self._mapa_depuracion_ampliado = False
            self.mapa_revision.establecer_pantalla_completa(False)
        else:
            self.marco_revision.pack_forget()
        self._revision_activa = False

    def _restaurar_paneles_depuracion(self) -> None:
        self.marco_archivos.pack(fill="x", padx=30, pady=(0, 15))
        self.marco_run.pack(fill="x", padx=30, pady=(0, 15))
        self.contenedor_resultados.pack(fill="both", expand=True, padx=30, pady=(0, 12))
        self.btn_salida.pack(fill="x", padx=30, pady=(0, 20))

    def _finalizar_ok(self, resultado) -> None:
        self.procesando = False
        self._ocultar_revision_depuracion()
        self._restaurar_paneles_depuracion()
        self.barra.set(1)
        self.lbl_estado.configure(text="Proceso finalizado correctamente.",
                                  text_color=_VERDE)
        self.btn_ejecutar.configure(state="normal", text="▶  Ejecutar proceso")
        lineas = [f"{k}:  {v:,}" if isinstance(v, int) else f"{k}:  {v}"
                  for k, v in resultado.metricas.items()]
        self._escribir_resumen("\n".join(lineas))
        self.mapa.actualizar(
            resultado.elegibles,
            self.cfg.get("columna_lat", "Latitud"),
            self.cfg.get("columna_lon", "Longitud"),
            resultado.contornos_pais,
            resultado.contornos_muestra,
        )
        excluidas_n = resultado.metricas.get("Tiendas excluidas", 0)
        elegibles_n = resultado.metricas.get("Tiendas elegibles finales", resultado.metricas.get("Tiendas elegibles", 0))
        messagebox.showinfo(
            NOMBRE_APP,
            "Proceso finalizado correctamente.\n\n"
            f"Tiendas excluidas: {excluidas_n:,}\n"
            f"Tiendas elegibles: {elegibles_n:,}\n\n"
            "Los archivos están en la carpeta 'Salida Depuración'.")

    def _finalizar_error(self, mensaje: str) -> None:
        self.procesando = False
        self.lbl_estado.configure(text="Proceso detenido por un error.",
                                  text_color=_ROJO)
        if self._revision_activa:
            self.btn_guardar_revision.configure(state="normal", text="Guardar cambios")
            self.btn_continuar_revision.configure(state="normal", text="Continuar y exportar")
            self.lbl_revision.configure(text=mensaje, text_color=_ROJO)
        else:
            self.btn_ejecutar.configure(state="normal", text="▶  Ejecutar proceso")
        self._escribir_resumen(f"ERROR:\n\n{mensaje}")
        messagebox.showerror(NOMBRE_APP, mensaje)

    def _escribir_resumen(self, texto: str) -> None:
        self.txt_resumen.configure(state="normal")
        self.txt_resumen.delete("1.0", "end")
        self.txt_resumen.insert("1.0", texto)
        self.txt_resumen.configure(state="disabled")


# =============================================================================
# MÓDULO: Selección de Muestra
# =============================================================================
class FrameSeleccion(ctk.CTkFrame):
    """Pantalla del módulo 'Selección de muestra' (T + suplentes S1..Sn)."""

    def __init__(self, master, rutas: dict, config: dict) -> None:
        super().__init__(master, fg_color="transparent")
        self.rutas = rutas
        self.config_app = config
        self.cola: queue.Queue = queue.Queue()
        self.t_inicio = 0.0
        self.procesando = False
        self._revision_activa = False
        self._mapa_ampliado = False
        self._datos_revision = pd.DataFrame()
        self._construir()
        self.refrescar_insumo()
        
    @property
    def cfg(self):
        pais = self.config_app.get("pais_activo", "Costa Rica")
        return self.config_app["paises"][pais]["modulo_seleccion"]

    def _construir(self) -> None:
        ctk.CTkLabel(self, text="Selección de muestra",
                     font=ctk.CTkFont(size=26, weight="bold"), text_color=_AZUL
                     ).pack(anchor="w", padx=30, pady=(25, 4))
        ctk.CTkLabel(
            self, text="Titulares (T) con cuotas GEC, agrupación por ruta y "
                       "dispersión, más suplentes S1..Sn (relación 1:"
                       f"{self.cfg.get('ratio_suplentes', 4)}).",
            font=ctk.CTkFont(size=14), text_color="#555555",
        ).pack(anchor="w", padx=30, pady=(0, 15))

        self.marco_in = ctk.CTkFrame(self, corner_radius=12)
        self.marco_in.pack(fill="x", padx=30, pady=(0, 15))
        
        hdr_box = ctk.CTkFrame(self.marco_in, fg_color="transparent")
        hdr_box.pack(fill="x", padx=20, pady=(12, 4))
        
        ctk.CTkLabel(hdr_box, text="Archivos de entrada  (carpeta 'Entrada Seleccion')",
                     font=ctk.CTkFont(size=15, weight="bold")
                     ).pack(side="left")
        
        ctk.CTkButton(hdr_box, text="Actualizar estado", width=120, height=30,
                       font=ctk.CTkFont(size=13), fg_color="#E0E0E0", text_color="#333333",
                       hover_color="#CCCCCC", command=self.refrescar_insumo
                       ).pack(side="right")
                       
        self.lbl_insumo = ctk.CTkLabel(self.marco_in, text="", anchor="w",
                                       font=ctk.CTkFont(size=14))
        self.lbl_insumo.pack(fill="x", padx=20, pady=(4, 4))
        self.lbl_params = ctk.CTkLabel(self.marco_in, text="", anchor="w",
                                       font=ctk.CTkFont(size=13),
                                       text_color="#444444")
        self.lbl_params.pack(fill="x", padx=20, pady=(0, 12))

        self.marco_run = ctk.CTkFrame(self, corner_radius=12)
        self.marco_run.pack(fill="x", padx=30, pady=(0, 15))
        self.btn_ejecutar = ctk.CTkButton(
            self.marco_run, text="▶  Seleccionar muestra", height=44,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color=_AZUL, hover_color=_AZUL_CLARO, command=self.ejecutar)
        self.btn_ejecutar.pack(fill="x", padx=20, pady=(18, 10))
        self.barra = ctk.CTkProgressBar(self.marco_run, height=14, progress_color=_AZUL)
        self.barra.set(0)
        self.barra.pack(fill="x", padx=20, pady=(0, 6))
        fila = ctk.CTkFrame(self.marco_run, fg_color="transparent")
        fila.pack(fill="x", padx=20, pady=(0, 14))
        self.lbl_estado = ctk.CTkLabel(fila, text="Listo para ejecutar.",
                                       font=ctk.CTkFont(size=14))
        self.lbl_estado.pack(side="left")
        self.lbl_tiempo = ctk.CTkLabel(fila, text="Tiempo: 00:00:00",
                                       font=ctk.CTkFont(size=14))
        self.lbl_tiempo.pack(side="right")

        self.marco_mapa = ctk.CTkFrame(self, fg_color="transparent")
        self.mapa_editor = MapaSeleccionGeografica(
            self.marco_mapa,
            on_toggle_pantalla=self._alternar_mapa_ampliado,
            on_cambio=self._registrar_cambio_mapa,
            icono_pantalla=ruta_recurso("Config/ampliar-pantalla.png"),
        )
        self.mapa_editor.pack(fill="both", expand=True)
        acciones_mapa = ctk.CTkFrame(self.marco_mapa, fg_color="transparent")
        acciones_mapa.pack(fill="x", pady=(8, 0))
        self.lbl_guardado_mapa = ctk.CTkLabel(
            acciones_mapa, text="Sin cambios pendientes.", anchor="w",
            text_color="#5D6874",
        )
        self.lbl_guardado_mapa.pack(side="left", fill="x", expand=True)
        self.btn_guardar_mapa = ctk.CTkButton(
            acciones_mapa, text="Guardar cambios", width=150, height=38,
            fg_color="transparent", border_width=1, border_color=_AZUL,
            text_color=_AZUL, command=self._guardar_cambios_mapa,
        )
        self.btn_guardar_mapa.pack(side="right", padx=(8, 0))
        self.btn_continuar_mapa = ctk.CTkButton(
            acciones_mapa, text="Continuar proceso", width=180, height=38,
            fg_color=_VERDE, hover_color="#166237",
            command=self._continuar_desde_mapa,
        )
        self.btn_continuar_mapa.pack(side="right", padx=(8, 0))

        self.marco_res = ctk.CTkFrame(self, corner_radius=12)
        self.marco_res.pack(fill="both", expand=True, padx=30, pady=(0, 12))
        ctk.CTkLabel(self.marco_res, text="Resumen de la muestra",
                     font=ctk.CTkFont(size=15, weight="bold")
                     ).pack(anchor="w", padx=20, pady=(12, 4))
        self.txt_resumen = ctk.CTkTextbox(self.marco_res, font=ctk.CTkFont(size=13),
                                          fg_color="#F7F8FA", text_color="#222222")
        self.txt_resumen.pack(fill="both", expand=True, padx=20, pady=(0, 14))
        self.txt_resumen.insert("1.0", "Aún no se ha ejecutado la selección.")
        self.txt_resumen.configure(state="disabled")

        self.btn_salida = ctk.CTkButton(
            self, text="📂  Abrir carpeta Salida Muestras", height=38,
            fg_color=_VERDE, hover_color="#166237",
            command=lambda: abrir_carpeta(self.rutas["salida_muestra"]),
        )
        self.btn_salida.pack(fill="x", padx=30, pady=(0, 20))

    def refrescar_insumo(self) -> None:
        # Hot-reload configuration from disk
        try:
            self.config_app = cargar_config()
        except Exception:
            pass
        pais_act = self.config_app.get("pais_activo", "Costa Rica")
        dir_sel = self.rutas.get("entrada_seleccion", self.rutas["salida"])
        dir_dep = self.rutas.get("salida_depuracion", self.rutas["salida"])
        
        f_target, tokens = buscar_archivo_universo_seleccion(dir_sel, dir_dep, pais_act)
            
        if f_target:
            txt_in = f"  📄 Universo: {f_target.name} (en '{f_target.parent.name}')\n  ✔ Estado: Listo para selección de muestra ({pais_act})"
            color_in = _VERDE
        else:
            txt_in = f"  📄 Universo: Buscando archivo para '{pais_act}' (tokens: {', '.join(tokens)})\n  ✖ Estado: No encontrado en 'Entrada Seleccion' (Coloque el archivo Excel en la carpeta)"
            color_in = _ROJO
            
        self.lbl_insumo.configure(text=txt_in, text_color=color_in)
        
        gec = self.cfg.get("cuotas_gec", {})
        tipo = self.cfg.get("cuotas_tipo", {})
        canal = self.cfg.get("cuotas_canal", {})
        
        # Parámetros adicionales según el país
        extras = []
        if "cuotas_agencia" in self.cfg:
            extras.append(f"Agencias: {len(self.cfg['cuotas_agencia'])}")
        if "cuotas_region" in self.cfg:
            extras.append(f"Regiones: {len(self.cfg['cuotas_region'])}")
        if "cuotas_subcanal" in self.cfg:
            extras.append(f"Subcanales: {len(self.cfg['cuotas_subcanal'])}")
        if "codigos_prioritarios" in self.cfg:
            extras.append(f"Prioritarios: {len(self.cfg['codigos_prioritarios'])} códigos")
            
        txt_extras = f"  ·  " + "  ·  ".join(extras) if extras else ""
        
        self.lbl_params.configure(
            text=f"  Muestra: {self.cfg.get('tamano_muestra')} T  ·  "
                 f"Suplentes 1:{self.cfg.get('ratio_suplentes')}  ·  "
                 f"Cuotas GEC: " + ", ".join([f"{k}: {v}" for k, v in gec.items()])
                 + f"  ·  Ruta: {self.cfg.get('min_pdv_ruta')}-{self.cfg.get('max_pdv_ruta')}"
                 + f"  ·  PXR mínimo: {self.cfg.get('pxr_minimo', 10)}{txt_extras}"
        )

    def ejecutar(self) -> None:
        if self.procesando:
            return
        self.refrescar_insumo()
        self._revision_activa = False
        self.procesando = True
        self.btn_ejecutar.configure(state="disabled", text="Procesando...")
        self.barra.set(0)
        self.t_inicio = time.perf_counter()
        threading.Thread(target=self._trabajo, args=(None,), daemon=True).start()
        self.after(120, self._bombear_cola)
        self._tic_reloj()

    def _crear_selector(self) -> SelectorMuestra:
        pais = self.config_app.get("pais_activo", "Costa Rica")
        cfg_selector = dict(self.config_app["paises"][pais]["modulo_seleccion"])
        cfg_dep = self.config_app["paises"][pais].get("modulo_depuracion", {})
        cfg_selector["pais_activo"] = pais
        cfg_selector["columna_codigo"] = (
            cfg_dep.get("columna_puente") or cfg_dep.get("llave_universo")
        )
        selector = SelectorMuestra(self.rutas["entrada_seleccion"], cfg_selector)
        selector.pais_activo = pais
        selector.rutas_app = self.rutas
        return selector

    def _trabajo_preparar_mapa(self) -> None:
        try:
            try:
                self.config_app = cargar_config()
            except Exception:
                pass
            self.cola.put(("progreso", 0.08, "Leyendo universo para la revisión geográfica..."))
            selector = self._crear_selector()
            datos = selector.cargar_universo()
            selector._resolver_columnas_configuradas(datos)
            faltantes = [
                c for c in (selector.cfg.get("columna_lat"), selector.cfg.get("columna_lon"))
                if not c or c not in datos.columns
            ]
            if faltantes:
                raise ErrorSeleccion(
                    f"No se puede construir el mapa. Faltan columnas GPS: {faltantes}."
                )
            self.cola.put(("mapa", datos, selector.cfg))
        except (ErrorSeleccion, PermissionError) as exc:
            obtener_logger().error("Preparación geográfica detenida: %s", exc)
            self.cola.put(("error", str(exc), None))
        except Exception:
            obtener_logger().error("Error inesperado:\n%s", traceback.format_exc())
            self.cola.put((
                "error", "Ocurrió un error inesperado al preparar el mapa. "
                "Revise el log en la carpeta 'Logs'.", None,
            ))

    def _trabajo(self, datos_revision: pd.DataFrame | None) -> None:
        try:
            selector = self._crear_selector()
            if datos_revision is not None:
                pais = self.config_app.get("pais_activo", "Costa Rica")
                guardar_revision_geografica(
                    datos_revision, self.rutas["salida_muestra"], pais,
                )
            resultado = selector.ejecutar(
                progreso=lambda p, m: self.cola.put(("progreso", p, m)),
                universo=datos_revision,
            )
            generar_salidas_muestra(resultado, self.rutas["salida_muestra"])
            self.cola.put(("fin", resultado, None))
        except (ErrorSeleccion, PermissionError) as exc:
            obtener_logger().error("Selección detenida: %s", exc)
            self.cola.put(("error", str(exc), None))
        except Exception:
            obtener_logger().error("Error inesperado:\n%s", traceback.format_exc())
            self.cola.put(("error", "Ocurrió un error inesperado. Revise el "
                                    "log en la carpeta 'Logs'.", None))

    def _trabajo_guardar_revision(self, datos_revision: pd.DataFrame) -> None:
        try:
            pais = self.config_app.get("pais_activo", "Costa Rica")
            ruta = guardar_revision_geografica(
                datos_revision, self.rutas["salida_muestra"], pais,
            )
            self.cola.put(("guardado", ruta, None))
        except PermissionError as exc:
            self.cola.put(("error", str(exc), None))
        except Exception:
            obtener_logger().error("Error guardando revisión:\n%s", traceback.format_exc())
            self.cola.put((
                "error", "No se pudo guardar la revisión geográfica. Revise el log.", None,
            ))

    def _bombear_cola(self) -> None:
        try:
            while True:
                tipo, a, b = self.cola.get_nowait()
                if tipo == "progreso":
                    self.barra.set(a)
                    self.lbl_estado.configure(text=b)
                    if self._revision_activa:
                        self.lbl_guardado_mapa.configure(text=b, text_color=_AZUL)
                elif tipo == "mapa":
                    self._mostrar_revision_mapa(a, b)
                    return
                elif tipo == "guardado":
                    self._guardado_revision_ok(a)
                    return
                elif tipo == "fin":
                    self._fin_ok(a)
                    return
                else:
                    self._fin_error(a)
                    return
        except queue.Empty:
            pass
        if self.procesando:
            self.after(120, self._bombear_cola)

    def _tic_reloj(self) -> None:
        if self.procesando:
            self.lbl_tiempo.configure(
                text=f"Tiempo: {formatear_duracion(time.perf_counter() - self.t_inicio)}")
            self.after(500, self._tic_reloj)

    def _mostrar_revision_mapa(self, datos: pd.DataFrame, cfg_selector: dict) -> None:
        self.procesando = False
        self._revision_activa = True
        self._datos_revision = datos.copy()
        for widget in (self.marco_in, self.marco_run, self.marco_res, self.btn_salida):
            widget.pack_forget()
        self.marco_mapa.pack(fill="both", expand=True, padx=30, pady=(0, 20))
        self.mapa_editor.cargar(
            datos,
            cfg_selector.get("columna_lat", ""),
            cfg_selector.get("columna_lon", ""),
            limites=cfg_selector.get("limites_pais"),
            categoria_inicial=cfg_selector.get("columna_gec"),
        )
        self.lbl_guardado_mapa.configure(
            text="Revise los puntos elegibles. Los cambios se guardan al continuar.",
            text_color="#5D6874",
        )
        self.btn_guardar_mapa.configure(state="normal", text="Guardar cambios")
        self.btn_continuar_mapa.configure(state="normal", text="Continuar proceso")

    def _registrar_cambio_mapa(self, cantidad: int) -> None:
        self.lbl_guardado_mapa.configure(
            text=f"{cantidad:,} puntos actualizados. Hay cambios pendientes de guardar.",
            text_color="#A65E00",
        )

    def _guardar_cambios_mapa(self) -> None:
        if self.procesando or not self._revision_activa:
            return
        self._datos_revision = self.mapa_editor.obtener_datos()
        self.procesando = True
        self.btn_guardar_mapa.configure(state="disabled", text="Guardando...")
        self.btn_continuar_mapa.configure(state="disabled")
        threading.Thread(
            target=self._trabajo_guardar_revision,
            args=(self._datos_revision.copy(),), daemon=True,
        ).start()
        self.after(120, self._bombear_cola)

    def _guardado_revision_ok(self, ruta) -> None:
        self.procesando = False
        self.btn_guardar_mapa.configure(state="normal", text="Guardar cambios")
        self.btn_continuar_mapa.configure(state="normal")
        self.lbl_guardado_mapa.configure(
            text=f"Cambios guardados en {ruta.name}.", text_color=_VERDE,
        )

    def _continuar_desde_mapa(self) -> None:
        if self.procesando or not self._revision_activa:
            return
        self._datos_revision = self.mapa_editor.obtener_datos()
        self.procesando = True
        self.t_inicio = time.perf_counter()
        self.btn_guardar_mapa.configure(state="disabled")
        self.btn_continuar_mapa.configure(state="disabled", text="Procesando...")
        self.lbl_guardado_mapa.configure(
            text="Guardando cambios, calculando PXR y seleccionando la muestra...",
            text_color=_AZUL,
        )
        threading.Thread(
            target=self._trabajo,
            args=(self._datos_revision.copy(),), daemon=True,
        ).start()
        self.after(120, self._bombear_cola)
        self._tic_reloj()

    def _alternar_mapa_ampliado(self) -> None:
        if not self._revision_activa:
            return
        if not self._mapa_ampliado:
            self.marco_mapa.pack_forget()
            self.marco_mapa.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.marco_mapa.lift()
            self._mapa_ampliado = True
        else:
            self.marco_mapa.place_forget()
            self.marco_mapa.pack(fill="both", expand=True, padx=30, pady=(0, 20))
            self._mapa_ampliado = False
        self.mapa_editor.establecer_pantalla_completa(self._mapa_ampliado)

    def _ocultar_revision_mapa(self) -> None:
        if self._mapa_ampliado:
            self.marco_mapa.place_forget()
            self._mapa_ampliado = False
            self.mapa_editor.establecer_pantalla_completa(False)
        else:
            self.marco_mapa.pack_forget()
        self._revision_activa = False

    def _restaurar_paneles(self) -> None:
        self.marco_in.pack(fill="x", padx=30, pady=(0, 15))
        self.marco_run.pack(fill="x", padx=30, pady=(0, 15))
        self.marco_res.pack(fill="both", expand=True, padx=30, pady=(0, 12))
        self.btn_salida.pack(fill="x", padx=30, pady=(0, 20))

    def _fin_ok(self, r) -> None:
        self.procesando = False
        if self._revision_activa:
            self._ocultar_revision_mapa()
            self._restaurar_paneles()
        self.barra.set(1)
        self.lbl_estado.configure(text="Muestra generada correctamente.",
                                  text_color=_VERDE)
        self.btn_ejecutar.configure(state="normal", text="▶  Seleccionar muestra")
        lineas = [f"{k}:  {v:,}" if isinstance(v, int) else f"{k}:  {v}"
                  for k, v in r.metricas.items()]
        self.txt_resumen.configure(state="normal")
        self.txt_resumen.delete("1.0", "end")
        self.txt_resumen.insert("1.0", "\n".join(lineas))
        self.txt_resumen.configure(state="disabled")
        messagebox.showinfo(
            NOMBRE_APP,
            "Selección finalizada.\n\n"
            f"Titulares: {r.metricas['Titulares (T) seleccionados']:,}\n"
            f"Suplentes: {r.metricas['Suplentes (S) asignados']:,}\n\n"
            "Archivos en la carpeta 'Salida Muestras'.")

    def _fin_error(self, mensaje: str) -> None:
        self.procesando = False
        self.lbl_estado.configure(text="Proceso detenido por un error.",
                                  text_color=_ROJO)
        if self._revision_activa:
            self.btn_guardar_mapa.configure(state="normal", text="Guardar cambios")
            self.btn_continuar_mapa.configure(state="normal", text="Continuar proceso")
            self.lbl_guardado_mapa.configure(text=mensaje, text_color=_ROJO)
        else:
            self.btn_ejecutar.configure(state="normal", text="▶  Seleccionar muestra")
        messagebox.showerror(NOMBRE_APP, mensaje)

# =============================================================================
# MÓDULO: Configuración
# =============================================================================

class FrameConfiguracion(ctk.CTkFrame):

    def __init__(self, master, rutas: dict, config: dict):
        super().__init__(master, fg_color="transparent")
        self.rutas = rutas
        self.config_app = config
        self._cola_datos: queue.Queue = queue.Queue()
        
        self.lista_paises = ["Chile", "Costa Rica", "Ecuador", "El Salvador", "Guatemala ABVO", "Guatemala EMBOCEN", "Honduras", "Nicaragua", "Panamá", "República Dominicana"]
        
        self._construir()
        self.al_cambiar_pais(self.config_app.get("pais_activo", "Costa Rica"), init=True)
        self.cargar_archivos()
        self.after(100, self._bombear_datos)

    @property
    def cfg(self):
        pais = self.config_app.get("pais_activo", "Costa Rica")
        return self.config_app["paises"][pais]["modulo_depuracion"]

    @property
    def cfg_sel(self):
        pais = self.config_app.get("pais_activo", "Costa Rica")
        return self.config_app["paises"][pais]["modulo_seleccion"]

    def _construir(self):
                # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=40, pady=(35, 10))
        
        # Titulos a la izquierda
        titulos = ctk.CTkFrame(header_frame, fg_color="transparent")
        titulos.pack(side="left")
        ctk.CTkLabel(titulos, text="Configuración", font=ctk.CTkFont(size=28, weight="bold"), text_color=_AZUL).pack(anchor="w")
        ctk.CTkLabel(titulos, text="Seleccione los archivos, hojas y ajuste los parámetros generales.", font=ctk.CTkFont(size=14), text_color="#666666").pack(anchor="w", pady=(5,0))
        
        # Selector de País a la derecha
        ctk.CTkLabel(header_frame, text="País:", font=ctk.CTkFont(size=15, weight="bold"), text_color=_AZUL).pack(side="left", padx=(0,10), expand=True, anchor="e")
        self.cmb_pais = ctk.CTkComboBox(header_frame, values=self.lista_paises, command=self.al_cambiar_pais, corner_radius=6, border_width=1, border_color=_AZUL, fg_color="#F9F9F9", height=32, text_color=_AZUL)
        self.cmb_pais.pack(side="right")
        self.cmb_pais.set(self.config_app.get("pais_activo", "Costa Rica"))
        
        # Contenedor principal con grid
        grid_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color="#A7B1BC",
            scrollbar_button_hover_color="#7F8C99",
        )
        grid_frame.pack(fill="both", expand=True, padx=30, pady=5)
        grid_frame.grid_columnconfigure(0, weight=1)
        grid_frame.grid_columnconfigure(1, weight=1)

        # Configuraciones de tarjeta ejecutiva
        card_kwargs = {"corner_radius": 8, "fg_color": "#FFFFFF", "border_width": 1, "border_color": _BORDE}

        # ====== CUADRANTE 1: Archivos ======
        marco_archivos = ctk.CTkFrame(grid_frame, **card_kwargs)
        marco_archivos.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        
        ctk.CTkLabel(marco_archivos, text="Archivos Principales", font=ctk.CTkFont(size=16, weight="bold"), text_color=_AZUL).pack(anchor="w", padx=20, pady=(20,15))
        
        self.cmb_universo = self._crear_combo_ejecutivo(marco_archivos, "Universo", command=self.al_cambiar_archivo)
        self.cmb_hoja_universo = self._crear_combo_ejecutivo(marco_archivos, "Hoja Universo", command=self.al_cambiar_hoja)
        self.cmb_incidencias = self._crear_combo_ejecutivo(
            marco_archivos, "Incidencias", command=self.al_cambiar_archivo_incidencias,
        )
        self.cmb_hoja_incidencias = self._crear_combo_ejecutivo(marco_archivos, "Hoja Incidencias")
        
        ctk.CTkFrame(marco_archivos, height=10, fg_color="transparent").pack() # Spacer

        # ====== CUADRANTE 2: Puntos Rutas ======
        marco_pr = ctk.CTkFrame(grid_frame, **card_kwargs)
        marco_pr.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        
        ctk.CTkLabel(marco_pr, text="Puntos de Rutas y Cluster", text_color=_AZUL, font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=20, pady=(20,15))
        
        self._pr_min = self._crear_campo_ejecutivo(marco_pr, "Min de Ruta", str(self.cfg_sel.get("min_pdv_ruta", "")))
        self._pr_max = self._crear_campo_ejecutivo(marco_pr, "Max de Ruta", str(self.cfg_sel.get("max_pdv_ruta", "")))
        self._pr_max_dif = self._crear_campo_ejecutivo(marco_pr, "Max dif. de Ruta", str(self.cfg_sel["puntos_rutas"].get("max_diferencia_ruta", "")))
        self._pr_min_base = self._crear_campo_ejecutivo(marco_pr, "Min Ruta de Base", str(self.cfg_sel["puntos_rutas"].get("min_ruta_base", "")))
        
        cluster_cfg = self.cfg_sel.get("dbscan") or self.cfg_sel.get("cluster", {})
        self._cluster_eps = self._crear_campo_ejecutivo(marco_pr, "Cluster EPS", str(cluster_cfg.get("eps", 0.01)))
        self._cluster_min_samples = self._crear_campo_ejecutivo(marco_pr, "Cluster Min Samples", str(cluster_cfg.get("min_samples", 1)))
        
        ctk.CTkFrame(marco_pr, height=10, fg_color="transparent").pack() # Spacer

        # ====== CUADRANTE 3: Mapeo de columnas ======
        marco_mapeo = ctk.CTkFrame(grid_frame, **card_kwargs)
        marco_mapeo.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        
        ctk.CTkLabel(marco_mapeo, text="Mapeo de Columnas", text_color=_AZUL, font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=20, pady=(20,15))
        
        self.cmb_puente = self._crear_combo_ejecutivo(marco_mapeo, "Código Puente", default_val=self.cfg.get("columna_puente", ""))
        self.cmb_lat = self._crear_combo_ejecutivo(marco_mapeo, "Latitud", default_val=self.cfg.get("columna_lat", ""))
        self.cmb_lon = self._crear_combo_ejecutivo(marco_mapeo, "Longitud", default_val=self.cfg.get("columna_lon", ""))
        self.cmb_ruta = self._crear_combo_ejecutivo(marco_mapeo, "Ruta", default_val=self.cfg_sel.get("columna_ruta", ""))
        self.cmb_gec = self._crear_combo_ejecutivo(marco_mapeo, "Gec", default_val=self.cfg_sel.get("columna_gec", ""))
        self.cmb_canal = self._crear_combo_ejecutivo(marco_mapeo, "Canal", default_val=self.cfg_sel.get("columna_canal", ""))
        self.cmb_fijos = self._crear_combo_ejecutivo(marco_mapeo, "Fijos", default_val=self.cfg_sel.get("columna_fijo", ""), command=self.al_cambiar_columna_fijos)
        self.cmb_valor_fijo = self._crear_combo_ejecutivo(marco_mapeo, "Valor del Fijo", default_val=self.cfg_sel.get("valor_fijo", "SI"))
        
        ctk.CTkFrame(marco_mapeo, height=10, fg_color="transparent").pack() # Spacer

        # ====== CUADRANTE 4: Muestra Selección ======
        marco_muestra = ctk.CTkFrame(grid_frame, **card_kwargs)
        marco_muestra.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)
        
        ctk.CTkLabel(marco_muestra, text="Muestra Selección", text_color=_AZUL, font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=20, pady=(20,4))
        ctk.CTkLabel(marco_muestra, text="Cuotas de selección y límites de repetitividad.", text_color="#6B7480", font=ctk.CTkFont(size=12)).pack(anchor="w", padx=20, pady=(0,10))
        
        grid_interior = ctk.CTkFrame(marco_muestra, fg_color="transparent")
        grid_interior.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        grid_interior.grid_columnconfigure(0, weight=1)
        grid_interior.grid_columnconfigure(1, weight=1)
        
        gec = self.cfg_sel.get("cuotas_gec", {})
        tipo = self.cfg_sel.get("cuotas_tipo", {})
        canal = self.cfg_sel.get("cuotas_canal", {})
        rep = self.cfg.get("rotacion", {}).get("cupos_por_gec", {})

        v_oro = gec.get("ORO", self.cfg_sel.get("muestra_ruta", {}).get("oro", 0))
        v_plata = gec.get("PLATA", self.cfg_sel.get("muestra_ruta", {}).get("plata", 0))
        v_bronce = gec.get("BRONCE", self.cfg_sel.get("muestra_ruta", {}).get("bronce", 0))
        v_fijo = tipo.get("FIJO", self.cfg_sel.get("muestra_ruta", {}).get("fijos", 0))
        v_var = tipo.get("VARIABLE", self.cfg_sel.get("muestra_ruta", {}).get("variables", 0))
        v_con = canal.get("ON", canal.get("ON PREMISE", self.cfg_sel.get("muestra_ruta", {}).get("canal_on", 0)))
        v_coff = canal.get("OFF", canal.get("HOME MARKET TRADICIONAL", self.cfg_sel.get("muestra_ruta", {}).get("canal_off", 0)))
        v_rep_oro = rep.get("ORO", 6)
        v_rep_plata = rep.get("PLATA", 4)
        v_rep_bronce = rep.get("BRONCE", 2)
        v_pxr = self.cfg.get("pxr_minimo", self.cfg_sel.get("pxr_minimo", 10))

        grupo_gec = self._crear_grupo_muestra(grid_interior, "1. GEC", 0, 0)
        self._mr_oro = self._crear_campo_grupo(grupo_gec, "1. Oro", str(v_oro))
        self._mr_plata = self._crear_campo_grupo(grupo_gec, "2. Plata", str(v_plata))
        self._mr_bronce = self._crear_campo_grupo(grupo_gec, "3. Bronce", str(v_bronce))

        grupo_tipo = self._crear_grupo_muestra(grid_interior, "2. Fijos / Variables", 0, 1)
        self._mr_var = self._crear_campo_grupo(grupo_tipo, "1. Variable", str(v_var))
        self._mr_fijos = self._crear_campo_grupo(grupo_tipo, "2. Fijos", str(v_fijo))

        grupo_canal = self._crear_grupo_muestra(grid_interior, "3. Canal", 1, 0)
        self._mr_coff = self._crear_campo_grupo(grupo_canal, "1. OFF", str(v_coff))
        self._mr_con = self._crear_campo_grupo(grupo_canal, "2. ON", str(v_con))

        grupo_rep = self._crear_grupo_muestra(grid_interior, "4. REP", 1, 1)
        self._mr_rep_oro = self._crear_campo_grupo(grupo_rep, "1. Oro REP", str(v_rep_oro))
        self._mr_rep_plata = self._crear_campo_grupo(grupo_rep, "2. Plata REP", str(v_rep_plata))
        self._mr_rep_bronce = self._crear_campo_grupo(grupo_rep, "3. Bronce REP", str(v_rep_bronce))

        grupo_pxr = self._crear_grupo_muestra(grid_interior, "5. PXR", 2, 0)
        grupo_pxr.grid_configure(columnspan=2)
        self._mr_pxr = self._crear_campo_grupo(
            grupo_pxr, "1. Mínimo elegible", str(v_pxr), solo_entero=True,
        )

        # ====== CUADRANTE 5: Cuotas Avanzadas por País (Regiones, Agencias, Subcanales) ======
        self.entries_cuotas_adicionales = {}
        
        self.marco_adicionales = ctk.CTkFrame(grid_frame, **card_kwargs)
        
        self.lbl_adicionales_titulo = ctk.CTkLabel(self.marco_adicionales, text="Cuotas y Parámetros Específicos del País", font=ctk.CTkFont(size=16, weight="bold"), text_color=_AZUL)
        self.lbl_adicionales_titulo.pack(anchor="w", padx=20, pady=(15,10))
        
        self.contenedor_adicionales = ctk.CTkFrame(self.marco_adicionales, fg_color="transparent")
        self.contenedor_adicionales.pack(fill="both", expand=True, padx=20, pady=(0,15))
        
        self._reconstruir_cuotas_adicionales_ui()

        # ====== BOTON GUARDAR ======
        footer_frame = ctk.CTkFrame(self, fg_color="transparent", height=42)
        footer_frame.pack(fill="x", padx=30, pady=(2, 8))
        footer_frame.pack_propagate(False)
        
        self.btn_guardar = ctk.CTkButton(footer_frame, text="Guardar Configuración", width=190, height=34, corner_radius=6,
                                         fg_color=_AZUL, hover_color=_AZUL_OSCURO, font=ctk.CTkFont(size=13, weight="bold"),
                                         command=self.guardar_configuracion)
        self.btn_guardar.pack(side="right", pady=4)
        
    def _crear_campo_ejecutivo(self, parent, label_text, default_value=""):
        # Contenedor para alinear label y entry horizontalmente
        fila = ctk.CTkFrame(parent, fg_color="transparent")
        fila.pack(fill="x", padx=20, pady=6)
        
        ctk.CTkLabel(fila, text=label_text, width=140, anchor="w", text_color="#333333", font=ctk.CTkFont(size=13)).pack(side="left")
        entry = ctk.CTkEntry(fila, corner_radius=6, border_width=1, border_color="#CCCCCC", fg_color="#F9F9F9", height=32)
        entry.pack(side="left", fill="x", expand=True)
        entry.insert(0, default_value)
        return entry
        
    def _crear_combo_ejecutivo(self, parent, label_text, command=None, default_val=""):
        fila = ctk.CTkFrame(parent, fg_color="transparent")
        fila.pack(fill="x", padx=20, pady=6)
        
        ctk.CTkLabel(fila, text=label_text, width=140, anchor="w", text_color="#333333", font=ctk.CTkFont(size=13)).pack(side="left")
        combo = ctk.CTkComboBox(fila, values=[""], command=command, corner_radius=6, border_width=1, border_color="#CCCCCC", fg_color="#F9F9F9", height=32)
        combo.pack(side="left", fill="x", expand=True)
        combo.set(default_val)
        return combo

    def _crear_grupo_muestra(self, parent, titulo, row, col):
        grupo = ctk.CTkFrame(
            parent, fg_color="#F8FAFC", corner_radius=7,
            border_width=1, border_color="#E2E7EC",
        )
        grupo.grid(row=row, column=col, sticky="nsew", padx=5, pady=5)
        ctk.CTkLabel(
            grupo, text=titulo, text_color=_AZUL,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=12, pady=(10, 4))
        return grupo

    @staticmethod
    def _validar_entero_en_edicion(valor: str) -> bool:
        """Permite editar el campo, pero impide introducir caracteres no numéricos."""
        return valor == "" or valor.isdigit()

    def _crear_campo_grupo(self, parent, etiqueta, valor="", solo_entero=False):
        fila = ctk.CTkFrame(parent, fg_color="transparent")
        fila.pack(fill="x", padx=12, pady=3)
        ctk.CTkLabel(
            fila, text=etiqueta, width=96, anchor="w", text_color="#3D4650",
            font=ctk.CTkFont(size=12),
        ).pack(side="left")
        entrada = ctk.CTkEntry(
            fila, width=72, corner_radius=6, border_width=1,
            border_color="#C9D0D7", fg_color="#FFFFFF", height=28,
            validate="key" if solo_entero else "none",
            validatecommand=(
                (self.register(self._validar_entero_en_edicion), "%P")
                if solo_entero else None
            ),
        )
        entrada.pack(side="right", fill="x", expand=True)
        entrada.insert(0, valor)
        return entrada

    def _crear_campo_grid(self, parent, label_text, row, col, default_value=""):
        ctk.CTkLabel(parent, text=label_text, anchor="w", text_color="#333333", font=ctk.CTkFont(size=13)).grid(row=row, column=col, sticky="w", padx=(0,10), pady=6)
        entry = ctk.CTkEntry(parent, width=80, corner_radius=6, border_width=1, border_color="#CCCCCC", fg_color="#F9F9F9", height=32)
        entry.grid(row=row, column=col+1, sticky="w", padx=(0,20), pady=6)
        entry.insert(0, default_value)
        return entry

    def _reconstruir_cuotas_adicionales_ui(self):
        for widget in self.contenedor_adicionales.winfo_children():
            widget.destroy()
        self.entries_cuotas_adicionales.clear()

        sections = [
            ("cuotas_region", "Cuotas por Región"),
            ("cuotas_subcanal", "Cuotas por Subcanal"),
            ("cuotas_agencia", "Cuotas por Agencia"),
            ("cuotas_fijo_canal", "Cuotas Fijo x Canal")
        ]

        row_idx = 0
        has_any = False

        for sec_key, sec_title in sections:
            sec_dict = self.cfg_sel.get(sec_key, {})
            if not sec_dict:
                continue
            has_any = True
            ctk.CTkLabel(self.contenedor_adicionales, text=sec_title, font=ctk.CTkFont(size=14, weight="bold"), text_color=_AZUL).grid(row=row_idx, column=0, columnspan=6, sticky="w", pady=(10, 5))
            row_idx += 1

            col_idx = 0
            for k, val in sec_dict.items():
                lbl_text = f"{k}:"
                ctk.CTkLabel(self.contenedor_adicionales, text=lbl_text, anchor="w", text_color="#333333", font=ctk.CTkFont(size=12)).grid(row=row_idx, column=col_idx*2, sticky="w", padx=(0,5), pady=4)
                entry = ctk.CTkEntry(self.contenedor_adicionales, width=75, corner_radius=6, border_width=1, border_color="#CCCCCC", fg_color="#F9F9F9", height=28)
                entry.grid(row=row_idx, column=col_idx*2+1, sticky="w", padx=(0,15), pady=4)
                entry.insert(0, str(val))
                self.entries_cuotas_adicionales[(sec_key, k)] = entry

                col_idx += 1
                if col_idx >= 3:
                    col_idx = 0
                    row_idx += 1
            if col_idx != 0:
                row_idx += 1

        codigos_prio = self.cfg_sel.get("codigos_prioritarios")
        if codigos_prio is not None:
            has_any = True
            ctk.CTkLabel(self.contenedor_adicionales, text="Códigos Prioritarios (separados por coma):", font=ctk.CTkFont(size=14, weight="bold"), text_color=_AZUL).grid(row=row_idx, column=0, columnspan=6, sticky="w", pady=(10, 5))
            row_idx += 1
            
            entry_prio = ctk.CTkEntry(self.contenedor_adicionales, width=480, corner_radius=6, border_width=1, border_color="#CCCCCC", fg_color="#F9F9F9", height=32)
            entry_prio.grid(row=row_idx, column=0, columnspan=6, sticky="w", padx=(0,15), pady=4)
            entry_prio.insert(0, ", ".join(str(x) for x in codigos_prio))
            self.entry_codigos_prioritarios = entry_prio
            row_idx += 1
        else:
            self.entry_codigos_prioritarios = None

        if has_any:
            self.marco_adicionales.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=10, pady=10)
        else:
            self.marco_adicionales.grid_forget()

    def al_cambiar_columna_fijos(self, choice):
        """Carga en segundo plano los valores reales de la columna de fijos."""
        valor_actual = self.cmb_valor_fijo.get() or self.cfg_sel.get("valor_fijo", "SI")
        self.cmb_valor_fijo.configure(values=[valor_actual])
        self.cmb_valor_fijo.set(valor_actual)
        archivo = self.cmb_universo.get()
        hoja = self.cmb_hoja_universo.get()
        ruta = self.rutas["entrada"] / archivo
        if not choice or not hoja or not ruta.exists():
            return

        token = (str(ruta), hoja, choice)
        self._token_valores_fijo = token
        threading.Thread(
            target=self._cargar_valores_fijo_hilo,
            args=(ruta, hoja, choice, token, valor_actual),
            daemon=True,
        ).start()

    def _cargar_valores_fijo_hilo(self, ruta, hoja, columna, token, valor_actual):
        valores = leer_valores_columna(ruta, hoja, columna)
        self._cola_datos.put(("valores_fijo", token, valores, valor_actual))

    def _bombear_datos(self):
        """Aplica en el hilo de Tk los resultados de lecturas en segundo plano."""
        try:
            while True:
                evento = self._cola_datos.get_nowait()
                if evento[0] == "columnas":
                    self._actualizar_combos_columnas(evento[1])
                elif evento[0] == "valores_fijo":
                    self._actualizar_valores_fijo(*evento[1:])
                elif evento[0] == "hojas":
                    self._actualizar_hojas(*evento[1:])
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._bombear_datos)

    def _actualizar_valores_fijo(self, token, valores, valor_actual):
        if token != getattr(self, "_token_valores_fijo", None):
            return
        opciones = list(dict.fromkeys([str(v) for v in valores if str(v).strip()]))
        if valor_actual and valor_actual not in opciones:
            opciones.insert(0, valor_actual)
        if not opciones:
            opciones = ["SI", "NO"]
        self.cmb_valor_fijo.configure(values=opciones)
        self.cmb_valor_fijo.set(valor_actual if valor_actual in opciones else opciones[0])

    def al_cambiar_pais(self, choice, init=False):
        self.config_app["pais_activo"] = choice
        
        # Refrescamos los valores de los campos al cambiar de país
        self.cmb_universo.set(self.cfg.get("archivo_universo", ""))
        self.cmb_hoja_universo.set(self.cfg.get("hoja_universo", ""))
        self.cmb_incidencias.set(self.cfg.get("archivo_incidencias", ""))
        self.cmb_hoja_incidencias.set(self.cfg.get("hoja_incidencias", ""))
        
        self._pr_min.delete(0, 'end'); self._pr_min.insert(0, str(self.cfg_sel.get("min_pdv_ruta", "")))
        self._pr_max.delete(0, 'end'); self._pr_max.insert(0, str(self.cfg_sel.get("max_pdv_ruta", "")))
        p_rutas = self.cfg_sel.get("puntos_rutas", {})
        self._pr_max_dif.delete(0, 'end'); self._pr_max_dif.insert(0, str(p_rutas.get("max_diferencia_ruta", "")))
        self._pr_min_base.delete(0, 'end'); self._pr_min_base.insert(0, str(p_rutas.get("min_ruta_base", "")))
        
        cluster_cfg = self.cfg_sel.get("dbscan") or self.cfg_sel.get("cluster", {})
        self._cluster_eps.delete(0, 'end'); self._cluster_eps.insert(0, str(cluster_cfg.get("eps", 0.01)))
        self._cluster_min_samples.delete(0, 'end'); self._cluster_min_samples.insert(0, str(cluster_cfg.get("min_samples", 1)))
        
        self.cmb_puente.set(self.cfg.get("columna_puente", ""))
        self.cmb_lat.set(self.cfg.get("columna_lat", ""))
        self.cmb_lon.set(self.cfg.get("columna_lon", ""))
        self.cmb_ruta.set(self.cfg_sel.get("columna_ruta") or "")
        self.cmb_gec.set(self.cfg_sel.get("columna_gec", ""))
        self.cmb_canal.set(self.cfg_sel.get("columna_canal", ""))
        fijo_val = self.cfg_sel.get("columna_fijo", "")
        self.cmb_fijos.set(fijo_val)
        self.cmb_valor_fijo.set(self.cfg_sel.get("valor_fijo", "SI"))
        if fijo_val:
            self.al_cambiar_columna_fijos(fijo_val)
        
        gec = self.cfg_sel.get("cuotas_gec", {})
        tipo = self.cfg_sel.get("cuotas_tipo", {})
        canal = self.cfg_sel.get("cuotas_canal", {})

        v_oro = gec.get("ORO", self.cfg_sel.get("muestra_ruta", {}).get("oro", 0))
        v_plata = gec.get("PLATA", self.cfg_sel.get("muestra_ruta", {}).get("plata", 0))
        v_bronce = gec.get("BRONCE", self.cfg_sel.get("muestra_ruta", {}).get("bronce", 0))
        v_fijo = tipo.get("FIJO", self.cfg_sel.get("muestra_ruta", {}).get("fijos", 0))
        v_var = tipo.get("VARIABLE", self.cfg_sel.get("muestra_ruta", {}).get("variables", 0))
        v_con = canal.get("ON", canal.get("ON PREMISE", self.cfg_sel.get("muestra_ruta", {}).get("canal_on", 0)))
        v_coff = canal.get("OFF", canal.get("HOME MARKET TRADICIONAL", self.cfg_sel.get("muestra_ruta", {}).get("canal_off", 0)))
        rep = self.cfg.get("rotacion", {}).get("cupos_por_gec", {})

        self._mr_oro.delete(0, 'end'); self._mr_oro.insert(0, str(v_oro))
        self._mr_plata.delete(0, 'end'); self._mr_plata.insert(0, str(v_plata))
        self._mr_bronce.delete(0, 'end'); self._mr_bronce.insert(0, str(v_bronce))
        self._mr_fijos.delete(0, 'end'); self._mr_fijos.insert(0, str(v_fijo))
        self._mr_var.delete(0, 'end'); self._mr_var.insert(0, str(v_var))
        self._mr_con.delete(0, 'end'); self._mr_con.insert(0, str(v_con))
        self._mr_coff.delete(0, 'end'); self._mr_coff.insert(0, str(v_coff))
        self._mr_rep_oro.delete(0, 'end'); self._mr_rep_oro.insert(0, str(rep.get("ORO", 6)))
        self._mr_rep_plata.delete(0, 'end'); self._mr_rep_plata.insert(0, str(rep.get("PLATA", 4)))
        self._mr_rep_bronce.delete(0, 'end'); self._mr_rep_bronce.insert(0, str(rep.get("BRONCE", 2)))
        self._mr_pxr.delete(0, 'end'); self._mr_pxr.insert(
            0, str(self.cfg.get("pxr_minimo", self.cfg_sel.get("pxr_minimo", 10))),
        )
        
        self._reconstruir_cuotas_adicionales_ui()

        if not init:
            self.cargar_archivos()
        
        try:
            self.master.master.master.actualizar_info_proyecto()
        except AttributeError:
            pass
            
    def al_cambiar_archivo(self, choice):
        self.cargar_hojas("universo")

    def al_cambiar_archivo_incidencias(self, choice):
        self.cargar_hojas("incidencias")

    def al_cambiar_hoja(self, choice):
        if not choice: return
        archivo = self.cmb_universo.get()
        ruta_archivo = self.rutas["entrada"] / archivo
        if not ruta_archivo.exists():
            return
        threading.Thread(target=self._cargar_columnas_hilo, args=(ruta_archivo, choice), daemon=True).start()

    def _cargar_columnas_hilo(self, ruta_archivo, hoja):
        cols = leer_columnas(ruta_archivo, hoja)
        if cols:
            self._cola_datos.put(("columnas", cols))
            
    def _actualizar_combos_columnas(self, cols):
        combos = [
            self.cmb_puente, self.cmb_lat, self.cmb_lon,
            self.cmb_ruta, self.cmb_gec, self.cmb_canal, self.cmb_fijos
        ]
        for combo in combos:
            val_actual = combo.get()
            opciones = list(cols)
            if val_actual and val_actual not in opciones:
                opciones.append(val_actual)
            combo.configure(values=opciones)
            if val_actual:
                combo.set(val_actual)
            elif opciones:
                combo.set(opciones[0])
        if self.cmb_fijos.get():
            self.al_cambiar_columna_fijos(self.cmb_fijos.get())

    def cargar_archivos(self) -> None:
        archivos = listar_archivos_excel(self.rutas["entrada"])
        if not archivos:
            self.cmb_universo.configure(values=["No se encontraron archivos"])
            self.cmb_incidencias.configure(values=["No se encontraron archivos"])
            self.cmb_universo.set("No se encontraron archivos")
            self.cmb_incidencias.set("No se encontraron archivos")
            return
        self.cmb_universo.configure(values=archivos)
        self.cmb_incidencias.configure(values=archivos)

        universo_cfg = self.cfg.get("archivo_universo", "")
        incidencias_cfg = self.cfg.get("archivo_incidencias", "")
        universo = universo_cfg if universo_cfg in archivos else next(
            (a for a in archivos if "universo" in a.lower()), archivos[0])
        incidencias = incidencias_cfg if incidencias_cfg in archivos else next(
            (a for a in archivos if any(p in a.lower() for p in ["incidencia", "incidencias", "export"])), archivos[0])
        self.cmb_universo.set(universo)
        self.cmb_incidencias.set(incidencias)
        hoja_universo = self.cfg.get("hoja_universo", "")
        hoja_incidencias = self.cfg.get("hoja_incidencias", "")
        self.cmb_hoja_universo.configure(values=[hoja_universo])
        self.cmb_hoja_incidencias.configure(values=[hoja_incidencias])
        self.cmb_hoja_universo.set(hoja_universo)
        self.cmb_hoja_incidencias.set(hoja_incidencias)
        self.al_cambiar_hoja(hoja_universo)

    def cargar_hojas(self, tipo: str) -> None:
        if tipo == "universo":
            archivo = self.cmb_universo.get()
            ruta = self.rutas["entrada"] / archivo
            if not ruta.exists():
                return
            configurada = self.cfg.get("hoja_universo", "")
            self.cmb_hoja_universo.configure(values=["Cargando hojas..."])
        else:
            archivo = self.cmb_incidencias.get()
            ruta = self.rutas["entrada"] / archivo
            if not ruta.exists():
                return
            configurada = self.cfg.get("hoja_incidencias", "")
            self.cmb_hoja_incidencias.configure(values=["Cargando hojas..."])
        threading.Thread(
            target=self._cargar_hojas_hilo,
            args=(ruta, tipo, configurada),
            daemon=True,
        ).start()

    def _cargar_hojas_hilo(self, ruta, tipo, configurada):
        try:
            hojas = listar_hojas(ruta)
        except ErrorLectura as exc:
            obtener_logger().warning("No se pudieron leer las hojas de %s: %s", ruta.name, exc)
            hojas = []
        self._cola_datos.put(("hojas", tipo, hojas, configurada))

    def _actualizar_hojas(self, tipo, hojas, configurada):
        if not hojas:
            return
        seleccion = configurada if configurada in hojas else hojas[0]
        if tipo == "universo":
            self.cmb_hoja_universo.configure(values=hojas)
            self.cmb_hoja_universo.set(seleccion)
            self.al_cambiar_hoja(seleccion)
        else:
            self.cmb_hoja_incidencias.configure(values=hojas)
            self.cmb_hoja_incidencias.set(seleccion)

    def guardar_configuracion(self):
        # Archivos
        self.cfg["archivo_universo"] = self.cmb_universo.get()
        self.cfg["hoja_universo"] = self.cmb_hoja_universo.get()
        self.cfg["archivo_incidencias"] = self.cmb_incidencias.get()
        self.cfg["hoja_incidencias"] = self.cmb_hoja_incidencias.get()
        
        # Puntos Rutas y Cluster
        try:
            self.cfg_sel["min_pdv_ruta"] = int(self._pr_min.get())
            self.cfg_sel["max_pdv_ruta"] = int(self._pr_max.get())
            if "puntos_rutas" not in self.cfg_sel:
                self.cfg_sel["puntos_rutas"] = {}
            self.cfg_sel["puntos_rutas"]["max_diferencia_ruta"] = int(self._pr_max_dif.get())
            self.cfg_sel["puntos_rutas"]["min_ruta_base"] = int(self._pr_min_base.get())
            
            self.cfg_sel["cluster"] = {
                "eps": float(self._cluster_eps.get()),
                "min_samples": int(self._cluster_min_samples.get())
            }
            self.cfg_sel["dbscan"] = dict(self.cfg_sel["cluster"])
            
            # Muestra de la ruta (Números Enteros)
            oro = int(self._mr_oro.get())
            plata = int(self._mr_plata.get())
            bronce = int(self._mr_bronce.get())
            fijos_val = int(self._mr_fijos.get())
            var_val = int(self._mr_var.get())
            con_val = int(self._mr_con.get())
            coff_val = int(self._mr_coff.get())
            rep_oro = int(self._mr_rep_oro.get())
            rep_plata = int(self._mr_rep_plata.get())
            rep_bronce = int(self._mr_rep_bronce.get())
            pxr_minimo = int(self._mr_pxr.get())

            if min(rep_oro, rep_plata, rep_bronce) <= 0:
                raise ValueError("Los límites REP deben ser mayores que cero.")
            if pxr_minimo <= 0:
                raise ValueError("El PXR mínimo debe ser mayor que cero.")

            # PXR pertenece a Depuración. Se replica en Selección para que los
            # módulos posteriores consuman exactamente el mismo umbral.
            self.cfg["pxr_minimo"] = pxr_minimo
            self.cfg_sel["pxr_minimo"] = pxr_minimo

            if "muestra_ruta" not in self.cfg_sel:
                self.cfg_sel["muestra_ruta"] = {}
            self.cfg_sel["muestra_ruta"]["oro"] = oro
            self.cfg_sel["muestra_ruta"]["plata"] = plata
            self.cfg_sel["muestra_ruta"]["bronce"] = bronce
            self.cfg_sel["muestra_ruta"]["fijos"] = fijos_val
            self.cfg_sel["muestra_ruta"]["variables"] = var_val
            self.cfg_sel["muestra_ruta"]["canal_on"] = con_val
            self.cfg_sel["muestra_ruta"]["canal_off"] = coff_val
            
            # Guardar Cuotas GEC, Tipo y Canal en enteros exactos
            total_calc = oro + plata + bronce
            if total_calc > 0:
                self.cfg_sel["tamano_muestra"] = total_calc
                self.cfg_sel["cuotas_gec"] = {
                    "ORO": oro,
                    "PLATA": plata,
                    "BRONCE": bronce
                }
                self.cfg_sel["cuotas_tipo"] = {
                    "FIJO": fijos_val,
                    "VARIABLE": var_val
                }
                
                # Respetar llaves de canal según país (ON / OFF vs ON PREMISE / HOME MARKET TRADICIONAL)
                old_canal_keys = list(self.cfg_sel.get("cuotas_canal", {}).keys())
                if "HOME MARKET TRADICIONAL" in old_canal_keys or "ON PREMISE" in old_canal_keys:
                    self.cfg_sel["cuotas_canal"] = {
                        "HOME MARKET TRADICIONAL": coff_val,
                        "ON PREMISE": con_val
                    }
                else:
                    self.cfg_sel["cuotas_canal"] = {
                        "OFF": coff_val,
                        "ON": con_val
                    }

            # Estos límites son consumidos directamente por la regla que
            # asigna NO ELEGIBLE REP durante la depuración.
            self.cfg.setdefault("rotacion", {})["cupos_por_gec"] = {
                "ORO": rep_oro,
                "PLATA": rep_plata,
                "BRONCE": rep_bronce,
            }

            # Cuotas adicionales dinámicas (Regiones, Agencias, Subcanales)
            if hasattr(self, "entries_cuotas_adicionales") and self.entries_cuotas_adicionales:
                for (sec_key, k), entry in self.entries_cuotas_adicionales.items():
                    if sec_key not in self.cfg_sel:
                        self.cfg_sel[sec_key] = {}
                    val_str = entry.get().strip()
                    if val_str.isdigit() or (val_str.startswith('-') and val_str[1:].isdigit()):
                        self.cfg_sel[sec_key][k] = int(val_str)
                    else:
                        try:
                            self.cfg_sel[sec_key][k] = float(val_str)
                        except ValueError:
                            pass

            # Guardar Códigos Prioritarios
            if hasattr(self, "entry_codigos_prioritarios") and self.entry_codigos_prioritarios is not None:
                txt_prio = self.entry_codigos_prioritarios.get().strip()
                if txt_prio:
                    parsed_codes = []
                    for item in txt_prio.split(","):
                        item_clean = item.strip()
                        if item_clean:
                            if item_clean.isdigit():
                                parsed_codes.append(int(item_clean))
                            else:
                                parsed_codes.append(item_clean)
                    self.cfg_sel["codigos_prioritarios"] = parsed_codes

        except ValueError as exc:
            detalle = (
                str(exc) if str(exc).startswith(("Los límites REP", "El PXR mínimo"))
                else "Los valores numéricos ingresados no son válidos."
            )
            messagebox.showerror("Error", detalle)
            return

        # Mapeo de columnas
        self.cfg["columna_puente"] = self.cmb_puente.get()
        self.cfg["columna_lat"] = self.cmb_lat.get()
        self.cfg["columna_lon"] = self.cmb_lon.get()
        self.cfg_sel["columna_lat"] = self.cmb_lat.get()
        self.cfg_sel["columna_lon"] = self.cmb_lon.get()
        self.cfg_sel["columna_ruta"] = self.cmb_ruta.get()
        self.cfg_sel["columna_gec"] = self.cmb_gec.get()
        # Depuración aplica la regla NO ELEGIBLE REP, por lo que debe usar la
        # misma columna GEC seleccionada en este formulario.
        self.cfg["columna_gec"] = self.cmb_gec.get()
        self.cfg_sel["columna_canal"] = self.cmb_canal.get()
        self.cfg_sel["columna_fijo"] = self.cmb_fijos.get()
        self.cfg["columna_fijo"] = self.cmb_fijos.get()
        self.cfg_sel["valor_fijo"] = self.cmb_valor_fijo.get()
        self.cfg["valor_fijo"] = self.cmb_valor_fijo.get()
        
        # Persistir a config.json
        guardar_config(self.config_app)
        
        try:
            self.master.master.master.actualizar_info_proyecto()
        except AttributeError:
            pass
            
        messagebox.showinfo("Éxito", "Todos los parámetros han sido guardados correctamente.")



# =============================================================================
# MÓDULOS FUTUROS (marcador de posición)
# =============================================================================
class FramePendiente(ctk.CTkFrame):
    """Pantalla genérica para módulos aún no disponibles."""

    def __init__(self, master, nombre: str) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=nombre,
                     font=ctk.CTkFont(size=26, weight="bold"), text_color=_AZUL
                     ).pack(anchor="w", padx=30, pady=(25, 8))
        ctk.CTkLabel(self, text="Este módulo estará disponible en una próxima "
                                "versión de Planning Tools.",
                     font=ctk.CTkFont(size=15), text_color="#666666"
                     ).pack(anchor="w", padx=30)


# Registro de módulos de la suite: agregar aquí los futuros procesos.
REGISTRO_MODULOS = [
    {"nombre": "Configuración",           "icono": "⚙️", "disponible": True},
    {"nombre": "Depuración de Universo",  "icono": "🧹", "disponible": True},
    {"nombre": "Selección de muestra",    "icono": "📊", "disponible": True},
    {"nombre": "Matriz de distancias",    "icono": "↔️", "disponible": True},
    {"nombre": "Optimización de rutas",   "icono": "📍", "disponible": True},
    {"nombre": "Cruce y generador de polígonos", "icono": "⬡", "disponible": True},
    {"nombre": "Validación de cuotas",    "icono": "✔️", "disponible": False},
    {"nombre": "Control de calidad",      "icono": "🔎", "disponible": False},
    {"nombre": "Dashboard de indicadores","icono": "📈", "disponible": False},
    {"nombre": "Exportación de reportes", "icono": "📦", "disponible": False},
]

# =============================================================================
# VENTANA PRINCIPAL
# =============================================================================
class VentanaPrincipal(ctk.CTk):
    """Ventana raíz con barra lateral de módulos (suite escalable)."""

    def __init__(self, rutas: dict, config: dict) -> None:
        super().__init__()
        self.title(f"{NOMBRE_APP} v{VERSION}")
        self.geometry("1180x820")
        self.minsize(1020, 700)
        self.rutas, self.config_app = rutas, config
        self._frames: dict[str, ctk.CTkFrame] = {}
        self._botones = {}
        self._construir()

    def _construir(self) -> None:
        lateral = ctk.CTkFrame(self, width=260, corner_radius=0, fg_color=_AZUL)
        lateral.pack(side="left", fill="y")
        lateral.pack_propagate(False)
        ruta_logo = ruta_recurso("Config/logo.png")
        if ruta_logo.exists():
            try:
                self.logo_image = ctk.CTkImage(
                    light_image=Image.open(ruta_logo),
                    dark_image=Image.open(ruta_logo),
                    size=(190, 100),
                )
                tarjeta_logo = ctk.CTkFrame(
                    lateral, fg_color="#FFFFFF", corner_radius=10,
                )
                tarjeta_logo.pack(fill="x", padx=22, pady=(24, 10))
                ctk.CTkLabel(
                    tarjeta_logo, text="", image=self.logo_image,
                ).pack(padx=10, pady=8)
            except Exception:
                self.logo_image = None
        if not getattr(self, "logo_image", None):
            ctk.CTkLabel(
                lateral, text="dichter & neira", justify="left",
                font=ctk.CTkFont(size=22, weight="bold"), text_color="white",
            ).pack(anchor="w", padx=24, pady=(28, 10))

        ctk.CTkLabel(lateral, text="PLANNING TOOLS", text_color="white",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", padx=24, pady=(2, 0))
        ctk.CTkLabel(lateral, text="Suite de planeación regional", text_color="#D5E8F7",
                     font=ctk.CTkFont(size=12)).pack(anchor="w", padx=24, pady=(2, 18))

        ctk.CTkFrame(lateral, height=2, fg_color="#4C8FCB").pack(fill="x", padx=20, pady=(6,15))
        self.lbl_proyecto = ctk.CTkLabel(
            lateral, text="Proyecto activo\nSin configurar", justify="left",
            anchor="w", wraplength=210, text_color="white",
            font=ctk.CTkFont(size=13),
        )
        self.lbl_proyecto.pack(fill="x", padx=22, pady=(0,20))

        ctk.CTkLabel(
            lateral, text=f"v{VERSION}", text_color="#D5E8F7",
            font=ctk.CTkFont(size=12),
        ).pack(side="bottom", pady=10)

        menu_lateral = ctk.CTkScrollableFrame(
            lateral, fg_color="transparent", corner_radius=0,
            scrollbar_button_color="#79AEDD",
            scrollbar_button_hover_color="#B7D5EE",
            width=248,
        )
        menu_lateral.pack(fill="both", expand=True, padx=(0, 3), pady=(0, 4))

        # El desplazamiento vive dentro de cada módulo que lo necesita. Usar un
        # CTkScrollableFrame aquí agregaba una segunda barra en el extremo
        # derecho de toda la aplicación.
        self.contenido = ctk.CTkFrame(self, fg_color=_FONDO, corner_radius=0)
        self.contenido.pack(side="left", fill="both", expand=True)

        for mod in REGISTRO_MODULOS:

            nombre = mod["nombre"]
            icono = mod["icono"]
            disponible = mod["disponible"]

            boton = ctk.CTkButton(
                menu_lateral,
                text=f"{icono}  {nombre}" + ("" if disponible else "   🔒"),
                anchor="w", height=38, corner_radius=8,
                fg_color="transparent", hover_color=_AZUL_CLARO,
                text_color="white", font=ctk.CTkFont(size=14),
                command=lambda n=nombre: self.mostrar_modulo(n),
            )

            boton.pack(fill="x", padx=14, pady=3)

            self._botones[nombre] = boton

        self.actualizar_info_proyecto()
        self.mostrar_modulo("Configuración")

    def actualizar_info_proyecto(self):

        pais = self.config_app.get("pais_activo", "Costa Rica")
        cfg = self.config_app["paises"][pais]["modulo_depuracion"]

        def abreviar(nombre: str, limite: int = 27) -> str:
            return nombre if len(nombre) <= limite else nombre[: limite - 1] + "…"

        texto = (
            f"Proyecto activo · {pais}\n"
            f"Universo: {abreviar(cfg['archivo_universo'])}\n"
            f"Incidencias: {abreviar(cfg['archivo_incidencias'])}"
        )

        self.lbl_proyecto.configure(text=texto)

    def mostrar_modulo(self, nombre: str) -> None:
        """Cambia la pantalla activa; instancia los frames bajo demanda."""

        if nombre not in self._frames:
            if nombre == "Configuración":
                frame = FrameConfiguracion(self.contenido, self.rutas, self.config_app)
            elif nombre == "Depuración de Universo":
                frame = FrameDepuracion(self.contenido, self.rutas, self.config_app)
            elif nombre == "Selección de muestra":
                frame = FrameSeleccion(self.contenido, self.rutas, self.config_app)
            elif nombre == "Matriz de distancias":
                frame = FrameMatrizDistancias(self.contenido, self.rutas, self.config_app)
            elif nombre == "Optimización de rutas":
                frame = FrameOptimizacionRutas(self.contenido, self.rutas, self.config_app)
            elif nombre == "Cruce y generador de polígonos":
                frame = FrameCrucePoligonos(self.contenido, self.rutas, self.config_app)
            else:
                frame = FramePendiente(self.contenido, nombre)
            self._frames[nombre] = frame

        for frame in self._frames.values():
            frame.pack_forget()
        self._frames[nombre].pack(fill="both", expand=True)

        for nom, boton in self._botones.items():

            if nom == nombre:
                boton.configure(fg_color="white", text_color=_AZUL,)
            else:
                boton.configure( fg_color="transparent", text_color="white",)

def lanzar_interfaz(rutas: dict, config: dict) -> None:
    """Punto de entrada de la capa de presentación."""
    try:
        VentanaPrincipal(rutas, config).mainloop()
    finally:
        cerrar_descargas_mapas()
