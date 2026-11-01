"""ANG-09 spike: launch a Desktop App build from a clean data folder and measure it.

    python desktop/measure.py <home dir> <out.json> -- <launch command...>

e.g. ``-- dist/LaserforceSimulator/LaserforceSimulator.exe --no-browser`` or
``-- <venv python> desktop/launcher.py --no-browser``. Prints one line per step
and writes every number to <out.json>. Leaves the app running only if
``--keep`` is passed before ``--``.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MAP_IMAGE = (
    Path(__file__).resolve().parent.parent
    / "Screenshots_and_video_examples"
    / os.environ.get("LF_MEASURE_MAP", "San_Marcos_Laser_Tag_Map.jpg")
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


class Api:
    def __init__(self, base: str) -> None:
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect
        )
        self.token = ""

    def call(
        self,
        method: str,
        path: str,
        body: object = None,
        *,
        raw: bool = False,
        data: bytes | None = None,
        ctype: str = "application/json",
    ) -> tuple[int, object]:
        if body is not None:
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", ctype)
        req.add_header("Accept", "*/*" if raw else "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with self.opener.open(req, timeout=600) as resp:
                payload = resp.read()
                status = resp.status
        except urllib.error.HTTPError as err:
            payload, status = err.read(), err.code
        if raw or not payload:
            return status, payload
        try:
            return status, json.loads(payload)
        except ValueError:
            return status, payload[:200]


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        return image.size


def poll(api: Api, path: str, limit: float = 900) -> tuple[dict, list[str], float]:
    """Poll a job until it leaves ``running``; returns the last data, the progress seen and the seconds."""
    t = time.perf_counter()
    seen: list[str] = []
    while True:
        status, body = api.call("GET", path)
        data = (
            body["data"]
            if isinstance(body, dict) and "data" in body
            else {"status": f"http {status}", "body": str(body)[:300]}
        )
        mark = f"{data.get('status')} {data.get('completed')}/{data.get('total')}"
        if not seen or seen[-1] != mark:
            seen.append(mark)
        if data.get("status") != "running" or time.perf_counter() - t > limit:
            return data, seen, time.perf_counter() - t
        time.sleep(0.5)


def main() -> None:
    args = sys.argv[1:]
    split = args.index("--")
    keep = "--keep" in args[:split]
    if keep:
        args.remove("--keep")
        split -= 1
    home, out = Path(args[0]), Path(args[1])
    command = args[split + 1 :]
    results: dict[str, object] = {"command": command}

    env = {**os.environ, "LF_DESKTOP_HOME": str(home)}
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    url = ""
    for line in proc.stdout:
        print("  app:", line.rstrip())
        if line.startswith("LF_READY"):
            _, launcher_s, url = line.split()
            results["launcher_ready_s"] = float(launcher_s)
            break
    if not url:
        raise SystemExit("app exited before LF_READY")
    base = url.split("/api/")[0]
    api = Api(base)
    status, page = api.call("GET", "/", raw=True)
    results["cold_start_to_spa_s"] = round(time.perf_counter() - t0, 2)
    results["spa_index_ok"] = status == 200 and b"<app-root" in page
    print("cold start", results["cold_start_to_spa_s"], "s")

    status, _ = api.call("GET", url[len(base) :], raw=True)
    results["desktop_login_status"] = status
    status, _ = api.call("GET", url[len(base) :], raw=True)
    results["desktop_login_reuse_status"] = status
    status, body = api.call("POST", "/api/token/refresh/", None, data=b"")
    api.token = body["data"]["access"]
    status, body = api.call("GET", "/api/accounts/me/")
    results["me"] = body["data"]

    status, body = api.call(
        "POST",
        "/api/teams/generate/",
        {"num_teams": "2", "players_per_team": "6", "mean": 50, "std_dev": 15},
    )
    red, blue = (t["id"] for t in body["data"]["created_teams"])

    times = []
    for _ in range(3):
        t = time.perf_counter()
        status, body = api.call(
            "POST", "/api/rounds/", {"team_red": red, "team_blue": blue}
        )
        times.append(round(time.perf_counter() - t, 2))
        assert status == 201, body
    round_id = body["data"]["id"]
    results["single_round_s"] = times
    print("single round", times)

    t = time.perf_counter()
    status, body = api.call(
        "POST",
        "/api/simulate-batch/",
        {"team_red": red, "team_blue": blue, "n": 50, "master_seed": 12345},
    )
    job = body["data"]["job_id"]
    data, seen, _ = poll(api, f"/api/simulate-batch/{job}/")
    results["batch50"] = {
        "status": data["status"],
        "error": data.get("error"),
        "seconds": round(time.perf_counter() - t, 2),
        "progress": seen,
    }
    print("batch 50", results["batch50"])

    status, pdf = api.call("GET", f"/api/rounds/{round_id}/report/", raw=True)
    results["round_report"] = {
        "status": status,
        "bytes": len(pdf),
        "is_pdf": pdf[:4] == b"%PDF",
    }
    print("pdf", results["round_report"])

    boundary = uuid.uuid4().hex
    image = MAP_IMAGE.read_bytes()
    form = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\nSpike Arena\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{MAP_IMAGE.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        ).encode()
        + image
        + f"\r\n--{boundary}--\r\n".encode()
    )
    t = time.perf_counter()
    status, body = api.call(
        "POST",
        "/api/maps/",
        data=form,
        ctype=f"multipart/form-data; boundary={boundary}",
    )
    map_result: dict[str, object] = {"upload_status": status}
    if status == 201:
        map_id = body["data"]["id"]
        width, height = image_size(MAP_IMAGE)
        status, _ = api.call("GET", f"/api/maps/{map_id}/zones/?zone_size=50")
        map_result["zones_status"] = status
        bases = [
            {"type": "red", "x_px": width // 10, "y_px": height // 2},
            {"type": "blue", "x_px": width * 9 // 10, "y_px": height // 2},
        ]
        status, body = api.call(
            "PUT", f"/api/maps/{map_id}/zone-config/", {"zone_size": 50, "bases": bases}
        )
        map_result["zone_config_status"] = status
        if status != 200:
            map_result["zone_config_body"] = str(body)[:300]
        status, body = api.call(
            "POST", f"/api/maps/{map_id}/sight-lines/compute/", {"zone_size": 50}
        )
        map_result["sight_lines_status"] = status
        if status != 200:
            map_result["sight_lines_body"] = str(body)[:300]
        map_result["seconds"] = round(time.perf_counter() - t, 2)
        t = time.perf_counter()
        status, body = api.call(
            "POST",
            "/api/rounds/",
            {"team_red": red, "team_blue": blue, "arena_map": map_id},
        )
        map_result["full_fidelity_round"] = {
            "status": status,
            "seconds": round(time.perf_counter() - t, 2),
        }
        if status != 201:
            map_result["full_fidelity_round"]["body"] = str(body)[:300]
    results["map"] = map_result
    print("map", map_result)

    league_body = {
        "league_name": "Spike League",
        "season_name": "Season 1",
        "start_date": "2026-11-01",
        "num_teams": 4,
        "mean": 50,
        "std_dev": 15,
        "map_mode": "none",
    }
    status, body = api.call("POST", "/api/leagues/", league_body)
    assert status == 201, body
    league_id, season_id = body["data"]["id"], body["data"]["draft_season"]["id"]
    status, _ = api.call("POST", f"/api/seasons/{season_id}/start/", None, data=b"")
    league: dict[str, object] = {
        "league_id": league_id,
        "season_id": season_id,
        "start_status": status,
    }
    t = time.perf_counter()
    status, _ = api.call("POST", f"/api/seasons/{season_id}/play-week/", None, data=b"")
    league["play_week"] = {
        "status": status,
        "seconds": round(time.perf_counter() - t, 2),
    }
    t = time.perf_counter()
    status, body = api.call(
        "POST", f"/api/seasons/{season_id}/play-two-months/", None, data=b""
    )
    league["play_two_months_post"] = {
        "status": status,
        "seconds": round(time.perf_counter() - t, 2),
    }
    if status == 200:
        data, seen, _ = poll(
            api, f"/api/seasons/{season_id}/play-jobs/{body['data']['job_id']}/"
        )
        league["play_two_months"] = {
            "status": data["status"],
            "error": data.get("error"),
            "seconds": round(time.perf_counter() - t, 2),
            "progress": seen,
        }
    else:
        league["play_two_months_body"] = str(body)[:300]
    results["league"] = league
    print("league", league)

    out.write_text(json.dumps(results, indent=2))
    if not keep:
        proc.kill()
    print("wrote", out)


if __name__ == "__main__":
    main()
