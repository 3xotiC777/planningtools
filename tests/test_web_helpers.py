from __future__ import annotations

import io
import zipfile

import pandas as pd

from web_app import mask_drawn_area, spreadsheet_bytes, zip_outputs


def test_selection_area_includes_boundary() -> None:
    points = pd.DataFrame({
        "LAT": [0.5, 1.0, 2.0],
        "LON": [0.5, 0.5, 0.5],
    })
    drawings = [{
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        }
    }]
    assert mask_drawn_area(points, "LAT", "LON", drawings).tolist() == [True, True, False]


def test_export_bundle_keeps_workbooks_readable() -> None:
    workbook = spreadsheet_bytes({"Datos": pd.DataFrame({"valor": [1, 2]})})
    bundle = zip_outputs({"Datos.xlsx": workbook})
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        data = pd.read_excel(io.BytesIO(archive.read("Datos.xlsx")))
    assert data["valor"].tolist() == [1, 2]
