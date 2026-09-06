# Phoenix MCP reference (`phoenix_*` tools)

A complete, categorized catalog of the **109 tools** the Arize Phoenix remote MCP
server exposes in this repo, with a copy-paste example for every tool built from
**real values in the live `default` project** (the `lfm2.5-mini-swe-agent`
benchmark corpus). It is the full detail behind the condensed "MCP tool
catalog" table in [README.md](README.md).

## Server & endpoint

- **Server**: Arize Phoenix **20.2.0**, running as the `phoenix` compose
  service (`arizephoenix/phoenix:20.2.0`, SQLite under `phoenix_data:/mnt/data`).
- **Endpoint**: `http://localhost:6006/mcp` (remote MCP; the same port serves
  the UI and OTLP HTTP ingestion). Registered in `opencode.json` for the
  opencode harness.
- **Auth**: off by default (this compose). If `PHOENIX_API_KEY` is set, every
  call needs `Authorization: Bearer <PHOENIX_API_KEY>`.
- **Code mode**: the server also exposes an `execute` tool
  (`PHOENIX_ENABLE_MCP_CODE_MODE=true` by default) that chains up to 50
  `call_tool(...)` calls per invocation against the tools below.
- Verify the server is reachable from the repo root with `kilo mcp list`
  (expect `phoenix connected`).

## Namespace & enumeration

Every tool is exposed to the client under a **`phoenix_` prefix**: the tool
named `spanSearch` in this catalog is called as `phoenix_spanSearch`, and so
on. The mapping is exactly **1:1** after stripping the prefix.

Enumerate the live catalog yourself instead of trusting this doc:

- `phoenix_tags` — the 16 category tags and their tool counts
  (`phoenix_list_tools` truncates around 1 MB / 30k lines of output, so tag
  detail is the reliable way to browse).
- `phoenix_get_schema(tools=[...])` — exact JSON schema for any subset of
  tools, including the object-payload shapes shown in the examples below.

## Legend

- **Params** in the tables: `param: type`; a trailing `[REQ]` marks a required
  parameter. `?` marks optional. Enum values are shown inline where relevant.
- **Hints** per tool:
  - `RO` — **read-only**; never mutates Phoenix state. Safe to run anytime.
  - `W` — **writes** (creates/updates/annotates/transfers).
  - `D` — **destructive** (deletes/removes data). Only run deliberately.
- Identifiers used in examples are real, live values from the `default` project
  (as of 2026-08-15). Placeholder IDs for entities that do not exist yet in
  this project (datasets, experiments, prompts, API keys, secrets) are marked
  `<placeholder>` and the GlobalID base64 shown is illustrative only.

Real values used throughout:

| Kind | Value |
| --- | --- |
| project name / ID | `default` / `UHJvamVjdDox` |
| trace ID (OTLP hex) | `657c9e36639b68d3991f71b51f40c3d2` |
| trace GlobalID | `VHJhY2U6NzE=` |
| span ID (OTLP hex) | `d3aa69357c940ad3` |
| span GlobalID | `U3BhbjozOTU1` |
| parent (root AGENT) span ID | `affe69f7208c8eae` |
| session ID | `python-pascal__HWcu9wS__agent` |
| session GlobalID | `UHJvamVjdFNlc3Npb246NzE=` |
| annotation config `user_feedback` | `Q2F0ZWdvcmljYWxBbm5vdGF0aW9uQ29uZmlnOjE=` |
| span attribute filter | `benchmark.tag:lfm2.5-mini-swe-agent` |
| users | `VXNlcjoy` (admin), `VXNlcjox` (system) |
| timestamps | start `2026-08-15T17:40:41.741210+00:00`, end `2026-08-15T17:40:44.235183+00:00` |

---

## phoenix-analytics-sql (2 tools) — SQL analytics over telemetry/datasets/experiments

The headline feature: **read-only SQLite SQL** against allowlisted tables
(`projects`, `traces`, `spans`, `span_annotations`, `span_costs`,
`span_cost_details`, `generative_models`, `project_sessions`, `datasets`,
`dataset_versions`, `dataset_examples`, `dataset_example_revisions`,
`experiments`, `experiments_dataset_examples`, `experiment_runs`,
`experiment_run_annotations`). SQLite dialect; `latency_ms` and
`graphql_node_id` are virtual (computed, not indexed); `percentile(x, p)` /
`median(x)` helpers are built in. 500 rows default, 5000 max.

| Tool | Params | Hints |
| --- | --- | --- |
| `describeSqlSchema` | `area`?, `tables`?, `detail` (`brief`/`detailed`/`full`), `search`? | RO |
| `executeSql` | `sql`[REQ], `validate_only`?, `row_limit`? | RO |

> These two tools carry **both** the `phoenix-analytics-sql` and
> `phoenix-mcp-meta` tags — the only dual-tagged tools in the catalog. That is
> why tag memberships (111) outnumber unique tools (109).

### Example — describeSqlSchema
```json
{"tables": ["spans", "traces", "project_sessions"], "detail": "detailed"}
```

### Example — executeSql (tagged spans, join `trace_rowid`)
`spans` has no `trace_id` column — join `spans.trace_rowid = traces.id`, and
read `benchmark.tag` out of the JSONB `attributes`:
```json
{
  "sql": "SELECT t.id AS trace_rowid, s.span_id, s.name, json_extract(s.attributes, '$.benchmark.tag') AS tag FROM spans s JOIN traces t ON s.trace_rowid = t.id WHERE json_extract(s.attributes, '$.benchmark.tag') = 'lfm2.5-mini-swe-agent' AND s.name = 'bash' LIMIT 3"
}
```

### Example — executeSql (span counts per trace)
```json
{
  "sql": "SELECT t.id, t.trace_id, json_extract(s.attributes, '$.benchmark.tag') AS tag, count(DISTINCT s.id) AS spans FROM traces t JOIN spans s ON s.trace_rowid = t.id WHERE json_extract(s.attributes, '$.benchmark.tag') = 'lfm2.5-mini-swe-agent' GROUP BY t.id ORDER BY t.id DESC LIMIT 5"
}
```

### Example — executeSql (sessions + trace counts)
```json
{
  "sql": "SELECT ps.id, ps.session_id, count(t.id) AS traces FROM project_sessions ps JOIN traces t ON t.project_session_rowid = ps.id GROUP BY ps.id ORDER BY ps.id LIMIT 5"
}
```

### Example — executeSql (validate without executing)
```json
{"sql": "SELECT count(*) FROM spans", "validate_only": true}
```

---

## traces (5 tools) — list, annotate, note, move, and delete traces

| Tool | Params | Hints |
| --- | --- | --- |
| `listProjectTraces` | `project_identifier`[REQ], `start_time`?, `end_time`?, `sort` (`start_time`/`latency_ms`), `order` (`asc`/`desc`), `limit`, `cursor`?, `include_spans`?, `session_identifier`?[] | RO |
| `annotateTraces` | `sync`?, `data`[REQ] (`name`, `annotator_kind`, `trace_id`, `result`?, `metadata`?, `identifier`?) | W |
| `createTraceNote` | `data`[REQ] (`trace_id`, `note`, `identifier`?) | W |
| `transferTraces` | `trace_identifiers`[REQ], `destination_project_identifier`[REQ] | W |
| `deleteTrace` | `trace_identifier`[REQ] | D |

### Example — listProjectTraces
```json
{
  "project_identifier": "default",
  "limit": 2,
  "sort": "start_time",
  "order": "desc",
  "include_spans": false
}
```

### Example — annotateTraces
```json
{
  "sync": true,
  "data": [
    {
      "name": "reward",
      "annotator_kind": "HUMAN",
      "trace_id": "657c9e36639b68d3991f71b51f40c3d2",
      "result": {"label": "positive", "score": 1.0, "explanation": "pascal fix verified"},
      "identifier": "reward-657c9e36"
    }
  ]
}
```

### Example — createTraceNote
```json
{
  "data": {
    "trace_id": "657c9e36639b68d3991f71b51f40c3d2",
    "note": "python-pascal follow-up: one-line fix applied, reward 1.0",
    "identifier": "pascal-followup"
  }
}
```

### Example — transferTraces
```json
{
  "trace_identifiers": ["VHJhY2U6NzE="],
  "destination_project_identifier": "archived-run"
}
```

### Example — deleteTrace
```json
{"trace_identifier": "VHJhY2U6NzE="}
```

---

## spans (7 tools) — search, list, create, annotate, note, delete, document-annotate

| Tool | Params | Hints |
| --- | --- | --- |
| `spanSearch` | `project_identifier`[REQ], `cursor`?, `limit`, `start_time`?, `end_time`?, `trace_id`?[], `span_id`?[], `parent_id`?, `name`?[], `status_code`?[], `attribute`?[] | RO |
| `getSpans` | same as `spanSearch` + `span_kind`?[] | RO |
| `createSpans` | `project_identifier`[REQ], `data`[REQ] (per span: `name`, `context`, `span_kind`, `start_time`, `end_time`, `status_code`, …) | W |
| `annotateSpans` | `sync`?, `data`[REQ] (`name`, `annotator_kind`, `span_id`, `result`?, `metadata`?, `identifier`?) | W |
| `createSpanNote` | `data`[REQ] (`span_id`, `note`, `identifier`?) | W |
| `deleteSpan` | `span_identifier`[REQ] | D |
| `annotateSpanDocuments` | `sync`?, `data`[REQ] (`name`, `annotator_kind`, `span_id`, `document_position`, `result`?, `metadata`?, `identifier`?) | W |

### Example — spanSearch (bash tool spans on the benchmark tag)
The `attribute` filter is `key:value` (single string in a `string[]`):
```json
{
  "project_identifier": "default",
  "attribute": ["benchmark.tag:lfm2.5-mini-swe-agent"],
  "name": ["bash"],
  "limit": 2
}
```

### Example — getSpans (LLM spans with token attributes)
```json
{
  "project_identifier": "default",
  "span_kind": ["LLM"],
  "trace_id": ["657c9e36639b68d3991f71b51f40c3d2"],
  "limit": 2
}
```

### Example — createSpans
```json
{
  "project_identifier": "default",
  "data": [
    {
      "name": "manual_check",
      "context": {"trace_id": "657c9e36639b68d3991f71b51f40c3d2", "span_id": "1112131415161718"},
      "span_kind": "TOOL",
      "parent_id": "affe69f7208c8eae",
      "start_time": "2026-08-15T17:40:44.235183+00:00",
      "end_time": "2026-08-15T17:40:44.535183+00:00",
      "status_code": "OK",
      "status_message": "",
      "attributes": {"tool.name": "bash", "benchmark.tag": "lfm2.5-mini-swe-agent"}
    }
  ]
}
```

### Example — annotateSpans
```json
{
  "sync": true,
  "data": [
    {
      "name": "command_succeeded",
      "annotator_kind": "CODE",
      "span_id": "d3aa69357c940ad3",
      "result": {"label": "yes", "score": 1.0}
    }
  ]
}
```

### Example — createSpanNote
```json
{
  "data": {
    "span_id": "d3aa69357c940ad3",
    "note": "final submit command; output.value empty (expected)"
  }
}
```

### Example — deleteSpan
```json
{"span_identifier": "U3BhbjozOTU1"}
```

### Example — annotateSpanDocuments
```json
{
  "sync": true,
  "data": [
    {
      "name": "doc_relevance",
      "annotator_kind": "HUMAN",
      "span_id": "d3aa69357c940ad3",
      "document_position": 0,
      "result": {"label": "relevant", "score": 0.8}
    }
  ]
}
```

---

## sessions (6 tools) — read, delete, list, annotate, note

| Tool | Params | Hints |
| --- | --- | --- |
| `getSession` | `session_identifier`[REQ] | RO |
| `deleteSession` | `session_identifier`[REQ] | D |
| `deleteSessions` | `session_identifiers`[REQ] | D |
| `listProjectSessions` | `project_identifier`[REQ], `cursor`?, `limit`, `order` (`asc`/`desc`) | RO |
| `annotateSessions` | `sync`?, `data`[REQ] (`name`, `annotator_kind`, `session_id`, `result`?, `metadata`?, `identifier`?) | W |
| `createSessionNote` | `data`[REQ] (`session_id`, `note`, `identifier`?) | W |

### Example — listProjectSessions
```json
{"project_identifier": "default", "limit": 5, "order": "desc"}
```

### Example — getSession
```json
{"session_identifier": "python-pascal__HWcu9wS__agent"}
```

### Example — deleteSession
```json
{"session_identifier": "python-pascal__HWcu9wS__agent"}
```

### Example — deleteSessions
```json
{
  "session_identifiers": [
    "python-pascal__HWcu9wS__agent",
    "python-wrap__uUPjydM__agent"
  ]
}
```

### Example — annotateSessions
```json
{
  "sync": true,
  "data": [
    {
      "name": "trial_outcome",
      "annotator_kind": "HUMAN",
      "session_id": "python-pascal__HWcu9wS__agent",
      "result": {"label": "passed", "score": 1.0}
    }
  ]
}
```

### Example — createSessionNote
```json
{
  "data": {
    "session_id": "python-pascal__HWcu9wS__agent",
    "note": "follow-up run of python-pascal after first timeout"
  }
}
```

---

## datasets (22 tools) — labels, CRUD, upload, versions, examples, splits, downloads

One-line purpose: manage datasets of `inputs`/`outputs` example rows (with
optional `splits`) that drive evals. The live project has **0 datasets**;
GlobalID placeholders below are illustrative.

| Tool | Params | Hints |
| --- | --- | --- |
| `listDatasetLabels` | `cursor`?, `limit` | RO |
| `createDatasetLabel` | `name`[REQ], `color`[REQ], `description`? | W |
| `getDatasetLabel` | `label_id`[REQ] | RO |
| `deleteDatasetLabel` | `label_id`[REQ] | D |
| `updateDatasetLabel` | `label_id`[REQ], `name`?, `color`?, `description`? | W |
| `listDatasetLabelsForDataset` | `dataset_identifier`[REQ] | RO |
| `setDatasetLabelsForDataset` | `dataset_identifier`[REQ], `dataset_label_ids`?[] | W |
| `addDatasetLabelToDataset` | `dataset_identifier`[REQ], `label_id`[REQ] | W |
| `removeDatasetLabelFromDataset` | `dataset_identifier`[REQ], `label_id`[REQ] | D |
| `listDatasets` | `cursor`?, `name`?, `limit` | RO |
| `getDataset` | `id`[REQ] | RO |
| `deleteDatasetById` | `id`[REQ] | D |
| `listDatasetVersionsByDatasetId` | `id`[REQ], `cursor`?, `limit` | RO |
| `uploadDataset` | `action` (`create`/`append`/`update`), `name`, `description`?, `inputs`?[], `outputs`?[], `metadata`?[], `splits`?[], `span_ids`?[], `example_ids`?[], `sync`? | W |
| `getDatasetExamples` | `id`[REQ], `version_id`?, `split`?[] | RO |
| `createDatasetSplit` | `dataset_identifier`[REQ], `name`[REQ], `description`?, `color`?, `metadata`?, `example_ids`?[] | W |
| `deleteDatasetSplit` | `dataset_identifier`[REQ], `split_id`[REQ] | D |
| `updateDatasetSplit` | `dataset_identifier`[REQ], `split_id`[REQ], `name`?, `description`?, `color`?, `metadata`?, `add_example_ids`?[], `remove_example_ids`?[] | D |
| `getDatasetCsv` | `id`[REQ], `version_id`? | RO |
| `getDatasetJSONL` | `id`[REQ], `version_id`? | RO |
| `getDatasetJSONLOpenAIFineTuning` | `id`[REQ], `version_id`? | RO |
| `getDatasetJSONLOpenAIEvals` | `id`[REQ], `version_id`? | RO |

### Example — uploadDataset (create, synchronous)
One example per array; `span_ids` links rows back to spans (here the pascal
trace's bash span):
```json
{
  "action": "create",
  "name": "quixbugs-python-swe",
  "description": "quixbugs Python tasks for agent evals",
  "sync": true,
  "inputs": [{"task": "Fix the bug in pascal.py"}],
  "outputs": [{"fixed_file": "fixed_pascal.py"}],
  "metadata": [{"task": "python-pascal", "tag": "lfm2.5-mini-swe-agent"}],
  "splits": [["train"]],
  "span_ids": ["d3aa69357c940ad3"]
}
```

### Example — listDatasets
```json
{"limit": 10}
```

### Example — getDataset
```json
{"id": "RGF0YXNldDox"}
```

### Example — listDatasetVersionsByDatasetId
```json
{"id": "RGF0YXNldDox", "limit": 10}
```

### Example — getDatasetExamples
```json
{"id": "RGF0YXNldDox", "split": ["train"]}
```

### Example — getDatasetCsv
```json
{"id": "RGF0YXNldDox"}
```

### Example — getDatasetJSONL
```json
{"id": "RGF0YXNldDox"}
```

### Example — getDatasetJSONLOpenAIFineTuning
```json
{"id": "RGF0YXNldDox"}
```

### Example — getDatasetJSONLOpenAIEvals
```json
{"id": "RGF0YXNldDox"}
```

### Example — deleteDatasetById
```json
{"id": "RGF0YXNldDox"}
```

### Example — listDatasetLabels
```json
{"limit": 10}
```

### Example — createDatasetLabel
```json
{"name": "quixbugs", "color": "#33c5e8", "description": "quixbugs Python corpus"}
```

### Example — getDatasetLabel
```json
{"label_id": "RGF0YXNldExhYmVsOjE="}
```

### Example — updateDatasetLabel
```json
{"label_id": "RGF0YXNldExhYmVsOjE=", "description": "quixbugs Python corpus (updated)"}
```

### Example — deleteDatasetLabel
```json
{"label_id": "RGF0YXNldExhYmVsOjE="}
```

### Example — listDatasetLabelsForDataset
```json
{"dataset_identifier": "quixbugs-python-swe"}
```

### Example — setDatasetLabelsForDataset
```json
{"dataset_identifier": "quixbugs-python-swe", "dataset_label_ids": ["RGF0YXNldExhYmVsOjE="]}
```

### Example — addDatasetLabelToDataset
```json
{"dataset_identifier": "quixbugs-python-swe", "label_id": "RGF0YXNldExhYmVsOjE="}
```

### Example — removeDatasetLabelFromDataset
```json
{"dataset_identifier": "quixbugs-python-swe", "label_id": "RGF0YXNldExhYmVsOjE="}
```

### Example — createDatasetSplit
```json
{
  "dataset_identifier": "quixbugs-python-swe",
  "name": "train",
  "description": "training split",
  "color": "#33c5e8",
  "example_ids": ["RGF0YXNldEV4YW1wbGU6MQ=="]
}
```

### Example — updateDatasetSplit
```json
{
  "dataset_identifier": "quixbugs-python-swe",
  "split_id": "RGF0YXNldFNwbGl0OjE=",
  "description": "training split (curated)",
  "add_example_ids": ["RGF0YXNldEV4YW1wbGU6Mg=="]
}
```

### Example — deleteDatasetSplit
```json
{"dataset_identifier": "quixbugs-python-swe", "split_id": "RGF0YXNldFNwbGl0OjE="}
```

---

## experiments (15 tools) — create experiments, run them, score runs

Run + score evaluations against a dataset. Live project has **0 experiments**;
GlobalIDs below are illustrative.

| Tool | Params | Hints |
| --- | --- | --- |
| `listExperiments` | `dataset_id`[REQ], `cursor`?, `limit` | RO |
| `createExperiment` | `dataset_id`[REQ], `name`?, `description`?, `metadata`?, `version_id`?, `splits`?[], `repetitions` | W |
| `getExperiment` | `experiment_id`[REQ] | RO |
| `deleteExperiment` | `experiment_id`[REQ], `delete_project`? | D |
| `updateExperiment` | `experiment_id`[REQ], `name`?, `description`?, `metadata`? | W |
| `getIncompleteExperimentRuns` | `experiment_id`[REQ], `cursor`?, `limit` | RO |
| `getExperimentJSON` | `experiment_id`[REQ] | RO |
| `getExperimentCSV` | `experiment_id`[REQ] | RO |
| `listExperimentTags` | `experiment_id`[REQ] | RO |
| `setExperimentTag` | `experiment_id`[REQ], `name`[REQ], `description`? | W |
| `deleteExperimentTag` | `experiment_id`[REQ], `tag_identifier`[REQ] | D |
| `listExperimentRuns` | `experiment_id`[REQ], `cursor`?, `limit`? | RO |
| `createExperimentRun` | `experiment_id`[REQ], `dataset_example_id`[REQ], `output`[REQ], `repetition_number`[REQ], `start_time`[REQ], `end_time`[REQ], `trace_id`?, `error`? | W |
| `getIncompleteExperimentEvaluations` | `experiment_id`[REQ], `evaluation_name`?[], `cursor`?, `limit` | RO |
| `upsertExperimentEvaluation` | `experiment_run_id`[REQ], `name`[REQ], `annotator_kind`[REQ] (`LLM`/`CODE`/`HUMAN`), `start_time`[REQ], `end_time`[REQ], `result`?, `error`?, `metadata`?, `trace_id`? | W |

### Example — listExperiments
```json
{"dataset_id": "RGF0YXNldDox", "limit": 10}
```

### Example — createExperiment
```json
{
  "dataset_id": "RGF0YXNldDox",
  "name": "mini-swe-agent-lfm2.5-8192",
  "description": "one-shot quixbugs sweep, max_tokens=8192",
  "repetitions": 1,
  "splits": ["train"]
}
```

### Example — getExperiment
```json
{"experiment_id": "RXhwZXJpbWVudDox"}
```

### Example — updateExperiment
```json
{"experiment_id": "RXhwZXJpbWVudDox", "description": "one-shot quixbugs sweep (updated)"}
```

### Example — deleteExperiment
```json
{"experiment_id": "RXhwZXJpbWVudDox"}
```

### Example — listExperimentRuns
```json
{"experiment_id": "RXhwZXJpbWVudDox", "limit": 10}
```

### Example — createExperimentRun
```json
{
  "experiment_id": "RXhwZXJpbWVudDox",
  "dataset_example_id": "RGF0YXNldEV4YW1wbGU6MQ==",
  "output": {"fixed_file": "fixed_pascal.py", "reward": 1.0},
  "repetition_number": 1,
  "start_time": "2026-08-15T17:40:41.741210+00:00",
  "end_time": "2026-08-15T17:40:44.235183+00:00",
  "trace_id": "657c9e36639b68d3991f71b51f40c3d2"
}
```

### Example — upsertExperimentEvaluation
```json
{
  "experiment_run_id": "RXhwZXJpbWVudFJ1bjox",
  "name": "pass_fail",
  "annotator_kind": "CODE",
  "start_time": "2026-08-15T17:40:44.235183+00:00",
  "end_time": "2026-08-15T17:40:44.535183+00:00",
  "result": {"label": "passed", "score": 1.0, "explanation": "verifier reward 1.0"},
  "trace_id": "657c9e36639b68d3991f71b51f40c3d2"
}
```

### Example — getIncompleteExperimentRuns
```json
{"experiment_id": "RXhwZXJpbWVudDox", "limit": 10}
```

### Example — getIncompleteExperimentEvaluations
```json
{"experiment_id": "RXhwZXJpbWVudDox", "evaluation_name": ["pass_fail"]}
```

### Example — getExperimentJSON
```json
{"experiment_id": "RXhwZXJpbWVudDox"}
```

### Example — getExperimentCSV
```json
{"experiment_id": "RXhwZXJpbWVudDox"}
```

### Example — listExperimentTags
```json
{"experiment_id": "RXhwZXJpbWVudDox"}
```

### Example — setExperimentTag
```json
{"experiment_id": "RXhwZXJpbWVudDox", "name": "swe", "description": "agentic SWE experiment"}
```

### Example — deleteExperimentTag
```json
{"experiment_id": "RXhwZXJpbWVudDox", "tag_identifier": "RXhwZXJpbWVudFRhZzox"}
```

---

## prompts (11 tools) — versioned prompt registry

| Tool | Params | Hints |
| --- | --- | --- |
| `getPrompts` | `cursor`?, `limit` | RO |
| `postPromptVersion` | `prompt`[REQ] (`name`), `version`[REQ] (`description`?, `model_provider`?, `model_name`?, `template`?, `template_type`?, `template_format`?, `invocation_parameters`?) | W |
| `listPromptVersions` | `prompt_identifier`[REQ], `cursor`?, `limit` | RO |
| `getPromptVersionByPromptVersionId` | `prompt_version_id`[REQ] | RO |
| `getPromptVersionByTagName` | `prompt_identifier`[REQ], `tag_name`[REQ] | RO |
| `getPromptVersionLatest` | `prompt_identifier`[REQ] | RO |
| `getPromptVersionTags` | `prompt_version_id`[REQ], `cursor`?, `limit` | RO |
| `createPromptVersionTag` | `prompt_version_id`[REQ], `name`[REQ], `description`? | W |
| `deletePromptVersionTag` | `prompt_version_id`[REQ], `tag_name`[REQ] | D |
| `deletePrompt` | `prompt_identifier`[REQ] | D |
| `patchPrompt` | `prompt_identifier`[REQ], `description`?, `metadata`? | W |

### Example — getPrompts
```json
{"limit": 10}
```

### Example — postPromptVersion
Prompt names must match `^[a-z0-9]([_a-z0-9-]*[a-z0-9])?$`:
```json
{
  "prompt": {
    "name": "quixbugs_repro",
    "description": "quixbugs one-line-fix agent prompt",
    "metadata": {"benchmark": "quixbugs"}
  },
  "version": {
    "description": "v1",
    "model_provider": "OPENAI",
    "model_name": "openai/LFM2.5-2.6B",
    "template": {
      "type": "chat",
      "messages": [
        {"role": "system", "content": "Fix the ONE buggy line. Write the fixed program to fixed_{{program}}.py."},
        {"role": "user", "content": "Analyze {{program}}.py and fix the bug."}
      ]
    },
    "template_type": "CHAT",
    "template_format": "MUSTACHE",
    "invocation_parameters": {"type": "openai", "openai": {"temperature": 0.1, "max_tokens": 8192}}
  }
}
```

### Example — listPromptVersions
```json
{"prompt_identifier": "quixbugs_repro", "limit": 10}
```

### Example — getPromptVersionLatest
```json
{"prompt_identifier": "quixbugs_repro"}
```

### Example — getPromptVersionByPromptVersionId
```json
{"prompt_version_id": "UHJvbXB0VmVyc2lvbjox"}
```

### Example — getPromptVersionByTagName
```json
{"prompt_identifier": "quixbugs_repro", "tag_name": "production"}
```

### Example — getPromptVersionTags
```json
{"prompt_version_id": "UHJvbXB0VmVyc2lvbjox"}
```

### Example — createPromptVersionTag
```json
{"prompt_version_id": "UHJvbXB0VmVyc2lvbjox", "name": "production", "description": "deployed prompt"}
```

### Example — deletePromptVersionTag
```json
{"prompt_version_id": "UHJvbXB0VmVyc2lvbjox", "tag_name": "production"}
```

### Example — patchPrompt
```json
{"prompt_identifier": "quixbugs_repro", "description": "quixbugs agent prompt (v2 metadata)", "metadata": {"owner": "benchmarking"}}
```

### Example — deletePrompt
```json
{"prompt_identifier": "quixbugs_repro"}
```

---

## annotation_configs (9 tools) — scoring schemas

| Tool | Params | Hints |
| --- | --- | --- |
| `listAnnotationConfigs` | `cursor`?, `limit` | RO |
| `createAnnotationConfig` | `createannotationconfigdata`[REQ] (`type`[REQ]: `CATEGORICAL`/`CONTINUOUS`/`FREEFORM`, `name`, `description`?, `optimization_direction`?, …) | W |
| `getAnnotationConfig` | `config_identifier`[REQ] | RO |
| `updateAnnotationConfig` | `config_id`[REQ], `createannotationconfigdata`[REQ] | W |
| `deleteAnnotationConfig` | `config_id`[REQ] | D |
| `getProjectAnnotationConfigs` | `project_identifier`[REQ], `cursor`?, `limit` | RO |
| `setProjectAnnotationConfigs` | `project_identifier`[REQ], `annotation_config_ids`[REQ] | W |
| `assignAnnotationConfigToProject` | `project_identifier`[REQ], `config_identifier`[REQ] | W |
| `unassignAnnotationConfigFromProject` | `project_identifier`[REQ], `config_identifier`[REQ] | D |

### Example — listAnnotationConfigs
```json
{"limit": 10}
```

### Example — createAnnotationConfig (CATEGORICAL)
Matches the live `user_feedback` config shape:
```json
{
  "createannotationconfigdata": {
    "name": "user_feedback",
    "type": "CATEGORICAL",
    "description": "User feedback labels for traces.",
    "optimization_direction": "MAXIMIZE",
    "values": [
      {"label": "positive", "score": 1.0},
      {"label": "negative", "score": 0.0}
    ]
  }
}
```

### Example — createAnnotationConfig (CONTINUOUS)
```json
{
  "createannotationconfigdata": {
    "name": "reward",
    "type": "CONTINUOUS",
    "description": "Verifier reward, 0.0–1.0",
    "optimization_direction": "MAXIMIZE",
    "lower_bound": 0.0,
    "upper_bound": 1.0
  }
}
```

### Example — createAnnotationConfig (FREEFORM)
```json
{
  "createannotationconfigdata": {
    "name": "failure_reason",
    "type": "FREEFORM",
    "description": "Free text failure signature",
    "optimization_direction": "NONE",
    "threshold": 0.5
  }
}
```

### Example — getAnnotationConfig
```json
{"config_identifier": "user_feedback"}
```

### Example — updateAnnotationConfig
```json
{
  "config_id": "Q2F0ZWdvcmljYWxBbm5vdGF0aW9uQ29uZmlnOjE=",
  "createannotationconfigdata": {
    "name": "user_feedback",
    "type": "CATEGORICAL",
    "description": "User feedback labels for traces (v2)",
    "optimization_direction": "MAXIMIZE",
    "values": [{"label": "positive", "score": 1.0}, {"label": "negative", "score": 0.0}]
  }
}
```

### Example — deleteAnnotationConfig
```json
{"config_id": "Q2F0ZWdvcmljYWxBbm5vdGF0aW9uQ29uZmlnOjE="}
```

### Example — getProjectAnnotationConfigs
```json
{"project_identifier": "default", "limit": 10}
```

### Example — setProjectAnnotationConfigs
```json
{
  "project_identifier": "default",
  "annotation_config_ids": ["Q2F0ZWdvcmljYWxBbm5vdGF0aW9uQ29uZmlnOjE="]
}
```

### Example — assignAnnotationConfigToProject
```json
{
  "project_identifier": "default",
  "config_identifier": "user_feedback"
}
```

### Example — unassignAnnotationConfigFromProject
```json
{
  "project_identifier": "default",
  "config_identifier": "user_feedback"
}
```

---

## annotations (6 tools) — read/delete annotations on spans, traces, sessions

Read-only listers take `include_annotation_names` / `exclude_annotation_names`
filters; the deletes take a filter and/or `delete_all`.

| Tool | Params | Hints |
| --- | --- | --- |
| `listSpanAnnotationsBySpanIds` | `project_identifier`[REQ], `span_ids`?[], `identifier`?[], `include_annotation_names`?[], `exclude_annotation_names`?[], `cursor`?, `limit` | RO |
| `deleteSpanAnnotations` | `project_identifier`[REQ], `name`?, `identifier`?, `annotator_kind`?, `start_time`?, `end_time`?, `delete_all`? | D |
| `listTraceAnnotationsByTraceIds` | `project_identifier`[REQ], `trace_ids`?[], `identifier`?[], `include_annotation_names`?[], `exclude_annotation_names`?[], `cursor`?, `limit` | RO |
| `deleteTraceAnnotations` | `project_identifier`[REQ], `name`?, `identifier`?, `annotator_kind`?, `start_time`?, `end_time`?, `delete_all`? | D |
| `listSessionAnnotationsBySessionIds` | `project_identifier`[REQ], `session_ids`?[], `identifier`?[], `include_annotation_names`?[], `exclude_annotation_names`?[], `cursor`?, `limit` | RO |
| `deleteSessionAnnotations` | `project_identifier`[REQ], `name`?, `identifier`?, `annotator_kind`?, `start_time`?, `end_time`?, `delete_all`? | D |

### Example — listTraceAnnotationsByTraceIds
```json
{
  "project_identifier": "default",
  "trace_ids": ["657c9e36639b68d3991f71b51f40c3d2"],
  "limit": 10
}
```

### Example — listSpanAnnotationsBySpanIds
```json
{
  "project_identifier": "default",
  "span_ids": ["d3aa69357c940ad3"],
  "include_annotation_names": ["command_succeeded"]
}
```

### Example — listSessionAnnotationsBySessionIds
```json
{
  "project_identifier": "default",
  "session_ids": ["python-pascal__HWcu9wS__agent"],
  "limit": 10
}
```

### Example — deleteTraceAnnotations
```json
{
  "project_identifier": "default",
  "name": "reward",
  "delete_all": true
}
```

### Example — deleteSpanAnnotations
```json
{
  "project_identifier": "default",
  "name": "command_succeeded",
  "delete_all": true
}
```

### Example — deleteSessionAnnotations
```json
{
  "project_identifier": "default",
  "name": "trial_outcome",
  "delete_all": true
}
```

---

## projects (5 tools) — CRUD over projects

| Tool | Params | Hints |
| --- | --- | --- |
| `getProjects` | `cursor`?, `limit`, `include_experiment_projects`?, `include_dataset_evaluator_projects`?, `name_contains`? | RO |
| `createProject` | `name`[REQ], `description`? | W |
| `getProject` | `project_identifier`[REQ] | RO |
| `updateProject` | `project_identifier`[REQ], `description`? | W |
| `deleteProject` | `project_identifier`[REQ] | D |

### Example — getProjects
```json
{"limit": 10}
```

### Example — getProject
```json
{"project_identifier": "default"}
```

### Example — createProject
```json
{"name": "archived-run", "description": "archived lfm2.5-opencode traces"}
```

### Example — updateProject
```json
{"project_identifier": "archived-run", "description": "archived opencode-era run"}
```

### Example — deleteProject
```json
{"project_identifier": "archived-run"}
```

---

## users (4 tools) — viewer, list, create, delete

| Tool | Params | Hints |
| --- | --- | --- |
| `getViewer` | *(no parameters)* | RO |
| `getUsers` | `cursor`?, `limit` | RO |
| `createUser` | `user`[REQ] (`auth_method`[REQ]: `LOCAL`, `email`?, `username`?, `role`?, `password`?), `send_welcome_email`? | W |
| `deleteUser` | `user_id`[REQ] | D |

### Example — getViewer
```json
{}
```
(`{"auth_method": "ANONYMOUS"}` with auth off — the default in this compose.)

### Example — getUsers
```json
{"limit": 10}
```

### Example — createUser
```json
{
  "user": {
    "email": "alice@localhost",
    "username": "alice",
    "role": "MEMBER",
    "auth_method": "LOCAL",
    "password": "change-me"
  },
  "send_welcome_email": false
}
```
Roles: `SYSTEM`/`ADMIN`/`MEMBER`/`VIEWER`.

### Example — deleteUser
```json
{"user_id": "VXNlcjoy"}
```

---

## api_keys (7 tools) — user + system API keys

`create*ApiKey` returns the plaintext `key` **once** — it cannot be recovered
later. The live project has auth off, so keys are not needed in this setup.

| Tool | Params | Hints |
| --- | --- | --- |
| `getUserApiKeys` | *(no parameters)* | RO |
| `createUserApiKey` | `data`[REQ] (`name`[REQ], `description`?, `expires_at`?) | W |
| `getAllUserApiKeys` | `cursor`?, `limit` | RO |
| `deleteUserApiKey` | `api_key_id`[REQ] | D |
| `getSystemApiKeys` | *(no parameters)* | RO |
| `createSystemApiKey` | `data`[REQ] (`name`[REQ], `description`?, `expires_at`?) | W |
| `deleteSystemApiKey` | `api_key_id`[REQ] | D |

### Example — getUserApiKeys
```json
{}
```

### Example — createUserApiKey
```json
{
  "data": {
    "name": "benchmark-ingest",
    "description": "OTLP bearer token for export-traces.py",
    "expires_at": "2027-08-15T00:00:00Z"
  }
}
```

### Example — getAllUserApiKeys
```json
{"limit": 10}
```

### Example — deleteUserApiKey
```json
{"api_key_id": "VXNlckFwaUtleTox"}
```

### Example — getSystemApiKeys
```json
{}
```

### Example — createSystemApiKey
```json
{
  "data": {
    "name": "ci-readonly",
    "description": "system key for CI dashboards",
    "expires_at": "2027-08-15T00:00:00Z"
  }
}
```

### Example — deleteSystemApiKey
```json
{"api_key_id": "U3lzdGVtQXBpS2V5OjE="}
```

---

## secrets (1 tool) — upsert or delete stored secrets

| Tool | Params | Hints |
| --- | --- | --- |
| `upsertOrDeleteSecrets` | `secrets`[REQ] (each: `key`[REQ], `value`[REQ] — string to set, explicit `null` to delete) | D |

### Example — upsertOrDeleteSecrets (set one, delete another)
```json
{
  "secrets": [
    {"key": "OPENAI_API_KEY", "value": "sk-...-redacted"},
    {"key": "STALE_SECRET", "value": null}
  ]
}
```

---

## chat_completions (1 tool) — OpenAI-compatible completions

Useful for smoke-testing the locally served model (`openai/LFM2.5-2.6B`) from
Phoenix's own chat route.

| Tool | Params | Hints |
| --- | --- | --- |
| `createChatCompletion` | `model`[REQ], `messages`[REQ], `stream`?, `temperature`?, `top_p`?, `max_tokens`?, `max_completion_tokens`?, `stop`? (string or string[]), `frequency_penalty`?, `presence_penalty`?, `seed`?, `n`?, `stream_options`?, `tools`?[], `tool_choice`?, `response_format`? | W |

### Example — createChatCompletion
```json
{
  "model": "openai/LFM2.5-2.6B",
  "messages": [
    {"role": "system", "content": "You are a coding agent."},
    {"role": "user", "content": "Return the single word: ok"}
  ],
  "temperature": 0.1,
  "max_tokens": 64,
  "stream": false
}
```

---

## chat (8 tools) — agent chat sessions (Phoenix's browser-assistant agent)

Session-based assistant chat with tool loop support. `session_id` here is the
GlobalID returned by `createAgentSession`, **not** the `python-*__...__agent`
project session ID.

| Tool | Params | Hints |
| --- | --- | --- |
| `listAgentSessions` | `cursor`?, `limit` | RO |
| `createAgentSession` | `model`[REQ] (`providerType`: `custom`/`builtin`, `providerId`+`modelName` or `provider`+`modelName`), `is_ephemeral`? | W |
| `getAgentSession` | `session_id`[REQ] | RO |
| `patchAgentSession` | `session_id`[REQ], `model`? | D |
| `listAgentSessionMessages` | `session_id`[REQ], `cursor`?, `limit` | RO |
| `compactAgentSession` | `session_id`[REQ], `model`[REQ] (must match the persisted selection) | W |
| `submitAgentSessionToolOutputs` | `session_id`[REQ], `toolOutputs`[REQ], `lastMessageId`[REQ] | W |
| `agentSessionChat` | `session_id`[REQ], `headless`[REQ], `model`[REQ], `id`[REQ], `contexts`?[], `editPermission`?, `requestedSkills`?[], `trigger`?, `message`?, `toolOutputs`?[], `lastMessageId`?, `recordLocalTraces`?, `exportRemoteTraces`?, `instrumentUserId`? | W |

### Example — listAgentSessions
```json
{"limit": 10}
```

### Example — createAgentSession
```json
{
  "model": {"providerType": "custom", "providerId": "llama-server", "modelName": "openai/LFM2.5-2.6B"},
  "is_ephemeral": true
}
```

### Example — getAgentSession
```json
{"session_id": "QWdlbnRTZXNzaW9uOjE="}
```

### Example — patchAgentSession
```json
{
  "session_id": "QWdlbnRTZXNzaW9uOjE=",
  "model": {"providerType": "builtin", "provider": "OPENAI", "modelName": "openai/LFM2.5-2.6B"}
}
```

### Example — listAgentSessionMessages
```json
{"session_id": "QWdlbnRTZXNzaW9uOjE=", "limit": 10}
```

### Example — compactAgentSession
```json
{
  "session_id": "QWdlbnRTZXNzaW9uOjE=",
  "model": {"providerType": "custom", "providerId": "llama-server", "modelName": "openai/LFM2.5-2.6B"}
}
```

### Example — submitAgentSessionToolOutputs
```json
{
  "session_id": "QWdlbnRTZXNzaW9uOjE=",
  "toolOutputs": [
    {"type": "tool-bash", "toolCallId": "call_01", "state": "output-available", "input": {"command": "ls"}, "output": {"stdout": "pascal.py\n"}}
  ],
  "lastMessageId": "msg_5f3c1a"
}
```

### Example — agentSessionChat
```json
{
  "session_id": "QWdlbnRTZXNzaW9uOjE=",
  "headless": true,
  "id": "turn-2026-08-15t17-40-44z-0001",
  "model": {"providerType": "custom", "providerId": "llama-server", "modelName": "openai/LFM2.5-2.6B"},
  "trigger": "message",
  "message": {
    "id": "msg_user_01",
    "role": "user",
    "parts": [{"type": "text", "text": "Why did python-pascal time out on the first attempt?"}]
  }
}
```

---

## Notes

- **109 unique tools across 16 tags.** The 2 SQL tools (`describeSqlSchema`,
  `executeSql`) are tagged both `phoenix-analytics-sql` and `phoenix-mcp-meta`,
  so tag memberships total **111** while unique tool count is 109. This doc
  lists every tool exactly once, under its primary tag.
- **Per-tag tool counts**: annotation_configs 9, annotations 6, api_keys 7,
  chat 8, chat_completions 1, datasets 22, experiments 15, phoenix-analytics-sql
  2, phoenix-mcp-meta 2, projects 5, prompts 11, secrets 1, sessions 6, spans 7,
  traces 5, users 4.
- **Naming**: tool names here map 1:1 to the `phoenix_*` MCP tools after
  stripping the prefix (e.g. `executeSql` → `phoenix_executeSql`).
- **Examples are snapshot-accurate** against the live `default` project
  (2026-08-15 state: 71 traces / 3,956 spans / 71 sessions, tag
  `benchmark.tag=lfm2.5-mini-swe-agent`, one `user_feedback` annotation
  config, 2 users, 0 datasets/experiments/prompts). IDs drift as new runs are
  exported — re-probe with `phoenix_getProjects` / `phoenix_listProjectTraces`
  / `phoenix_spanSearch` to refresh.
- **SQL dialect gotchas** (see README "Data quality caveats"): SQLite only;
  `spans` joins to `traces` via `trace_rowid`; `benchmark.tag` lives in span
  JSONB (`json_extract(attributes, '$.benchmark.tag')`); agent spans and
  sessions have `start_time = 1970-01-01` so their `latency_ms` is meaningless;
  `percentile`/`median` helpers are built in; max 5000 rows / 4 MiB response.
