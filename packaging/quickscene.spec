# PyInstaller spec for the Windows release: python -m PyInstaller packaging/quickscene.spec
# Produces dist/QuickScene/ (QuickScene.exe plus its libraries); see packaging/build_release.ps1

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root)],
    datas=[
        (str(root / "quickscene" / "gui" / "static"), "quickscene/gui/static"),
        (str(root / "config" / "settings.yaml"), "config"),
        *collect_data_files("pyproj"),
    ],
    hiddenimports=collect_submodules("osmium"),
    # tkinter stays in: the UI's "Browse..." buttons open native file dialogs through it
    excludes=["pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="QuickScene", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="QuickScene")
