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

## AI Harness

- opencode compatible harness, we use **kilo cli**

The goal of the lab is to create /skills for 3 modes in 3 dirs, can be switched together with MCPs by opencode config.

## MCP providers

- Arize Phoenix: Built-in MCP with LLM and codemode / 

## Lab environment
 - litellm + small llm in llama-server + Arize Phoenix

**Note:** As leaner setup as possible (no external DBs - main focus on execution traces ONLY!)

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


## monty
Special note: codemode uses monty (a Python derivative) https://github.com/pydantic/monty/


