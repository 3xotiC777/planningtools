# -*- coding: utf-8 -*-
import unittest

from mapa_base import (
    ajustar_aspecto_mercator,
    desproyectar_mercator,
    desplazar_mercator,
    proyectar_mercator,
    zoom_mercator,
)


class ProyeccionMapaTests(unittest.TestCase):
    def test_proyeccion_y_retorno_conservan_coordenadas(self):
        limites = (-75.5, -73.5, 3.5, 5.5)
        area = (28, 28, 872, 472)
        for lon, lat in [(-74.1, 4.6), (-75.0, 5.0), (-73.7, 3.8)]:
            x, y = proyectar_mercator(lon, lat, limites, area)
            lon_final, lat_final = desproyectar_mercator(x, y, limites, area)
            self.assertAlmostEqual(lon_final, lon, places=8)
            self.assertAlmostEqual(lat_final, lat, places=8)

    def test_zoom_reduce_extension_alrededor_del_centro(self):
        limites = (-75.5, -73.5, 3.5, 5.5)
        acercado = zoom_mercator(limites, 0.5)
        self.assertAlmostEqual(acercado[1] - acercado[0], 1.0, places=8)
        self.assertLess(acercado[3] - acercado[2], limites[3] - limites[2])

    def test_arrastrar_derecha_mueve_la_vista_hacia_occidente(self):
        limites = (-75.5, -73.5, 3.5, 5.5)
        movido = desplazar_mercator(limites, 0.1, 0.0)
        self.assertLess(movido[0], limites[0])
        self.assertLess(movido[1], limites[1])

    def test_ajuste_de_aspecto_evita_deformar_el_mapa(self):
        area = (0, 0, 1200, 400)
        limites = ajustar_aspecto_mercator((-83.0, -77.0, 7.0, 10.0), area)
        x1, y1 = proyectar_mercator(-82.0, 8.0, limites, area)
        x2, _ = proyectar_mercator(-81.0, 8.0, limites, area)
        _, y2 = proyectar_mercator(-82.0, 9.0, limites, area)
        # Cerca del ecuador, un grado vertical y horizontal debe conservar
        # prácticamente la misma escala visual.
        self.assertLess(abs(abs(x2 - x1) - abs(y2 - y1)) / abs(x2 - x1), 0.03)


if __name__ == "__main__":
    unittest.main()
