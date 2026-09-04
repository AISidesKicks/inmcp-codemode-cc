# AGENTS.md

You are an experienced Agentic Infra Engineer in "mcp vs codemode (Contrast & Compare)" [mcp.codemode.cc](https://mcp.codemode.cc)

## Project context

This is an **educational EDU AI LAB** project, part of "Local AI is not cheap!"
It contrasts & compares different ways how to integrate AI agentic harnesses into infra — it demonstrates upcoming Agentic Infra trends.

See more in [INTENT.md](INTENT.md), this lab is part of [Local AI is not CHEAP!(localai.isnot.cheap)](https://localai.isnot.cheap)

## lab environment
 - litellm + small llm in llama-server + Arize Phoenix

**Note:** As leaner setup as possible (no external DBs - main focus on execution traces)

## lab execution

The tone for documenting lab steps is informal.

 - Main programming language is Python 3.12, use PEP conventions with condensed naming
 - `Ruff` linter (Python 3.12, via pixi 'cdmd' env) — lint before committing
 - Local `monty` installation can be used to check code before sending to MCP server  

## Repository layout

- `README.md` - main documentation
- `docs/` - landing page (`index.html`), GitHub Pages `CNAME` - web home for this project.
- `docker` - minimal lab stack (llama.cpp + LiteLLM + Phoenix), see docker/README.md
- `datasets` - cinematic-01 micro dataset inputs (mirrored from localai.isnot.cheap)
- `LICENSE`
- `eduailab` - lab guide as 01- 02- 03- markdown docs
- `setup` - setup.md instructions and scripts
- `skills` - set of skills for each lab mode
- `smoketests` - python smoketest harnesses (mirrored), pair with datasets/
- `scratch` - temp stuff (persists across restarts)

## CMOD python environment (isolated with pixi `cmod`)

We are running inside "pixi shell" - check it before implementing plans in code.
Before installing Python packages double-check "pixi info | grep Name" returns 'cmod'
Pixi Python env has a preinstalled set of tools — suggest set expansion, if needed.

## CMOD docker environment (isolated with prefix `cmod-`)

All project related containers, volumes, networks and so on must have 'cmod-' prefix, even test and temp ones!
Don't stop any other containers or delete any resources without explicit HITL approval!

## For temporary work always use $SCRATCH and $TMPDIR

- `$SCRATCH` — scratch disk dir for logs and artifacts that persist through crashes (`$PIXI_PROJECT_ROOT/scratch`, gitignored except `.gitkeep`)
- `$TMPDIR` — tmpfs dir at `/run/user/$UID/pixi_tmp/$PIXI_PROJECT_NAME` (created by pixi `default` task)

## Commit conventions

Auto-commit locally - don't push, so we can keep track of development, using these rules:

- Small granular conventional commits.
- Format: lowercase `type(scope): subject`
- Examples: `docs(readme): fix broken lfm model links`, `fix(web): restore landing page html structure`, `chore: add gitignore`.
- Typos fixing: only use `fix(scope):` for text corrections (typos, grammar, broken links), commit them as readability improvements (not typo fixes)
- Do NOT use `fix:` for semantic additions (new sections, features, renumbering) — use `docs(scope):` with a meaningful subject instead.

## Task completion notification

When the work for a task is done — announce "All tasks are done".