# cinematic-01 design notes

Field-decision notes for the `cinematic-01` micro dataset. Written down so the
"dataset as ground truth" choice stays deliberate, not accidental.

## Motivation

The lab is about *metering* local inference in a lean stack: token counts,
per-call latency and Phoenix execution traces from llama.cpp + LiteLLM +
Phoenix, with no external databases. The cinematic-01 dataset gives the smoke
pipeline a small, fun, stable set of studio->film->year triplets to run
generate/test cycles against. (The caching-tier demos of the original lab live
in [localai.isnot.cheap](https://localai.isnot.cheap); this lab runs
caching-free — see "Caching is disabled" below.)

## Model output as ground truth

The old generator invented parody studios + blurbs. This one flips the source of
truth:

- **Studio names are seeded** (the 20 entries in `generate.py`) — real-world
  facts we hard-code, because the tiny 2.6B model does not reliably recall a
  canonical studio catalog.
- **Film titles and release years are model output** — for each studio the model
  is asked for up to N films, then for each film it is re-asked for the year.

Consequence: the CSV is *de facto* ground truth for the test step, even though
years/titles are model-produced. `test.py` therefore evaluates **consistency**
(re-asking yields the same studio, the same year within +/-2, exact year on a
reworded prompt) rather than correctness against an external curated list.
That is exactly what we care about for the eval runs, and it is honest about
the model's limitations.

## Structured output

Every call goes through `llm.chat()` with a Pydantic `response_format`
(`StudioList` / `FilmList` / `YearAnswer`) plus
`enable_json_schema_validation=True`. The 2.6B camel is comfortable emitting
small JSON. Reasoning is **always on** (`{"enabled": True}`, no opt-in flag):
LFM2.5-2.6B is a pure reasoning model whose chat template hardcodes the think
open, so the old `--reasoning` opt-in and the budget-0 off-switch were removed.
Every call gets token headroom (`max_tokens` 1536 default) — with thinking on,
a 256-token cap truncates into all-think/empty content — and the thinking text
is recorded per call via `llm.reasoning_content()`. The `generate.py` path
uses the same always-on reasoning.

## Serial execution

Both engines run single-slot (`--parallel 1` on both containers in
`docker/docker-compose.yml`), so client-side concurrency only queues requests
without adding throughput. test.py therefore executes strictly serially (no
thread pool, no `--workers`); row order is inherently deterministic and each
call resolves its own master key. Run mode is recorded in `meta`
(`reasoning: enabled`) and shown in the rendered report.

## Dedup and the year guard

- Film titles are deduped **exactly** on the normalized title (letters+digits,
  lowercase) across studios — otherwise shared titles (e.g. a franchise owned by
  multiple studios) would double-count in the year scenarios.
- Years are guarded to `1900..2023`. Out-of-range model guesses are recorded in
  the run log (`year_valid: false`) and **excluded from the CSV** so the
  dataset stays clean and testable.

## Caching is disabled

No caching tier anywhere in this lab, by design:

- the stack has no Redis, and `docker/litellm_config.yaml` defines no cache
  section, so LiteLLM request caching is off;
- both engines run `--parallel 1` without `--cache-ram`/`--cache-reuse`, so
  the engine prefix caches stay idle too.

Per-call results therefore carry no cache fields: the `usage` token counts
plus `seconds` are the metering signals.

## Sessions tracing

Every model call gets a probe-style trace in the Phoenix project `cdmd-lab`
(direct OTLP to `http://localhost:6006/v1/traces`): an AGENT turn root
(`llm.turn`, named like the gateway span — `<run_id> recall <film>` etc.)
carrying `session.id` + `user.id` (`edu-harness`) and
`input.value`/`output.value`, with the gateway-routed call nested underneath
as an LLM child (via the client-side chat span). Verdict-echo stamps get the
same AGENT turn shape, with the echoed word as output.value (tiny 8-token
budgets often return empty model text). A whole run groups into one Phoenix
Session (test.py: session = run-id, one turn per call; generate.py:
`cinematic-01-generate`; optimize.py: session = run-id), and the Sessions
row's first-input/last-output/user resolve from the AGENT roots.

The gateway metering path (LiteLLM otel callback → project `default`) is
untouched: span naming (`<run_id> <opt> eval|judge|films`),
`metadata.test_status` stamps and the `eval` span annotations keep landing
there. The session rides plain module state (`llm.SESSION_STATE`), not
contextvars — ambient propagation would not survive thread boundaries.
`--no-session` (test.py) or `llm.TRACING["enabled"] = False` disables it;
tracing stays best-effort and never fails the run.

## Optimizer runs

`optimize.py` (+ `optimize_common.py`) formalizes the prompt-optimizer sweep
that was proven in `scratch/` — which stays the playground for new optimizer
experiments. Each optimizer rewrites/recovers the studio-recall prompt, scored
by normalized exact match (`eval_text`). Two stages (`--stage`, default all):

- **sweep** — the micro 6/4 split (fixed seed, `TASK_MAX_TOKENS = 1536`) for
  the 9 kept optimizers (`--optimizer all`: `gepa`, 4×`depeval-*`,
  3×`dspy-*`, `adalflow-tgd`). The rewrite-only `refiner` baseline is explicit
  opt-in only, never part of `all`.
- **films** — every best prompt over the corpus slice (`--csv`, `--films N`,
  0 = all rows) at `FILMS_MAX_TOKENS = 8192` (INTENT §3 films budget), one
  Phoenix span per film.

Serial by construction: both engines run `--parallel 1`, so the films legs
score strictly sequentially — no worker flag here, wall time is the accepted
cost (~25 min for the full sweep plus hours for full-corpus legs). Tagging
rides the proven gateway `metadata.generation_name` path: spans land as
`<run_id> <opt> eval` / `<run_id> <opt> judge` / `<run_id> <opt> films`, and
`health_all()` gates the run on gateway + both llama servers.

Each result records the `render` kind its best prompt needs for the films leg
(`system` for gepa/dspy instructions that expect the film as the user message,
`filled` for `{film}` placeholder templates). Artifacts per run (`--run-id`
required): the out json (`datasets/cinematic-01/runs/<run-id>-optimize.json`,
sweep results + best prompts + films summaries) and one films json per
optimizer (`<run-id>-<opt>-films.json`, meta + per-film score/feedback).
Resume-safe: `--optimizer all` skips optimizers already recorded in the out
json (sweep) or already carrying a films entry (films legs); an explicit
`--optimizer <name>` always re-runs. For the user-facing howto (prereqs,
per-optimizer one-liners, trace verification, resume) see
[eduailab/nn-gentraces.md](../../eduailab/nn-gentraces.md).

### opt-scale-20260904 results

First formalized full run (2026-09-04), landed as a fresh Phoenix trace set
after the manual wipe: sweep ~19 min + 9 films legs (154 films each,
`FILMS_MAX_TOKENS = 8192`) ~2h04m, all 18 legs ok, no resume needed.

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

(task/judge = sweep leg call counts; each films leg adds exactly 154 task
calls and 0 judge calls. Micro-split before/after stays noisy at 4 films —
the corpus legs are the honest comparison, and there the spread is tight:
0.338–0.377 with dspy-bootstrap on top.)

Phoenix readback: 3422 spans labelled `opt-scale-20260904 ...` (43 unique
names), 44 judge-labelled spans across all 7 sweep judge legs, zero
judge-model leakage in non-judge run spans — check semantics PASS. Note for
future runs: `scratch/promptopt_phoenix_check.py`'s unbounded fetch only
reaches the most recent 1000 spans, which at this run size are all films
spans — bound the fetch by the run's time window (or raise the limit) to see
the sweep-stage judge spans.

Artifacts: `datasets/cinematic-01/runs/opt-scale-20260904-optimize.json` plus
one `opt-scale-20260904-<opt>-films.json` per optimizer.

## Layout

```
datasets/cinematic-01/dataset.csv     QUOTE_ALL dataset (studio name, film name, year)
datasets/cinematic-01/generate.json   per-call run log/checkpoint from generate.py
datasets/cinematic-01/runs/<run-id>/results.json   raw rows from one test.py run
datasets/cinematic-01/runs/<run-id>/eval.json      scored scenarios from one test.py run
datasets/cinematic-01/runs/<run-id>/report.md      rendered report (report.py, no live calls)
datasets/cinematic-01/results.json    "latest" copy of runs/<run-id>/results.json
datasets/cinematic-01/eval.json       "latest" copy of runs/<run-id>/eval.json
```

`<run-id>` defaults to `run-<YYYYMMDD-HHMMSS>-<model_alias>` (see `--run-id` in
test.py); each run's artifacts stay on disk, so its numbers stay reviewable.
The root-level `results.json`/`eval.json` copies are refreshed on every run so
tools that read the old fixed paths keep working.

### results.json shape

- `meta`: `name`, `test`, `run_id`, `run_at`, `model_alias`, `base_url`,
  `dataset`, `sample`, `year_tolerance`, `reasoning` (`"enabled"`).
- one block per scenario (`scenario_1_studio_recall`, `scenario_2_year_match`,
  `scenario_3_year_repeat`), each with a `score` string and `rows`. A row
  carries its scenario fields (`guess`, `expected`, `predicted`,
  `metric_score`) plus `answer`, `correct`, `reasoning` (thinking snippet),
  `seconds` and `usage` (`prompt_tokens`/`completion_tokens`/`total_tokens`).
  `run_name`/`resp_id` exist only in-process for the Phoenix span annotation
  and are popped before the artifacts are written.
- `eval.json` mirrors `meta` plus per-scenario metric/score/fraction summaries.

For observed values see a tracked run dir, e.g.
`datasets/cinematic-01/runs/temp02-cache-strip/`.

Mirrors `smoketests/cinematic-01/` so the dataset dir scopes the `cinematic-01` prefix.
