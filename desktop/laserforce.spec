# ANG-09 spike: PyInstaller one-folder build of the Desktop App.
# Build from the repo root, after `npm --prefix frontend run build`:
#   <desktop venv>\Scripts\pyinstaller desktop\laserforce.spec --noconfirm
# Output: dist\LaserforceSimulator\LaserforceSimulator.exe
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = SPECPATH + "\\.."
PROJECT = ROOT + "\\laserforce_simulator"
# collect_submodules imports each package in an isolated subprocess started
# before this file runs: it sees neither pathex nor env set here. Build with
#   PYTHONPATH=<repo>\laserforce_simulator
#   DJANGO_SETTINGS_MODULE=laserforce_simulator.settings  SECRET_KEY=build
# in the environment (matches.simulation reads settings on import).

# Django loads apps, middleware, URLconfs, migrations, renderers and Celery
# transports by dotted string, which PyInstaller's import scan cannot see.
hidden = []
for package in (
    "laserforce_simulator",
    "accounts",
    "teams",
    "matches",
    "core",
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "corsheaders",
    "whitenoise",
    "django.contrib",
    "celery",
    "kombu.transport",
    "waitress",
):
    hidden += collect_submodules(package, filter=lambda name: ".tests" not in name)

datas = [(ROOT + "\\frontend\\dist\\frontend\\browser", "spa")]
for package in ("django", "rest_framework", "django_filters"):
    datas += collect_data_files(package)
# simplejwt's token_blacklist migration 0010 resolves its own __file__ on
# import, so the .py source must exist on disk beside the bytecode.
datas += collect_data_files(
    "rest_framework_simplejwt", include_py_files=True, subdir="token_blacklist/migrations"
)

a = Analysis(
    [ROOT + "\\desktop\\launcher.py"],
    pathex=[PROJECT],
    hiddenimports=hidden,
    datas=datas,
    excludes=["pytest", "tkinter", "IPython", "psycopg2", "boto3", "storages", "redis"],
)
# The spike's build Python is conda-based: its extension modules need the DLLs
# in conda's Library\bin, but the analysis picked same-named copies off PATH
# ("DLL load failed while importing _ssl" / "pyexpat"). Swap every top-level
# DLL that conda also ships for conda's copy. A python.org CPython would not
# need this.
import os
import sys

_conda_bin = os.path.join(sys.base_prefix, "Library", "bin")
_swapped = []
for _i, (_dest, _src, _kind) in enumerate(a.binaries):
    _conda = os.path.join(_conda_bin, os.path.basename(_dest))
    if os.sep not in _dest and "/" not in _dest and os.path.exists(_conda) and os.path.normcase(_src) != os.path.normcase(_conda):
        a.binaries[_i] = (_dest, _conda, _kind)
        _swapped.append(_dest)
print("ANG-09 swapped for conda copies:", _swapped)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LaserforceSimulator",
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LaserforceSimulator")
