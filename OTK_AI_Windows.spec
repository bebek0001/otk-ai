
# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

project_dir = Path(SPECPATH)

datas = [
    (str(project_dir / "gosts"), "gosts"),
    (str(project_dir / "seed"), "seed"),
]
if (project_dir / "build_info.txt").exists():
    datas.append((str(project_dir / "build_info.txt"), "."))

binaries = []
hiddenimports = []

# CustomTkinter: темы, шрифты и ресурсы
ctk_datas, ctk_binaries, ctk_imports = collect_all(
    "customtkinter"
)

datas += ctk_datas
binaries += ctk_binaries
hiddenimports += ctk_imports

# CAD / 3D и ML помощника технолога (scikit-learn + scipy грузят модули динамически)
for package in ["OCP", "vtkmodules", "sklearn", "scipy"]:
    pkg_datas, pkg_binaries, pkg_imports = collect_all(
        package
    )

    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_imports

a = Analysis(
    ["main.py"],
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OTK AI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OTK AI",
)
