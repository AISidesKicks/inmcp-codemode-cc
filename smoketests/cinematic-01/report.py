#!/usr/bin/env python3
"""Render a reproducible report.md for a cinematic-01 smoke run.

Reads the per-run artifacts written by test.py
(datasets/cinematic-01/runs/<run-id>/results.json and eval.json) and renders
a human-readable report.md from the numbers ALONE — no live gateway calls, no
recomputation against the model. Re-rendering the same run-id therefore always
produces the same report (modulo the rendered_at timestamp).

Usage:
  python smoketests/cinematic-01/report.py [--run-id <id>] [--out <path>]

Defaults:
  --run-id  latest run (newest runs/<run-*/eval.json)
  --out     datasets/cinematic-01/runs/<run-id>/report.md

Example:
  pixi run cinematic-01-test && pixi run cinematic-01-report
"""

import argparse
import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS_DIR = os.path.join(REPO_ROOT, "datasets", "cinematic-01", "runs")

MODEL_ALIASES = {
    "local-gguf": "LFM2.5-2.6B Q8_0 GGUF (LocalAI llama.cpp, retired)",
    "local-judge": "granite-4.0-h-tiny Q4_K_XL GGUF (unsloth llama.cpp)",
    "local-thinking": "LFM2.5-2.6B Q4_K_M GGUF (LocalAI llama.cpp)",
    "local-llama": "LFM2.5-2.6B W8A16 (vLLM)",
    "local-vllm": "LFM2.5-2.6B W8A16 (vLLM)",
    "local-sglang": "LFM2.5-2.6B W8A16 (SGLang)",
}

ENGINE_BY_ALIAS = {
    "local-gguf": "llama.cpp (Q8_0, retired)",
    "local-judge": "llama.cpp (Q4_K_XL)",
    "local-thinking": "llama.cpp (Q4_K_M)",
    "local-llama": "vLLM (W8A16)",
    "local-vllm": "vLLM (W8A16)",
    "local-sglang": "SGLang (W8A16)",
}


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def latest_run_id():
    runs = []
    for name in os.listdir(RUNS_DIR):
        run_dir = os.path.join(RUNS_DIR, name)
        if not os.path.isdir(run_dir):
            continue
        eval_path = os.path.join(run_dir, "eval.json")
        if os.path.isfile(eval_path):
            runs.append((os.path.getmtime(eval_path), name))
    if not runs:
        sys.exit(f"no runs under {RUNS_DIR}; run cinematic-01-test first")
    return max(runs)[1]


def avg(values):
    return sum(values) / len(values) if values else 0.0


def render(results, eval_summary, run_id):
    meta = results["meta"]
    model_alias = meta.get("model_alias", "local-judge")
    model_hw = MODEL_ALIASES.get(model_alias, model_alias)

    lines = []
    a = lines.append
    a(f"# cinematic-01 smoke run: {run_id}")
    a("")
    a(f"- **model**: `{model_alias}` — {model_hw}")
    engine = ENGINE_BY_ALIAS.get(model_alias, "llama.cpp engine")
    a(f"- **gateway**: `{meta['base_url']}` (LiteLLM, {engine})")
    a(f"- **dataset**: `{meta['dataset']}`")
    a(f"- **sample**: `{meta['sample']}` rows (round-robin across studios)")
    a(
        f"- **mode**: `{meta.get('reasoning', 'disabled')}` reasoning, "
        f"`{meta.get('workers', 4)}` workers"
    )
    a(f"- **run_at**: `{meta['run_at']}`")
    a(f"- **test**: `{meta['test']}`")
    a("")

    d1 = eval_summary["scenario_1_studio_recall"]
    d2 = eval_summary["scenario_2_year_match"]
    d3 = eval_summary["scenario_3_year_repeat"]

    a("## Scenarios")
    a("")
    a("| # | Scenario | Metric | Score | Threshold | Pass |")
    a("|---|----------|--------|-------|-----------|------|")
    a(
        f"| 1 | Studio recall | {d1['metric']} | **{d1['score']}** ({d1['fraction']:.0%}) | — | — |"
    )
    a(
        f"| 2 | Year match (±{meta['year_tolerance']}) | {d2['metric']} | **{d2['score']}** ({d2['fraction']:.0%}) | — | — |"
    )
    a(
        f"| 3 | Year repeat | {d3['metric']} | **{d3['score']}** | {d3['threshold']} | {'PASS' if d3.get('passed') else '**FAIL**'} |"
    )
    a("")

    rows1 = results["scenario_1_studio_recall"]["rows"]
    rows2 = results["scenario_2_year_match"]["rows"]
    rows3 = results["scenario_3_year_repeat"]["rows"]
    tok1 = sum((r["usage"].get("total_tokens") or 0) for r in rows1)
    tok2 = sum((r["usage"].get("total_tokens") or 0) for r in rows2)
    tok3 = sum((r["usage"].get("total_tokens") or 0) for r in rows3)
    t1 = avg([r["seconds"] for r in rows1])
    t2 = avg([r["seconds"] for r in rows2])
    t3 = avg([r["seconds"] for r in rows3])

    a("## Observations")
    a("")
    a("| Scenario | Calls | Total tokens | Avg latency |")
    a("|----------|-------|--------------|-------------|")
    a(f"| 1 | {len(rows1)} | {tok1} | {t1:.2f}s |")
    a(f"| 2 | {len(rows2)} | {tok2} | {t2:.2f}s |")
    a(f"| 3 | {len(rows3)} | {tok3} | {t3:.2f}s |")
    a("")

    misses = [r for r in rows1 if not r["correct"]]
    a("## Miss detail — studio recall")
    a("")
    a("| Studio | Film | Guessed |")
    a("|--------|------|---------|")
    for r in misses:
        a(f"| {r['studio']} | {r['film']} | {r['guess'] or '—'} |")
    a("")

    a("## Re-run")
    a("")
    a("```sh")
    a(
        f"pixi run cinematic-01-test -- --model {model_alias}"
    )
    a("pixi run cinematic-01-report")
    a("```")
    a("")
    a(
        "Artifacts for this report:"
        if os.path.isdir(os.path.join(RUNS_DIR, run_id))
        else "Artifacts:"
    )
    a("")
    a(f"- `datasets/cinematic-01/runs/{run_id}/results.json` — raw rows")
    a(f"- `datasets/cinematic-01/runs/{run_id}/eval.json` — scored scenarios")
    a(f"- `datasets/cinematic-01/runs/{run_id}/report.md` — this report")
    a("- latest copies: `datasets/cinematic-01/results.json`, `eval.json`")
    a("")
    a("---")
    a("")
    a(
        f"*Rendered by `smoketests/cinematic-01/report.py` "
        f"at {time.strftime('%Y-%m-%dT%H:%M:%S%z')}.*"
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Render a reproducible report.md")
    parser.add_argument(
        "--run-id", default=None, help="run id; defaults to the latest run"
    )
    parser.add_argument(
        "--out", default=None, help="output path; defaults to the run dir"
    )
    args = parser.parse_args()

    run_id = args.run_id or latest_run_id()
    run_dir = os.path.join(RUNS_DIR, run_id)
    results_path = os.path.join(run_dir, "results.json")
    eval_path = os.path.join(run_dir, "eval.json")
    if not (os.path.isfile(results_path) and os.path.isfile(eval_path)):
        sys.exit(
            f"missing artifacts for run {run_id}: need {results_path} and {eval_path}"
        )
    out_path = args.out or os.path.join(run_dir, "report.md")

    results = load_json(results_path)
    eval_summary = load_json(eval_path)
    report = render(results, eval_summary, run_id)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(report + "\n")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
