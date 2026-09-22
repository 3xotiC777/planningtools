"""Create geometry-only FP polygons from the private, locally synced source.

The source GeoPackages contain customer attributes and must never be committed.
Usage: python tools/create_fp_polygons.py SOURCE_DIRECTORY OUTPUT_DIRECTORY
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

FILES = ("ICEKO_ABVO.gpkg", "ICEKO_CR.gpkg", "ICEKO_EMBOCEN.gpkg", "ICEKO_NI.gpkg")


def geometry_only(source: Path, destination: Path) -> None:
    layers: list[gpd.GeoDataFrame] = []
    for name, kind in pyogrio.list_layers(source):
        if "polygon" not in kind.lower():
            continue
        layer = gpd.read_file(source, layer=name)
        if layer.crs is None:
            raise ValueError(f"Capa sin CRS: {source.name} / {name}")
        if layers and layer.crs != layers[0].crs:
            layer = layer.to_crs(layers[0].crs)
        layers.append(gpd.GeoDataFrame(geometry=layer.geometry.copy(), crs=layer.crs))
    if not layers:
        raise ValueError(f"No hay capas de polígonos en {source}")

    clean = gpd.GeoDataFrame(pd.concat(layers, ignore_index=True), crs=layers[0].crs)
    if clean.geometry.isna().any() or clean.geometry.is_empty.any():
        raise ValueError(f"Geometría vacía en {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    clean.to_file(destination, layer="no_elegible_fp", driver="GPKG", index=False)

    result = gpd.read_file(destination)
    if list(result.columns) != ["geometry"] or len(result) != len(clean):
        raise AssertionError(f"El archivo generado contiene atributos o perdió geometrías: {destination}")
    if not result.geometry.union_all().equals(clean.geometry.union_all()):
        raise AssertionError(f"La geometría cambió durante la limpieza: {destination}")
    print(f"{destination.name}: {len(result)} geometrías, {result.crs}, solo geometría")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    for filename in FILES:
        source = args.source / filename
        if not source.is_file():
            raise FileNotFoundError(source)
        geometry_only(source, args.destination / filename)


if __name__ == "__main__":
    main()
