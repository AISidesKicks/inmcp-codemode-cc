# cinematic-01 design notes

Field-decision notes for the `cinematic-01` micro dataset. Written down so the
"dataset as ground truth" choice stays deliberate, not accidental.

## Motivation

The lab is about *metering* and *billing* inference: tokens, cache hits, KV
reuse. The cinematic-01 dataset gives the smoke pipeline a small, fun, stable
set of studio->film->year triplets to run generate/test cycles against.

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
That is exactly what we care about for cache/eval demos, and it is honest about
the model's limitations.

## Structured output

Every call goes through `llm.chat()` with a Pydantic `response_format`
(`StudioList` / `FilmList` / `YearAnswer`) plus
`enable_json_schema_validation=True`. The 2.6B camel is comfortable emitting
small JSON. Reasoning is a test-time opt-in (`--reasoning`, mirroring
`generate.py`): off by default so the answer lands in `content` fast instead of
eating the token budget; when on, the engine gets headroom (max_tokens up to
1536) and the thinking text is recorded per call via
`llm.reasoning_content()`. The `generate.py` path keeps reasoning **off**
(`{"enabled": False}`) because the dataset needs clean, fast schema answers.

## Concurrency

The `test.py` step runs its three scenarios (studio recall, year match, year
repeat) through a `ThreadPoolExecutor` with `--workers` (default 4), matching
the engines' 4 parallel slots (llama.cpp `--parallel 4`, vLLM
`--max-num-seqs 4`, SGLang `--max-running-requests 4`) and the 128K contexts.
Row order stays deterministic (`executor.map`); each call resolves its own
master key per thread. The cache demo stays sequential so Q2 can observe Q1's
Redis hit. Run mode is recorded in `meta` (`reasoning: enabled/disabled`,
`workers`) and shown in the rendered report.

## Dedup and the year guard

- Film titles are deduped **exactly** on the normalized title (letters+digits,
  lowercase) across studios — otherwise shared titles (e.g. a franchise owned by
  multiple studios) would double-count in the year scenarios.
- Years are guarded to `1900..2023`. Out-of-range model guesses are recorded in
  the run log (`year_valid: false`) and **excluded from the CSV** so the
  dataset stays clean and testable.

## Caching regime is part of the data

Each call passes `cache={}` (LiteLLM Redis caching on; boolean `True` regresses
with 400s). The run log keeps per-call `cache_regime`
(`litellm-redis-hit`/`miss` from the hidden `x-litellm-cache-key` header) and
llama.cpp `timings` (`prompt_n`, `cache_n`, `predicted_n` from `model_extra`)
so an eval can show cost-to-serve per prompt.

## Layout

```
datasets/cinematic-01/dataset.csv     QUOTE_ALL dataset (studio name, film name, year)
datasets/cinematic-01/generate.json   per-call run log/checkpoint from generate.py
datasets/cinematic-01/runs/<run-id>/results.json   raw rows from one test.py run
datasets/cinematic-01/runs/<run-id>/eval.json      scored scenarios from one test.py run
datasets/cinematic-01/results.json    "latest" copy of runs/<run-id>/results.json
datasets/cinematic-01/eval.json       "latest" copy of runs/<run-id>/eval.json
```

`<run-id>` defaults to `run-<YYYYMMDD-HHMMSS>-<model_alias>` (see `--run-id` in
test.py) and is tracked, so each run's numbers stay reviewable in history. The
root-level `results.json`/`eval.json` copies are refreshed on every run so tools
that read the old fixed paths keep working.

Mirrors `smoketests/cinematic-01/` so the dataset dir scopes the `cinematic-01` prefix.