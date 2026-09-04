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

## Concurrency

Both engines run single-slot (`--parallel 1` on both containers in
`docker/docker-compose.yml`), so `test.py --workers` (default 4) bounds
client-side concurrency only — extra requests just queue in llama.cpp. Row
order stays deterministic (`executor.map`); each call resolves its own master
key per thread. Run mode is recorded in `meta` (`reasoning: enabled`,
`workers`) and shown in the rendered report.

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
  `dataset`, `sample`, `year_tolerance`, `reasoning` (`"enabled"`), `workers`.
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
