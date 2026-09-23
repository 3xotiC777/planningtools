"""Configuración compartida temporal del servidor web.

Render Free no conserva el sistema de archivos entre reinicios o despliegues.
No se almacenan los Excel de los usuarios en este archivo.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

_LOCK = threading.Lock()
STORE_PATH = Path(os.environ.get(
    "PLANNINGTOOLS_CONFIG_STORE",
    str(Path(tempfile.gettempdir()) / "planningtools-shared-config.json"),
))


def _decode(data: bytes) -> dict:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("paises"), dict):
        raise ValueError("La configuración del servidor no tiene países válidos.")
    if value.get("pais_activo") not in value["paises"]:
        raise ValueError("El país activo no existe en la configuración.")
    return value


def load_configuration(default_path: Path, store_path: Path = STORE_PATH) -> tuple[dict, str]:
    source = store_path if store_path.is_file() else default_path
    data = source.read_bytes()
    return _decode(data), hashlib.sha256(data).hexdigest()


def save_configuration(
    config: dict, expected_revision: str, default_path: Path,
    store_path: Path = STORE_PATH,
) -> str:
    _decode(json.dumps(config, ensure_ascii=False).encode("utf-8"))
    with _LOCK:
        _, current_revision = load_configuration(default_path, store_path)
        if current_revision != expected_revision:
            raise ValueError(
                "Otra persona guardó cambios en el servidor. Recargue la configuración antes de volver a guardar."
            )
        data = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
        store_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".planningtools-", suffix=".tmp",
            dir=store_path.parent, delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            try:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
        os.replace(temp_path, store_path)
        return hashlib.sha256(data).hexdigest()
