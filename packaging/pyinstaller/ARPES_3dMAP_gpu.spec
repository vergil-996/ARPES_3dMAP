# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

from pathlib import Path

REPO_ROOT = Path(SPECPATH).resolve().parents[1]

datas = []
binaries = []
hiddenimports = ['plugin_api', 'theme', 'ui_controls', 'bandscope.extensions.ui']
hiddenimports += collect_submodules('skimage')
hiddenimports += collect_submodules('pywt')
for package in ('siui', 'pyvista', 'pyvistaqt', 'vtk', 'matplotlib', 'cupy', 'cupyx', 'cuda',
                'cryptography'):
    package_data, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_hiddenimports
# CUDA wheels expose their headers and DLLs through the ``nvidia`` namespace.
# Keep that directory layout so cuda-pathfinder can discover it after freezing.
datas += collect_data_files('nvidia', include_py_files=True)
datas += [(str(REPO_ROOT / 'assets' / 'app.ico'), 'assets')]


def assert_no_extension_sources(analysis):
    """基础安装包不得含有扩展源码。

    扩展以 .bsplugin 单独发布、安装在用户目录；plugins/ 只是打包插件的源。
    整目录收集或误加 hiddenimports 都会把插件代码混进基础包，这里直接让构建
    失败，而不是等到发布后才发现。
    """
    plugin_root = (REPO_ROOT / 'plugins').resolve()
    leaked = sorted(
        name for name, path, _kind in list(analysis.datas) + list(analysis.pure)
        if name == 'plugins' or name.startswith('plugins.')
        or (path and Path(path).resolve().is_relative_to(plugin_root))
    )
    if leaked:
        raise SystemExit(f'Base package must not contain extension sources: {leaked[:5]}')


a = Analysis(
    [str(REPO_ROOT / 'start.py')],
    pathex=[str(REPO_ROOT)],
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
assert_no_extension_sources(a)
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
    icon=[str(REPO_ROOT / 'assets' / 'app.ico')],
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
