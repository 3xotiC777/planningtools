# -*- mode: python ; coding: utf-8 -*-
# Compilación:  pyinstaller "Planning Tools.spec"
# Genera: dist/Planning Tools.exe  (un solo archivo, sin consola)
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for paquete in ["customtkinter", "ortools"]:
    d, b, h = collect_all(paquete)
    datas += d; binaries += b; hiddenimports += h

datas += [
    ("Config/logo.png", "Config"),
    ("Config/ampliar-pantalla.png", "Config"),
]
hiddenimports += ["geopandas", "pyogrio", "shapely"]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "IPython", "notebook", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    [],
    name="Planning Tools",
    console=False,          # sin ventana de consola (app de escritorio)
    upx=True,
    icon=None,              # opcional: icon="Config/planning_tools.ico"
)
