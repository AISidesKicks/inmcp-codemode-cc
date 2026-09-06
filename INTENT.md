# mcp.codemode.cc

Note: Update mainly this doc, expand AGENTS.md only with final project dir structure.

Clone as much of structure and logic from https://localai.isnot.cheap , https://github.com/AISidesKicks/localai-isnot-cheap

## Project context

This is an **educational EDU AI LAB** project — "MCP codemode (Contrast & Compare)".

It demonstrates how AI harnesses interact with complex data structures using: MCP, codemode, cli+skills, ZTA

Your goal is to create an exploratory lab which is driven by AI Harness and explore 4 modes: MCP LLM, MCP codemode, Agent skills + CLI, ZTA

Lab must clearly demonstrate differences in tokenomics for all 4 modes.

The lab doesn't need to be perfect or very descriptive - main goal is to provide hands-on experience inside AI Harness.

User can proactively ask AI Harness - so keep info condensed, AI will create dynamic follow-up, if needed.

Ideally lab will also deliver screenshots of observability GUIs - so newcomers can explore it too.

Ideally we want also to ship BACKUP of observability data - so MCP part of lab can be executed without painful LLM runs.

We also want to demonstrate ZTA (Zero Token Architecture) - report strips in Python, zero LLM tokens. Three direct interfaces against the running Phoenix (no MCP, no harness):

- SDK: `arize-phoenix-client` (installed) - spans / traces / annotations / experiments helpers
- REST API: plain `requests` against `http://localhost:6006/v1/...`
- graphQL: POST `http://localhost:6006/graphql` - same endpoint the Phoenix UI itself uses; one query, one round trip, project stats included:
  `{ projects(first: 3) { edges { node { name traceCount tokenCountTotal } } } }` (verified live on Phoenix 20.7); mutations too (`createProject`, `transferTracesToProject`, Phoenix 11.9+)

Contrast & compare punchline: `text-to-graphql-mcp` is the anti-ZTA - an LLM writes the graphQL for you (gpt-4o via LangGraph). Same data, nonzero token bill.

Status: dockerized in the lab as `cmod-text-to-graphql` (judge as generator via LiteLLM) and harness-tested 2026-09-05 — granite needs the connection pattern named in the prompt; howto in `eduailab/text-to-graphql.md`. Note: the `cmod-litellm` gateway sits stopped since the 2026-09-06 engine-direct switch (harness no longer routes through it) — container removal pending.

# The EDU AI LAB

## 1. AI Harness

- opencode compatible harness, we use **kilo cli**

The goal of the lab is to create /skills for 3 modes in 3 dirs, can be switched together with MCPs by opencode config.

## 2. MCP provider

- Arize Phoenix: Built-in MCP with LLM and codemode

## 3. Lab environment
 - two small llms in llama-server + Arize Phoenix (LiteLLM gateway stopped 2026-09-06 — harness is engine-direct, removal pending)

**Note:** As leaner setup as possible (no external DBs - main focus on execution traces ONLY!)

Docker variant of the lab env lives in `docker/` — two llama.cpp servers
(non-thinking granite judge @ 64K ctx + thinking LFM2.5-2.6B tested model
@ 32K ctx, Q4 GGUFs, built-in WebUI) + Arize Phoenix, profiles `lab` /
`phoenix` (the LiteLLM gateway service stays defined but stopped —
engine-direct since 2026-09-06, removal pending). See `docker/README.md`.

Gateway-free OTEL mode (2026-09-06): drop the proxy from the trace path —
client-side `OpenAIInstrumentor()` wraps OpenAI-SDK calls aimed straight at
the llama.cpp engines and emits OpenInference spans via Arize libs into a
dedicated `otel-direct` Phoenix project. Span names + arbitrary attributes
(`session_id` → Phoenix Sessions grouping, `tag.tags`) are set at span
creation — no gateway stamper, no `metadata.requester_metadata` nesting,
zero LiteLLM router/proxy spans (bypass proven via span census).
Probe: `scratch/otel_direct_probe.py`. Gotchas: `phoenix.otel.register`
uses an explicit endpoint verbatim (pass `.../v1/traces`); the grpcio wheel
needs conda `libabseil==20260526.0` + `libgrpc==1.83.0` for its sonames.

Gateway search verdict: Portkey v1 evaluated 2026-09-06 — lean (~2 MB TS,
no DB/Redis) but no built-in OTEL export (in-memory ring + SSE console
only); parked in `scratch/gateway`. Going direct instead.

## 3. Generating traces in Phoenix

 a. Run naive script
  - 154 films with reasoning (reasoning 8192 budget)

 Tested prompt optimizers (scratch sweep, cinematic-01 micro-set, 6 train /
 4 val fixed split, LLM-as-judge):

 b. gepa (standalone, DefaultAdapter) — reflection-guided prompt evolution
 - optimize -> 154 films -> trace runs

 c. deepeval GEPA — same genetic-pareto idea inside the deepeval optimizer
 - optimize -> 154 films -> trace runs

 d. deepeval MIPROV2 — bayesian instruction/demos proposals
 (needs optuna: `pixi add --pypi optuna`)
 - optimize -> 154 films -> trace runs

 e. deepeval COPRO — coordinate ascent over instruction candidates
 - optimize -> 154 films -> trace runs

 f. promptrefiner `BaseStrategy.refine` — rewrite-only baseline
 - optimize -> 154 films -> trace runs

 g. dspy BootstrapFewShot (3.3.1) — cheapest: bootstrapped demos, no LLM
  proposal calls (judge-as-teacher via teacher_settings; demos live inside
  the dspy program, prompt-only eval never sees them)
 - optimize -> 154 films -> trace runs

 h. dspy SIMBA (3.3.1) — introspective mini-batch ascent; compile asserts
  len(trainset) >= bsize, so bsize <= 6 on our 6-row split; rollout LMs
  library-copied at temp 1.0 (rest governed by engine flags)
 - optimize -> 154 films -> trace runs

 i. dspy MIPROv2 (3.3.1) — bayesian instruction/demos proposals; auto=None
  + trimmed num_candidates/num_trials
 - optimize -> 154 films -> trace runs

 j. deepeval SIMBA (4.2.1) — same PromptOptimizer wiring as copro/miprov2
 - optimize -> 154 films -> trace runs

 k. adalflow TGDOptimizer (1.1.3) — text-grad via EvalFnToTextLoss +
  BackwardEngine over the gateway (AdalComponent/Trainer path deliberately
  skipped); needs workarounds: BackwardEngine(**kwargs) only, LazyImport
  forbids subclassing, loss forward wants id= per row
 - optimize -> 154 films -> trace runs

  Ruled out: promptimal (hardcoded gpt-4o). Kept: all of the above.

  Full-run status (2026-09-06, `opt-20260906-gw`): §3a-k executed
  end-to-end on the engine-direct trace path — naive `test.py --sample 0`
  (recall 56/154, year match 121/154, repeat 0.81 PASS, 462 echo verdicts +
  462 eval annotations) plus all 10 optimizers with full-corpus films legs
  (adalflow-tgd tops at 0.403); per-test Phoenix sessions in `cdmd-lab`,
  every scored row's turn root carries an `eval` ok/miss span annotation
  (score-searchable miss=miss filter works); runbook +
  results in `eduailab/nn-gentraces.md`, artifacts in
  `datasets/cinematic-01/runs/opt-20260906-gw-*`.

  Sweep experience (2026-09-04 opt-scale-20260904 full run on the current
 pair — non-thinking granite-4.0-h-tiny judge (`local-judge`) + thinking
 LFM2.5-2.6B tested (`local-thinking`); formalized in
 `smoketests/cinematic-01/optimize.py`, user howto in
 `eduailab/nn-gentraces.md`):
 - LFM2.5-2.6B splits its reasoning into `reasoning_content` on llama.cpp;
   the evaluator still drops a closed inline `</think>` block defensively
   (an unclosed one counts as a miss)
 - judge budget stays 4096 tokens (`JUDGE_MAX_TOKENS`) — smaller budgets
   starved reflections into empty content in early probes
 - deepeval diagnosis/rewrite schemas need json_repair + list->string coercion
   (`optimize_common.LocalLLM`)
 - val deltas are noise at n=4 — the 154-film corpus legs are the honest
   comparison (films 0.338–0.377, dspy-bootstrap on top)
 - sweep call counts: cheapest dspy-bootstrap 8 task / 1 judge, heaviest
   dspy-simba 81 / 0 and adalflow-tgd 24 / 12; full per-optimizer table
   (films scores + wall seconds) in `smoketests/cinematic-01/design.md`
 - spans labelled `<run_id> <opt> eval` (tested) + `<run_id> <opt> judge`
   (judge) — client-side OpenInference spans straight into the Phoenix
   project `cdmd-lab` (engine-direct since 2026-09-06, no gateway in the
   trace path); each scored row's AGENT turn root carries an `eval` ok/miss
   span annotation so failed rows stay score-searchable; artifacts in
   `datasets/cinematic-01/runs/opt-scale-20260904-*`

## 4. Backup traces to /sidecar

We will make a sidecar backup (and show to lab user/harness how to backup)

A. Local SQLite (Default)


B. Exporting with Phonix CLI (CLI snapshoting)

```
px trace list --limit 10000 --format json --project default > ./sidecar/phoenix_traces_films_raw.json
Resolving project: default
Fetching last 10000 trace(s)...
Found 4451 trace(s)
```

Status: sidecar artifacts 2026-09-06 — SQLite volume tar.gz (`cmod-phoenix-backup-20.7.tgz`,
method A) + PX CLI export (`phoenix_traces_films_raw.json`, 4451 traces, method B).

# Installed tools

## Arize Phoenix CLI

```
npm install @arizeai/phoenix-cli
px --version
1.17.0
```
## Arize Phoenix SDK

```
cat ./pixi.toml | grep arize
arize-phoenix-client = ">=3.3.0,<4"
arize-phoenix-evals = ">=2.0.0,<3"
```

# Sources:

## Arize Phoenix LLM anchor
https://arize.com/docs/phoenix/llms.txt

## Arize Phoenix CLI
https://arize.com/docs/phoenix/sdk-api-reference/typescript/arizeai-phoenix-cli

## Arize Phoenix SKILL
https://github.com/Arize-ai/phoenix/blob/main/docs/phoenix/skill.md
https://github.com/Arize-ai/phoenix/tree/main/.agents/skills

## Arize Phoenix SDK
https://arize.com/docs/phoenix/sdk-api-reference
https://arize-phoenix.readthedocs.io/projects/client/
https://arize-phoenix.readthedocs.io/projects/evals/

## Arize Phoenix REST API
https://arize.com/docs/phoenix/sdk-api-reference/rest-api/overview
https://arize.com/docs/phoenix/sdk-api-reference/rest-api/api-reference
https://github.com/AISidesKicks/tutorials_python

## Arize Phoenix graphql
https://arize.com/docs/ax/graphql-reference
https://github.com/AISidesKicks/graphql-api-examples
https://arize.com/blog/text-to-graphql-mcp-server/
https://github.com/Arize-ai/text-to-graphql-mcp
https://colab.research.google.com/github/Arize-ai/tutorials_python/blob/main/Arize_Tutorials/GraphQL/Create_Performance_Monitors_Use_Case.ipynb

## monty
Special note: codemode uses monty (a Python derivative) https://github.com/pydantic/monty/
https://arize.com/docs/phoenix/sdk-api-reference/rest-api/api-reference

# Inspiration
[Anthropic Claude: The AI-Native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook)
