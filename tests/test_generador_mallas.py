# -*- coding: utf-8 -*-
import math
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, box

from generador_mallas import generar_malla_visitas


class GeneradorMallasTests(unittest.TestCase):
    def test_genera_celdas_metricas_para_pdv_o_zona_urbana_cercana(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            ruta_excel = carpeta / "pdv.xlsx"
            ruta_gpkg = carpeta / "zonas.gpkg"
            ruta_salida = carpeta / "malla.gpkg"

            pdv = pd.DataFrame(
                {
                    "Latitud": [9.0000, 9.0300],
                    "Longitud": [-79.5000, -79.4700],
                    "Nombre": ["A", "B"],
                }
            )
            pdv.to_excel(ruta_excel, index=False)

            centro = gpd.GeoSeries([Point(-79.5, 9.0)], crs="EPSG:4326").to_crs("EPSG:32617").iloc[0]
            poligonos_m = gpd.GeoDataFrame(
                {
                    "Clasificación Por Zona": ["Zona Urbana", "Zona Rural", "Zona Urbana"],
                    "NOMBRE": ["Cercana", "Rural", "Lejana"],
                },
                geometry=[
                    box(centro.x - 450, centro.y - 450, centro.x + 450, centro.y + 450),
                    box(centro.x + 800, centro.y - 300, centro.x + 1400, centro.y + 300),
                    box(centro.x + 30_000, centro.y, centro.x + 30_600, centro.y + 600),
                ],
                crs="EPSG:32617",
            ).to_crs("EPSG:4326")
            poligonos_m.to_file(ruta_gpkg, layer="zonificacion", driver="GPKG", engine="pyogrio")

            resultado = generar_malla_visitas(
                ruta_excel,
                ruta_gpkg,
                ruta_salida,
                hoja="Sheet1",
                columna_latitud="Latitud",
                columna_longitud="Longitud",
                capa="zonificacion",
                tamano_celda_m=200,
                crs_metrico="EPSG:32617",
            )

            self.assertTrue(ruta_salida.is_file())
            self.assertEqual(resultado.poligonos_urbanos_cercanos, 1)
            self.assertEqual(resultado.capa.crs.to_epsg(), 4326)
            self.assertTrue(resultado.capa["ID_CUADRO"].is_unique)
            self.assertTrue(
                (
                    (resultado.capa["PDV_EN_CELDA"] > 0)
                    | (resultado.capa["INTERSECTA_URBANA"] == "SI")
                ).all()
            )

            malla_m = resultado.capa.to_crs("EPSG:32617")
            anchos = malla_m.geometry.bounds.maxx - malla_m.geometry.bounds.minx
            altos = malla_m.geometry.bounds.maxy - malla_m.geometry.bounds.miny
            self.assertTrue(anchos.between(199.999, 200.001).all())
            self.assertTrue(altos.between(199.999, 200.001).all())
            self.assertTrue((resultado.capa["FORMA"] == "CUADRADO").all())
            self.assertTrue((resultado.capa["LADO_M"] == 200).all())

            puntos = gpd.GeoSeries(
                [Point(-79.5, 9.0), Point(-79.47, 9.03)], crs="EPSG:4326"
            ).to_crs("EPSG:32617")
            cobertura = malla_m.geometry.union_all()
            self.assertTrue(all(cobertura.covers(punto) for punto in puntos))

    def test_conserva_la_celda_del_pdv_aunque_no_haya_urbano_en_15_km(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            ruta_excel = carpeta / "pdv.xlsx"
            ruta_gpkg = carpeta / "zonas.gpkg"
            ruta_salida = carpeta / "malla.gpkg"
            pd.DataFrame({"LAT": [9.0], "LON": [-79.5]}).to_excel(ruta_excel, index=False)

            centro = gpd.GeoSeries([Point(-79.5, 9.0)], crs="EPSG:4326").to_crs("EPSG:32617").iloc[0]
            zonas = gpd.GeoDataFrame(
                {"Clasificación Por Zona": ["Zona Urbana"]},
                geometry=[box(centro.x + 40_000, centro.y, centro.x + 40_600, centro.y + 600)],
                crs="EPSG:32617",
            ).to_crs("EPSG:4326")
            zonas.to_file(ruta_gpkg, layer="zonas", driver="GPKG", engine="pyogrio")

            resultado = generar_malla_visitas(
                ruta_excel,
                ruta_gpkg,
                ruta_salida,
                columna_latitud="LAT",
                columna_longitud="LON",
                capa="zonas",
                crs_metrico="EPSG:32617",
            )
            self.assertEqual(resultado.poligonos_urbanos_cercanos, 0)
            self.assertEqual(resultado.celdas, 1)
            self.assertEqual(int(resultado.capa.iloc[0]["PDV_EN_CELDA"]), 1)
            self.assertEqual(resultado.capa.iloc[0]["INTERSECTA_URBANA"], "NO")

    def test_permite_generar_circulos_indicando_el_radio(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            ruta_excel = carpeta / "pdv.xlsx"
            ruta_gpkg = carpeta / "zonas.gpkg"
            ruta_salida = carpeta / "circulos.gpkg"
            pd.DataFrame({"LAT": [9.0], "LON": [-79.5]}).to_excel(ruta_excel, index=False)

            centro = gpd.GeoSeries([Point(-79.5, 9.0)], crs="EPSG:4326").to_crs("EPSG:32617").iloc[0]
            zonas = gpd.GeoDataFrame(
                {"Clasificación Por Zona": ["Zona Urbana"]},
                geometry=[box(centro.x + 40_000, centro.y, centro.x + 40_500, centro.y + 500)],
                crs="EPSG:32617",
            ).to_crs("EPSG:4326")
            zonas.to_file(ruta_gpkg, layer="zonas", driver="GPKG", engine="pyogrio")

            resultado = generar_malla_visitas(
                ruta_excel,
                ruta_gpkg,
                ruta_salida,
                columna_latitud="LAT",
                columna_longitud="LON",
                capa="zonas",
                forma="Círculos",
                tamano_celda_m=200,
                crs_metrico="EPSG:32617",
            )

            self.assertEqual(resultado.forma, "CIRCULO")
            self.assertEqual(resultado.medida_m, 200)
            self.assertTrue((resultado.capa["FORMA"] == "CIRCULO").all())
            self.assertTrue((resultado.capa["RADIO_M"] == 200).all())
            circulo_m = resultado.capa.to_crs("EPSG:32617").geometry.iloc[0]
            self.assertAlmostEqual(circulo_m.area, math.pi * 200**2, delta=math.pi * 200**2 * 0.01)
            self.assertTrue(circulo_m.covers(centro))

    def test_permite_generar_hexagonos_regulares_indicando_el_lado(self):
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            ruta_excel = carpeta / "pdv.xlsx"
            ruta_gpkg = carpeta / "zonas.gpkg"
            ruta_salida = carpeta / "hexagonos.gpkg"
            pd.DataFrame({"LAT": [9.0], "LON": [-79.5]}).to_excel(ruta_excel, index=False)

            centro = gpd.GeoSeries(
                [Point(-79.5, 9.0)], crs="EPSG:4326"
            ).to_crs("EPSG:32617").iloc[0]
            zonas = gpd.GeoDataFrame(
                {"Clasificación Por Zona": ["Zona Urbana"]},
                geometry=[
                    box(
                        centro.x + 40_000,
                        centro.y,
                        centro.x + 40_500,
                        centro.y + 500,
                    )
                ],
                crs="EPSG:32617",
            ).to_crs("EPSG:4326")
            zonas.to_file(ruta_gpkg, layer="zonas", driver="GPKG", engine="pyogrio")

            resultado = generar_malla_visitas(
                ruta_excel,
                ruta_gpkg,
                ruta_salida,
                columna_latitud="LAT",
                columna_longitud="LON",
                capa="zonas",
                forma="Hexágonos",
                tamano_celda_m=200,
                crs_metrico="EPSG:32617",
            )

            self.assertEqual(resultado.forma, "HEXAGONO")
            self.assertEqual(resultado.medida_m, 200)
            self.assertTrue((resultado.capa["FORMA"] == "HEXAGONO").all())
            self.assertTrue((resultado.capa["LADO_M"] == 200).all())
            hexagono_m = resultado.capa.to_crs("EPSG:32617").geometry.iloc[0]
            self.assertEqual(len(hexagono_m.exterior.coords), 7)
            area_esperada = 3 * math.sqrt(3) * 200**2 / 2
            self.assertAlmostEqual(hexagono_m.area, area_esperada, delta=area_esperada * 0.01)
            self.assertTrue(hexagono_m.covers(centro))


if __name__ == "__main__":
    unittest.main()
