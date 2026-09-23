"""Pruebas de las cargas LATAM y delimitación por país en la web."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from poligonos import obtener_poligono_delimitacion_muestra
from web_polygons import polygon_record, write_latam_zip, write_sample_gpkg


ROOT = Path(__file__).resolve().parents[1]
FP_CR = ROOT / "Poligonos Muestras" / "DELIMITACION PAISES" / "ICEKO_CR.gpkg"


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class WebPolygonTests(unittest.TestCase):
    def test_latam_zip_accepts_one_complete_shapefile_and_uses_safe_names(self):
        content = make_zip({
            "nested/LATAM DN.shp": b"shp", "nested/LATAM DN.shx": b"shx",
            "nested/LATAM DN.dbf": b"dbf", "nested/LATAM DN.prj": b"prj",
            "../../irrelevant.txt": b"ignored",
        })
        record = polygon_record("latam.zip", content, "latam")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            write_latam_zip(record, target)
            self.assertEqual({p.name for p in target.iterdir()}, {
                "LATAM DN.shp", "LATAM DN.shx", "LATAM DN.dbf", "LATAM DN.prj",
            })
            self.assertEqual((target / "LATAM DN.dbf").read_bytes(), b"dbf")

    def test_latam_zip_rejects_incomplete_or_ambiguous_archive(self):
        incomplete = make_zip({"a.shp": b"x", "a.dbf": b"x"})
        ambiguous = make_zip({f"{stem}{ext}": b"x" for stem in ("one", "two") for ext in (".shp", ".shx", ".dbf")})
        for content in (incomplete, ambiguous, b"not a zip"):
            with self.subTest(content=content[:10]), self.assertRaises(ValueError):
                polygon_record("latam.zip", content, "latam")

    def test_uploaded_gpkg_is_used_for_unmapped_country(self):
        record = polygon_record("mi_delimitacion.gpkg", FP_CR.read_bytes(), "sample")
        with tempfile.TemporaryDirectory() as folder:
            target = write_sample_gpkg(record, Path(folder))
            self.assertEqual(target.name, "mi_delimitacion.gpkg")
            result = obtener_poligono_delimitacion_muestra(Path(folder), "El Salvador", target.name)
            self.assertEqual(len(result), 77)
            with self.assertRaises(ValueError):
                obtener_poligono_delimitacion_muestra(Path(folder), "El Salvador", "../fuera.gpkg")

    def test_gpkg_rejects_bad_file(self):
        with self.assertRaises(ValueError):
            polygon_record("muestra.gpkg", b"not a database", "sample")


if __name__ == "__main__":
    unittest.main()
