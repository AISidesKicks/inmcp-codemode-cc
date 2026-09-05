# Generating Phoenix traces (`optimize.py` sweep)

Howto for a lab user driving an AI harness (Kilo/opencode MCP) who wants a
fresh, attributable set of Phoenix execution traces: one `optimize.py` run
sweeps 9 prompt optimizers over the cinematic-01 studio-recall task and lands
**~3.4k run-labelled spans** (task evals + judge reflections + films corpus
legs) in the Phoenix `default` project — every span carries your `--run-id`
prefix, so the whole set stays one query away.

Time warning up front: the full run takes **~2.5 h wall** — ~19 min sweep +
9 films legs of ~10.5–17.5 min each (154 films at the 8192-token reasoning
budget, [INTENT.md §3](../INTENT.md)). In a hurry? Add `--films 20`: ~2 min
per films leg, coffee-break scale, and the trace shapes are identical (just
fewer films spans).

## Prereqs

- **Stack healthy** — `cmod-litellm` (:4000 gateway), `cmod-llama` (:8080,
  granite judge), `cmod-llama-thinking` (:8081, LFM2.5-2.6B tested),
  `cmod-phoenix` (:6006). No manual probing needed:
  `optimize_common.health_all()` gates the run at startup and aborts unless
  gateway + both llama servers answer.
- **Dataset present** — `datasets/cinematic-01/dataset.csv`, 154 film rows.
  The sweep itself only needs its fixed seeded 6-train/4-val slice; the films
  legs score the corpus (`--csv`, `--films N`).
- **Pixi env** — run inside the `cdmd` shell (`pixi shell`) or prefix commands
  with `pixi run`. Python 3.12; gepa, deepeval, dspy, adalflow and
  promptrefiner are preinstalled.
- **Fresh trace set (optional, HITL)** — traces accumulate in the
  `cmod-phoenix-data` volume; old runs coexist fine because span prefixes keep
  them apart. If you really want a clean slate, wiping that volume is **user
  work — agents never touch `cmod-*` containers/volumes**, and never a casual
  `docker compose down -v`. Back up first (prior art:
  `scratch/cmod-phoenix-backup-20.3.0.tgz`).

## The one-liner

```bash
pixi run python smoketests/cinematic-01/optimize.py --stage all --run-id opt-scale-20260905
```

Flag cheatsheet:

| Flag | Meaning |
| --- | --- |
| `--stage sweep\|films\|all` | sweep = micro 6/4 split legs, films = corpus legs (default `all`) |
| `--optimizer <name>\|all` | single runner or everything except `refiner` (default `all`) |
| `--films N` | films corpus slice; `0` = all rows (default) |
| `--csv` | films corpus path (default `datasets/cinematic-01/dataset.csv`) |
| `--out` | out json path (default `datasets/cinematic-01/runs/<run-id>-optimize.json`) |
| `--run-id` | **required** — it doubles as the Phoenix span prefix |

Coffee-break variant (sweep + 9 short films legs, ~30–40 min total):

```bash
pixi run python smoketests/cinematic-01/optimize.py --stage all --films 20 --run-id opt-coffee-20260905
```

## One runner, one sentence

Every optimizer reflects/mutates through the local granite judge and evaluates
through the 2.6B tested model via the LiteLLM gateway (condensed from the
[optimize.py](../smoketests/cinematic-01/optimize.py) docstring):

| optimizer | tool + method |
| --- | --- |
| `gepa` | standalone `gepa.optimize`, judge as `reflection_lm` mutating a full-prompt candidate (DefaultAdapter via task_lm + evaluator) |
| `depeval-gepa` | deepeval `PromptOptimizer` driving the GEPA algorithm, judge as reflection+mutation model |
| `depeval-miprov2` | deepeval `PromptOptimizer` x MIPROV2 (bayesian instruction/demos proposals) |
| `depeval-copro` | deepeval `PromptOptimizer` x COPRO (coordinate ascent over instruction candidates) |
| `depeval-simba` | deepeval `PromptOptimizer` x SIMBA (introspective mini-batch) |
| `dspy-bootstrap` | dspy `BootstrapFewShot`: judge-as-teacher bootstraps demos, no proposal LLM — the cheapest |
| `dspy-simba` | dspy SIMBA: introspective mini-batch ascent, judge proposes rule/demo edits |
| `dspy-miprov2` | dspy MIPROv2: bayesian search over instructions + demo sets |
| `adalflow-tgd` | adalflow `TGDOptimizer` text-grad (`EvalFnToTextLoss` + `BackwardEngine`), manual accept/revert loop |
| `refiner` | promptrefiner `BaseStrategy.refine` rewrite-only baseline; explicit opt-in only (`--optimizer refiner`), never in `all` |

## Where traces & artifacts land

- **Phoenix spans** (project `default`, same server as
  [phoenix-mcp.md](phoenix-mcp.md)): `<run_id> <opt> eval` (tested-model task
  calls), `<run_id> <opt> judge` (judge reflections), `<run_id> <opt> films`
  (one span per film) — riding the proven gateway
  `metadata.generation_name` path.
- **Sweep out json**: `datasets/cinematic-01/runs/<run-id>-optimize.json` —
  per-optimizer status, val before/after, call counts, wall seconds, the
  `render` kind its best prompt needs for the films leg (`system` vs
  `filled`), the full `best_prompt`, plus nested films summaries.
- **Films jsons**: `datasets/cinematic-01/runs/<run-id>-<opt>-films.json` —
  meta + score + one row per film (`film` / `score` / `feedback`).

## Real results (opt-scale-20260904)

First formalized full run (2026-09-04), landed as a fresh Phoenix trace set,
all 18 legs ok:

| optimizer        | sweep before → after | films | task | judge | sweep s | films s |
|------------------|---------------------:|------:|-----:|------:|--------:|--------:|
| gepa             | 0.00 → 0.00          | 0.344 | 24   | 2     | 110.5   | 870.2   |
| depeval-gepa     | 0.00 → 0.00          | 0.351 | 34   | 4     | 110.7   | 669.3   |
| depeval-miprov2  | 0.00 → 0.00          | 0.338 | 25   | 4     | 115.0   | 1029.2  |
| depeval-copro    | 0.00 → 0.00          | 0.357 | 18   | 1     | 52.1    | 777.1   |
| dspy-bootstrap   | 0.25 → 0.25          | 0.377 | 8    | 1     | 33.6    | 1043.9  |
| depeval-simba    | 0.00 → 0.25          | 0.364 | 68   | 1     | 252.0   | 632.5   |
| dspy-simba       | 0.00 → 0.25          | 0.344 | 81   | 0     | 251.0   | 1016.2  |
| dspy-miprov2     | 0.25 → 0.00          | 0.357 | 26   | 9     | 100.6   | 766.0   |
| adalflow-tgd     | 0.25 → 0.25          | 0.338 | 24   | 12    | 101.0   | 647.9   |

Totals: sweep legs 33.6–252.0 s ≈ **~19 min**; films legs 632.5–1043.9 s
(each exactly 154 task calls, zero judge calls) ≈ **~2h04m**. Don't read the
micro-split before/after columns as a ranking — 4 val films is noisy; the
corpus legs are the honest comparison, and there the spread is tight:
0.338–0.377 with dspy-bootstrap on top. Full table + notes live in
[design.md "Optimizer runs"](../smoketests/cinematic-01/design.md).

## Verify your traces

Quick semantic check (`scratch/` tool, gitignored — the playground stays
playground):

```bash
pixi run python scratch/promptopt_phoenix_check.py opt-scale-20260905
```

It verifies: ≥1 `<run_id> ...` span exists in project `default`, ≥1 run span
is named `... judge`, and zero judge-model references in non-judge run spans.
What a healthy full run looks like (opt-scale-20260904): **3422** run-labelled
spans across **43** unique names, **44** judge-labelled spans, zero judge-model
leakage → `PASS`.

Caveat, learned the hard way: the check's fetch is unbounded — the Phoenix
client returns only the **most recent 1000 spans**, and at full-run size the
run-labelled spans in that window are films spans only (the sweep-stage judge
spans are hours older), so the judge check fails spuriously. Verified live on
opt-scale-20260904: 250 run-labelled films spans, 0 judge spans, `FAIL` —
while the run really carries 44 judge spans. Bound the fetch by the run's
time window (and raise the timeout — the client default is 5 s, one big fetch
trips it):

```python
from datetime import datetime, timedelta, timezone

from phoenix.client import Client

run_id = "opt-scale-20260905"
df = Client().spans.get_spans_dataframe(
    project_identifier="default",
    start_time=datetime.now(timezone.utc) - timedelta(hours=24),  # covers one full run (~2.5 h) with slack
    end_time=datetime.now(timezone.utc),
    limit=20000,
    timeout=60,
)
names = df["name"].astype(str)
mine = df[names.str.startswith(run_id)]
mine_names = mine["name"].astype(str)
judge = mine[mine_names.str.endswith(" judge")]
print(len(mine), "run spans,", len(judge), "judge spans,", mine_names.nunique(), "unique names")
```

Or browse interactively with the Phoenix MCP tools (catalog:
[phoenix-mcp.md](phoenix-mcp.md)) — `spanSearch`/`getSpans` accept
`start_time`/`end_time`/`name` filters, so the same windowing applies.

## Crash = resume

Kill the run mid-way (Ctrl-C, laptop nap, gateway hiccup) → re-run the exact
same command with the same `--run-id`: `--optimizer all` skips optimizers
already recorded in the out json (sweep) and films legs already carrying an
entry — only the interrupted leg re-runs. An explicit `--optimizer <name>`
always re-runs that one from scratch. Per-optimizer failures are recorded as
findings and never abort the run.
