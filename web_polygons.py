"""Validación y preparación de polígonos cargados para una depuración web."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path, PureWindowsPath


_SHAPE_PARTS = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
_REQUIRED_PARTS = {".shp", ".shx", ".dbf"}
_MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024


def _safe_name(name: str, suffix: str) -> str:
    clean = PureWindowsPath(str(name)).name
    if not clean or clean in {".", ".."} or Path(clean).suffix.lower() != suffix:
        raise ValueError(f"Seleccione un archivo {suffix} válido.")
    return clean


def _latam_members(content: bytes) -> dict[str, zipfile.ZipInfo]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            groups: dict[str, dict[str, zipfile.ZipInfo]] = {}
            total = 0
            for member in archive.infolist():
                if member.is_dir():
                    continue
                filename = PureWindowsPath(member.filename).name
                extension = Path(filename).suffix.lower()
                if extension not in _SHAPE_PARTS:
                    continue
                total += member.file_size
                if total > _MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("El ZIP LATAM supera 250 MB al descomprimirlo.")
                stem = Path(filename).stem.casefold()
                group = groups.setdefault(stem, {})
                if extension in group:
                    raise ValueError("El ZIP LATAM contiene componentes duplicados.")
                group[extension] = member
            valid = {stem: parts for stem, parts in groups.items() if _REQUIRED_PARTS <= parts.keys()}
            if not valid:
                raise ValueError("El ZIP LATAM debe incluir SHP, SHX y DBF del mismo polígono.")
            if "latam dn" in valid:
                return valid["latam dn"]
            if len(valid) != 1:
                raise ValueError("Hay varios Shapefiles en el ZIP LATAM; deje solo uno o incluya LATAM DN.")
            return next(iter(valid.values()))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ValueError("No se pudo abrir el ZIP de polígonos LATAM.") from exc


def polygon_record(name: str, content: bytes, kind: str) -> dict:
    """Valida la estructura del archivo antes de conservarlo en la sesión."""
    if kind == "latam":
        clean = _safe_name(name, ".zip")
        _latam_members(content)
    elif kind == "sample":
        clean = _safe_name(name, ".gpkg")
        if not content.startswith(b"SQLite format 3\x00"):
            raise ValueError("El archivo de delimitación no es un GeoPackage válido.")
    else:
        raise ValueError("Tipo de polígono no reconocido.")
    return {
        "name": clean,
        "content": content,
        "fingerprint": hashlib.sha256(content).hexdigest(),
    }


def write_latam_zip(record: dict, folder: Path) -> None:
    """Extrae únicamente el Shapefile elegido, siempre con nombres conocidos."""
    members = _latam_members(record["content"])
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(record["content"])) as archive:
        for extension, member in members.items():
            (folder / f"LATAM DN{extension}").write_bytes(archive.read(member))


def write_sample_gpkg(record: dict, folder: Path) -> Path:
    name = _safe_name(record["name"], ".gpkg")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    target.write_bytes(record["content"])
    return target
