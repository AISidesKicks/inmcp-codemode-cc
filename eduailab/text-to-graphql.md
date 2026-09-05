# Text-to-GraphQL MCP (`cmod-text-to-graphql`, the anti-ZTA demo)

Howto for a lab user driving an AI harness (Kilo/opencode MCP) who wants to
watch the opposite of the Zero Token Architecture strips
([INTENT.md](../INTENT.md)): instead of you writing graphQL against Phoenix,
an LLM writes it for you. Same `localhost:6006/graphql` endpoint, same data —
nonzero token bill. This is Arize's
[text-to-graphql-mcp](https://github.com/Arize-ai/text-to-graphql-mcp)
dockerized as the fifth lab service (stack in
[docker/README.md](../docker/README.md)) and registered as a remote MCP in
your harness config.

Time warning up front: one `generate_graphql_query` call runs a multi-call
LangGraph loop (introspection → options → construct → validate → optimize ×3
→ execute) against a small local model — expect **tens of seconds to a couple
of minutes**, hence the 120s MCP timeout. Every one of those LLM calls lands
as a Phoenix trace. That's the point.

## Wiring (compose env vars)

| Env var | Value | What it does |
| --- | --- | --- |
| `OPENAI_BASE_URL` | `http://cmod-litellm:4000/v1` | generator rides the LiteLLM gateway → every call traced |
| `OPENAI_API_KEY` | LiteLLM master key | gateway auth |
| `MODEL_NAME` | `local-judge` | granite-4.0-h-tiny as generator; swap to `local-thinking` = one line, next experiment |
| `MODEL_TEMPERATURE` | `0` | the one deliberate break of the engine-only sampling rule — a LangGraph agent always sends a temperature |
| `GRAPHQL_ENDPOINT` | `http://cmod-phoenix:6006/graphql` | the target; introspected on first call, cached in the container layer |

Serving notes: built from GitHub `main` (the PyPI 0.1.3 wheel is broken — it
still imports the dead `langchain.prompts`), and a compose `command` override
flips FastMCP from stdio to streamable HTTP on :8000 (`/mcp`). No code
changes, `lab` profile.

## How to drive it from the harness

`opencode.json` (untracked lab-local config) registers it as the remote MCP
`text-to-graphql` (`http://localhost:8000/mcp`, 120s timeout). Restart your
harness with that config and you get 5 tools:

| tool | what it does |
| --- | --- |
| `generate_graphql_query` | the full agent loop: introspect Phoenix schema → construct → validate → optimize → report |
| `validate_graphql_query` | validate-only pass |
| `execute_graphql_query` | POST a given query to Phoenix → data + viz recommendation |
| `get_query_history` | every generate/execute attempt so far, with error texts |
| `get_query_examples` | canned example queries |

Latency heads-up: `generate` is the slow one (the loop above); validate /
execute / history are instant. Prereq: the `lab` profile is up
(`--profile lab` brings all five services including this one).

## Live lab walkthrough (2026-09-05)

Three probes, generator = granite judge (`local-judge`), all queries and
error texts quoted from `get_query_history` (ids 1–9):

**Probe 1 — miss.** "Show the project name and the total trace count" →

```graphql
query GetProjectTraceCount($projectId: ID!) {
  project(id: $projectId) {
    name
    traceCount(timeRange: ALL_TIME, filterCondition: "", sessionFilterCondition: "")
  }
}
```

Validate caught it — `Cannot query field 'project' on type 'Query'. Did you
mean 'projects'?` — and the agent got stuck resubmitting the same invalid
query.

**Probe 2 — near-miss, then worse.** "List the first 3 projects with their
names and trace counts" produced the right connection shape once, but
hallucinated enum args on `traceCount`:

```graphql
query {
  projects(first: 3) {
    edges {
      node {
        name
        traceCount(timeRange: ALL_TIME, filterCondition: "", sessionFilterCondition: "")
      }
    }
  }
}
```

→ `Expected value of type 'TimeRange', found ALL_TIME.` Retries with tighter
prompts ("Do not pass any optional arguments, use bare field names only")
then made it drop the connection traversal entirely — `name` / `traceCount`
put directly on `projects` → `Cannot query field 'name' on type
'ProjectConnection'.`

**Probe 3 — success, from the harness.** The winning prompt names the pattern
verbatim:

> Show the name and trace count of the first 3 projects. Traverse the
> connection pattern exactly: projects { edges { node { ... } } }. Pass no
> arguments to traceCount.

→ valid on the first try, no retries:

```graphql
query {
  projects(first: 3) {
    edges {
      node {
        name
        traceCount
      }
    }
  }
}
```

`execute_graphql_query` → `SUCCESS`: project `default`, `traceCount` 1754 as
of 2026-09-05 (the number only goes up — the service traces its own calls),
plus a `data_grid` visualization recommendation ("Project Trace Counts").

## Findings

- **Model-bound, not stack-bound.** The plumbing works end-to-end; granite
  4.0-h-tiny can't rediscover the Relay `edges { node }` connection pattern
  from introspection alone. The original Arize demo runs on gpt-4o.
- **Prompt shape is the lever.** Naming the traversal pattern in the prompt
  flipped miss → valid on the first try; "be explicit about optional args"
  alone made it worse.
- **Next experiment:** `MODEL_NAME=local-thinking` in `docker-compose.yml`
  (one line) — can the thinking model find the pattern unaided?
- **Upstream bug:** the `validate_graphql_query` MCP tool crashes on Arize
  `main` with `GraphQLAgent.validate_query() missing 1 required positional
  argument: 'session_id'`. Report-as-finding, not fixed here.

## Tokenomics punchline

ZTA strip ([INTENT.md](../INTENT.md)): the same question as one hand-written
graphQL POST = **0** LLM tokens. This path: a dozen judge calls per question,
all visible as Phoenix traces — the project's own traceCount climbed
1727 → 1745 → 1754 during the 2026-09-05 session, each step being the service
pricing itself into the very data it queries.

## Prior art

- `scratch/test_ttgql_mcp.py` — raw MCP handshake + `generate` + `execute`
  probe (streamable HTTP, session id and all)
- `scratch/test_ttgql_once.py` — single-shot:
  `pixi run python scratch/test_ttgql_once.py "<your question>"`

`scratch/` is gitignored; it persists locally. The probes speak raw
JSON-RPC/SSE over streamable HTTP, so they double as a stack sanity check
when the harness is out of the picture.
