# -*- coding: utf-8 -*-
"""
depuracion.py — Módulo de negocio: Depuración de Universo.

Responsabilidad única: orquestar el pipeline de depuración
(leer -> validar -> normalizar -> cruzar -> join inteligente -> validación GIS LATAM & Delimitaciones -> resultados)
y devolver un objeto ResultadoDepuracion con los DataFrames y métricas.
"""

from __future__ import annotations

import time
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from lector_excel import ErrorLectura, leer_hoja, listar_archivos_excel, listar_hojas
from logs import obtener_logger
from normalizacion import (
    extraer_anio,
    generar_codigo_puente,
    llave_sufijo,
    normalizar_llave,
)
from poligonos import (
    extraer_contornos_mapa,
    obtener_poligono_delimitacion_muestra,
    obtener_poligono_pais_latam,
    validar_coordenadas_en_poligono,
)
from validaciones import (
    ErrorValidacion,
    ResultadoCalidadLlave,
    evaluar_calidad_llave,
    resolver_columna,
    validar_archivo_y_hoja,
    validar_columnas,
)

ProgresoCallback = Callable[[float, str], None]


@dataclass
class ResultadoDepuracion:
    """Contenedor de resultados y métricas del proceso."""
    elegibles: pd.DataFrame = field(default_factory=pd.DataFrame)
    excluidas: pd.DataFrame = field(default_factory=pd.DataFrame)
    sin_cupo: pd.DataFrame = field(default_factory=pd.DataFrame)
    metricas: dict = field(default_factory=dict)
    inicio: datetime = field(default_factory=datetime.now)
    duracion_seg: float = 0.0
    pais_activo: str = "Costa Rica"
    nombre_hoja: str = "BASE"
    contornos_pais: list[list[tuple[float, float]]] = field(default_factory=list)
    contornos_muestra: list[list[tuple[float, float]]] = field(default_factory=list)


class DepuradorUniverso:
    """Pipeline de depuración inteligente del universo de clientes contra incidencias y polígonos GIS."""

    def __init__(self, carpeta_entrada: Path, config_modulo: dict, config_global: dict | None = None) -> None:
        self.entrada = carpeta_entrada
        self.cfg = config_modulo
        self.config_global = config_global or {}
        self.pais_activo = (
            self.config_global.get("pais_activo")
            or self.cfg.get("pais_activo")
            or "Nicaragua"
        )
        self.log = obtener_logger()

    # ------------------------------------------------------------------ API --
    def ejecutar(self, progreso: ProgresoCallback | None = None) -> ResultadoDepuracion:
        """Ejecuta el pipeline de depuración inteligente y devuelve el resultado."""
        avisar = progreso or (lambda p, m: None)
        t0 = time.perf_counter()
        res = ResultadoDepuracion()
        res.pais_activo = self.pais_activo
        cfg = self.cfg

        # Cargar Polígonos GIS: 1) Frontera LATAM (NAME_0), 2) Delimitación Muestra
        base_app = self.entrada.parent
        carpeta_latam = base_app / "Poligonos Muestras" / "LATAM"
        # El paquete local/ejecutable lleva solo las geometrías FP, nunca los
        # atributos de clientes presentes en los GeoPackages originales.
        raiz_codigo = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        carpeta_delim = raiz_codigo / "Poligonos Muestras" / "DELIMITACION PAISES"
        if not carpeta_delim.is_dir():
            carpeta_delim = base_app / "Poligonos Muestras" / "DELIMITACION PAISES"

        poligono_latam_gdf = obtener_poligono_pais_latam(carpeta_latam, self.pais_activo)
        poligono_delim_gdf = obtener_poligono_delimitacion_muestra(carpeta_delim, self.pais_activo)
        res.contornos_pais = extraer_contornos_mapa(poligono_latam_gdf)
        res.contornos_muestra = extraer_contornos_mapa(poligono_delim_gdf)

        # 1) Búsqueda inteligente de archivos ---------------------------------
        avisar(0.05, f"Buscando archivos de entrada dinámicamente para {self.pais_activo}...")
        ruta_uni = self._encontrar_archivo_universo()
        ruta_inc = self._encontrar_archivo_incidencias()

        hoja_uni = cfg.get("hoja_universo", "BASE PDV")
        hoja_inc = cfg.get("hoja_incidencias", "Export")
        res.nombre_hoja = hoja_uni

        validar_archivo_y_hoja(ruta_uni, hoja_uni, "Universo")
        validar_archivo_y_hoja(ruta_inc, hoja_inc, "Incidencias")

        # 2) Lectura de datos -------------------------------------------------
        avisar(0.10, f"Leyendo universo desde '{ruta_uni.name}' (Hoja: {hoja_uni})...")
        universo = leer_hoja(ruta_uni, hoja_uni, "Universo")
        
        avisar(0.35, f"Leyendo incidencias desde '{ruta_inc.name}' (Hoja: {hoja_inc})...")
        incidencias = leer_hoja(ruta_inc, hoja_inc, "Incidencias")

        total_universo, total_incidencias = len(universo), len(incidencias)

        # 3) Resolución dinámica de columnas ---------------------------------
        col_codigo = resolver_columna(universo, cfg.get("llave_universo", "CÓDIGO"), "CÓDIGO")
        col_fijo = resolver_columna(universo, cfg.get("columna_fijo", "Cliente Fijo"))
        col_gec = resolver_columna(universo, cfg.get("columna_gec", "GEC"), "GEC")
        col_lat = resolver_columna(universo, cfg.get("columna_lat", "Latitud"), "Latitud")
        col_lon = resolver_columna(universo, cfg.get("columna_lon", "Longitud"), "Longitud")
        mod_sel = {}
        if self.config_global and self.pais_activo in self.config_global.get("paises", {}):
            mod_sel = self.config_global["paises"][self.pais_activo].get("modulo_seleccion", {})
        col_canal = resolver_columna(
            universo,
            mod_sel.get("columna_canal", cfg.get("columna_canal", "Canal")),
        )

        avisar(0.50, "Validando columnas requeridas...")
        validar_columnas(universo, [col_codigo], "Universo")
        validar_columnas(
            incidencias,
            [cfg["llave_incidencias"], "Estatus", "Comentario", "UFA", "Estudio_pais"],
            "Incidencias",
        )

        # Identificar clientes fijos
        if col_fijo and col_fijo in universo.columns:
            val_fijo = str(cfg.get("valor_fijo", "")).strip().upper()
            if not val_fijo: val_fijo = "SI"
            universo["_ES_FIJO"] = normalizar_llave(universo[col_fijo]) == val_fijo
        else:
            universo["_ES_FIJO"] = False

        # Códigos prioritarios por país (ej. Guatemala ABVO)
        cfg_app = self.config_global
        if cfg_app and "paises" in cfg_app and self.pais_activo in cfg_app["paises"]:
            codigos_prio = mod_sel.get("codigos_prioritarios")
            if codigos_prio:
                s_cod = normalizar_llave(universo[col_codigo])
                set_prio = {str(x).strip() for x in codigos_prio}
                es_prio = s_cod.isin(set_prio)
                universo["_ES_FIJO"] = universo["_ES_FIJO"] | es_prio
                self.log.info("Depuración (%s): %s tiendas marcadas como Fijo/Protegido por Códigos Prioritarios.", self.pais_activo, int(es_prio.sum()))

        total_fijos = int(universo["_ES_FIJO"].sum())
        self.log.info("Clientes fijos (protegidos) identificados: %s", f"{total_fijos:,}")

        # 4) Preparar llaves de cruce -----------------------------------------
        avisar(0.60, "Normalizando llaves de cruce...")
        universo["_LLAVE"] = normalizar_llave(universo[col_codigo])
        incidencias["_LLAVE"] = normalizar_llave(incidencias[cfg["llave_incidencias"]])

        calidad_uni = evaluar_calidad_llave(universo["_LLAVE"], f"Universo.{col_codigo}")
        calidad_inc = evaluar_calidad_llave(incidencias["_LLAVE"], "Incidencia.Id_PDV")

        tipo_cruce = self._preparar_llaves_cruce(universo, incidencias, col_codigo)

        # 5) Filtrar incidencias por Estudio País -----------------------------
        avisar(0.70, f"Filtrando incidencias para el estudio: {cfg['reglas']['estudio_pais']}...")
        inc_estudio = self._filtrar_incidencias_estudio(incidencias)

        # 6) Join Inteligente: Unir columnas de Incidencias al Universo -------
        avisar(0.80, "Realizando Join de Incidencias sobre el Universo...")
        cols_inc_traer = [
            "_CRUCE", "Visitas", "A_Total", "ENV_Total", "I_Total", "NC_Total",
            "A_Actual", "ENV_Actual", "I_Actual", "NC_Actual", "UFA", "UFI",
            "% Aprobacion", "% Incidencia", "Estatus", "Comentario"
        ]
        cols_disponibles = [c for c in cols_inc_traer if c in inc_estudio.columns or c == "_CRUCE"]
        
        inc_sub = inc_estudio[cols_disponibles].drop_duplicates("_CRUCE", keep="first")
        
        # Eliminar columnas duplicadas si ya existen en universo antes del merge
        cols_conflicto = [c for c in cols_disponibles if c != "_CRUCE" and c in universo.columns]
        if cols_conflicto:
            universo = universo.drop(columns=cols_conflicto)

        universo_joined = universo.merge(inc_sub, on="_CRUCE", how="left")

        # Rellenar nulos para campos numéricos y de estado
        num_cols = ["Visitas", "A_Total", "ENV_Total", "I_Total", "NC_Total", "A_Actual", "ENV_Actual", "I_Actual", "NC_Actual", "% Aprobacion", "% Incidencia"]
        for nc in num_cols:
            if nc in universo_joined.columns:
                universo_joined[nc] = pd.to_numeric(universo_joined[nc], errors="coerce").fillna(0)

        if "Estatus" in universo_joined.columns:
            universo_joined["Estatus"] = universo_joined["Estatus"].fillna("Sin Visita")
        if "Comentario" in universo_joined.columns:
            universo_joined["Comentario"] = universo_joined["Comentario"].fillna("Elegible")

        # 7) Validación de Coordenadas contra Polígonos LATAM y Delimitación --
        avisar(0.83, f"Validando coordenadas contra polígonos GIS ({self.pais_activo})...")
        universo_joined["_DENTRO_POLIGONO_PAIS"] = validar_coordenadas_en_poligono(
            universo_joined, col_lat, col_lon, poligono_latam_gdf
        )
        universo_joined["_DENTRO_DELIMITACION_MUESTRA"] = validar_coordenadas_en_poligono(
            universo_joined, col_lat, col_lon, poligono_delim_gdf
        )

        # 8) Calcular columna de ELEGIBLE según jerarquía exacta -------------
        avisar(0.85, "Evaluando reglas de elegibilidad (fijos, EG, INC, REP, FP)...")
        universo_joined["ELEGIBLE"] = self._evaluar_elegibilidad(universo_joined, col_gec)

        # Conteo de exclusiones por tipo
        mask_eg = universo_joined["ELEGIBLE"] == "NO ELEGIBLE EG"
        mask_inc = (universo_joined["ELEGIBLE"] == "NO ELEGIBLE INC") | (universo_joined["ELEGIBLE"] == "No Elegible INC")
        mask_rep = (universo_joined["ELEGIBLE"] == "NO ELEGIBLE REP") | (universo_joined["ELEGIBLE"] == "No Elegible Rep")
        mask_fp = universo_joined["ELEGIBLE"] == "NO ELEGIBLE FP"
        mask_no_elegible = (universo_joined["ELEGIBLE"] == "No Elegible") | (universo_joined["ELEGIBLE"] == "NO ELEGIBLE")
        
        encontradas_eg = int(mask_eg.sum())
        encontradas_inc = int((mask_inc | mask_no_elegible).sum())
        encontradas_rep = int(mask_rep.sum())
        encontradas_fp = int(mask_fp.sum())
        total_excluidas = encontradas_eg + encontradas_inc + encontradas_rep + encontradas_fp

        # Desglose de repeticiones por GEC
        gec_norm = normalizar_llave(universo_joined[col_gec]) if col_gec in universo_joined.columns else pd.Series([""] * len(universo_joined))
        excl_oro = int((mask_rep & (gec_norm == "ORO")).sum())
        excl_plata = int((mask_rep & (gec_norm == "PLATA")).sum())
        excl_bronce = int((mask_rep & (gec_norm == "BRONCE")).sum())

        canal_norm = (
            normalizar_llave(universo_joined[col_canal])
            if col_canal and col_canal in universo_joined.columns
            else pd.Series("", index=universo_joined.index)
        )
        canal_on = canal_norm.str.contains(r"\bON\b", regex=True, na=False)
        canal_off = (
            canal_norm.str.contains(r"\bOFF\b", regex=True, na=False)
            | canal_norm.str.contains("HOME MARKET", regex=False, na=False)
        )

        def desglose(gec: str, canal: pd.Series) -> int:
            return int((mask_rep & (gec_norm == gec) & canal).sum())

        # Limpieza de columnas técnicas
        cols_limpiar = [c for c in ("_LLAVE", "_CRUCE", "_ES_FIJO", "_DENTRO_POLIGONO_PAIS", "_DENTRO_DELIMITACION_MUESTRA") if c in universo_joined.columns]
        res.elegibles = universo_joined.drop(columns=cols_limpiar)
        res.excluidas = universo_joined[universo_joined["ELEGIBLE"] != "ELEGIBLE"].drop(columns=cols_limpiar)

        # 9) Métricas del Resumen del Proceso ---------------------------------
        res.duracion_seg = time.perf_counter() - t0
        res.metricas = {
            "País procesado": self.pais_activo,
            "Total de registros del universo": total_universo,
            "Clientes fijos (protegidos)": total_fijos,
            "Total de incidencias leídas": total_incidencias,
            "Incidencias del país": len(inc_estudio),
            "Tiendas excluidas": total_excluidas,
            "Excluidas Geográficas País (NO ELEGIBLE EG)": encontradas_eg,
            "Excluidas por Incidencias (NO ELEGIBLE INC)": encontradas_inc,
            "Excluidas por Rotación (NO ELEGIBLE REP)": encontradas_rep,
            "  - Excluidas ORO": excl_oro,
            "      · ORO / Canal ON": desglose("ORO", canal_on),
            "      · ORO / Canal OFF": desglose("ORO", canal_off),
            "  - Excluidas PLATA": excl_plata,
            "      · PLATA / Canal ON": desglose("PLATA", canal_on),
            "      · PLATA / Canal OFF": desglose("PLATA", canal_off),
            "  - Excluidas BRONCE": excl_bronce,
            "      · BRONCE / Canal ON": desglose("BRONCE", canal_on),
            "      · BRONCE / Canal OFF": desglose("BRONCE", canal_off),
            "Excluidas Fuera Polígono Muestra (NO ELEGIBLE FP)": encontradas_fp,
            "Tiendas elegibles": int((universo_joined["ELEGIBLE"] == "ELEGIBLE").sum()),
            "Tiendas elegibles finales": int((universo_joined["ELEGIBLE"] == "ELEGIBLE").sum()),
            "Tipo de cruce": tipo_cruce,
            "Fecha y hora de ejecución": f"{res.inicio:%Y-%m-%d %H:%M:%S}",
            "Tiempo total del proceso": f"{res.duracion_seg:.1f} segundos",
        }
        avisar(0.95, "Proceso finalizado con éxito. Generando salidas...")
        return res

    def aplicar_revision_geografica(
        self,
        res: ResultadoDepuracion,
        datos_revisados: pd.DataFrame,
    ) -> ResultadoDepuracion:
        """Aplica los cambios manuales y la regla PXR antes de exportar."""
        out = datos_revisados.copy()
        if "ELEGIBLE" not in out.columns:
            out["ELEGIBLE"] = "ELEGIBLE"

        mod_sel = self.config_global.get("paises", {}).get(
            self.pais_activo, {},
        ).get("modulo_seleccion", {})
        candidatos_ruta = [
            mod_sel.get("columna_ruta"), "Ruta", "RUTA", "Ruta Venta",
            "RUTA PREVENTA", "RutaEmbotellador",
        ]
        candidatos_codigo = [
            self.cfg.get("columna_puente"), self.cfg.get("llave_universo"),
            "Codigo D&N", "Codigo DN", "CÓDIGO", "CODIGO", "Código",
            "RefID", "RefIDEmbotellador", "RefIDBase (d&n)",
            "ID cliente/PDV", "NSR Client ID",
        ]
        col_ruta = resolver_columna(out, [c for c in candidatos_ruta if c])
        col_codigo = resolver_columna(out, [c for c in candidatos_codigo if c])
        if not col_ruta:
            raise ErrorValidacion(
                "No se encontró la columna de Ruta necesaria para calcular PXR. "
                "Revise el mapeo de columnas en Configuración."
            )
        if not col_codigo:
            raise ErrorValidacion(
                "No se encontró la columna de Código necesaria para calcular PXR. "
                "Revise el Código Puente en Configuración."
            )

        try:
            minimo = max(1, int(self.cfg.get("pxr_minimo", mod_sel.get("pxr_minimo", 10))))
        except (TypeError, ValueError):
            minimo = 10

        estado_antes = normalizar_llave(out["ELEGIBLE"])
        elegibles_antes = estado_antes == "ELEGIBLE"
        agrupador = out[col_ruta].fillna("(SIN RUTA)").astype(str)

        # El tamaño de cada ruta se calcula sobre el universo que sobrevivió
        # a la revisión del mapa. Las tiendas ya excluidas no inflan el PXR.
        out["PXR"] = pd.Series(0, index=out.index, dtype="Int64")
        if elegibles_antes.any():
            conteos = out.loc[elegibles_antes].groupby(
                agrupador.loc[elegibles_antes], dropna=False
            )[col_codigo].transform("count")
            out.loc[elegibles_antes, "PXR"] = conteos.astype("Int64")

        mask_pxr = elegibles_antes & (out["PXR"] < minimo)
        out.loc[mask_pxr, "ELEGIBLE"] = f"NO ELEGIBLE PXR <{minimo}"
        estado_final = normalizar_llave(out["ELEGIBLE"])

        res.elegibles = out
        res.excluidas = out.loc[estado_final != "ELEGIBLE"].copy()
        total_elegibles = int((estado_final == "ELEGIBLE").sum())
        total_excluidas = int((estado_final != "ELEGIBLE").sum())
        res.metricas["Tiendas excluidas"] = total_excluidas
        res.metricas["Excluidas Geográficas País (NO ELEGIBLE EG)"] = int(
            (estado_final == "NO ELEGIBLE EG").sum()
        )
        res.metricas[f"Excluidas por PXR (NO ELEGIBLE PXR <{minimo})"] = int(mask_pxr.sum())
        res.metricas["PXR mínimo configurado"] = minimo
        res.metricas["Tiendas elegibles"] = total_elegibles
        res.metricas["Tiendas elegibles finales"] = total_elegibles
        self.log.info(
            "Revisión geográfica (%s): %s cambios PXR; %s elegibles finales.",
            self.pais_activo, f"{int(mask_pxr.sum()):,}", f"{total_elegibles:,}",
        )
        return res

    # ----------------------------------------------------------- internos ----
    def _encontrar_archivo_universo(self) -> Path:
        """Encuentra inteligentemente el archivo del Universo en Entrada."""
        nom_cfg = self.cfg.get("archivo_universo", "Universo.xlsx")
        ruta_directa = self.entrada / nom_cfg
        if ruta_directa.exists():
            return ruta_directa

        archivos = listar_archivos_excel(self.entrada)
        if not archivos:
            return ruta_directa

        cand = next((a for a in archivos if "universo" in a.lower() or "legible" in a.lower() or "elegible" in a.lower()), archivos[0])
        return self.entrada / cand

    def _encontrar_archivo_incidencias(self) -> Path:
        """Encuentra inteligentemente el archivo de Incidencias en Entrada."""
        nom_cfg = self.cfg.get("archivo_incidencias", "Incidencia.xlsx")
        ruta_directa = self.entrada / nom_cfg
        if ruta_directa.exists():
            return ruta_directa

        archivos = listar_archivos_excel(self.entrada)
        if not archivos:
            return ruta_directa

        cand = next((a for a in archivos if any(p in a.lower() for p in ["incidencia", "export"])), archivos[0])
        return self.entrada / cand

    def _preparar_llaves_cruce(
        self, universo: pd.DataFrame, incidencias: pd.DataFrame, col_codigo: str
    ) -> str:
        """Define la llave técnica de cruce ('_CRUCE')."""
        puente = resolver_columna(universo, self.cfg.get("columna_puente", "Codigo DN"))
        prefijo = self.cfg.get("prefijo_codigo_puente")
        prefijos_por_longitud = self.cfg.get("prefijos_codigo_puente_por_longitud") or {}
        prefijo_defecto = self.cfg.get("prefijo_codigo_puente_defecto")
        n_sufijo = int(self.cfg.get("n_digitos_cruce", 7))

        if puente and puente in universo.columns:
            universo["_CRUCE"] = normalizar_llave(universo[puente])
            incidencias["_CRUCE"] = incidencias["_LLAVE"]
            tipo = f"Exacto: {self.cfg['llave_incidencias']} <-> {puente}"
        elif prefijo or prefijos_por_longitud:
            nom_puente = self.cfg.get("columna_puente", "Codigo D&N")
            codigo_gen = generar_codigo_puente(
                universo[col_codigo],
                prefijo=prefijo,
                n_digitos=n_sufijo,
                prefijos_por_longitud=prefijos_por_longitud,
                prefijo_defecto=prefijo_defecto,
            )
            universo[nom_puente] = codigo_gen
            universo["_CRUCE"] = normalizar_llave(codigo_gen)
            incidencias["_CRUCE"] = incidencias["_LLAVE"]
            if prefijos_por_longitud:
                detalle = ", ".join(f"largo {k} → {v}" for k, v in prefijos_por_longitud.items())
                detalle += f", resto → {prefijo_defecto}"
            else:
                detalle = f"prefijo {prefijo}"
            tipo = f"Calculado ({detalle}): {self.cfg['llave_incidencias']} <-> {nom_puente}"
        else:
            n = int(self.cfg.get("n_digitos_cruce", 8))
            universo["_CRUCE"] = llave_sufijo(universo["_LLAVE"], n)
            incidencias["_CRUCE"] = llave_sufijo(incidencias["_LLAVE"], n)
            tipo = f"Flexible: últimos {n} dígitos"

        self.log.info("Tipo de cruce: %s", tipo)
        return tipo

    def _filtrar_incidencias_estudio(self, incidencias: pd.DataFrame) -> pd.DataFrame:
        """Filtra las incidencias por el Estudio País del objetivo."""
        r = self.cfg.get("reglas", {})
        estudio_target = r.get("estudio_pais", "")
        if not estudio_target or "Estudio_pais" not in incidencias.columns:
            return incidencias.copy()

        estudio_norm = normalizar_llave(incidencias["Estudio_pais"])
        objetivo_norm = normalizar_llave(pd.Series([estudio_target]))[0]
        
        filtradas = incidencias[estudio_norm == objetivo_norm].copy()
        if len(filtradas) == 0:
            self.log.warning("No se encontraron incidencias exactas para '%s'; usando todas.", estudio_target)
            return incidencias.copy()
        return filtradas

    def _evaluar_elegibilidad(self, df: pd.DataFrame, col_gec: str) -> pd.Series:
        """
        Evalúa las reglas de elegibilidad según el orden estricto solicitado:
        1. ELEGIBLE (Clientes fijos)
        2. NO ELEGIBLE EG (Fuera de Frontera LATAM NAME_0)
        3. NO ELEGIBLE INC (Incidencias de Auditoría)
        4. NO ELEGIBLE REP (Cupo Anual GEC Agotado)
        5. NO ELEGIBLE FP (Fuera de Polígono de Delimitación Muestra)
        6. ELEGIBLE (Pasa todas las validaciones)
        """
        r_cupos_original = self.cfg.get("rotacion", {}).get("cupos_por_gec", {"ORO": 6, "PLATA": 4, "BRONCE": 2})
        r_cupos = {normalizar_llave(pd.Series([clave])).iloc[0]: valor for clave, valor in r_cupos_original.items()}
        cupo_oro = float(r_cupos.get("ORO", 5))
        cupo_plata = float(r_cupos.get("PLATA", 3))
        cupo_bronce = float(r_cupos.get("BRONCE", 2))

        indice = df.index

        def numerica(nombre: str) -> pd.Series:
            if nombre not in df.columns:
                return pd.Series(0.0, index=indice)
            directa = pd.to_numeric(df[nombre], errors="coerce")
            texto = df[nombre].map(lambda valor: "" if pd.isna(valor) else str(valor).strip())
            alternativa = pd.to_numeric(texto.str.replace(",", ".", regex=False), errors="coerce")
            return directa.fillna(alternativa).fillna(0)

        def booleana(nombre: str, defecto: bool) -> pd.Series:
            if nombre not in df.columns:
                return pd.Series(defecto, index=indice, dtype=bool)
            return df[nombre].fillna(defecto).astype(bool)

        # La asignación por máscaras conserva exactamente la prioridad de la
        # versión fila-a-fila, evitando ``DataFrame.apply`` en universos grandes.
        resultado = pd.Series("ELEGIBLE", index=indice, dtype="object")
        es_fijo = booleana("_ES_FIJO", False)
        env_total = numerica("ENV_Total")

        resultado.loc[es_fijo & (env_total > 0)] = "NO ELEGIBLE INC"
        pendientes = ~es_fijo

        fuera_pais = ~booleana("_DENTRO_POLIGONO_PAIS", True)
        mascara = pendientes & fuera_pais
        resultado.loc[mascara] = "NO ELEGIBLE EG"
        pendientes &= ~mascara

        nc_actual = numerica("NC_Actual")
        i_total = numerica("I_Total")
        estatus = normalizar_llave(df["Estatus"]) if "Estatus" in df.columns else pd.Series("", index=indice)
        comentario = normalizar_llave(df["Comentario"]) if "Comentario" in df.columns else pd.Series("", index=indice)
        incidencia = (env_total > 0) | (
            ((nc_actual > 2) | (i_total > 2) | (comentario == "NO ELEGIBLE"))
            & estatus.isin(["ULTIMA INCIDENCIA", "SIN VISITA"])
        )
        mascara = pendientes & incidencia
        resultado.loc[mascara] = "NO ELEGIBLE INC"
        pendientes &= ~mascara

        gec_texto = normalizar_llave(df[col_gec]) if col_gec in df.columns else pd.Series("", index=indice)
        gec_limpio = gec_texto.str.replace(r"[^A-Z]+", " ", regex=True).str.strip()
        gec = pd.Series("", index=indice, dtype="object")
        for categoria in ("ORO", "PLATA", "BRONCE"):
            coincide = gec_limpio.str.contains(rf"(?:^| ){categoria}(?: |$)", regex=True, na=False)
            gec.loc[coincide] = categoria
        a_actual = numerica("A_Actual")
        repeticion = (
            ((gec == "ORO") & (a_actual >= cupo_oro))
            | ((gec == "PLATA") & (a_actual >= cupo_plata))
            | ((gec == "BRONCE") & (a_actual >= cupo_bronce))
        )
        mascara = pendientes & repeticion
        resultado.loc[mascara] = "NO ELEGIBLE REP"
        pendientes &= ~mascara

        fuera_muestra = ~booleana("_DENTRO_DELIMITACION_MUESTRA", True)
        resultado.loc[pendientes & fuera_muestra] = "NO ELEGIBLE FP"
        return resultado
