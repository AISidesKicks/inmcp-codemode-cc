# Minimal docker lab stack

Lean 4-service lab env: two llama.cpp servers (tested model + judge) +
LiteLLM gateway + Arize Phoenix.
All resources carry the `cmod-` prefix (project rule). No Postgres, no Redis,
no cache, no exporters, no monitoring sidecars — Phoenix falls back to SQLite
and LiteLLM keeps everything in-memory.

The model split: `cmod-llama` hosts the judge (`LiquidAI/LFM2.5-2.6B`
Q4_K_M, 64K ctx) and `cmod-llama-thinking` hosts the tested model
(`LiquidAI/LFM2.5-1.2B-Thinking` UD-Q4_K_XL via unsloth, 32K ctx). Only the
tested model is routed through LiteLLM (and thus traced in Phoenix); the
judge is called directly on its port — no gateway, no trace.

## Prerequisites

- Docker Engine 24+ and Docker Compose v2 (`docker compose version` to check;
  no `version:` key needed in the compose file)
- NVIDIA GPU + `nvidia-container-toolkit` for the `lab` profile (llama.cpp
  runs on the GPU); the `phoenix` profile alone runs anywhere

## Setup

1. Download the GGUF checkpoints into `docker/models/` (the containers mount
   `./models:/models:ro`, so they land at `/models/`):

   ```sh
   curl -fL --retry 3 -o docker/models/LFM2.5-2.6B-Q4_K_M.gguf \
     https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF/resolve/main/LFM2.5-2.6B-Q4_K_M.gguf

   curl -fL --retry 3 -o docker/models/LFM2.5-1.2B-Thinking-UD-Q4_K_XL.gguf \
     https://huggingface.co/unsloth/LFM2.5-1.2B-Thinking-GGUF/resolve/main/LFM2.5-1.2B-Thinking-UD-Q4_K_XL.gguf
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
http://localhost:8080 (judge, `LiquidAI/LFM2.5-2.6B` Q4_K_M GGUF, 64K ctx,
single slot) or http://localhost:8081 (tested, `LiquidAI/LFM2.5-1.2B-Thinking`
UD-Q4_K_XL GGUF, 32K ctx, single slot). The UIs serve at `/` and the
OpenAI-compatible API at `/v1`.

Chatting there hits the engines directly — no LiteLLM metering, no Phoenix
trace. Route via the gateway on 4000 (`local-judge` / `local-thinking`) for
that; see Traces.

## Port map

| Port | Service            | Notes                                            |
|------|--------------------|--------------------------------------------------|
| 4000 | LiteLLM            | OpenAI-compatible gateway                        |
| 8080 | llama.cpp (judge)  | API + WebUI at `/`, 2.6B Q4_K_M, 64K ctx, single slot, `lab` profile |
| 8081 | llama.cpp (tested) | API + WebUI at `/`, 1.2B-Thinking Q4_K_XL, 32K ctx, single slot, `lab` profile |
| 6006 | Phoenix            | UI + OTLP HTTP (`/v1/traces`) + MCP (`/mcp`), `lab` + `phoenix` profiles |

Heads-up: the pixi env also ships a native `litellm` package — a native
`litellm --port 4000` run clashes with the container port, and a native
llama-server would clash on 8080. Run one or the other.

## Model aliases in LiteLLM

| Alias           | Backend            | Notes                                        |
|-----------------|--------------------|----------------------------------------------|
| `local-judge`   | llama.cpp (8080)   | judge, serves `LiquidAI/LFM2.5-2.6B` Q4_K_M  |
| `local-thinking`| llama.cpp (8081)   | tested, serves `LiquidAI/LFM2.5-1.2B-Thinking` Q4_K_XL |

## Traces

LiteLLM is wired for OpenTelemetry in `litellm_config.yaml`
(`callbacks: ["otel"]`, `otel: true`) plus the `OTEL_EXPORTER_OTLP_*` env in
the compose file — every request through the gateway lands as a trace in
Phoenix: litellm → OTLP HTTP → `http://cmod-phoenix:6006/v1/traces` →
Phoenix UI at http://localhost:6006. Phoenix MCP is used directly at
`http://localhost:6006/mcp` (not proxied through LiteLLM). Chatting via the
llama-server WebUI on 8080 bypasses the gateway — no metering, no Phoenix
trace; route via 4000 (`local-judge` / `local-thinking`) for that.

## Lean choices (deliberate)

- No Postgres / Redis: LiteLLM runs in-memory, Phoenix on SQLite
- No `--cache-ram` / `--cache-reuse` / multi-slot: two single-slot instances
  (judge 64K ctx, tested 32K ctx) stay independent — separate lifecycle,
  healthchecks and ctx budgets, and both fit the card with headroom
- Judge bypasses the gateway entirely: reflection calls are chatty and
  uninteresting as traces; direct `localhost:8080` calls keep Phoenix
  scoped to tested-model runs
- No exporters / VictoriaMetrics: the lab's focus is execution traces
- No `mcp_servers` section in the LiteLLM config: Phoenix MCP is reached
  directly

## Phoenix data backup

Phoenix state lives in the named volume `cmod-phoenix-data`:

```sh
docker run --rm -v cmod-phoenix-data:/data -v $SCRATCH:/backup alpine \
  tar czf /backup/cmod-phoenix-backup.tgz /data
```