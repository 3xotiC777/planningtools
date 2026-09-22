# -*- coding: utf-8 -*-
"""
main.py — Punto de entrada de Planning Tools.

Responsabilidad única: inicializar carpetas, configuración y logging, y
lanzar la interfaz gráfica. Cualquier error de arranque se informa mediante
ventana emergente (el usuario final no ve consola).

Compilación (ver README):
    pyinstaller "Planning Tools.spec"
"""

from __future__ import annotations

import sys
import traceback
from tkinter import messagebox

from logs import configurar_logger
from utilidades import NOMBRE_APP, cargar_config, rutas_app


def main() -> int:
    try:
        rutas = rutas_app()
        logger, ruta_log = configurar_logger(rutas["logs"])
        config = cargar_config()
        logger.info("Carpeta base: %s", rutas["base"])
        logger.info("Log de esta sesión: %s", ruta_log.name)

        from interfaz import lanzar_interfaz  # import tardío: acelera arranque
        lanzar_interfaz(rutas, config)
        logger.info("Aplicación cerrada por el usuario.")
        return 0

    except Exception as exc:  # noqa: BLE001 — último recurso antes de cerrar
        detalle = traceback.format_exc()
        try:
            from logs import obtener_logger
            obtener_logger().error("Error fatal de arranque:\n%s", detalle)
        except Exception:
            pass
        try:
            messagebox.showerror(
                NOMBRE_APP,
                f"No fue posible iniciar la aplicación.\n\n{exc}\n\n"
                "Revise la carpeta 'Logs' para más detalle.")
        except Exception:
            print(detalle, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
