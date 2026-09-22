"""Interfaz web de Planning Tools. Los cálculos usan los motores de escritorio."""

from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

import folium
import pandas as pd
import shapely
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import shape
from shapely.ops import unary_union
from streamlit_folium import st_folium

from depuracion import DepuradorUniverso
from generador_mallas import generar_malla_visitas, listar_capas_gpkg, listar_campos_gpkg
from herramientas_geograficas import (
    cargar_poligonos,
    crear_capa_poligonos,
    cruzar_puntos_poligonos,
    unir_capas_poligonos,
)
from matriz_distancias import (
    calcular_distancias_por_cercania,
    calcular_matriz_distancias,
    exportar_matriz_distancias,
    exportar_ruta_optima,
    ordenar_ruta_por_cercania,
)
from optimizacion_rutas import (
    detectar_columnas_coordenadas,
    ejecutar_qa_vial_mt,
    exportar_planificacion,
    mover_puntos,
    planificar_archivos,
)
from seleccion import SelectorMuestra
from reportes import _generar_pdf


ROOT = Path(__file__).resolve().parent
APP_TITLE = "Planning Tools"
MAX_MAP_POINTS = 2500

st.set_page_config(page_title=APP_TITLE, page_icon="🗺️", layout="wide")
st.markdown(
    """
    <style>
      :root { --azul: #0d5cab; --tinta: #1c293a; }
      .block-container { padding-top: 1.6rem; max-width: 1600px; }
      h1, h2, h3 { color: var(--tinta); letter-spacing: -.025em; }
      [data-testid="stSidebar"] { background: #eef4f9; }
      [data-testid="stMetricValue"] { color: var(--azul); }
      .stButton button[kind="primary"] { background: var(--azul); }
    </style>
    """,
    unsafe_allow_html=True,
)


def configuration() -> dict:
    if "config" not in st.session_state:
        st.session_state.config = json.loads(
            (ROOT / "Config" / "config.json").read_text(encoding="utf-8")
        )
    return st.session_state.config


def save_upload(upload, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    name = Path(upload.name).name
    if name in {"", ".", ".."}:
        raise ValueError("El archivo no tiene un nombre válido.")
    target = folder / name
    target.write_bytes(upload.getvalue())
    return target


def spreadsheet_bytes(frames: dict[str, pd.DataFrame]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, frame in frames.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
    return output.getvalue()


def zip_outputs(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files.items():
            bundle.writestr(Path(name).name, data)
    return output.getvalue()


def download_result(key: str, label: str, filename: str, mime: str) -> None:
    data = st.session_state.get(key)
    if data is not None:
        st.download_button(label, data, file_name=filename, mime=mime, key=f"dl_{key}")


def show_error(exc: Exception) -> None:
    st.error(str(exc))


def progress_callback(bar):
    def update(value: float, message: str) -> None:
        bar.progress(min(1.0, max(0.0, float(value))), text=message)
    return update


def map_points(df: pd.DataFrame, lat_col: str, lon_col: str, *, draw: bool = False, key: str = "map"):
    lat = pd.to_numeric(df[lat_col], errors="coerce")
    lon = pd.to_numeric(df[lon_col], errors="coerce")
    valid = df.loc[lat.between(-90, 90) & lon.between(-180, 180)].copy()
    if valid.empty:
        st.info("No hay coordenadas válidas para mostrar.")
        return {}
    valid = valid.iloc[:: max(1, len(valid) // MAX_MAP_POINTS)].head(MAX_MAP_POINTS)
    center = [
        float(pd.to_numeric(valid[lat_col]).median()),
        float(pd.to_numeric(valid[lon_col]).median()),
    ]
    map_ = folium.Map(location=center, zoom_start=6, tiles="OpenStreetMap", control_scale=True)
    for _, row in valid.iterrows():
        folium.CircleMarker(
            [float(row[lat_col]), float(row[lon_col])],
            radius=3,
            color="#0d5cab",
            fill=True,
            fill_opacity=0.8,
        ).add_to(map_)
    if draw:
        Draw(
            export=False,
            draw_options={"polyline": False, "marker": False, "circlemarker": False},
            edit_options={"edit": True, "remove": True},
        ).add_to(map_)
    st.caption("© OpenStreetMap contributors. El mapa muestra hasta 2.500 puntos; los cálculos usan todos.")
    return st_folium(map_, height=510, width=None, key=key, returned_objects=["all_drawings"])


def polygons_from_drawings(drawings: list[dict]) -> list[dict]:
    result = []
    for index, feature in enumerate(drawings or [], 1):
        geometry = feature.get("geometry") if feature.get("type") == "Feature" else feature
        if not geometry:
            continue
        polygon = shape(geometry)
        if polygon.geom_type != "Polygon":
            continue
        result.append({
            "nombre": f"Área {index}",
            "coordenadas": [(float(x), float(y)) for x, y in polygon.exterior.coords],
        })
    return result


def mask_drawn_area(df: pd.DataFrame, lat_col: str, lon_col: str, drawings: list[dict]) -> pd.Series:
    polygons = [shape(item["geometry"]) for item in drawings if item.get("geometry")]
    if not polygons:
        return pd.Series(False, index=df.index)
    area = unary_union(polygons)
    lat = pd.to_numeric(df[lat_col], errors="coerce")
    lon = pd.to_numeric(df[lon_col], errors="coerce")
    valid = lat.between(-90, 90) & lon.between(-180, 180)
    selected = pd.Series(False, index=df.index)
    if valid.any():
        points = shapely.points(lon.loc[valid].to_numpy(), lat.loc[valid].to_numpy())
        selected.loc[valid] = shapely.covers(area, points)
    return selected


def header() -> None:
    st.title("Planning Tools")
    st.caption("Planeación regional · depuración, muestra y análisis geográfico")


def page_configuration() -> None:
    st.header("Configuración")
    cfg = configuration()
    country = st.selectbox(
        "País activo",
        list(cfg["paises"]),
        index=list(cfg["paises"]).index(cfg["pais_activo"]),
        key="country_select",
    )
    cfg["pais_activo"] = country
    tab_dep, tab_sel, tab_full = st.tabs(["Depuración", "Selección", "Configuración completa"])
    with tab_dep:
        dep = cfg["paises"][country]["modulo_depuracion"]
        dep["hoja_universo"] = st.text_input("Hoja de universo", dep.get("hoja_universo", ""))
        dep["hoja_incidencias"] = st.text_input("Hoja de incidencias", dep.get("hoja_incidencias", ""))
        for key, label in (
            ("llave_universo", "Código de universo"),
            ("columna_puente", "Columna puente"),
            ("llave_incidencias", "Código de incidencias"),
            ("columna_lat", "Latitud"),
            ("columna_lon", "Longitud"),
        ):
            dep[key] = st.text_input(label, dep.get(key, ""), key=f"dep_{key}_{country}")
    with tab_sel:
        sel = cfg["paises"][country]["modulo_seleccion"]
        sel["tamano_muestra"] = st.number_input(
            "Tamaño de muestra", min_value=1, value=int(sel.get("tamano_muestra", 1))
        )
        sel["ratio_suplentes"] = st.number_input(
            "Suplentes por titular", min_value=0, value=int(sel.get("ratio_suplentes", 0))
        )
        for key, label in (
            ("columna_gec", "GEC"),
            ("columna_ruta", "Ruta"),
            ("columna_lat", "Latitud"),
            ("columna_lon", "Longitud"),
        ):
            sel[key] = st.text_input(label, sel.get(key, ""), key=f"sel_{key}_{country}")
    with tab_full:
        raw = st.text_area(
            "Parámetros JSON de esta sesión",
            json.dumps(cfg, ensure_ascii=False, indent=2),
            height=350,
            key=f"config_json_{country}",
        )
        if st.button("Aplicar configuración JSON"):
            try:
                parsed = json.loads(raw)
                if "paises" not in parsed or "pais_activo" not in parsed:
                    raise ValueError("Faltan las claves paises o pais_activo.")
                st.session_state.config = parsed
                st.success("Configuración aplicada en esta sesión.")
            except (ValueError, TypeError) as exc:
                show_error(exc)
    st.download_button(
        "Descargar configuración",
        json.dumps(configuration(), ensure_ascii=False, indent=2).encode("utf-8"),
        "config.json",
        "application/json",
    )


def page_depuracion() -> None:
    st.header("Depuración de universo")
    cfg = configuration()
    country = cfg["pais_activo"]
    st.caption(f"País: {country}")
    universe = st.file_uploader("Universo Excel", type=["xlsx", "xlsm"], key="dep_universe")
    incidents = st.file_uploader("Incidencias Excel", type=["xlsx", "xlsm"], key="dep_incidents")
    fixed = st.file_uploader("Fijos Excel (opcional)", type=["xlsx", "xlsm"], key="dep_fixed")
    country_polygons = st.file_uploader(
        "Polígono LATAM en ZIP con archivos SHP, SHX y DBF (opcional)",
        type=["zip"], key="dep_latam",
    )
    sample_polygons = st.file_uploader(
        "Delimitación de muestra en GeoPackage (opcional)",
        type=["gpkg"], key="dep_sample",
    )
    if st.button("Ejecutar depuración", type="primary", disabled=not (universe and incidents)):
        with tempfile.TemporaryDirectory(prefix="planning-dep-") as temp:
            base = Path(temp)
            input_dir = base / "Entrada Depuracion"
            dep_cfg = copy.deepcopy(cfg["paises"][country]["modulo_depuracion"])
            u_path = save_upload(universe, input_dir)
            i_path = save_upload(incidents, input_dir)
            dep_cfg["archivo_universo"] = u_path.name
            dep_cfg["archivo_incidencias"] = i_path.name
            if fixed:
                f_path = save_upload(fixed, base / "Entrada")
                dep_cfg.setdefault("rotacion", {})["archivo_fijos"] = f_path.name
            if country_polygons:
                with zipfile.ZipFile(io.BytesIO(country_polygons.getvalue())) as archive:
                    allowed = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
                    target = base / "Poligonos Muestras" / "LATAM"
                    target.mkdir(parents=True, exist_ok=True)
                    for item in archive.infolist():
                        if Path(item.filename).suffix.lower() in allowed and not item.is_dir():
                            extension = Path(item.filename).suffix.lower()
                            (target / f"LATAM DN{extension}").write_bytes(archive.read(item))
            if sample_polygons:
                save_upload(sample_polygons, base / "Poligonos Muestras" / "DELIMITACION PAISES")
            bar = st.progress(0, text="Preparando archivos...")
            try:
                result = DepuradorUniverso(input_dir, dep_cfg, cfg).ejecutar(progress_callback(bar))
                files = {
                    "Universo_Elegible.xlsx": spreadsheet_bytes({"Universo": result.elegibles}),
                    "Excluidas.xlsx": spreadsheet_bytes({"Excluidas": result.excluidas}),
                    "Metricas_Depuracion.json": json.dumps(result.metricas, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                }
                if not result.sin_cupo.empty:
                    files["Tiendas_Sin_Cupo.xlsx"] = spreadsheet_bytes({"Sin cupo": result.sin_cupo})
                st.session_state.dep_result = result
                st.session_state.dep_zip = zip_outputs(files)
                st.success(f"Depuración terminada: {len(result.elegibles):,} registros en el universo resultante.")
            except Exception as exc:
                show_error(exc)
    result = st.session_state.get("dep_result")
    if result is not None:
        st.dataframe(result.elegibles.head(500), width="stretch")
        country_cfg = cfg["paises"][country]["modulo_depuracion"]
        lat, lon = country_cfg.get("columna_lat"), country_cfg.get("columna_lon")
        if lat in result.elegibles and lon in result.elegibles:
            map_points(result.elegibles, lat, lon, key="dep_map")
    download_result("dep_zip", "Descargar resultados de depuración", "Depuracion.zip", "application/zip")


def page_selection() -> None:
    st.header("Selección de muestra")
    cfg = configuration()
    country = cfg["pais_activo"]
    uploaded = st.file_uploader("Universo elegible Excel", type=["xlsx", "xlsm"], key="sel_input")
    use_previous = st.session_state.get("dep_result") is not None
    if use_previous:
        st.info("También puedes usar el universo generado en Depuración durante esta sesión.")
    source = st.radio(
        "Origen",
        ["Archivo cargado", "Resultado de depuración"] if use_previous else ["Archivo cargado"],
        horizontal=True,
    )
    source_id = (
        country, source,
        id(st.session_state.dep_result) if source == "Resultado de depuración" else
        (uploaded.name, len(uploaded.getvalue())) if uploaded else None,
    )
    if source_id != st.session_state.get("sel_source_id") and (
        source == "Resultado de depuración" or uploaded
    ):
        try:
            st.session_state.sel_review = (
                st.session_state.dep_result.elegibles.copy()
                if source == "Resultado de depuración"
                else pd.read_excel(io.BytesIO(uploaded.getvalue()))
            )
            st.session_state.sel_source_id = source_id
        except Exception as exc:
            show_error(exc)
    review = st.session_state.get("sel_review")
    if review is not None and source_id == st.session_state.get("sel_source_id"):
        sel_cfg = cfg["paises"][country]["modulo_seleccion"]
        lat, lon = sel_cfg.get("columna_lat"), sel_cfg.get("columna_lon")
        with st.expander("Revisión geográfica", expanded=False):
            if lat in review and lon in review:
                state = map_points(review, lat, lon, draw=True, key="sel_review_map")
                drawings = (state or {}).get("all_drawings", [])
                choice = st.selectbox(
                    "Estado para los puntos dentro del área",
                    ["ELEGIBLE", "NO ELEGIBLE EG", "NO ELEGIBLE INC", "NO ELEGIBLE REP", "NO ELEGIBLE FP"],
                )
                if st.button("Aplicar estado al área", disabled=not drawings):
                    selected = mask_drawn_area(review, lat, lon, drawings)
                    if "ELEGIBLE" not in review:
                        review["ELEGIBLE"] = "ELEGIBLE"
                    review.loc[selected, "ELEGIBLE"] = choice
                    st.session_state.sel_review = review
                    st.success(f"{int(selected.sum()):,} puntos actualizados.")
            else:
                st.warning("Las columnas GPS configuradas no están en el universo.")
            st.dataframe(review.head(500), width="stretch")
            st.download_button(
                "Descargar revisión geográfica",
                spreadsheet_bytes({"Revision Geografica": review}),
                "Revision_Geografica.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    if st.button("Seleccionar muestra", type="primary", disabled=source == "Archivo cargado" and not uploaded):
        try:
            universe = st.session_state.sel_review.copy()
            with tempfile.TemporaryDirectory(prefix="planning-sel-") as temp:
                selector = SelectorMuestra(Path(temp), copy.deepcopy(cfg["paises"][country]["modulo_seleccion"]))
                selector.pais_activo = country
                bar = st.progress(0, text="Preparando muestra...")
                result = selector.ejecutar(progress_callback(bar), universo=universe)
                files = {
                    "Seleccion.xlsx": spreadsheet_bytes({
                        "Titulares": result.titulares,
                        "Suplentes": result.suplentes,
                        "Auditoria": pd.DataFrame(list(result.metricas.items()), columns=["Indicador", "Valor"]),
                    }),
                    "Universo_Revisado.xlsx": spreadsheet_bytes({"Universo": result.universo_revisado}),
                }
                pdf_path = Path(temp) / f"Resumen_{country.replace(' ', '_')}.pdf"
                try:
                    pdf_result = copy.copy(result)
                    previous = st.session_state.get("dep_result")
                    pdf_result.metricas = {
                        **(previous.metricas if previous is not None else {}),
                        **result.metricas,
                    }
                    _generar_pdf(pdf_result, pdf_path)
                    files[pdf_path.name] = pdf_path.read_bytes()
                except Exception as exc:
                    st.warning(f"No se pudo generar el PDF consolidado: {exc}")
                st.session_state.sel_result = result
                st.session_state.sel_zip = zip_outputs(files)
                st.success(f"Selección terminada: {len(result.titulares):,} titulares y {len(result.suplentes):,} suplentes.")
        except Exception as exc:
            show_error(exc)
    result = st.session_state.get("sel_result")
    if result is not None:
        st.dataframe(result.titulares.head(500), width="stretch")
        sel_cfg = cfg["paises"][country]["modulo_seleccion"]
        lat, lon = sel_cfg.get("columna_lat"), sel_cfg.get("columna_lon")
        if lat in result.titulares and lon in result.titulares:
            map_points(result.titulares, lat, lon, key="sel_map")
    download_result("sel_zip", "Descargar selección y auditoría", "Seleccion.zip", "application/zip")


def page_routes() -> None:
    st.header("Optimización de rutas")
    planning_tab, points_tab = st.tabs(["Nueva planificación", "Puntos en Excel"])
    with planning_tab:
        base = st.file_uploader("Base de puntos Excel", type=["xlsx", "xlsm"], key="route_base")
        forecast = st.file_uploader("Forecast mensual Excel", type=["xlsx", "xlsm"], key="route_forecast")
        qa = st.checkbox("Aplicar QA vial automático", value=True)
        if st.button("Planificar rutas", type="primary", disabled=not (base and forecast)):
            try:
                with tempfile.TemporaryDirectory(prefix="planning-route-") as temp:
                    base_path = save_upload(base, Path(temp))
                    forecast_path = save_upload(forecast, Path(temp))
                    bar = st.progress(0, text="Leyendo bases...")
                    st.session_state.route_result = planificar_archivos(
                        base_path, forecast_path, progress_callback(bar), qa_vial_automatico=qa
                    )
                st.success(f"Planificación terminada: {len(st.session_state.route_result.puntos):,} puntos.")
            except Exception as exc:
                show_error(exc)
        result = st.session_state.get("route_result")
        if result is not None:
            rows = pd.DataFrame(
                [
                    {
                        "ID": p.id, "PDV": p.nombre, "MT": p.mt,
                        "Selección": p.seleccion, "Día": p.dia,
                        "Latitud": p.lat, "Longitud": p.lon,
                    }
                    for p in result.puntos
                ]
            )
            st.dataframe(rows, width="stretch")
            if result.avisos:
                with st.expander(f"Avisos ({len(result.avisos)})"):
                    for message in result.avisos:
                        st.write(message)
            if not rows.empty:
                map_points(rows, "Latitud", "Longitud", key="route_map")
                with st.expander("QA vial por territorio"):
                    territories = sorted({p.mt for p in result.puntos})
                    selected_mt = st.selectbox("MT", territories)
                    if st.button("Ejecutar QA vial de este MT"):
                        try:
                            message = ejecutar_qa_vial_mt(result, selected_mt)
                            st.info(message)
                            st.rerun()
                        except Exception as exc:
                            show_error(exc)
                with st.expander("Mover puntos a otro día"):
                    ids = st.multiselect("Puntos", rows["ID"].astype(str).tolist())
                    day = st.number_input("Día nuevo", min_value=1, value=1)
                    mt = st.text_input("MT nuevo (opcional)")
                    if st.button("Aplicar movimiento", disabled=not ids):
                        mover_puntos(result, set(ids), int(day), mt or None)
                        st.success(f"Se movieron {len(ids)} puntos.")
                        st.rerun()
            if base and st.button("Preparar Excel planificado"):
                try:
                    with tempfile.TemporaryDirectory(prefix="planning-export-") as temp:
                        source = save_upload(base, Path(temp))
                        target = Path(temp) / "Planificacion.xlsx"
                        exportar_planificacion(source, target, result)
                        st.session_state.route_xlsx = target.read_bytes()
                except Exception as exc:
                    show_error(exc)
            download_result(
                "route_xlsx", "Descargar planificación", "Planificacion.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    with points_tab:
        uploaded = st.file_uploader("Excel de puntos", type=["xlsx", "xlsm"], key="points_excel")
        if uploaded:
            fingerprint = (uploaded.name, len(uploaded.getvalue()))
            if st.session_state.get("points_fingerprint") != fingerprint:
                st.session_state.points_df = pd.read_excel(io.BytesIO(uploaded.getvalue()))
                st.session_state.points_fingerprint = fingerprint
            df = st.session_state.points_df
            if df.empty:
                st.info("El archivo no contiene filas.")
                return
            try:
                suggested_lat, suggested_lon = detectar_columnas_coordenadas(df)
            except Exception as exc:
                st.warning(str(exc))
                suggested_lat, suggested_lon = str(df.columns[0]), str(df.columns[min(1, len(df.columns)-1)])
            left, right = st.columns(2)
            lat_col = left.selectbox("Latitud", list(df.columns), index=list(df.columns).index(suggested_lat))
            lon_col = right.selectbox("Longitud", list(df.columns), index=list(df.columns).index(suggested_lon))
            filter_col = st.selectbox("Agrupar o filtrar por", ["Todas"] + list(df.columns))
            if filter_col != "Todas":
                choices = df[filter_col].dropna().astype(str).unique().tolist()
                choices = sorted(choices)[:1000]
                selected = st.multiselect("Valores", choices)
                visible = df.loc[df[filter_col].astype(str).isin(selected)] if selected else df
            else:
                visible = df
            st.caption(f"{len(visible):,} filas visibles")
            page = st.number_input("Página de edición (500 filas)", min_value=1, max_value=max(1, (len(visible)+499)//500))
            fragment = visible.iloc[(page-1)*500:page*500].copy()
            edited = st.data_editor(fragment, width="stretch", num_rows="dynamic", key=f"points_editor_{page}")
            if st.button("Guardar edición de esta página"):
                kept = df.drop(index=fragment.index)
                st.session_state.points_df = pd.concat([kept, edited], ignore_index=True)
                st.success("Cambios guardados en esta sesión.")
            new_col = st.text_input("Nueva columna")
            if st.button("Agregar columna", disabled=not new_col):
                if new_col in df:
                    st.warning("La columna ya existe.")
                else:
                    st.session_state.points_df[new_col] = ""
                    st.rerun()
            area_state = map_points(visible, lat_col, lon_col, draw=True, key="points_map")
            if (area_state or {}).get("all_drawings"):
                if st.button("Seleccionar puntos dentro del área"):
                    selected = mask_drawn_area(
                        visible, lat_col, lon_col, area_state["all_drawings"]
                    )
                    st.session_state.points_area = visible.loc[selected].copy()
                    st.success(f"{int(selected.sum()):,} puntos seleccionados.")
            if st.session_state.get("points_area") is not None:
                st.dataframe(st.session_state.points_area.head(500), width="stretch")
                st.download_button(
                    "Descargar puntos del área",
                    spreadsheet_bytes({"Seleccion": st.session_state.points_area}),
                    "Puntos_Area.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            st.download_button(
                "Descargar copia editada",
                spreadsheet_bytes({"Puntos": st.session_state.points_df}),
                "Puntos_Editados.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def polygon_file(upload, folder: Path) -> Path:
    """Guarda capas geográficas sin permitir rutas del ZIP fuera del directorio temporal."""
    if upload.name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(upload.getvalue())) as archive:
            allowed = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
            for member in archive.infolist():
                if member.is_dir() or Path(member.filename).suffix.lower() not in allowed:
                    continue
                (folder / Path(member.filename).name).write_bytes(archive.read(member))
        shapes = list(folder.glob("*.shp"))
        if not shapes:
            raise ValueError("El ZIP no contiene un archivo SHP.")
        return shapes[0]
    return save_upload(upload, folder)


def page_polygons() -> None:
    st.header("Cruce y generador de polígonos")
    crossing_tab, grid_tab = st.tabs(["Cruce y dibujo", "Generador de mallas"])
    with crossing_tab:
        points_file = st.file_uploader("Puntos Excel", type=["xlsx", "xlsm"], key="poly_points")
        layer_file = st.file_uploader(
            "Capa de polígonos (GPKG, GeoJSON, Excel WKT o ZIP de Shapefile)",
            type=["gpkg", "geojson", "json", "xlsx", "zip"], key="poly_layer",
        )
        points = None
        lat_col = lon_col = None
        if points_file:
            try:
                points = pd.read_excel(io.BytesIO(points_file.getvalue()))
                suggested_lat, suggested_lon = detectar_columnas_coordenadas(points)
                left, right = st.columns(2)
                lat_col = left.selectbox("Latitud", list(points.columns), index=list(points.columns).index(suggested_lat), key="poly_lat")
                lon_col = right.selectbox("Longitud", list(points.columns), index=list(points.columns).index(suggested_lon), key="poly_lon")
            except Exception as exc:
                show_error(exc)
        if points is not None and lat_col and lon_col:
            map_state = map_points(points, lat_col, lon_col, draw=True, key="poly_draw_map")
        else:
            blank = folium.Map(location=[8.5, -79.5], zoom_start=4, tiles="OpenStreetMap")
            Draw(export=False, draw_options={"polyline": False, "marker": False, "circlemarker": False},
                 edit_options={"edit": True, "remove": True}).add_to(blank)
            map_state = st_folium(blank, height=510, width=None, key="poly_blank_map", returned_objects=["all_drawings"])
        drawings = polygons_from_drawings((map_state or {}).get("all_drawings", []))
        st.caption(f"Áreas dibujadas: {len(drawings)}")
        if st.button("Cruzar puntos con polígonos", type="primary", disabled=points is None or (layer_file is None and not drawings)):
            try:
                with tempfile.TemporaryDirectory(prefix="planning-poly-") as temp:
                    folder = Path(temp)
                    loaded = cargar_poligonos(polygon_file(layer_file, folder)) if layer_file else None
                    generated = crear_capa_poligonos(drawings) if drawings else None
                    combined = unir_capas_poligonos(loaded, drawings) if loaded is not None else generated
                    crossed = cruzar_puntos_poligonos(points, lat_col, lon_col, combined)
                    st.session_state.poly_crossed = crossed
                    st.session_state.poly_layer_result = combined
                    st.session_state.poly_excel = spreadsheet_bytes({"Cruce": crossed})
                    st.success(f"Cruce completado: {(crossed['DENTRO_POLIGONO'] == 'SI').sum():,} puntos dentro de polígonos.")
            except Exception as exc:
                show_error(exc)
        crossed = st.session_state.get("poly_crossed")
        if crossed is not None:
            st.dataframe(crossed.head(500), width="stretch")
        download_result(
            "poly_excel", "Descargar cruce", "Cruce_Poligonos.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        layer_result = st.session_state.get("poly_layer_result")
        if layer_result is not None:
            st.download_button(
                "Descargar polígonos GeoJSON",
                layer_result.to_json().encode("utf-8"),
                "Poligonos.geojson",
                "application/geo+json",
            )
    with grid_tab:
        grid_points = st.file_uploader("Puntos de venta Excel", type=["xlsx", "xlsm"], key="grid_points")
        grid_gpkg = st.file_uploader("GeoPackage nacional", type=["gpkg"], key="grid_gpkg")
        shape_type = st.selectbox("Forma", ["Cuadrados", "Hexágonos", "Círculos"])
        size = st.number_input("Lado o radio en metros", min_value=1.0, value=300.0)
        buffer_km = st.number_input("Distancia a zona urbana en km", min_value=0.0, value=15.0)
        sheet = layer = zone_col = lat_col = lon_col = None
        if grid_points:
            book = pd.ExcelFile(io.BytesIO(grid_points.getvalue()))
            sheet = st.selectbox("Hoja de puntos", book.sheet_names)
            columns = list(pd.read_excel(book, sheet_name=sheet, nrows=0).columns)
            lat_col = st.selectbox("Columna latitud", columns, index=next((i for i,c in enumerate(columns) if "LAT" in str(c).upper()), 0))
            lon_col = st.selectbox("Columna longitud", columns, index=next((i for i,c in enumerate(columns) if "LON" in str(c).upper()), 0))
        if grid_gpkg:
            with tempfile.TemporaryDirectory(prefix="planning-layers-") as temp:
                path = save_upload(grid_gpkg, Path(temp))
                layers = listar_capas_gpkg(path)
                if layers:
                    layer = st.selectbox("Capa geográfica", layers)
                    fields = listar_campos_gpkg(path, layer)
                    zone_col = st.selectbox("Campo de clasificación urbana", fields)
        urban_value = st.text_input("Valor de zona urbana", "Zona Urbana")
        if st.button("Generar malla", type="primary", disabled=not (grid_points and grid_gpkg and layer and zone_col)):
            try:
                with tempfile.TemporaryDirectory(prefix="planning-grid-") as temp:
                    folder = Path(temp)
                    xlsx_path = save_upload(grid_points, folder)
                    gpkg_path = save_upload(grid_gpkg, folder)
                    target = folder / "Malla_Visitas.gpkg"
                    result = generar_malla_visitas(
                        xlsx_path, gpkg_path, target, hoja=sheet,
                        columna_latitud=lat_col, columna_longitud=lon_col,
                        capa=layer, columna_zona=zone_col, valor_zona=urban_value,
                        tamano_celda_m=float(size), forma=shape_type, buffer_km=float(buffer_km),
                    )
                    st.session_state.grid_result = result
                    st.session_state.grid_gpkg_bytes = target.read_bytes()
                    st.success("Malla generada.")
            except Exception as exc:
                show_error(exc)
        download_result("grid_gpkg_bytes", "Descargar malla GeoPackage", "Malla_Visitas.gpkg", "application/geopackage+sqlite3")


def excel_columns(upload, sheet: str) -> list[str]:
    return [str(col) for col in pd.read_excel(io.BytesIO(upload.getvalue()), sheet_name=sheet, nrows=0).columns]


def column_picker(label: str, columns: list[str], hint: str, key: str) -> str:
    index = next((i for i, col in enumerate(columns) if hint in col.upper()), 0)
    return st.selectbox(label, columns, index=index, key=key)


def page_distances() -> None:
    st.header("Matriz de distancias")
    mode = st.radio(
        "Cálculo",
        ["Por columna puente", "Por cercanía", "Ruta ordenada"],
        horizontal=True,
    )
    origin = st.file_uploader("Primer Excel", type=["xlsx", "xlsm"], key="dist_origin")
    reference = None
    if mode != "Ruta ordenada":
        reference = st.file_uploader("Segundo Excel", type=["xlsx", "xlsm"], key="dist_reference")
    if not origin:
        return
    origin_sheets = pd.ExcelFile(io.BytesIO(origin.getvalue())).sheet_names
    sheet_origin = st.selectbox("Hoja del primer Excel", origin_sheets)
    origin_cols = excel_columns(origin, sheet_origin)
    if not origin_cols:
        st.warning("El primer Excel no tiene columnas.")
        return
    col_lat_o = column_picker("Latitud del primer Excel", origin_cols, "LAT", "dist_lat_o")
    col_lon_o = column_picker("Longitud del primer Excel", origin_cols, "LON", "dist_lon_o")
    keep_origin = st.multiselect("Columnas del primer Excel en la salida", origin_cols, default=origin_cols[: min(6, len(origin_cols))])
    if mode == "Ruta ordenada":
        group = column_picker("Columna de ruta o grupo", origin_cols, "RUTA", "dist_group")
        sheet_reference = reference_cols = None
        col_lat_r = col_lon_r = bridge_o = bridge_r = keep_reference = None
    elif reference:
        ref_sheets = pd.ExcelFile(io.BytesIO(reference.getvalue())).sheet_names
        sheet_reference = st.selectbox("Hoja del segundo Excel", ref_sheets)
        reference_cols = excel_columns(reference, sheet_reference)
        if not reference_cols:
            st.warning("El segundo Excel no tiene columnas.")
            return
        col_lat_r = column_picker("Latitud del segundo Excel", reference_cols, "LAT", "dist_lat_r")
        col_lon_r = column_picker("Longitud del segundo Excel", reference_cols, "LON", "dist_lon_r")
        keep_reference = st.multiselect("Columnas del segundo Excel en la salida", reference_cols, default=reference_cols[: min(6, len(reference_cols))])
        bridge_o = bridge_r = None
        if mode == "Por columna puente":
            bridge_o = column_picker("Código puente del primero", origin_cols, "COD", "dist_bridge_o")
            bridge_r = column_picker("Código puente del segundo", reference_cols, "COD", "dist_bridge_r")
    else:
        st.info("Carga el segundo Excel para configurar el cálculo.")
        return
    if st.button("Calcular distancias", type="primary"):
        try:
            with tempfile.TemporaryDirectory(prefix="planning-distance-") as temp:
                folder = Path(temp)
                origin_path = save_upload(origin, folder)
                if mode == "Ruta ordenada":
                    data = ordenar_ruta_por_cercania(
                        origin_path, hoja=sheet_origin, columna_reinicio=group,
                        columna_latitud=col_lat_o, columna_longitud=col_lon_o,
                        columnas_salida=keep_origin,
                    )
                    target = folder / "Orden_Ruta.xlsx"
                    exportar_ruta_optima(target, data)
                    name = target.name
                else:
                    ref_path = save_upload(reference, folder)
                    if mode == "Por columna puente":
                        data = calcular_matriz_distancias(
                            origin_path, ref_path, hoja_origen=sheet_origin,
                            hoja_destino=sheet_reference, puente_origen=bridge_o,
                            puente_destino=bridge_r, latitud_origen=col_lat_o,
                            longitud_origen=col_lon_o, latitud_destino=col_lat_r,
                            longitud_destino=col_lon_r, columnas_origen=keep_origin,
                            columnas_destino=keep_reference,
                        )
                    else:
                        data = calcular_distancias_por_cercania(
                            origin_path, ref_path, hoja_analisis=sheet_origin,
                            hoja_referencia=sheet_reference, latitud_analisis=col_lat_o,
                            longitud_analisis=col_lon_o, latitud_referencia=col_lat_r,
                            longitud_referencia=col_lon_r, columnas_analisis=keep_origin,
                            columnas_referencia=keep_reference,
                        )
                    target = folder / "Comparativa_Distancias_GPS.xlsx"
                    exportar_matriz_distancias(target, data)
                    name = target.name
                st.session_state.distance_df = data
                st.session_state.distance_xlsx = target.read_bytes()
                st.session_state.distance_filename = name
                st.success(f"Cálculo terminado: {len(data):,} filas.")
        except Exception as exc:
            show_error(exc)
    if st.session_state.get("distance_df") is not None:
        st.dataframe(st.session_state.distance_df.head(1000), width="stretch")
    download_result(
        "distance_xlsx", "Descargar resultado",
        st.session_state.get("distance_filename", "Distancias.xlsx"),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def main() -> None:
    header()
    cfg = configuration()
    with st.sidebar:
        st.image(str(ROOT / "Config" / "logo.png"), width=150)
        st.markdown("### Planeación regional")
        page = st.radio(
            "Módulo",
            [
                "Configuración",
                "Depuración de universo",
                "Selección de muestra",
                "Optimización de rutas",
                "Cruce y generador de polígonos",
                "Matriz de distancias",
            ],
            label_visibility="collapsed",
        )
        st.divider()
        st.caption(f"País activo: {cfg['pais_activo']}")
        st.caption("Los archivos se procesan durante esta sesión y no se publican en GitHub.")
    pages = {
        "Configuración": page_configuration,
        "Depuración de universo": page_depuracion,
        "Selección de muestra": page_selection,
        "Optimización de rutas": page_routes,
        "Cruce y generador de polígonos": page_polygons,
        "Matriz de distancias": page_distances,
    }
    pages[page]()


if __name__ == "__main__":
    main()
