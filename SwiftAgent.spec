# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['mysql.connector', 'mysql.connector.plugins', 'mysql.connector.plugins.mysql_native_password', 'mysql.connector.locales', 'mysql.connector.locales.eng', 'mysql.connector.locales.eng.client_error', 'wmi', 'win32com', 'win32com.client', 'win32event', 'win32api', 'win32service', 'win32serviceutil', 'win32timezone', 'servicemanager', 'winerror', 'pythoncom']
hiddenimports += collect_submodules('mysql.connector')


a = Analysis(
    ['agent\\swift_agent_service.py'],
    pathex=['.'],
    binaries=[],
    datas=[('agent\\agent_config.txt', '.'), ('assets\\icon.ico', 'assets')],
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
    name='SwiftAgent',
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
    version='agent\\version_info_agent.txt',
    icon=['assets\\icon.ico'],
)
