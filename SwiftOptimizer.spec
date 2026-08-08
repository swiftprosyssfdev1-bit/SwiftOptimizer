# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['mysql.connector', 'mysql.connector.plugins', 'mysql.connector.plugins.mysql_native_password', 'mysql.connector.locales', 'mysql.connector.locales.eng', 'mysql.connector.locales.eng.client_error', 'dotenv', 'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets', 'wmi', 'win32com', 'win32com.client', 'win32event', 'win32api', 'win32timezone', 'pythoncom', 'pywintypes']
hiddenimports += collect_submodules('mysql.connector')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets\\logo.png', 'assets'), ('assets\\icon.ico', 'assets'), ('.env', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['django', 'mysql.connector.django', 'mysql.connector.aio', 'jinja2', 'PIL', 'numpy', 'tzdata'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SwiftOptimizer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info_optimizer.txt',
    icon=['assets\\icon.ico'],
)
