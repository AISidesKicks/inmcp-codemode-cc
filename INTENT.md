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

We also want to demonstrate ZTA (Zero Token Architecture) - create report strips in Python using SDK (installed)

# The EDU AI LAB

## 1. AI Harness

- opencode compatible harness, we use **kilo cli**

The goal of the lab is to create /skills for 3 modes in 3 dirs, can be switched together with MCPs by opencode config.

## 2. MCP provider

- Arize Phoenix: Built-in MCP with LLM and codemode

## 3. Lab environment
 - litellm + small llm in llama-server + Arize Phoenix

**Note:** As leaner setup as possible (no external DBs - main focus on execution traces ONLY!)

Docker variant of the lab env lives in `docker/` — two llama.cpp servers
(non-thinking granite judge @ 64K ctx + thinking LFM2.5-2.6B tested model
@ 32K ctx, Q4 GGUFs, built-in WebUI) + LiteLLM gateway + Arize Phoenix,
profiles `lab` / `phoenix`. See `docker/README.md`.

## 3. Generating traces in Phoenix

 a. Run naive script
  - 100 films with reasoning (reasoning 8192 budget)

 Tested prompt optimizers (scratch sweep, cinematic-01 micro-set, 6 train /
 4 val fixed split, LLM-as-judge):

 b. gepa (standalone, DefaultAdapter) — reflection-guided prompt evolution
 - optimize -> trace runs

 c. deepeval GEPA — same genetic-pareto idea inside the deepeval optimizer
 - optimize -> trace runs

 d. deepeval MIPROV2 — bayesian instruction/demos proposals
 (needs optuna: `pixi add --pypi optuna`)
 - optimize -> trace runs

 e. deepeval COPRO — coordinate ascent over instruction candidates
 - optimize -> trace runs

 f. promptrefiner `BaseStrategy.refine` — rewrite-only baseline
 - optimize -> trace runs

 g. dspy BootstrapFewShot (3.3.1) — cheapest: bootstrapped demos, no LLM
  proposal calls (judge-as-teacher via teacher_settings; demos live inside
  the dspy program, prompt-only eval never sees them)
 - optimize -> trace runs

 h. dspy SIMBA (3.3.1) — introspective mini-batch ascent; compile asserts
  len(trainset) >= bsize, so bsize <= 6 on our 6-row split; rollout LMs
  library-copied at temp 1.0 (rest governed by engine flags)
 - optimize -> trace runs

 i. dspy MIPROv2 (3.3.1) — bayesian instruction/demos proposals; auto=None
  + trimmed num_candidates/num_trials
 - optimize -> trace runs

 j. deepeval SIMBA (4.2.1) — same PromptOptimizer wiring as copro/miprov2
 - optimize -> trace runs

 k. adalflow TGDOptimizer (1.1.3) — text-grad via EvalFnToTextLoss +
  BackwardEngine over the gateway (AdalComponent/Trainer path deliberately
  skipped); needs workarounds: BackwardEngine(**kwargs) only, LazyImport
  forbids subclassing, loss forward wants id= per row
 - optimize -> trace runs

 Ruled out: promptimal (hardcoded gpt-4o). Kept: all of the above.

 Sweep experience (2026-09-04, 1.2B-Thinking pair):
 - think-block stays inline in content on llama.cpp regardless of
   auto/deepseek/`--special` template kwargs -> evaluator strips it
   (LFM2.5-2.6B splits reasoning cleanly instead)
 - chatty small thinkers as judge need a 4096-token budget (2048 starved
   reflections into empty content)
 - deepeval diagnosis/rewrite schemas need json_repair + list->string coercion
 - task/judge call counts: gepa 28/2, deepeval gepa 34/4, miprov2 25/4,
   copro 18/1, refiner 8/1
 - val deltas are noise at n=4 with weak 1.2B recall
 - spans labelled `<run_id> <opt> eval` (tested) + `<run_id> <opt> judge`
   via gateway OTEL tagging (`metadata.generation_name`)

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

## Arize Phoenix API
https://arize.com/docs/phoenix/sdk-api-reference/rest-api/overview
https://arize.com/docs/phoenix/sdk-api-reference/rest-api/api-reference

## monty
Special note: codemode uses monty (a Python derivative) https://github.com/pydantic/monty/
https://arize.com/docs/phoenix/sdk-api-reference/rest-api/api-reference

# Inspiration
[Anthropic Claude: The AI-Native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook)
