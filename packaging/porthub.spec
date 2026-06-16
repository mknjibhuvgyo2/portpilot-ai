# PyInstaller spec for PORTHUB — builds a single-file binary that boots the
# FastAPI backend with the bundled Vue frontend.
#   pyinstaller packaging/porthub.spec --noconfirm
# Run on each target OS/arch (PyInstaller does NOT cross-compile).
import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.dirname(os.path.abspath(SPECPATH))  # SPECPATH = .../packaging -> repo root
BACKEND = os.path.join(ROOT, "backend")
FRONTEND_DIST = os.path.join(ROOT, "frontend", "dist")
sys.path.insert(0, BACKEND)  # so collect_submodules("app") can import the package

datas = [(FRONTEND_DIST, os.path.join("frontend", "dist"))]
binaries = []
hiddenimports = []

# uvicorn loads its loops/protocols dynamically — pull them all in.
for pkg in ("uvicorn",):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h

hiddenimports += collect_submodules("app")            # app templates/providers
hiddenimports += [
    "anyio", "httpx", "httpcore", "h11", "websockets", "watchfiles",
    "passlib.handlers.bcrypt", "bcrypt", "jose", "jose.backends",
    "pydantic", "pydantic_settings", "email_validator",
    "sqlalchemy.dialects.sqlite", "pynvml",
]

block_cipher = None

a = Analysis(
    [os.path.join(BACKEND, "launcher.py")],
    pathex=[BACKEND],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy.tests"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="porthub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,            # server app — keep the console for logs
    disable_windowed_traceback=False,
    target_arch=None,        # native arch of the build machine
    codesign_identity=None,
    entitlements_file=None,
)

# macOS: also wrap into a .app bundle.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="PORTHUB.app",
        icon=None,
        bundle_identifier="ai.porthub.app",
        info_plist={
            "CFBundleName": "PORTHUB",
            "CFBundleDisplayName": "PORTHUB",
            "CFBundleShortVersionString": "0.1.2",
            "LSBackgroundOnly": False,
        },
    )
