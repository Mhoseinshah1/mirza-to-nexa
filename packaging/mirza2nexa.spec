# PyInstaller spec — builds a one-folder Windows app (faster start, fewer AV false positives than one-file).
# Build:  pyinstaller --noconfirm --clean packaging/mirza2nexa.spec
from pathlib import Path

ROOT = Path(SPECPATH).parent
STATIC = ROOT / "src" / "mirza2nexa" / "web" / "static"

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(STATIC), "mirza2nexa/web/static")],
    hiddenimports=[
        "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    ],
    excludes=["tkinter", "test", "unittest", "pytest", "playwright"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Mirza2Nexa",
    console=True,  # console window shows the local URL and allows Ctrl+C; closing it stops the server
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Mirza2Nexa", upx=False)
