# Generating Phoenix traces (`optimize.py` sweep)

Howto for a lab user driving an AI harness (Kilo/opencode MCP) who wants a
fresh, attributable set of Phoenix execution traces: one `optimize.py` run
sweeps 9 prompt optimizers over the cinematic-01 studio-recall task and lands
**~10k run-labelled spans** (task evals + judge reflections + verdict-echo
stamps + films corpus legs) in the Phoenix **`cdmd-lab`** project — every span
carries your `--run-id` prefix, so the whole set stays one query away.

Since 2026-09-06 the harness is **engine-direct**: the OpenAI SDK talks
straight to the two llama.cpp servers and the client emits OpenInference spans
(AGENT turn roots + LLM children) straight into Phoenix. No gateway in the
trace path — no `metadata.*` nesting, no double-export, spans land with real
names and sessions at creation time.

Time warning up front: the full run takes **~2.5 h wall** — ~20 min sweep +
9 films legs of ~10–18 min each (154 films at the 8192-token reasoning
budget, [INTENT.md §3](../INTENT.md)). In a hurry? Add `--films 20`: ~2 min
per films leg, coffee-break scale, and the trace shapes are identical (just
fewer films spans). For per-test-type client vs trace timing methodology
(probes / smoke / one optimizer leg) see [test-timing.md](test-timing.md).

## Prereqs

- **Stack healthy** — `cmod-llama` (:8080, granite judge),
  `cmod-llama-thinking` (:8081, LFM2.5-2.6B tested), `cmod-phoenix` (:6006).
  The `cmod-litellm` gateway stays defined in the docker stack but **stopped**
  (removal pending) — the harness no longer routes through it, so don't start
  it. No manual probing needed: `optimize_common.health_all()` gates the run
  at startup and aborts unless both llama servers answer.
- **Dataset present** — `datasets/cinematic-01/dataset.csv`, 154 film rows.
  The sweep itself only needs its fixed seeded 6-train/4-val slice; the films
  legs score the corpus (`--csv`, `--films N`).
- **Pixi env** — run inside the `cdmd` shell (`pixi shell`) or prefix commands
  with `pixi run`. Python 3.12; gepa, deepeval, dspy, adalflow and
  promptrefiner are preinstalled. `litellm` is no longer a direct dependency
  (it remains installed transitively for dspy only); `trulens-providers-litellm`
  is gone.
- **Fresh trace set (optional, HITL)** — traces accumulate in the
  `cmod-phoenix-data` volume; old runs coexist fine because span prefixes keep
  them apart. If you really want a clean slate, wiping that volume is **user
  work — agents never touch `cmod-*` containers/volumes**, and never a casual
  `docker compose down -v`. Back up first (prior art:
  `scratch/cmod-phoenix-backup-20.3.0.tgz`).

## The one-liner

```bash
pixi run python smoketests/cinematic-01/optimize.py --stage all --run-id opt-20260906-gw
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
pixi run python smoketests/cinematic-01/optimize.py --stage all --films 20 --run-id opt-coffee-20260906
```

## One runner, one sentence

Every optimizer reflects/mutates through the local granite judge and evaluates
through the 2.6B tested model, engine-direct over the OpenAI-compatible APIs
(condensed from the [optimize.py](../smoketests/cinematic-01/optimize.py)
docstring):

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

- **Phoenix spans** (project `cdmd-lab`, same server as
  [phoenix-mcp.md](phoenix-mcp.md)): `<run_id> <opt> eval` (tested-model task
  calls), `<run_id> <opt> judge` (judge reflections), `<run_id> <opt> films`
  (one AGENT turn root per film with the engine call nested as LLM child) —
  emitted client-side at span creation, no gateway stamper in between. Each
  scored row (sweep val legs + films legs) gets its own Phoenix session
  (`<run_id> <opt> ... <film>`, the scored turn + its verdict-echo turn), and
  the scored turn root carries an **`eval` ok/miss span annotation** so failed
  rows stay score-searchable.
- **Sweep out json**: `datasets/cinematic-01/runs/<run-id>-optimize.json` —
  per-optimizer status, val before/after, call counts, wall seconds, the
  `render` kind its best prompt needs for the films leg (`system` vs
  `filled`), the full `best_prompt`, plus nested films summaries.
- **Films jsons**: `datasets/cinematic-01/runs/<run-id>-<opt>-films.json` —
  meta + score + one row per film (`film` / `score` / `feedback` / turn-root
  `span_id`).

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

## Fresh rerun (opt-scale-20260905, gateway era)

Second full run (2026-09-05) after the verdict-echo stamping fix — INTENT §3
re-executed end-to-end on the then-current **gateway** path (spans via LiteLLM
OTEL, `metadata.test_status` stamping, double export). Naive `test.py` at 154
films × 3 scenarios (8192 budget) + all 10 optimizers (the 9 `--optimizer all`
legs plus `refiner` as explicit opt-in), every films leg the full 154-film
corpus. Naive scored recall 51/154, year match 121/154, repeat ExactMatch
0.79 (FAIL vs 0.8) and stamped **462 echo verdicts**; per-optimizer:

| optimizer        | sweep before → after | films | task | judge | sweep s | films s |
|------------------|---------------------:|------:|-----:|------:|--------:|--------:|
| gepa             | 0.00 → 0.00          | 0.338 | 28   | 2     | 115.0   | 713.0   |
| depeval-gepa     | 0.00 → 0.00          | 0.338 | 34   | 4     | 124.8   | 631.1   |
| depeval-miprov2  | 0.25 → 0.00          | 0.299 | 25   | 4     | 102.1   | 873.6   |
| depeval-copro    | 0.25 → 0.00          | 0.351 | 18   | 1     | 69.2    | 604.2   |
| dspy-bootstrap   | 0.00 → 0.25          | 0.344 | 8    | 1     | 41.0    | 856.0   |
| depeval-simba    | 0.00 → 0.25          | 0.403 | 68   | 1     | 241.9   | 492.9   |
| dspy-simba       | 0.00 → 0.00          | 0.338 | 89   | 1     | 253.1   | 879.4   |
| dspy-miprov2     | 0.25 → 0.00          | 0.390 | 26   | 8     | 103.7   | 873.7   |
| adalflow-tgd     | 0.25 → 0.25          | 0.344 | 24   | 14    | 105.5   | 588.4   |
| refiner          | 0.00 → 0.00          | 0.364 | 8    | 1     | 24.2    | 571.6   |

Totals: sweep legs 24.2–253.1 s ≈ **~19 min**; films legs 492.9–879.4 s
(10 × 154 task calls, zero judge calls) ≈ **~1h55m**. depeval-simba tops the
corpus at 0.403 (0.299–0.403 spread — wider than the 0904 run). Note: the
gateway exported every call **twice**, so raw run-labelled span counts were
2× the unique calls — gone with the engine-direct switch.

## Engine-direct full run (opt-20260906-gw, score-annotation era)

Third full run (2026-09-06), first on the **engine-direct** trace path:
no gateway, per-test Phoenix sessions in `cdmd-lab`, `eval` ok/miss span
annotations on every scored row's turn root (replaces the gateway
`metadata.test_status`). Naive `test.py --sample 0` (full corpus, 8192
budget): recall **56/154**, year match **121/154**, repeat ExactMatch
**0.81 (PASS)**, **462 echo verdicts + 462 eval annotations** (302 ok /
160 miss). All 10 optimizers + films legs ok:

| optimizer        | sweep before → after | films | task | judge | sweep s | films s |
|------------------|---------------------:|------:|-----:|------:|--------:|--------:|
| gepa             | 0.00 → 0.00          | 0.364 | 24   | 2     | 127.8   | 796.0   |
| depeval-gepa     | 0.25 → 0.00          | 0.364 | 34   | 4     | 155.5   | 648.4   |
| depeval-miprov2  | 0.25 → 0.00          | 0.325 | 25   | 4     | 95.2    | 703.9   |
| depeval-copro    | 0.25 → 0.00          | 0.357 | 18   | 1     | 71.7    | 618.8   |
| dspy-bootstrap   | 0.00 → 0.00          | 0.370 | 8    | 1     | 53.8    | 1014.4  |
| depeval-simba    | 0.00 → 0.25          | 0.383 | 68   | 1     | 299.8   | 569.6   |
| dspy-simba       | 0.00 → 0.00          | 0.351 | 84   | 1     | 280.1   | 1006.4  |
| dspy-miprov2     | 0.25 → 0.25          | 0.331 | 29   | 8     | 127.2   | 993.4   |
| adalflow-tgd     | 0.00 → 0.00          | 0.403 | 24   | 12    | 126.1   | 637.4   |
| refiner          | 0.25 → 0.25          | 0.344 | 8    | 1     | 22.1    | 488.7   |

Totals: sweep legs 22.1–299.8 s ≈ **~19 min**; films legs 488.7–1014.4 s
(10 × 154 task calls, zero judge calls) ≈ **~1h58m**. adalflow-tgd tops the
corpus at 0.403 (0.325–0.403 spread). Phoenix readback (SQL analytics):
2086 lab sessions (one per scored row), 2086 `eval` annotations
(863 ok / 1223 miss), 13 client-side `<run_id> <opt> judge` spans
(dspy/adalflow judge calls bypass `llm.chat`, so they emit no client spans —
the sweep table's judge column stays the honest count). Sweep-val annotation
posts raced Phoenix ingest on a few legs (transient 404s) and were backfilled
offline from the recorded span ids — the films legs' echo-verdict margin
makes them race-free.

## Verify your traces

Quick windowed check against `cdmd-lab` (bound the fetch by the run's time
window — the Phoenix client returns only the most recent 1000 spans by
default, and at full-run size that window is films spans only):

```python
from datetime import datetime, timedelta, timezone

from phoenix.client import Client

run_id = "opt-20260906-gw"
df = Client().spans.get_spans_dataframe(
    project_identifier="cdmd-lab",
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

Score-searchability check (the engine-direct replacement for the old
`metadata.test_status` count): pull the run's eval annotations and split by
label. Via SQL against the Phoenix analytics endpoint it is one query:

```sql
SELECT a.result_label, COUNT(*) AS n
FROM span_annotations a
WHERE a.name = 'eval'
GROUP BY a.result_label;
-- healthy full run: ok + miss == 462 (naive) + 9..10 x 154 (films) + sweep legs
```

Or browse interactively with the Phoenix MCP tools (catalog:
[phoenix-mcp.md](phoenix-mcp.md)) — `spanSearch`/`getSpans` accept
`start_time`/`end_time`/`name` filters and `listSpanAnnotationsBySpanIds`
returns the `eval` labels, so the same windowing applies.

## Crash = resume

Kill the run mid-way (Ctrl-C, laptop nap, engine hiccup) → re-run the exact
same command with the same `--run-id`: `--optimizer all` skips optimizers
already recorded in the out json (sweep) and films legs already carrying an
entry — only the interrupted leg re-runs. An explicit `--optimizer <name>`
always re-runs that one from scratch. Per-optimizer failures are recorded as
findings and never abort the run. The opt-in refiner leg re-runs the same
way with `--optimizer refiner`.
