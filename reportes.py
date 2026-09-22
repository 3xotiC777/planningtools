# -*- coding: utf-8 -*-
"""
reportes.py — Generación de salidas unificadas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from depuracion import ResultadoDepuracion
from logs import obtener_logger
from utilidades import NOMBRE_APP, VERSION, ruta_recurso, rutas_app

# A4 vertical a 150 dpi.
_A4 = (1240, 1754)
_MARGEN = 90


def generar_salidas(res: ResultadoDepuracion, carpeta_salida: Path) -> list[Path]:
    """Guarda el Excel de depuración y guarda métricas en JSON para el consolidado final."""
    log = obtener_logger()
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    rutas: list[Path] = []
    
    # Obtener carpeta resumenes
    r_app = rutas_app()
    carpeta_resumenes = r_app.get("resumenes", carpeta_salida.parent / "Resumenes")
    carpeta_resumenes.mkdir(parents=True, exist_ok=True)

    pais_clean = getattr(res, "pais_activo", "Pais").replace(" ", "_")
    hoja_nombre = getattr(res, "nombre_hoja", "Universo")

    # 1. Excel de Salida
    nombre_excel = f"Universo_Elegible_{pais_clean}.xlsx"
    ruta_excel = _exportar_excel(res.elegibles, carpeta_salida / nombre_excel, hoja_nombre)
    rutas.append(ruta_excel)

    _exportar_excel(res.elegibles, carpeta_salida / "Universo_Elegible.xlsx", hoja_nombre)

    # 2. Guardar métricas de depuración en JSON en lugar de imprimir PDF
    ruta_json = carpeta_resumenes / f"Metricas_Depuracion_{pais_clean}.json"
    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(res.metricas, f, indent=4, ensure_ascii=False)
    rutas.append(ruta_json)

    log.info("Salidas de depuración guardadas para '%s'", pais_clean)
    return rutas


def generar_salidas_muestra(res, carpeta_salida: Path) -> list[Path]:
    """Exporta un único Excel con Titulares y Suplentes, y genera el PDF Consolidado."""
    log = obtener_logger()
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    rutas_ret: list[Path] = []
    
    # Obtener carpeta resumenes
    r_app = rutas_app()
    carpeta_resumenes = r_app.get("resumenes", carpeta_salida.parent / "Resumenes")
    carpeta_resumenes.mkdir(parents=True, exist_ok=True)
    
    pais_clean = getattr(res, "pais_activo", getattr(res, "pais", "Pais")).replace(" ", "_")

    # 1. Unificar Titulares y Suplentes en un solo DataFrame
    titulares = res.titulares.copy()
    suplentes = res.suplentes.copy()
    
    if "Tipo" not in titulares.columns:
        titulares.insert(0, "Tipo", "TITULAR")
    if "Tipo" not in suplentes.columns:
        suplentes.insert(0, "Tipo", "SUPLENTE")
        
    df_consolidado = pd.concat([titulares, suplentes], ignore_index=True)
    
    # Exportar el Excel consolidado con múltiples hojas (Muestra y Auditoria)
    ruta_excel = carpeta_salida / f"Seleccion_{pais_clean}.xlsx"
    try:
        with pd.ExcelWriter(ruta_excel, engine="openpyxl") as writer:
            df_consolidado.to_excel(writer, sheet_name="Muestra", index=False)
            auditoria = pd.DataFrame(
                {"Indicador": list(res.metricas.keys()), "Valor": list(res.metricas.values())}
            )
            auditoria.to_excel(writer, sheet_name="Auditoria", index=False)
        log.info("Archivo generado: %s (%s registros)", ruta_excel.name, f"{len(df_consolidado):,}")
        rutas_ret.append(ruta_excel)
    except PermissionError as exc:
        raise PermissionError(
            f"No se pudo escribir '{ruta_excel.name}'. Cierre el archivo si lo tiene "
            f"abierto en Excel y vuelva a ejecutar."
        ) from exc

    # Guardar la base completa revisada, incluidos los cambios geográficos y PXR.
    universo_revisado = getattr(res, "universo_revisado", pd.DataFrame())
    if isinstance(universo_revisado, pd.DataFrame) and not universo_revisado.empty:
        rutas_ret.append(
            guardar_revision_geografica(universo_revisado, carpeta_salida, pais_clean)
        )

    # 2. Generar el PDF Consolidado
    metricas_consolidadas = {}
    # Leer el JSON de depuración si existe
    ruta_json = carpeta_resumenes / f"Metricas_Depuracion_{pais_clean}.json"
    if ruta_json.exists():
        try:
            with open(ruta_json, "r", encoding="utf-8") as f:
                metricas_dep = json.load(f)
                metricas_consolidadas.update(metricas_dep)
        except Exception as e:
            log.warning(f"No se pudo leer el JSON de depuración: {e}")
            
    # Mezclar con las métricas de selección
    metricas_consolidadas.update(res.metricas)
    
    # Actualizar res temporalmente
    old_metricas = getattr(res, "metricas", {})
    res.metricas = metricas_consolidadas
    
    # Ensure res has pais_activo if it doesn't
    if not hasattr(res, "pais_activo"):
        res.pais_activo = pais_clean.replace("_", " ")
        
    # Mocking inicio for selection object if missing
    if not hasattr(res, "inicio"):
        from datetime import datetime
        res.inicio = datetime.now()
    
    nombre_pdf = f"Resumen_{pais_clean}.pdf"
    ruta_pdf = _generar_pdf(res, carpeta_resumenes / nombre_pdf)
    rutas_ret.append(ruta_pdf)
    
    # Restaurar
    res.metricas = old_metricas
    
    log.info("Salidas de muestra y resumen consolidado generadas en: %s", carpeta_salida)
    return rutas_ret


def guardar_revision_geografica(
    datos: pd.DataFrame,
    carpeta_salida: Path,
    pais: str,
) -> Path:
    """Persiste la revisión manual para que ningún cambio del mapa se pierda."""
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    pais_clean = str(pais).replace(" ", "_")
    ruta = carpeta_salida / f"Revision_Geografica_{pais_clean}.xlsx"
    return _exportar_excel(datos, ruta, "Revision Geografica")

# ------------------------------------------------------------------ Excel ----
def _exportar_excel(df: pd.DataFrame, ruta: Path, hoja: str) -> Path:
    try:
        df.to_excel(ruta, sheet_name=hoja, index=False, engine="openpyxl")
    except PermissionError as exc:
        raise PermissionError(
            f"No se pudo escribir '{ruta.name}'. Cierre el archivo si lo tiene "
            f"abierto en Excel y vuelva a ejecutar."
        ) from exc
    obtener_logger().info(
        "Archivo generado: %s (%s registros)", ruta.name, f"{len(df):,}"
    )
    return ruta


# -------------------------------------------------------------------- PDF ----
def _fuente(tamano: int, negrita: bool = False) -> ImageFont.FreeTypeFont:
    """Carga una fuente del sistema con degradación elegante."""
    candidatas = (
        ["arialbd.ttf", "DejaVuSans-Bold.ttf"] if negrita
        else ["arial.ttf", "DejaVuSans.ttf"]
    )
    for nombre in candidatas:
        try:
            return ImageFont.truetype(nombre, tamano)
        except OSError:
            continue
    return ImageFont.load_default(size=tamano)


def _generar_pdf(res: ResultadoDepuracion, ruta: Path) -> Path:
    """Resumen ejecutivo paginado, renderizado con Pillow."""
    azul, gris, negro = (18, 60, 105), (110, 110, 110), (25, 25, 25)
    pais = getattr(res, "pais_activo", "País")
    omitir_pdf = {
        "Tipo de cruce", "Fecha y hora de ejecución", "Tiempo total del proceso", "País procesado",
        "PDV por ruta (mín/prom/máx)", "Dispersión: dist. al T más cercano (prom km)", "Semilla aleatoria"
    }
    detalle = [(k, v) for k, v in res.metricas.items() if k not in omitir_pdf]
    paginas: list[Image.Image] = []
    dibujos: list[ImageDraw.ImageDraw] = []

    try:
        logo_base = Image.open(ruta_recurso("Config/logo.png")).convert("RGBA")
        logo_base.thumbnail((190, 106), Image.Resampling.LANCZOS)
    except OSError:
        logo_base = None
        obtener_logger().warning("No se pudo cargar el logo para el resumen PDF.")

    def crear_pagina(primera: bool) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
        img = Image.new("RGB", _A4, "white")
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, _A4[0], 150], fill=azul)
        d.text((_MARGEN, 42), NOMBRE_APP, font=_fuente(44, True), fill="white")
        subtitulo = (
            f"Resumen Ejecutivo - Planeación de Universo ({pais})"
            if primera else f"Detalle del proceso - continuación ({pais})"
        )
        d.text((_MARGEN, 100), subtitulo, font=_fuente(24), fill=(200, 220, 240))
        if logo_base is not None:
            panel = (960, 18, 1160, 132)
            d.rounded_rectangle(panel, radius=12, fill="white")
            x_logo = panel[0] + (panel[2] - panel[0] - logo_base.width) // 2
            y_logo = panel[1] + (panel[3] - panel[1] - logo_base.height) // 2
            img.paste(logo_base, (x_logo, y_logo), logo_base)

        if not primera:
            d.text((_MARGEN, 205), "Detalle del proceso", font=_fuente(28, True), fill=negro)
            return img, d, 265

        y = 210
        d.text(
            (_MARGEN, y), f"Fecha de ejecución: {res.inicio:%d/%m/%Y %H:%M:%S}",
            font=_fuente(22), fill=gris,
        )
        y += 60
        principales = [
            ("Universo", res.metricas.get("Total de registros del universo", 0)),
            ("Excluidas", res.metricas.get("Tiendas excluidas", 0)),
            (
                "Elegibles",
                res.metricas.get(
                    "Tiendas elegibles finales", res.metricas.get("Tiendas elegibles", 0),
                ),
            ),
        ]
        ancho_tarjeta = (_A4[0] - 2 * _MARGEN - 60) // 3
        for i, (titulo, valor) in enumerate(principales):
            x0 = _MARGEN + i * (ancho_tarjeta + 30)
            d.rounded_rectangle(
                [x0, y, x0 + ancho_tarjeta, y + 150],
                radius=14, outline=azul, width=3,
            )
            d.text((x0 + 25, y + 25), titulo, font=_fuente(24), fill=gris)
            valor_txt = f"{valor:,}" if isinstance(valor, (int, float)) else str(valor)
            d.text((x0 + 25, y + 65), valor_txt, font=_fuente(48, True), fill=azul)
        y += 210
        d.text((_MARGEN, y), "Detalle del proceso", font=_fuente(28, True), fill=negro)
        return img, d, y + 55

    img, d, y = crear_pagina(True)
    paginas.append(img)
    dibujos.append(d)
    limite_inferior = _A4[1] - 115
    for k, v in detalle:
        filas_grupo = {
            "Excluidas por Rotación (NO ELEGIBLE REP)": 10,
            "Muestra GEC (titulares)": 4,
        }.get(k, 1)
        if y + (44 * filas_grupo) > limite_inferior:
            img, d, y = crear_pagina(False)
            paginas.append(img)
            dibujos.append(d)
        valor = f"{v:,}" if isinstance(v, int) else str(v)
        d.text((_MARGEN, y), k, font=_fuente(22), fill=negro)
        d.text((_A4[0] - _MARGEN - d.textlength(valor, font=_fuente(22, True)), y),
               valor, font=_fuente(22, True), fill=azul)
        y += 44
        d.line([_MARGEN, y - 8, _A4[0] - _MARGEN, y - 8], fill=(228, 228, 228))

    for numero, d_pagina in enumerate(dibujos, start=1):
        d_pagina.text(
            (_MARGEN, _A4[1] - 70),
            f"Generado automáticamente por {NOMBRE_APP} v{VERSION}",
            font=_fuente(18), fill=gris,
        )
        pagina_txt = f"Página {numero} de {len(paginas)}"
        d_pagina.text(
            (_A4[0] - _MARGEN - d_pagina.textlength(pagina_txt, font=_fuente(18)), _A4[1] - 70),
            pagina_txt, font=_fuente(18), fill=gris,
        )

    paginas[0].save(
        ruta, "PDF", resolution=150.0, save_all=True,
        append_images=paginas[1:],
    )
    obtener_logger().info("Archivo generado: %s", ruta.name)
    return ruta
