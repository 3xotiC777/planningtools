# -*- coding: utf-8 -*-
"""
proyecto.py

Administrador de la configuración del proyecto.
Toda la aplicación leerá y escribirá aquí.
"""

from pathlib import Path
import json


class Proyecto:

    def __init__(self):

        self.archivo_universo = None
        self.archivo_incidencias = None

        self.hoja_universo = None
        self.hoja_incidencias = None

        self.columnas = {}

    def guardar(self, ruta: Path):

        datos = {
            "archivo_universo": self.archivo_universo,
            "archivo_incidencias": self.archivo_incidencias,
            "hoja_universo": self.hoja_universo,
            "hoja_incidencias": self.hoja_incidencias,
            "columnas": self.columnas,
        }

        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=4, ensure_ascii=False)

    @classmethod
    def cargar(cls, ruta: Path):

        proyecto = cls()

        if not ruta.exists():
            return proyecto

        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)

        proyecto.archivo_universo = datos.get("archivo_universo")
        proyecto.archivo_incidencias = datos.get("archivo_incidencias")

        proyecto.hoja_universo = datos.get("hoja_universo")
        proyecto.hoja_incidencias = datos.get("hoja_incidencias")

        proyecto.columnas = datos.get("columnas", {})

        return proyecto