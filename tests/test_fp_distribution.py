"""Regression tests for private, country-specific FP distribution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from poligonos import obtener_poligono_delimitacion_muestra
from utilidades import rutas_app


class FpDistributionTests(unittest.TestCase):
    def test_only_country_specific_geometry_is_distributed(self) -> None:
        folder = Path(__file__).resolve().parents[1] / "Poligonos Muestras" / "DELIMITACION PAISES"
        expected = {
            "Costa Rica": ("ICEKO_CR.gpkg", 77),
            "Nicaragua": ("ICEKO_NI.gpkg", 152),
            "Guatemala ABVO": ("ICEKO_ABVO.gpkg", 72),
            "Guatemala EMBOCEN": ("ICEKO_EMBOCEN.gpkg", 39),
        }
        self.assertEqual({p.name for p in folder.iterdir()}, {name for name, _ in expected.values()})
        for country, (_, count) in expected.items():
            with self.subTest(country=country):
                polygons = obtener_poligono_delimitacion_muestra(folder, country)
                self.assertEqual(len(polygons), count)
                self.assertEqual(list(polygons.columns), ["geometry"])
                self.assertIsNotNone(polygons.crs)

    def test_missing_required_fp_polygon_is_not_silently_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):
                obtener_poligono_delimitacion_muestra(Path(folder), "Costa Rica")
            self.assertIsNone(obtener_poligono_delimitacion_muestra(Path(folder), "Chile"))

    def test_logs_are_local_while_configuration_stays_synced(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shared = root / "carpeta sincronizada"
            local_logs = root / "logs locales"
            with patch.dict("os.environ", {
                "PLANNING_TOOLS_BASE": str(shared),
                "PLANNING_TOOLS_LOG_DIR": str(local_logs),
            }):
                paths = rutas_app()
            self.assertEqual(paths["config"], shared / "Config")
            self.assertEqual(paths["logs"], local_logs)
            self.assertTrue(local_logs.is_dir())


if __name__ == "__main__":
    unittest.main()
