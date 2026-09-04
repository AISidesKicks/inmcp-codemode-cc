# Minimal docker lab stack

Lean 3-service lab env: llama.cpp server + LiteLLM gateway + Arize Phoenix.
All resources carry the `cmod-` prefix (project rule). No Postgres, no Redis,
no cache, no exporters, no monitoring sidecars — Phoenix falls back to SQLite
and LiteLLM keeps everything in-memory.

## Prerequisites

- Docker Engine 24+ and Docker Compose v2 (`docker compose version` to check;
  no `version:` key needed in the compose file)
- NVIDIA GPU + `nvidia-container-toolkit` for the `lab` profile (llama.cpp
  runs on the GPU); the `phoenix` profile alone runs anywhere

## Setup

1. Download the GGUF checkpoint into `docker/models/Q8/` (the container mounts
   `./models:/models:ro`, so it lands at `/models/Q8/`):

   ```sh
   curl -L -o docker/models/Q8/LFM2.5-2.6B-Q8_0.gguf \
     https://huggingface.co/LiquidAI/LFM2.5-2.6B-GGUF/resolve/main/LFM2.5-2.6B-Q8_0.gguf
   ```

   or via the HF CLI:

   ```sh
   huggingface-cli download LiquidAI/LFM2.5-2.6B-GGUF \
     LFM2.5-2.6B-Q8_0.gguf --local-dir docker/models/Q8/
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
for both `cmod-llama` and `cmod-phoenix` healthchecks before starting.

Stop / tear down:

```sh
docker compose -f docker/docker-compose.yml stop
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml down -v   # wipes the Phoenix volume too
```

## WebUI

llama-server ships a built-in WebUI — with the `lab` profile up, open
http://localhost:8080. The UI serves at `/` and the OpenAI-compatible API at
`/v1`. Loaded model: `LiquidAI/LFM2.5-2.6B` (Q8_0 GGUF), 64K ctx, single slot.

Chatting there hits the engine directly — no LiteLLM metering, no Phoenix
trace. Route via the gateway on 4000 (`local-gguf`) for that; see Traces.

## Port map

| Port | Service    | Notes                                            |
|------|------------|--------------------------------------------------|
| 4000 | LiteLLM    | OpenAI-compatible gateway                        |
| 8080 | llama.cpp  | API + built-in WebUI at `/`, 64K ctx, single slot, `lab` profile |
| 6006 | Phoenix    | UI + OTLP HTTP (`/v1/traces`) + MCP (`/mcp`), `lab` + `phoenix` profiles |

Heads-up: the pixi env also ships a native `litellm` package — a native
`litellm --port 4000` run clashes with the container port, and a native
llama-server would clash on 8080. Run one or the other.

## Model aliases in LiteLLM

| Alias        | Backend   | Notes                                    |
|--------------|-----------|------------------------------------------|
| `local-gguf` | llama.cpp | serves `LiquidAI/LFM2.5-2.6B` (Q8_0 GGUF) |

## Traces

LiteLLM is wired for OpenTelemetry in `litellm_config.yaml`
(`callbacks: ["otel"]`, `otel: true`) plus the `OTEL_EXPORTER_OTLP_*` env in
the compose file — every request through the gateway lands as a trace in
Phoenix: litellm → OTLP HTTP → `http://cmod-phoenix:6006/v1/traces` →
Phoenix UI at http://localhost:6006. Phoenix MCP is used directly at
`http://localhost:6006/mcp` (not proxied through LiteLLM). Chatting via the
llama-server WebUI on 8080 bypasses the gateway — no metering, no Phoenix
trace; route via 4000 (`local-gguf`) for that.

## Lean choices (deliberate)

- No Postgres / Redis: LiteLLM runs in-memory, Phoenix on SQLite
- No `--cache-ram` / `--cache-reuse` / multi-slot on llama.cpp: single slot
  with 64K ctx, KV buffer ~1 GiB f16 — fits the card fine
- No exporters / VictoriaMetrics: the lab's focus is execution traces
- No `mcp_servers` section in the LiteLLM config: Phoenix MCP is reached
  directly

## Phoenix data backup

Phoenix state lives in the named volume `cmod-phoenix-data`:

```sh
docker run --rm -v cmod-phoenix-data:/data -v $SCRATCH:/backup alpine \
  tar czf /backup/cmod-phoenix-backup.tgz /data
```