# Persist an integer RNG seed per round, not the RNG state snapshot

**Status:** accepted

SIM-07 makes every persisted round replayable. We persist a small **integer RNG
seed** on `GameRound` (`rng_seed`, `BigIntegerField`, 63-bit) and reseed the
global RNG with `random.seed(rng_seed)` before re-simulating, rather than storing
the `random.getstate()` snapshot (a version + 625-int Mersenne vector + Gaussian
cache) the in-memory batch path previously carried. A `BatchSimulator` batch run
derives its per-round seeds from a master seed via `random.Random(master_seed)`
(a *seed chain*): random per run by default, optionally supplied so an entire
batch is reproducible (used by tests to watch aggregate results move after a
weight/logic change). 63-bit seeds are chosen so every value fits a signed BIGINT
on both SQLite and Postgres.

**Considered options:** (a) store the `getstate()` tuple — rejected: not a scalar,
JSON-serialises as a 625-element list, no integer DB typing, and invites a future
reader to wrongly try `random.seed(state)`; (b) a hardcoded constant master seed —
rejected: would make batch simulation fully deterministic and stop it sampling
outcome variance; (c) per-round random seed with no chain — rejected: loses
whole-batch reproducibility for the test/forecast use case.

**Consequence:** the in-memory batch path now reseeds the *global* RNG once per
round from the master chain, which makes serial and parallel (`workers>1`) runs
produce identical games for a given master seed — a guaranteed, tested property
(previously they could differ). Replay is faithful **only while the round's
rosters and map config are unchanged** — the seed captures randomness, not world
state; this aligns with [ADR-0004](0004-simulation-data-is-disposable.md) (rounds
are regenerable, frozen-replay snapshotting is deferred to whoever needs it).
`score_round_worker` (the `score_averages` command's parallel path) keeps its own
independent `getstate()` plumbing and is intentionally out of SIM-07's scope.
