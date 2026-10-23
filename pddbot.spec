# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：onedir 模式，产出 dist/pddbot/ 目录（内含 pddbot.exe）。

浏览器内核不打进包里，首次运行由 browser_setup 自动下载到 exe 旁的 ms-playwright，
以免 exe 体积过大、也便于复用。
"""
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("data", "data"), (".env.example", ".")]
binaries = []
hiddenimports = ["pddbot"]

# nicegui 有大量静态资源，playwright 带 driver，都要整包收集
for pkg in ("nicegui", "playwright", "openai", "openpyxl"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("pddbot")

a = Analysis(
    ["main.py"],
    pathex=[],
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
    name="pddbot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 保留控制台窗口，便于看首次下载进度与运行日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pddbot",
)
