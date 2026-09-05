# Per-test-type timing (cinematic-01)

How long does each cinematic-01 test type actually take? Three types, measured
from both the **client view** (what the test process sees) and the **Phoenix
trace view** (what the gateway saw), one sample run, `2026-09-05`, clean
`default` project. Sample size is tiny (2 films, 4 val rows) and the models are
tiny too — read the numbers as **ballpark, not benchmark** (±1-2s call-to-call
is normal on local GGUF).

Echo-verdict overhead is explicitly out of scope here (decision: ignore — the
echoes are metadata stamps, not tests). For the big sweep/films legs (~2.5h)
see [nn-gentraces.md](nn-gentraces.md).

## Prereqs

Same stack as [nn-gentraces.md](nn-gentraces.md): `cmod-litellm` (:4000),
`cmod-llama` (:8080 judge), `cmod-llama-thinking` (:8081 tested),
`cmod-phoenix` (:6006). Run inside the `cdmd` pixi shell. For clean numbers
start from an empty Phoenix `default` project (wiping `cmod-phoenix-data` is
HITL user work — agents never touch `cmod-*` volumes). Give every type its own
`--run-id` — the run_id is the first token of every span name and drives all
grouping/filtering (stamper: [docker/otel_kind_stamper.py](../docker/otel_kind_stamper.py)).

## The three commands

```bash
# 1. echo probes (verdict stamping, ~0.15s each) — add -w '%{time_total}' for client wall
curl -s -X POST http://localhost:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-cmod-lab-81f4e2d7c9a5-master-key' \
  -d '{"model":"local-judge","messages":[{"role":"user","content":"Echo back to me: TEST FAIL"}],"max_tokens":8,"metadata":{"generation_name":"timing-probe-YYYYMMDD probe FAIL studio-recall"}}' \
  -w '%{time_total}'
# (second call with TEST PASS / probe PASS)

# 2. naive smoke test (task calls + echo stamps + phoenix annotations)
/usr/bin/time pixi run python smoketests/cinematic-01/test.py --sample 2 --skip-health --run-id timing-smoke-YYYYMMDD

# 3. one optimizer leg (rewrite-only refiner baseline)
/usr/bin/time pixi run python smoketests/cinematic-01/optimize.py --stage sweep --optimizer refiner --run-id timing-refiner-YYYYMMDD
```

`--sample 2` keeps the smoke leg at coffee scale; drop it for the full 154-row
run. `/usr/bin/time` gives the process wall; the optimizer json adds its own
`wall_seconds` (measured inside the run, excludes startup).

## Client view

Measured 2026-09-05 (`timing-probe/smoke/refiner-20260905`):

| type | wall | LLM calls | per-call seconds |
| --- | --- | --- | --- |
| echo probes | 0.29s (2 calls) | 2 | 0.144 / 0.146 (`time_total`) |
| naive smoke | 23.3s process | 6 task + 6 echo | task rows 3.34 / 5.41 / 4.16 / 5.41 / 2.49 / 1.32 — mean 3.69, sum 22.1; echoes untimed (ignored) |
| refiner leg | 24.4s optimizer wall (27.8s process) | 8 task + 1 judge + 8 echo | per-call not recorded — task/judge seconds stay in `optimize_common.STATS` and never reach the json (known gap); the trace view below fills that hole |

Where the per-call numbers come from: probe `time_total`; smoke rows carry
their own `seconds` in `runs/<run-id>/results.json`
([report.py](../smoketests/cinematic-01/report.py) has the avg helpers);
refiner only reports counts + wall.

Workers note: `test.py` defaults to `--workers 4`, so rows inside a scenario
run concurrently (scenarios themselves are sequential). Per-scenario wall is
the **max** of its rows, not the sum: 5.41 + 5.41 + 2.49 ≈ 13.3s of scenario
time inside the 23.3s wall (rest ≈ 6s python/dataset startup, ~0.3s echoes,
~3.5s parsing + json writes + 6 Phoenix annotation writes). The naive row sum
(22.1s) double-counts concurrent rows.

## Trace view (Phoenix)

Group by `metadata.run_id`; every gateway request = 1 trace = 1 CHAIN root
(`Received Proxy Server Request`, the gateway wall per request) + **exactly 2
LLM spans** (the `litellm_request`/`raw_gen_ai_request` pair, both renamed to
the `generation_name`) + 5 UNKNOWN noise spans. Span classification:
`metadata.test_status` set → echo, `judge` in name → judge, else task.

| type × category | traces | root total / mean / max (s) |
| --- | ---: | --- |
| probe (echo-named, `test_status` FAIL/PASS) | 2 | 0.282 / 0.141 / 0.143 |
| smoke task | 6 | 20.45 / 3.41 / 5.41 |
| smoke echo | 6 | 0.338 / 0.056 / 0.059 |
| refiner task — before | 4 | 8.41 / 2.10 / 2.55 |
| refiner task — after | 2 of 4 ⚠ | 8.56 / 4.28 / 5.83 |
| refiner judge | 1 | 0.24 / 0.24 / 0.24 |
| refiner echo | 4 of 8 ⚠ | 0.45 / 0.11 / 0.16 |

⚠ = the export-drop incident, see below — those traces never reached Phoenix.

Noise anatomy (5 spans/trace): `auth` and `proxy_pre_call` are ~0ms, but
litellm's `router` and `self` wrapper spans each cover the whole model call —
noise ≈ **2× the root duration** in span-seconds. Don't sum "all spans" and
call it workload.

LLM spans per request carry the same duration as their root (±5ms) — one
request, two spans, one wall clock. So: root total 20.45s for smoke ≈ the
sum of the 6 client row seconds minus client-side overhead.

## Reconciling the two views

- **Client wall ≈ gateway root within ~10ms** once the SDK is warm
  (year/repeat rows: Δ 6-8ms). The **first litellm calls of a process** pay a
  client-side warmup: recall Iron Man measured 3.34s client vs 1.95s root
  (~1.4s of litellm SDK init), its concurrent sibling +0.26s. Both views are
  honest — they just measure different walls.
- **2-LLM-spans-per-request invariant**: held 25/25 complete traces (50 LLM
  spans + 25 roots + 128 noise = 203 spans).
- **Phoenix can undercount.** Client sent 31 requests (2 + 12 + 17); Phoenix
  held only 25 complete traces + 3 orphaned `auth`/`router` spans. The
  gateway's final OTLP export batch died with `ConnectionResetError(104)`
  ("Exception while exporting Span batch", verified in `docker logs
  cmod-litellm`) — 5 refiner requests (2 after-task + 4 after-echo… minus one
  partial) completed 200 OK but their traces were dropped. When trace counts
  and client counts disagree, the **gateway access log is ground truth**.
  Refiner wall sanity check with log-confirmed counts still reconciles:
  ~8.4s before + ~0.5s echoes + 0.24s judge + ~14.5s after + slack ≈ 24.4s.

## Methodology notes

- **Export lag**: BatchSpanProcessor ships batches every few seconds — wait
  ~8-15s after a run before Phoenix queries, and don't trust a too-early
  empty result.
- **Filtering**: flat span attributes `metadata.run_id`,
  `metadata.test_status`, `metadata.generation_name` (stamped from the span
  name by the stamper). `getSpans` accepts e.g.
  `attribute: ["metadata.test_status:FAIL"]`. Group traces by
  `context.trace_id` — `trace_id`/`parent_id` can be absent from getSpans
  payloads, so classify the root by `span_kind == "CHAIN"`, not parent checks.
- **Tiny models, tiny sample**: n=2 films, n=4 val rows, single run. Times
  swing ±1-2s per call between runs; use this doc for order-of-magnitude and
  methodology, not for regression gates.
- **Span duration is gateway-side** — it excludes client queue/SDK time (see
  warmup above) and includes llama-server wait.
- Echo-verdict calls are metadata stamps: cheap (max_tokens=8, ~0.05-0.15s
  gateway-side, prompt cache makes them faster as the run warms up) and
  deliberately untimed client-side.

Full-sweep and films-leg timings (~2.5h / coffee-break variants) live in
[nn-gentraces.md](nn-gentraces.md).
