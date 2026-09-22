# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace

from modulos_geograficos import MapaInteractivo


class EdicionPoligonosTests(unittest.TestCase):
    def test_detecta_un_clic_dentro_del_poligono(self):
        cuadrado = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        self.assertTrue(MapaInteractivo._punto_en_poligono(5, 5, cuadrado))
        self.assertFalse(MapaInteractivo._punto_en_poligono(15, 5, cuadrado))

    def test_achica_y_mueve_conservando_la_forma(self):
        mapa = SimpleNamespace(_geografica=lambda x, y: (x, y))
        cuadrado = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        resultado = MapaInteractivo._transformar_edicion(
            mapa,
            cuadrado,
            (5, 5),
            factor=0.5,
            delta_x=2,
            delta_y=-1,
        )
        self.assertEqual(
            resultado,
            [(4.5, 1.5), (9.5, 1.5), (9.5, 6.5), (4.5, 6.5), (4.5, 1.5)],
        )


if __name__ == "__main__":
    unittest.main()
