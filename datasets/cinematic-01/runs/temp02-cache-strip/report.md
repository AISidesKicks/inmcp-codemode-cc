# cinematic-01 smoke run: temp02-cache-strip

- **model**: `local-thinking` — LFM2.5-2.6B Q4_K_M GGUF (LocalAI llama.cpp)
- **gateway**: `http://localhost:4000` (LiteLLM, llama.cpp (Q4_K_M))
- **dataset**: `/home/roro/Workspaces/mcp-codemode-cc/datasets/cinematic-01/dataset.csv`
- **sample**: `2` rows (round-robin across studios)
- **mode**: `enabled` reasoning, `2` workers
- **run_at**: `2026-09-04T19:38:33+0200`
- **test**: `smoketests/cinematic-01/test.py`

## Scenarios

| # | Scenario | Metric | Score | Threshold | Pass |
|---|----------|--------|-------|-----------|------|
| 1 | Studio recall | manual exact match | **1/2** (50%) | — | — |
| 2 | Year match (±2) | abs diff <= 2 | **1/2** (50%) | — | — |
| 3 | Year repeat | deepeval.ExactMatchMetric | **0.5** | 0.8 | **FAIL** |

## Observations

| Scenario | Calls | Total tokens | Avg latency |
|----------|-------|--------------|-------------|
| 1 | 2 | 1088 | 4.99s |
| 2 | 2 | 651 | 1.98s |
| 3 | 2 | 620 | 1.95s |

## Miss detail — studio recall

| Studio | Film | Guessed |
|--------|------|---------|
| DC Studios | The Dark Knight | Warner Bros. Pictures |

## Re-run

```sh
pixi run cinematic-01-test -- --model local-thinking
pixi run cinematic-01-report
```

Artifacts for this report:

- `datasets/cinematic-01/runs/temp02-cache-strip/results.json` — raw rows
- `datasets/cinematic-01/runs/temp02-cache-strip/eval.json` — scored scenarios
- `datasets/cinematic-01/runs/temp02-cache-strip/report.md` — this report
- latest copies: `datasets/cinematic-01/results.json`, `eval.json`

---

*Rendered by `smoketests/cinematic-01/report.py` at 2026-09-04T19:38:51+0200.*
