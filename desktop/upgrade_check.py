"""ANG-09 spike: relaunch a Desktop App on an existing data folder, then on a broken one.

    python desktop/upgrade_check.py <home dir> <out.json> -- <launch command...>

1. Relaunch on <home> (already used by measure.py): the League must survive.
2. Break the migration history the way UX-01 did on real databases
   (InconsistentMigrationHistory: admin.0001 applied before accounts.0001),
   relaunch: expect the NOTICE line, a ``.bak`` file and an empty fresh database.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure import Api  # noqa: E402


def launch(
    command: list[str], home: Path
) -> tuple[subprocess.Popen, Api, list[str], float]:
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        command,
        env={**os.environ, "LF_DESKTOP_HOME": str(home)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []
    for line in proc.stdout:
        lines.append(line.rstrip())
        if line.startswith("LF_READY"):
            break
    url = lines[-1].split()[-1]
    base = url.split("/api/")[0]
    api = Api(base)
    api.call("GET", "/", raw=True)
    ready = round(time.perf_counter() - t0, 2)
    api.call("GET", url[len(base) :], raw=True)
    _, body = api.call("POST", "/api/token/refresh/", None, data=b"")
    api.token = body["data"]["access"]
    return proc, api, lines, ready


def main() -> None:
    args = sys.argv[1:]
    home, out = Path(args[0]), Path(args[1])
    command = args[args.index("--") + 1 :]
    results: dict[str, object] = {}

    proc, api, lines, ready = launch(command, home)
    _, body = api.call("GET", "/api/leagues/")
    results["relaunch"] = {
        "warm_start_s": ready,
        "leagues": body["meta"]["count"],
        "notice": [l for l in lines if l.startswith("NOTICE")],
    }
    proc.kill()
    proc.wait()
    print("relaunch", results["relaunch"])

    db = sqlite3.connect(home / "db.sqlite3")
    db.execute(
        "DELETE FROM django_migrations WHERE app = 'accounts' AND name = '0001_initial'"
    )
    db.commit()
    db.close()

    proc, api, lines, ready = launch(command, home)
    _, body = api.call("GET", "/api/leagues/")
    results["broken_history"] = {
        "start_s": ready,
        "leagues": body["meta"]["count"],
        "notice": [l for l in lines if l.startswith("NOTICE")],
        "bak_files": sorted(p.name for p in home.glob("*.bak")),
    }
    proc.kill()
    print("broken history", results["broken_history"])
    out.write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
