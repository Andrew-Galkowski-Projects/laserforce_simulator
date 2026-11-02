# DESK-02: PyInstaller one-folder build of the Desktop App (ADR-0048).
# Build with desktop/build.ps1, which sets the environment this file needs:
#   PYTHONPATH=<repo>\laserforce_simulator
#   DJANGO_SETTINGS_MODULE=laserforce_simulator.settings  SECRET_KEY=build-only
# collect_submodules imports each package in an isolated subprocess started
# before this file runs: it sees neither pathex nor env set here, and
# matches.simulation reads settings on import.
# Output: <distpath>\LaserforceSimulator\LaserforceSimulator.exe
# Build on a python.org CPython: a conda one bundles the wrong _ssl / pyexpat
# DLLs off PATH (ADR-0048).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.dirname(SPECPATH)
PROJECT = os.path.join(ROOT, "laserforce_simulator")

# Django loads apps, middleware, URLconfs, migrations, renderers and Celery
# transports by dotted string, which PyInstaller's import scan cannot see.
hidden = []
for package in (
    "laserforce_simulator",
    "desktop_app",
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
    # DESK-03: no "webview" here. pyinstaller-hooks-contrib's hooks for
    # webview, clr_loader and pythonnet bundle the App window (pywebview over
    # WebView2, with its .NET DLLs). Add "webview" to this list only if CI's
    # frozen `smoke.py --window` self-test fails on an import.
):
    hidden += collect_submodules(package, filter=lambda name: ".tests" not in name)

# desktop_app.launcher.spa_dir() reads the SPA from <_MEIPASS>\spa.
datas = [(os.path.join(ROOT, "frontend", "dist", "frontend", "browser"), "spa")]
for package in ("django", "rest_framework", "django_filters"):
    datas += collect_data_files(package)
# simplejwt's token_blacklist migration 0010 resolves its own __file__ on
# import, so the .py source must exist on disk beside the bytecode.
datas += collect_data_files(
    "rest_framework_simplejwt",
    include_py_files=True,
    subdir="token_blacklist/migrations",
)

a = Analysis(
    [os.path.join(ROOT, "desktop", "main.py")],
    pathex=[PROJECT],
    hiddenimports=hidden,
    datas=datas,
    # Test tooling, and the Hosted-only drivers requirements-runtime.txt
    # installs for the other deploy.
    excludes=[
        "pytest",
        "tkinter",
        "IPython",
        "psycopg2",
        "boto3",
        "storages",
        "redis",
        "gunicorn",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LaserforceSimulator",
    # DESK-03: no console window; the App window is the UI. A piped child's
    # print still reaches the pipe (LF_READY for the smoke), and with no pipe
    # launcher.quiet_std_streams() swaps the None streams for os.devnull.
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="LaserforceSimulator")
