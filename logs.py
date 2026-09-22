# -*- coding: utf-8 -*-
"""
logs.py — Sistema de bitácora de Planning Tools.

Responsabilidad única: configurar el logger de la aplicación con salida
simultánea a archivo (carpeta Logs) y consola, incluyendo el usuario de
Windows y metadatos de la ejecución.
"""

from __future__ import annotations

import getpass
import logging
import platform
import sys
from datetime import datetime
from pathlib import Path

from utilidades import NOMBRE_APP, VERSION

_NOMBRE_LOGGER = "planning_tools"


def configurar_logger(carpeta_logs: Path) -> tuple[logging.Logger, Path]:
    """
    Crea el logger de la aplicación. Devuelve (logger, ruta_del_archivo_log).
    Idempotente: si ya está configurado, reutiliza los handlers existentes.
    """
    logger = logging.getLogger(_NOMBRE_LOGGER)
    if logger.handlers:                       # ya configurado
        for h in logger.handlers:
            if isinstance(h, logging.FileHandler):
                return logger, Path(h.baseFilename)

    carpeta_logs.mkdir(parents=True, exist_ok=True)
    ruta_log = carpeta_logs / f"planning_tools_{datetime.now():%Y%m%d_%H%M%S}.log"

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    archivo = logging.FileHandler(ruta_log, encoding="utf-8")
    archivo.setFormatter(formato)
    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)

    logger.setLevel(logging.INFO)
    logger.addHandler(archivo)
    logger.addHandler(consola)

    logger.info("=" * 74)
    logger.info("%s v%s", NOMBRE_APP, VERSION)
    logger.info("Usuario:  %s", getpass.getuser())
    logger.info("Equipo:   %s (%s)", platform.node(), platform.platform())
    logger.info("Python:   %s", platform.python_version())
    logger.info("=" * 74)
    return logger, ruta_log


def obtener_logger() -> logging.Logger:
    """Acceso al logger de la aplicación desde cualquier módulo."""
    return logging.getLogger(_NOMBRE_LOGGER)
