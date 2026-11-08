"""Real per-role targets from the Laserforce TDF event logs (CAL-02, spec #563 D1 / D8).

Usage:
    python -I scripts/tdf_role_stats.py <logs dir> --out data/tdf_role_targets_2026.json
        [--fetch] [--test-file data/lfstats_test_internationals_2026.json]

Pure parsing, no Django. A TDF log is UTF-16, tab-separated; the rows read here are
entity-start (type 3: the role in column 7 as 1 Commander / 2 Heavy / 3 Scout / 4 Ammo /
5 Medic, the team in column 5), events (type 4: ``time code actor text [target]``),
player-state (type 9: ``time entity state`` with 0 Active, 2 coming back, 3 Down) and
sm5-stats (type 7: ``id shotsHit shotsFired timesZapped ...``).

A Tag is a zap (code 0205 without a Down, 0206 with one) on an ENEMY; a zap on a teammate
is a shot, not a Tag, and LFstats ``shotsHit`` is never a tag count (it also counts
Resupplies and Base zaps). Times tagged counts enemy Tags on a player; a Missile hit
(0306) is not one. Shots fired are the player's 0201 misses, 0203 Base zaps and
0205 / 0206 zaps; accuracy on enemies is Tags over Tags plus misses. Active share is the
share of the 15 minutes spent in state 0 of the player-state rows; an Elimination is a
Down the player never returns from. Games without exactly twelve players at the five
roles (one Medic a team) are skipped and counted.

The logs stay out of the repo. ``--fetch`` downloads a missing ``<slug>.tdf`` from the
public archive for every slug in the Test file's games. Treat the logs as untrusted data:
run with ``python -I`` and keep this script outside the logs folder.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

ARCHIVE_URL = "https://lfstats-modern-archive.s3.us-west-1.amazonaws.com/{slug}.tdf"
DEFAULT_TEST_FILE = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "lfstats_test_internationals_2026.json"
)
GAME_MS = 900_000
ROLE_CODES = {"1": "commander", "2": "heavy", "3": "scout", "4": "ammo", "5": "medic"}
ROLES = ["commander", "heavy", "scout", "ammo", "medic"]
# Per team: one Commander, one Heavy, two Scouts, one Ammo, one Medic.
TEAM_ROLES = Counter({"commander": 1, "heavy": 1, "scout": 2, "ammo": 1, "medic": 1})
MISS, BASE_ZAP, ZAP, ZAP_DOWN = "0201", "0203", "0205", "0206"
SHOT_CODES = {MISS, BASE_ZAP, ZAP, ZAP_DOWN}
TAG_CODES = {ZAP, ZAP_DOWN}
STATE_ACTIVE, STATE_DOWN = "0", "3"
# Shot cadence bins: a gap of ``x`` seconds falls in the first bin with ``x <= edge``.
CADENCE_BINS: list[tuple[float, str]] = [
    (0.5, "<0.5"),
    (1.0, "0.5-1"),
    (2.0, "1-2"),
    (5.0, "2-5"),
    (10.0, "5-10"),
    (30.0, "10-30"),
    (60.0, "30-60"),
    (float("inf"), "60+"),
]
# The team's own Medic's Elimination minute: ``lower <= minute < upper``.
MEDIC_BUCKETS: list[tuple[str, float, float]] = [
    ("by_5", 0.0, 5.0),
    ("5_to_8", 5.0, 8.0),
    ("8_to_12", 8.0, 12.0),
    ("after_12", 12.0, float("inf")),
]
MEDIC_SURVIVES = "survives"
MEDIC_SPLIT_METRICS = ("tags", "times_tagged", "downs_given", "downs_taken")
DECIMALS = 4


@dataclass
class ParsedLog:
    """One TDF log, read as rows: players and their team, events, states, stats."""

    roles: dict[str, str] = field(default_factory=dict)
    teams: dict[str, str] = field(default_factory=dict)
    events: list[tuple[int, str, str, str | None]] = field(default_factory=list)
    states: list[tuple[int, str, str]] = field(default_factory=list)
    stats: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class GameStats:
    """One usable game: per-player counters and the per-game matrices."""

    roles: dict[str, str]
    teams: dict[str, str]
    counts: dict[str, Counter[str]]
    stats: dict[str, dict[str, int]]  # the sm5-stats row per player (LFstats' counts)
    active_ms: dict[str, int]
    elim_ms: dict[str, int]
    enemies_on_floor: dict[str, float]  # team -> enemies on the floor, game average
    who_tags_whom: Counter[tuple[str, str]]
    who_downs_whom: Counter[tuple[str, str]]
    gaps: dict[str, list[float]]  # role -> gaps between one player's shots, seconds


def parse_log(path: Path) -> ParsedLog:
    """Read the player, event, state and stats rows of one UTF-16 TDF log."""
    log = ParsedLog()
    text = path.read_text(encoding="utf-16")
    for line in text.splitlines():
        p = line.split("\t")
        if p[0] == "3" and len(p) >= 8 and p[3] == "player":
            log.roles[p[2]] = ROLE_CODES.get(p[7], p[7])
            log.teams[p[2]] = p[5]
        elif p[0] == "4" and len(p) >= 4:
            target = p[5] if len(p) >= 6 else None
            log.events.append((int(p[1]), p[2], p[3], target))
        elif p[0] == "9" and len(p) >= 4:
            log.states.append((int(p[1]), p[2], p[3]))
        elif p[0] == "7" and len(p) >= 5:
            log.stats[p[1]] = {
                "shotsHit": int(p[2]),
                "shotsFired": int(p[3]),
                "timesZapped": int(p[4]),
            }
    log.events.sort(key=lambda e: e[0])
    log.states.sort(key=lambda s: s[0])
    return log


def is_full_game(log: ParsedLog) -> bool:
    """Twelve players at the five roles, each team one of each with two Scouts."""
    players = [e for e, r in log.roles.items() if r in ROLES]
    if len(players) != 12:
        return False
    by_team: dict[str, Counter[str]] = defaultdict(Counter)
    for e in players:
        by_team[log.teams[e]][log.roles[e]] += 1
    return len(by_team) == 2 and all(c == TEAM_ROLES for c in by_team.values())


def _active_and_eliminations(
    players: list[str], states: list[tuple[int, str, str]]
) -> tuple[dict[str, int], dict[str, int]]:
    timeline: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for t, ent, st in states:
        timeline[ent].append((t, st))
    active_ms: dict[str, int] = {}
    elim_ms: dict[str, int] = {}
    for e in players:
        tl = timeline.get(e, [])
        active, state, since = 0, STATE_ACTIVE, 0
        for t, st in tl:
            if state == STATE_ACTIVE:
                active += t - since
            state, since = st, t
        if state == STATE_ACTIVE:
            active += GAME_MS - since
        active_ms[e] = active
        last_active = max([t for t, st in tl if st == STATE_ACTIVE], default=0)
        downs_after = [t for t, st in tl if st == STATE_DOWN and t > last_active]
        if downs_after and tl and tl[-1][1] != STATE_ACTIVE:
            elim_ms[e] = downs_after[0]
    return active_ms, elim_ms


def _enemies_on_floor(
    players: list[str], teams: dict[str, str], elim_ms: dict[str, int]
) -> dict[str, float]:
    """Enemies not yet Eliminated, averaged over the game's 900 seconds, per team."""
    seconds = GAME_MS // 1000
    out: dict[str, float] = {}
    for team in sorted(set(teams[e] for e in players)):
        total = 0
        for o in players:
            if teams[o] == team:
                continue
            total += min(seconds, -(-elim_ms.get(o, GAME_MS + 1) // 1000))
        out[team] = total / seconds
    return out


def game_stats(log: ParsedLog) -> GameStats | None:
    """Count one game's Tags, shots, Downs, states and matrices; None if not full."""
    if not is_full_game(log):
        return None
    roles, teams = log.roles, log.teams
    players = [e for e, r in roles.items() if r in ROLES]
    counts: dict[str, Counter[str]] = {e: Counter() for e in players}
    who_tags_whom: Counter[tuple[str, str]] = Counter()
    who_downs_whom: Counter[tuple[str, str]] = Counter()
    gaps: dict[str, list[float]] = defaultdict(list)
    last_shot: dict[str, int] = {}
    for t, code, actor, target in log.events:
        if actor not in counts:
            continue
        if code in SHOT_CODES:
            counts[actor]["shots_fired"] += 1
            if actor in last_shot:
                gaps[roles[actor]].append((t - last_shot[actor]) / 1000)
            last_shot[actor] = t
        if code == MISS:
            counts[actor]["misses"] += 1
        elif code == BASE_ZAP:
            counts[actor]["base_zaps"] += 1
        elif code in TAG_CODES and target in counts:
            if teams[actor] == teams[target]:
                counts[actor]["team_zaps"] += 1
                continue
            counts[actor]["tags"] += 1
            counts[target]["times_tagged"] += 1
            who_tags_whom[(roles[actor], roles[target])] += 1
            if code == ZAP_DOWN:
                counts[actor]["downs_given"] += 1
                counts[target]["downs_taken"] += 1
                who_downs_whom[(roles[actor], roles[target])] += 1
    active_ms, elim_ms = _active_and_eliminations(players, log.states)
    return GameStats(
        roles=roles,
        teams=teams,
        counts=counts,
        stats=log.stats,
        active_ms=active_ms,
        elim_ms=elim_ms,
        enemies_on_floor=_enemies_on_floor(players, teams, elim_ms),
        who_tags_whom=who_tags_whom,
        who_downs_whom=who_downs_whom,
        gaps=dict(gaps),
    )


def _round(x: float) -> float:
    return round(x, DECIMALS)


def mean_se(values: list[float]) -> dict[str, float | int]:
    """Mean and standard error of the mean (0 when one value), with ``n``."""
    n = len(values)
    if n == 0:
        return {"mean": 0.0, "se": 0.0, "n": 0}
    mean = statistics.mean(values)
    se = statistics.stdev(values) / n**0.5 if n > 1 else 0.0
    return {"mean": _round(mean), "se": _round(se), "n": n}


def _player_metrics(game: GameStats, e: str) -> dict[str, float]:
    c = game.counts[e]
    lfstats = game.stats.get(e, {})
    active_min = game.active_ms[e] / 60000
    metrics: dict[str, float] = {
        "tags": c["tags"],
        "times_tagged": c["times_tagged"],
        # LFstats' own counts, for the record: shotsHit also counts Resupplies and
        # Base zaps, timesZapped also counts a teammate's zaps on the player.
        "shots_hit_lfstats": lfstats.get("shotsHit", 0),
        "times_zapped_lfstats": lfstats.get("timesZapped", 0),
        "shots_fired": c["shots_fired"],
        "misses": c["misses"],
        "base_zaps": c["base_zaps"],
        "team_zaps": c["team_zaps"],
        "downs_given": c["downs_given"],
        "downs_taken": c["downs_taken"],
        "active_share": game.active_ms[e] / GAME_MS,
        "tags_per_active_min": c["tags"] / max(active_min, 0.1),
        "eliminated": 1.0 if e in game.elim_ms else 0.0,
        "enemies_on_floor": game.enemies_on_floor[game.teams[e]],
    }
    if c["tags"] + c["misses"]:
        metrics["accuracy"] = c["tags"] / (c["tags"] + c["misses"])
    if e in game.elim_ms:
        metrics["elim_minute"] = game.elim_ms[e] / 60000
    return metrics


def _medic_bucket(game: GameStats, team: str) -> str:
    medic = next(
        e for e, r in game.roles.items() if r == "medic" and game.teams[e] == team
    )
    if medic not in game.elim_ms:
        return MEDIC_SURVIVES
    minute = game.elim_ms[medic] / 60000
    return next(name for name, lo, hi in MEDIC_BUCKETS if lo <= minute < hi)


def _cadence(gaps: list[float]) -> dict[str, Any]:
    bins: Counter[str] = Counter()
    for x in gaps:
        bins[next(name for edge, name in CADENCE_BINS if x <= edge)] += 1
    n = len(gaps)
    return {
        "median": _round(statistics.median(gaps)) if gaps else 0.0,
        "bins": {
            name: _round(bins[name] / n) if n else 0.0 for _, name in CADENCE_BINS
        },
        "n": n,
    }


def aggregate(games: Iterable[GameStats | None]) -> dict[str, Any]:
    """The output payload: per-role means, matrices, cadence and the Medic split."""
    per_role: dict[str, dict[str, list[float]]] = {r: defaultdict(list) for r in ROLES}
    split: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: {r: defaultdict(list) for r in ROLES}
    )
    split_team_games: Counter[str] = Counter()
    tags_pairs: Counter[tuple[str, str]] = Counter()
    downs_pairs: Counter[tuple[str, str]] = Counter()
    gaps: dict[str, list[float]] = {r: [] for r in ROLES}
    floor: list[float] = []
    total = used = 0
    for game in games:
        total += 1
        if game is None:
            continue
        used += 1
        tags_pairs.update(game.who_tags_whom)
        downs_pairs.update(game.who_downs_whom)
        for r in ROLES:
            gaps[r].extend(game.gaps.get(r, []))
        floor.extend(game.enemies_on_floor[t] for t in sorted(game.enemies_on_floor))
        buckets = {t: _medic_bucket(game, t) for t in game.enemies_on_floor}
        split_team_games.update(buckets.values())
        for e, role in game.roles.items():
            if role not in ROLES:
                continue
            metrics = _player_metrics(game, e)
            for name, value in metrics.items():
                per_role[role][name].append(value)
            bucket = split[buckets[game.teams[e]]]
            for name in MEDIC_SPLIT_METRICS:
                bucket[role][name].append(metrics[name])

    def matrix(pairs: Counter[tuple[str, str]]) -> dict[str, dict[str, float]]:
        return {
            a: {d: _round(pairs[(a, d)] / used) if used else 0.0 for d in ROLES}
            for a in ROLES
        }

    return {
        "games": {"total": total, "used": used, "skipped": total - used},
        "player_games": {r: len(per_role[r]["tags"]) for r in ROLES},
        "per_role": {
            r: {name: mean_se(values) for name, values in per_role[r].items()}
            for r in ROLES
        },
        "who_tags_whom": matrix(tags_pairs),
        "who_downs_whom": matrix(downs_pairs),
        "cadence": {r: _cadence(gaps[r]) for r in ROLES},
        "enemies_on_floor": mean_se(floor),
        "by_medic_elimination": {
            name: {
                "team_games": split_team_games[name],
                "per_role": {
                    r: {m: mean_se(split[name][r][m]) for m in MEDIC_SPLIT_METRICS}
                    for r in ROLES
                },
            }
            for name in [b[0] for b in MEDIC_BUCKETS] + [MEDIC_SURVIVES]
        },
    }


def build_payload(logs_dir: Path) -> dict[str, Any]:
    """Parse every ``.tdf`` in ``logs_dir`` (sorted by name) into the payload."""
    return aggregate(game_stats(parse_log(p)) for p in sorted(logs_dir.glob("*.tdf")))


def download_log(slug: str, dest: Path) -> None:
    """Fetch one log from the public archive into ``dest``."""
    with urllib.request.urlopen(ARCHIVE_URL.format(slug=slug)) as response:
        dest.write_bytes(response.read())


def fetch_missing(
    logs_dir: Path,
    test_file: Path,
    fetch: Callable[[str, Path], None] | None = None,
) -> list[str]:
    """Download every Test-file slug whose ``<slug>.tdf`` is missing; the slugs fetched."""
    fetch = fetch or download_log
    test = json.loads(test_file.read_text(encoding="utf-8"))
    logs_dir.mkdir(parents=True, exist_ok=True)
    fetched: list[str] = []
    for game in test["games"]:
        slug = game["slug"]
        dest = logs_dir / f"{slug}.tdf"
        if not dest.is_file():
            fetch(slug, dest)
            fetched.append(slug)
    return fetched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("logs_dir", type=Path, help="folder of <slug>.tdf logs")
    parser.add_argument("--out", type=Path, required=True, help="output JSON path")
    parser.add_argument(
        "--fetch", action="store_true", help="download the Test file's missing logs"
    )
    parser.add_argument(
        "--test-file",
        type=Path,
        default=DEFAULT_TEST_FILE,
        help="the LFS-01 Test file whose games' slugs name the logs",
    )
    args = parser.parse_args(argv)
    if args.fetch:
        fetched = fetch_missing(args.logs_dir, args.test_file)
        print(f"fetched {len(fetched)} log(s) into {args.logs_dir}")
    payload = build_payload(args.logs_dir)
    args.out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    games = payload["games"]
    print(
        f"games: {games['used']} used, {games['skipped']} skipped of {games['total']};"
        f" wrote {args.out}"
    )
    for r in ROLES:
        row = payload["per_role"][r]
        print(
            f"  {r:10} tags {row['tags']['mean']:6.2f}"
            f"  times tagged {row['times_tagged']['mean']:6.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
