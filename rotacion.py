# -*- coding: utf-8 -*-
"""
rotacion.py — Etapa de elegibilidad por rotación de GEC.

Responsabilidad única: calcular, para cada tienda del universo ya depurado,
cuántas visitas de su cupo anual le quedan disponibles y marcar como no
elegibles las que ya lo agotaron.

Conceptos (del negocio):
    - Cupo anual por GEC (si no son fijos): Oro=6, Plata=4, Bronce=2 visitas.
    - A_Actual: veces que el PDV fue ejecutado en el año en curso.
    - A_Total : veces ejecutado en todo el estudio (control de fatiga).
    - Fijos   : tiendas que se visitan siempre; no aplican cupo. Se cargan
      desde un listado opcional (Entrada/Fijos.xlsx) por CÓDIGO o Codigo DN.
    - Cupo_Restante = cupo_GEC - A_Actual. Elegible si > 0 o si es fija.
      Además sirve como peso de priorización en la selección de muestra.

Todos los parámetros son configurables por país en config.json ("rotacion").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from lector_excel import leer_hoja
from logs import obtener_logger
from normalizacion import normalizar_llave

CONFIG_ROTACION_DEFECTO: dict = {
    "activa": True,
    "cupos_por_gec": {"ORO": 6, "PLATA": 4, "BRONCE": 2},
    "cupo_por_defecto": 2,          # GEC no reconocido -> el cupo más restrictivo
    "columna_gec": "GEC",
    "archivo_fijos": "Fijos.xlsx",  # opcional; si no existe, no hay fijos
    "hoja_fijos": 0,                 # primera hoja
}


@dataclass
class ResultadoRotacion:
    """Salida de la etapa de rotación."""
    universo: pd.DataFrame = field(default_factory=pd.DataFrame)   # con columnas nuevas
    sin_cupo: pd.DataFrame = field(default_factory=pd.DataFrame)   # excluidas por rotación
    metricas: dict = field(default_factory=dict)


class EvaluadorRotacion:
    """Aplica la regla de cupo anual por GEC sobre el universo depurado."""

    def __init__(self, carpeta_entrada: Path, cfg_rotacion: dict) -> None:
        self.entrada = carpeta_entrada
        self.cfg = {**CONFIG_ROTACION_DEFECTO, **(cfg_rotacion or {})}
        self.log = obtener_logger()

    # ------------------------------------------------------------------ API --
    def aplicar(
        self, universo: pd.DataFrame, incidencias: pd.DataFrame
    ) -> ResultadoRotacion:
        """
        universo:    DataFrame ya depurado, con columnas '_CRUCE' y GEC.
        incidencias: DataFrame de incidencias del estudio/país con '_CRUCE',
                     'A_Actual' y 'A_Total' (una fila por PDV, ya deduplicado).
        """
        cfg, log = self.cfg, self.log
        res = ResultadoRotacion()

        if not cfg.get("activa", True):
            log.info("Rotación: etapa desactivada en configuración.")
            res.universo = universo
            return res

        col_gec = cfg["columna_gec"]
        if col_gec not in universo.columns:
            log.warning(
                "Rotación: no existe la columna '%s' en el universo; "
                "la etapa se omite.", col_gec,
            )
            res.universo = universo
            return res

        # 1) Traer A_Actual / A_Total al universo (PDV sin registro -> 0) -----
        ejecuciones = (
            incidencias[["_CRUCE", "A_Actual", "A_Total"]]
            .drop_duplicates("_CRUCE")
        )
        uni = universo.merge(ejecuciones, on="_CRUCE", how="left")
        uni["A_Actual"] = pd.to_numeric(uni["A_Actual"], errors="coerce").fillna(0).astype(int)
        uni["A_Total"] = pd.to_numeric(uni["A_Total"], errors="coerce").fillna(0).astype(int)

        # 2) Cupo anual según GEC --------------------------------------------
        cupos = {str(k).upper(): int(v) for k, v in cfg["cupos_por_gec"].items()}
        gec_norm = normalizar_llave(uni[col_gec])
        uni["Cupo_Anual"] = gec_norm.map(cupos).fillna(int(cfg["cupo_por_defecto"])).astype(int)
        gec_desconocidos = int((~gec_norm.isin(cupos)).sum())
        if gec_desconocidos:
            log.warning(
                "Rotación: %s tiendas con GEC fuera de %s; se les asignó el "
                "cupo por defecto (%s).",
                f"{gec_desconocidos:,}", list(cupos), cfg["cupo_por_defecto"],
            )

        # 3) Fijos (listado opcional) ----------------------------------------
        uni["Es_Fijo"] = self._marcar_fijos(uni)

        # 4) Cupo restante y elegibilidad ------------------------------------
        uni["Cupo_Restante"] = (uni["Cupo_Anual"] - uni["A_Actual"]).clip(lower=0)
        con_cupo = (uni["Cupo_Anual"] - uni["A_Actual"]) > 0
        elegible = con_cupo | uni["Es_Fijo"]

        res.sin_cupo = uni[~elegible].copy()
        res.sin_cupo["Motivo de Exclusión"] = (
            "Rotación agotada: A_Actual >= cupo anual del GEC"
        )
        res.universo = uni[elegible].reset_index(drop=True)

        # 5) Métricas ---------------------------------------------------------
        res.metricas = {
            "Rotación - tiendas fijas": int(uni["Es_Fijo"].sum()),
            "Rotación - excluidas por cupo agotado": len(res.sin_cupo),
            "Rotación - elegibles con cupo": len(res.universo),
            "Rotación - GEC sin cupo definido": gec_desconocidos,
        }
        log.info("Rotación aplicada (cupos %s):", cupos)
        for k, v in res.metricas.items():
            log.info("  %s: %s", k, f"{v:,}")
        return res

    # ----------------------------------------------------------- internos ----
    def _marcar_fijos(self, uni: pd.DataFrame) -> pd.Series:
        """
        Marca fijos si existe Entrada/<archivo_fijos>. El listado puede traer
        los códigos en cualquier columna cuyo nombre contenga 'COD' (CÓDIGO,
        Codigo DN, Cod Cliente...); se normalizan y comparan contra la llave
        del universo y contra la llave de cruce.
        """
        ruta = self.entrada / self.cfg["archivo_fijos"]
        if not ruta.exists():
            self.log.info(
                "Rotación: no hay listado de fijos ('%s' no existe); "
                "ninguna tienda se marca como fija.", ruta.name,
            )
            return pd.Series(False, index=uni.index)

        fijos = leer_hoja(ruta, self._nombre_hoja(ruta), "Fijos")
        cols_codigo = [c for c in fijos.columns
                       if isinstance(c, str) and "COD" in c.upper()]
        if not cols_codigo:
            self.log.warning(
                "Rotación: '%s' no tiene columnas de código (que contengan "
                "'COD'); se ignora el listado.", ruta.name,
            )
            return pd.Series(False, index=uni.index)

        llaves_fijas: set[str] = set()
        for c in cols_codigo:
            llaves_fijas |= set(normalizar_llave(fijos[c])) - {""}

        marca = uni["_CRUCE"].isin(llaves_fijas)
        if "_LLAVE" in uni.columns:
            marca |= uni["_LLAVE"].isin(llaves_fijas)
        self.log.info(
            "Rotación: %s tiendas marcadas como fijas desde '%s' "
            "(columnas %s).", f"{int(marca.sum()):,}", ruta.name, cols_codigo,
        )
        return marca

    def _nombre_hoja(self, ruta: Path) -> str:
        """Resuelve la hoja de fijos: índice 0 -> primera hoja del libro."""
        hoja = self.cfg["hoja_fijos"]
        if isinstance(hoja, int):
            from lector_excel import listar_hojas
            return listar_hojas(ruta)[hoja]
        return str(hoja)
