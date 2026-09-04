# Minimal docker lab stack

Lean 4-service lab env: two llama.cpp servers (tested model + judge) +
LiteLLM gateway + Arize Phoenix.
All resources carry the `cmod-` prefix (project rule). No Postgres, no Redis,
no cache, no exporters, no monitoring sidecars — Phoenix falls back to SQLite
and LiteLLM keeps everything in-memory.

The model split: `cmod-llama` hosts the judge (`ibm-granite/granite-4.0-h-tiny`
UD-Q4_K_XL via unsloth, 64K ctx — non-thinking, so its whole budget goes to
reflection/JSON text) and `cmod-llama-thinking` hosts the tested model
(`LiquidAI/LFM2.5-2.6B` Q4_K_M, 32K ctx — it thinks, and llama.cpp splits its
reasoning cleanly). Both models are routable through LiteLLM — the tested
model as `local-thinking`, the judge as `local-judge` — and sweep traffic tags
its calls with `metadata.generation_name`, so Phoenix spans land as
`<run_id> <opt> eval` / `<run_id> <opt> judge`. Direct :8080 stays for ad-hoc
probes and the WebUI (untraced).

## Prerequisites

- Docker Engine 24+ and Docker Compose v2 (`docker compose version` to check;
  no `version:` key needed in the compose file)
- NVIDIA GPU + `nvidia-container-toolkit` for the `lab` profile (llama.cpp
  runs on the GPU); the `phoenix` profile alone runs anywhere

## Setup

1. Download the GGUF checkpoints into `docker/models/` (the containers mount
   `./models:/models:ro`, so they land at `/models/`):

   ```sh
   curl -fL --retry 3 -o docker/models/granite-4.0-h-tiny-UD-Q4_K_XL.gguf \
     https://huggingface.co/unsloth/granite-4.0-h-tiny-GGUF/resolve/main/granite-4.0-h-tiny-UD-Q4_K_XL.gguf

   curl -fL --retry 3 -o docker/models/LFM2.5-2.6B-Q4_K_M.gguf \
     https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF/resolve/main/LFM2.5-2.6B-Q4_K_M.gguf
   ```

   `docker/models/**/*.gguf` is gitignored.

2. Keys: `docker/.env.example` is committed; the stack boots with its default
   `LITELLM_MASTER_KEY` even with no `.env` at all. Copy it to `docker/.env`
   only if you want to override the key.

## Run

Observability only (Phoenix, for native pixi lab runs / trace inspection):

```sh
docker compose -f docker/docker-compose.yml --profile phoenix up -d
```

Full lab stack (llama + gateway + Phoenix):

```sh
docker compose -f docker/docker-compose.yml --profile lab up -d
```

Bare `up` starts nothing — every service is behind a profile. LiteLLM waits
for `cmod-llama`, `cmod-llama-thinking` and `cmod-phoenix` healthchecks
before starting.

Stop / tear down:

```sh
docker compose -f docker/docker-compose.yml stop
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml down -v   # wipes the Phoenix volume too
```

## WebUI

Both llama-servers ship a built-in WebUI — with the `lab` profile up, open
http://localhost:8080 (judge, `ibm-granite/granite-4.0-h-tiny` UD-Q4_K_XL GGUF,
64K ctx, single slot) or http://localhost:8081 (tested,
`LiquidAI/LFM2.5-2.6B` Q4_K_M GGUF, 32K ctx, single slot). The UIs serve at
`/` and the OpenAI-compatible API at `/v1`.

Chatting there hits the engines directly — no LiteLLM metering, no Phoenix
trace. Route via the gateway on 4000 (`local-judge` / `local-thinking`) for
that; see Traces.

## Port map

| Port | Service            | Notes                                            |
|------|--------------------|--------------------------------------------------|
| 4000 | LiteLLM            | OpenAI-compatible gateway                        |
| 8080 | llama.cpp (judge)  | API + WebUI at `/`, granite-4.0-h-tiny Q4_K_XL, 64K ctx, single slot, `lab` profile |
| 8081 | llama.cpp (tested) | API + WebUI at `/`, 2.6B Q4_K_M, 32K ctx, single slot, `lab` profile |
| 6006 | Phoenix            | UI + OTLP HTTP (`/v1/traces`) + MCP (`/mcp`), `lab` + `phoenix` profiles |

Heads-up: the pixi env also ships a native `litellm` package — a native
`litellm --port 4000` run clashes with the container port, and a native
llama-server would clash on 8080. Run one or the other.

## Model aliases in LiteLLM

| Alias           | Backend            | Notes                                        |
|-----------------|--------------------|----------------------------------------------|
| `local-judge`   | llama.cpp (8080)   | judge, serves `ibm-granite/granite-4.0-h-tiny` Q4_K_XL |
| `local-thinking`| llama.cpp (8081)   | tested, serves `LiquidAI/LFM2.5-2.6B` Q4_K_M |

## Sampling parameters (engine defaults)

Sampling is controlled **only** by llama.cpp engine flags in
`docker-compose.yml` — no lab client (smoketests, sweep, WebUI) sends a
`temperature`, and the LiteLLM config sets no sampling defaults, so whatever
the engine pins governs all traffic through the gateway:

| Service               | Flags                                                   | Source |
|-----------------------|---------------------------------------------------------|--------|
| `cmod-llama-thinking` (tested) | `--temp 0.1 --top-k 50 --repeat-penalty 1.1`   | LiquidAI LFM2.5-2.6B model card generation trio |
| `cmod-llama` (judge)  | `--temp 0.1 --top-p 0.9`                                | lab research (granite card publishes no sampling spec) |

The judge engine keeps llama.cpp's default `top_p 0.95` — the granite card
specifies none — while the tested engine keeps default `top_p 0.95` (vendor
specifies only temp/top-k/repeat-penalty).

Reasoning is **on only** for the tested engine: it starts with
`--reasoning on --reasoning-format deepseek --reasoning-preserve` and lab code
sends `reasoning: {"enabled": True}` on every call. LFM2.5-2.6B is a pure
reasoning model (its template hardcodes the think-open), so the old
budget-0 `reasoning_budget_tokens: 0` off-switch was removed from
`llm.py` — always-on needs the larger `max_tokens` budgets (1536) the
smoketests now default to. The judge (granite) is non-thinking and runs its
whole budget on reflection/JSON text.

## Traces

LiteLLM is wired for OpenTelemetry in `litellm_config.yaml`
(`callbacks: ["otel"]`, `otel: true`) plus the `OTEL_EXPORTER_OTLP_*` env in
the compose file — every request through the gateway lands as a trace in
Phoenix: litellm → OTLP HTTP → `http://cmod-phoenix:6006/v1/traces` →
Phoenix UI at http://localhost:6006. Calls that send
`metadata.generation_name` (the sweep does via `extra_body={"metadata": ...}`)
get it as the Phoenix span name — `<run_id> <opt> eval` for the tested model,
`<run_id> <opt> judge` for the judge. Phoenix MCP is used directly at
`http://localhost:6006/mcp` (not proxied through LiteLLM). Chatting via the
llama-server WebUI on 8080 bypasses the gateway — no metering, no Phoenix
trace; route via 4000 (`local-judge` / `local-thinking`) for that.

## Lean choices (deliberate)

- No Postgres / Redis: LiteLLM runs in-memory, Phoenix on SQLite
- No `--cache-ram` / `--cache-reuse` / multi-slot: two single-slot instances
  (judge 64K ctx, tested 32K ctx) stay independent — separate lifecycle,
  healthchecks and ctx budgets, and both fit the card with headroom
- Non-thinking granite judge (mamba-attention hybrid arch): 64K ctx is cheap
  (linear-scaling KV) and the no-think template puts the whole budget into
  reflection/JSON text instead of reasoning tokens
- Judge via the gateway for tagged sweep traffic only: reflection calls are
  chatty, so sweeps send `metadata.generation_name` and Phoenix shows compact
  `<run_id> <opt> judge` spans; ad-hoc probes / the WebUI stay on direct
  `localhost:8080`, untraced
- No exporters / VictoriaMetrics: the lab's focus is execution traces
- No `mcp_servers` section in the LiteLLM config: Phoenix MCP is reached
  directly

## Phoenix data backup

Phoenix state lives in the named volume `cmod-phoenix-data`:

```sh
docker run --rm -v cmod-phoenix-data:/data -v $SCRATCH:/backup alpine \
  tar czf /backup/cmod-phoenix-backup.tgz /data
```