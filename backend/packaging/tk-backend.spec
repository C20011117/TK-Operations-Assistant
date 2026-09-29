# PyInstaller 配置：目录模式（onedir），输出 dist/tk-backend/tk-backend.exe
# 用法（在 backend/ 目录）：uv run --group build pyinstaller packaging/tk-backend.spec --noconfirm
# 不用单文件模式：单文件每次启动都要解压到临时目录，启动慢且更容易被杀毒软件误报。
# console=True：由外壳以 CREATE_NO_WINDOW 启动，不会弹出黑窗口，同时保证标准输出可用（就绪行）。

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent  # backend/

hiddenimports = (
    collect_submodules("tk_workspace")
    + collect_submodules("uvicorn")
    + ["keyring.backends.Windows", "tiktoken_ext", "tiktoken_ext.openai_public", "sqlalchemy.dialects.sqlite"]
)

datas = [(str(ROOT / "migrations"), "migrations")]
for dist in ("keyring", "fastapi", "pydantic", "langchain-core", "langchain-openai", "openai"):
    datas += copy_metadata(dist)

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "IPython", "matplotlib", "numpy.tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tk-backend",
    console=True,
    upx=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="tk-backend", upx=False)
