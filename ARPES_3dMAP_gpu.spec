# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas = []
binaries = []
hiddenimports = []
hiddenimports += collect_submodules('skimage')
hiddenimports += collect_submodules('pywt')
for package in ('siui', 'pyvista', 'pyvistaqt', 'vtk', 'matplotlib', 'cupy', 'cupyx', 'cuda'):
    package_data, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_hiddenimports
# CUDA wheels expose their headers and DLLs through the ``nvidia`` namespace.
# Keep that directory layout so cuda-pathfinder can discover it after freezing.
datas += collect_data_files('nvidia', include_py_files=True)
datas += [('app.ico', '.')]

a = Analysis(
    ['start.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BandScope_NVIDIA',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BandScope_NVIDIA',
)
