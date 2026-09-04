#!/usr/bin/env python3
"""cinematic-01 smoke test: studio recall, film+year match, year repeat + cache demo.

Reads the generated CSV (datasets/cinematic-01/dataset.csv) as ground truth
(studio names seeded, films/years model-generated — see design.md) and evaluates
the model against it:

1. studio recall   — which studio produced a film (schema answer via StudioList)
  2. film+year match — model's year for a film within +/-2 of the dataset year
  3. year repeat     — deepeval ExactMatchMetric (threshold 0.8) over reworded
                       year prompts, so fresh Redis misses instead of hits
  + the cache demo in three modes (`--model` routes through the gateway):
      1level   — distinct +1/+2/+3 suffixes, LiteLLM bypassed per request
                 (`cache={"no-cache": True}`), engine prefix cache does reuse
      2level   — Q1/Q2/Q3 identical-prompt legacy demo, Q2 Redis hit
      no-cache — noise-prefixed prompts, cold at every tier

Writes per-run datasets/cinematic-01/runs/<run-id>/results.json (raw rows) and
datasets/cinematic-01/runs/<run-id>/eval.json (scored scenarios), plus refreshed
"latest" copies at datasets/cinematic-01/results.json and eval.json.
"""

import argparse
import concurrent.futures
import csv
import json
import os
import re
import sys
import time

import llm
from deepeval.metrics.exact_match.exact_match import ExactMatchMetric
from deepeval.test_case import LLMTestCase

sys.path.insert(0, os.path.dirname(__file__))

from llm import (
    DEFAULT_BASE_URL,
    MODEL,
    StudioList,
    YearAnswer,
    cache_regime,
    cached_tokens,
    chat,
    completion_text,
    get_repo_root,
    health,
    reasoning_content,
    sglang_prefix_metrics,
    timings,
    usage_fields,
    vllm_prefix_metrics,
)

YEAR_TOLERANCE = 2
REMINDER = "Please answer again:"
MAX_TRIES = 3
CACHE_MODES = ("1level", "2level", "no-cache", "both")

BASE_PROMPT = "Give me a short summary about Marvel Cinematic Universe."
Q3_SUFFIX = " - 3rd repeat"

DEFAULT_CSV = os.path.join(get_repo_root(), "datasets", "cinematic-01", "dataset.csv")
RUNS_DIR = os.path.join(get_repo_root(), "datasets", "cinematic-01", "runs")
RESULTS_PATH = os.path.join(get_repo_root(), "datasets", "cinematic-01", "results.json")
EVAL_PATH = os.path.join(get_repo_root(), "datasets", "cinematic-01", "eval.json")


def normalize(name):
    """Case/punctuation-insensitive token for comparing answers to studios."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def parse_model(text, schema):
    """Best-effort schema parse; None on malformed output.

    vLLM's unguided completions often wrap JSON in Markdown code fences, so a
    fenced block is stripped before validation.
    """
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        last = stripped.rfind("```")
        if first_nl != -1 and last > first_nl:
            stripped = stripped[first_nl + 1 : last].strip()
    try:
        return schema.model_validate_json(stripped)
    except Exception:  # noqa: BLE001 - malformed answers count as misses
        return None


def query_schema(content, schema, max_tokens, reasoning):
    """Schema answer with retries; returns (model, text, seconds, resp).

    Retries prepend a reminder so a cached empty/truncated answer for the
    original prompt is bypassed, not replayed. Empty completions degrade to a
    miss instead of failing the run. Guided decoding is skipped for vLLM
    (returns empty completions); parse-then-retry covers it instead.
    """
    guided = "vllm" not in llm.MODEL
    last_resp = None
    seconds = 0.0
    for attempt in range(1, MAX_TRIES + 1):
        payload = (REMINDER + " " + content) if attempt > 1 else content
        resp, seconds = chat(
            payload,
            max_tokens=max_tokens,
            response_format=schema,
            reasoning=reasoning,
            guided=guided,
        )
        if resp is None:
            continue
        last_resp = resp
        parsed = parse_model(completion_text(resp), schema)
        if parsed is not None:
            return parsed, completion_text(resp), seconds, resp
    return None, completion_text(last_resp), seconds, last_resp


def load_rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = []
        for raw in csv.DictReader(fh):
            year = (raw["year"] or "").strip()
            rows.append(
                {
                    "studio": raw["studio name"].strip(),
                    "film": raw["film name"].strip(),
                    "year": int(year) if year else None,
                }
            )
    return rows


def sample_rows(rows, n):
    """Up to n rows spread across studios (round-robin) for a sane smoke budget."""
    buckets = {}
    order = []
    for row in rows:
        if row["studio"] not in buckets:
            buckets[row["studio"]] = []
            order.append(row["studio"])
        buckets[row["studio"]].append(row)
    picked = []
    while len(picked) < n and any(buckets[s] for s in order):
        for studio in order:
            if len(picked) == n:
                break
            if buckets[studio]:
                picked.append(buckets[studio].pop(0))
    return picked


def scenario_studio_recall(sample, args, executor):
    """Which studio produced each sampled film; exact normalized name match."""

    def one(row):
        prompt = (
            f'Which studio produced the movie "{row["film"]}"? Reply with only a '
            'JSON object shaped like {"studios": ["Studio Name"]}.'
        )
        parsed, text, seconds, resp = query_schema(
            prompt, StudioList, args.max_tokens, args.reasoning
        )
        guess = parsed.studios[0] if parsed else None
        correct = guess is not None and normalize(guess) == normalize(row["studio"])
        return {
            "studio": row["studio"],
            "film": row["film"],
            "guess": guess,
            "answer": text[:200],
            "correct": correct,
            "cache_regime": cache_regime(resp),
            "reasoning": reasoning_content(resp),
            "seconds": round(seconds, 3),
            "usage": usage_fields(resp),
        }

    rows = list(executor.map(one, sample))
    for row in rows:
        print(
            f"  recall {row['studio']:<28} -> {row.get('guess') or '?'}"
            f"{'  OK' if row['correct'] else '  miss'} {row['seconds']}s",
            flush=True,
        )
    correct = sum(1 for r in rows if r["correct"])
    return rows, correct


def scenario_year_match(sample, args, executor):
    """Model's year for each film, within +/-2 of the dataset year."""

    def one(row):
        if row["year"] is None:
            return None
        prompt = (
            f'In what year was the movie "{row["film"]}" released? Reply with '
            'only a JSON object shaped like {"title": "Title", "year": 1995}.'
        )
        parsed, text, seconds, resp = query_schema(
            prompt, YearAnswer, args.max_tokens, args.reasoning
        )
        year = parsed.year if parsed else None
        ok = year is not None and abs(year - row["year"]) <= YEAR_TOLERANCE
        return {
            "studio": row["studio"],
            "film": row["film"],
            "expected": row["year"],
            "predicted": year,
            "correct": ok,
            "answer": text[:200],
            "cache_regime": cache_regime(resp),
            "reasoning": reasoning_content(resp),
            "seconds": round(seconds, 3),
            "usage": usage_fields(resp),
        }

    rows = [r for r in executor.map(one, sample) if r is not None]
    for row in rows:
        print(
            f"  year   {row['film']:<40} {row['expected']} vs {row.get('predicted')}"
            f" {'OK' if row['correct'] else 'miss'} {row['seconds']}s",
            flush=True,
        )
    correct = sum(1 for r in rows if r["correct"])
    return rows, correct


def scenario_year_repeat(sample, args, threshold, executor):
    """Reworded year re-answer scored with deepeval ExactMatchMetric."""

    def one(row):
        if row["year"] is None:
            return None
        prompt = (
            f'{REMINDER} what year was the movie "{row["film"]}" released? '
            'Reply with only a JSON object shaped like {"title": "Title", "year": 1995}.'
        )
        parsed, text, seconds, resp = query_schema(
            prompt, YearAnswer, args.max_tokens, args.reasoning
        )
        year = parsed.year if parsed else None
        predicted = str(year) if year is not None else ""
        if not predicted:
            metric_score = 0.0
        else:
            metric = ExactMatchMetric(threshold=threshold)
            metric.measure(
                LLMTestCase(
                    input=prompt,
                    actual_output=predicted,
                    expected_output=str(row["year"]),
                )
            )
            metric_score = metric.score
        return {
            "studio": row["studio"],
            "film": row["film"],
            "expected": str(row["year"]),
            "predicted": predicted,
            "metric_score": metric_score,
            "answer": text[:200],
            "cache_regime": cache_regime(resp),
            "reasoning": reasoning_content(resp),
            "seconds": round(seconds, 3),
            "usage": usage_fields(resp),
        }

    rows = [r for r in executor.map(one, sample) if r is not None]
    for row in rows:
        print(
            f"  repeat {row['film']:<40} {row['expected']} vs {row['predicted']}"
            f" {'OK' if row['metric_score'] == 1.0 else 'miss'} {row['seconds']}s",
            flush=True,
        )
    score = sum(r["metric_score"] for r in rows) / len(rows) if rows else 0.0
    return rows, score, score >= threshold


def ctdemo_rows(calls, mode, args):
    """Run one cache-demo variant; retries are capped at one attempt so a
    re-ask never mutates the prompt mid-demo and breaks the suffix/prefix
    reuse invariants."""
    rows = []
    for spec in calls:
        resp, seconds = chat(
            spec["content"],
            max_tokens=256,
            reasoning=args.reasoning,
            cache_mode=mode,
            retries=1,
            model=args.model,
        )
        regime = cache_regime(resp)
        row = {
            "mode": mode,
            "call": spec["call"],
            "observed_regime": regime,
            "prompt": spec["content"],
            "base_only": spec["content"] == BASE_PROMPT,
            "reasoning": reasoning_content(resp),
            "seconds": round(seconds, 4),
            "usage": usage_fields(resp),
            "cached_tokens": cached_tokens(resp),
            "timings": timings(resp),
            "content_head": completion_text(resp)[:160],
        }
        rows.append(row)
        print(
            f"  [{mode:<8}] {row['call']} {regime:<20} {round(seconds, 4):>8.4f}s "
            f"cached={row['cached_tokens']} "
            f"cache_n={row['timings'].get('cache_n')}",
            flush=True,
        )
    return rows


def cache_demo(args, mode):
    """Per-mode three-call demo. `both` runs each of the three modes in turn."""
    if mode == "both":
        rows = []
        for m in CACHE_MODES[:3]:
            rows.extend(cache_demo(args, m)[0])
        return rows, "both"

    def prefix_metrics():
        if "sglang" in llm.MODEL:
            return sglang_prefix_metrics("http://localhost:30000")
        return vllm_prefix_metrics(args.base_url.replace(":4000", ":8000"))

    pre = prefix_metrics() or {}
    if mode == "1level":
        calls = [
            {"call": "base 1", "content": BASE_PROMPT + " - 1"},
            {"call": "base 2", "content": BASE_PROMPT + " - 2"},
            {"call": "base 3", "content": BASE_PROMPT + " - 3"},
        ]
    elif mode == "2level":
        calls = [
            {"call": "Q1", "content": BASE_PROMPT},
            {"call": "Q2", "content": BASE_PROMPT},
            {"call": "Q3", "content": BASE_PROMPT + Q3_SUFFIX},
        ]
    else:  # no-cache
        calls = [
            {
                "call": "N1",
                "content": "Tell me about the Marvel Cinematic Universe film history.",
            },
            {
                "call": "N2",
                "content": "List some details from the Marvel Cinematic Universe movies.",
            },
            {
                "call": "N3",
                "content": "Provide a summary covering the Marvel Cinematic Universe.",
            },
        ]
    print(f"cache demo: {mode}", flush=True)
    rows = ctdemo_rows(calls, mode, args)
    post = prefix_metrics() or {}
    if pre and post:
        rows.append(
            {
                "mode": mode,
                "call": "engine",
                "observed_regime": "",
                "prompt": "",
                "base_only": False,
                "reasoning": None,
                "seconds": 0.0,
                "usage": {},
                "cached_tokens": None,
                "timings": {},
                "content_head": "",
                "prefix_cache_delta": {
                    "hits": post["hits"] - pre["hits"],
                    "queries": post["queries"] - pre["queries"],
                },
            }
        )
        print(
            f"  [{mode:<8}] engine prefix-cache Δ hits={post['hits'] - pre['hits']} "
            f"queries={post['queries'] - pre['queries']}",
            flush=True,
        )
    return rows, mode


def default_run_id(model_alias):
    """Unique per invocation: timestamp + model alias."""
    return f"run-{time.strftime('%Y%m%d-%H%M%S')}-{model_alias}"


def main():
    parser = argparse.ArgumentParser(description="Run the cinematic-01 smoke test.")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="dataset CSV path")
    parser.add_argument(
        "--sample",
        type=int,
        default=20,
        help="films to probe per scenario (default 20)",
    )
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument(
        "--reasoning",
        action="store_true",
        help="let the model reason first (slower, larger token budget)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="max concurrent calls (matches the engines' 4 parallel slots)",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.8, help="ExactMatchMetric threshold"
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="LiteLLM gateway URL"
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="run identifier, defaults to timestamp+model (e.g. run-20260822-120000-local-gguf)",
    )
    parser.add_argument(
        "--model", default=MODEL, help="gateway model alias (default local-gguf)"
    )
    parser.add_argument(
        "--cache-mode",
        default="1level",
        choices=CACHE_MODES,
        help="cache demo mode: 1level (default) | 2level | no-cache | both",
    )
    parser.add_argument("--skip-health", action="store_true", help="skip health probes")
    args = parser.parse_args()
    llm.MODEL = args.model  # chat() defaults to MODEL when no model kwarg given

    if args.reasoning and args.max_tokens is None:
        args.max_tokens = 1536
    if args.max_tokens is None:
        args.max_tokens = 512
    args.reasoning = {"enabled": True} if args.reasoning else {"enabled": False}
    if args.workers < 1:
        sys.exit("--workers must be >= 1")

    if not args.skip_health and not health(args.base_url + "/health/readiness"):
        sys.exit(f"gateway not ready at {args.base_url}/health/readiness")
    print(f"gateway {args.base_url} healthy; dataset {args.csv}")

    run_id = args.run_id or default_run_id(args.model)

    run_dir = os.path.join(RUNS_DIR, run_id)
    run_results = os.path.join(run_dir, "results.json")
    run_eval = os.path.join(run_dir, "eval.json")
    print(f"run id: {run_id}")

    rows = load_rows(args.csv)
    if not rows:
        sys.exit("empty dataset; run cinematic-01-generate first")
    sample = sample_rows(rows, args.sample)
    print(f"{len(rows)} rows loaded, sampling {len(sample)} ({args.sample})")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        recall, recall_ok = scenario_studio_recall(sample, args, executor)
        print(f"scenario 1 studio recall: {recall_ok}/{len(recall)}")

        year_match, year_ok = scenario_year_match(sample, args, executor)
        print(
            f"scenario 2 year match (+/-{YEAR_TOLERANCE}): {year_ok}/{len(year_match)}"
        )

        year_repeat, exact_score, exact_passed = scenario_year_repeat(
            sample, args, args.threshold, executor
        )
        print(
            f"scenario 3 year repeat ExactMatchMetric: {exact_score:.2f} "
            f"({'PASS' if exact_passed else 'FAIL'})"
        )

    demo, demo_mode = cache_demo(args, args.cache_mode)
    print(f"cache demo {demo_mode} done")

    meta = {
        "name": "cinematic-01",
        "test": "smoketests/cinematic-01/test.py",
        "run_id": run_id,
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model_alias": args.model,
        "cache_mode": demo_mode,
        "base_url": args.base_url,
        "dataset": args.csv,
        "sample": len(sample),
        "year_tolerance": YEAR_TOLERANCE,
        "reasoning": "enabled" if args.reasoning["enabled"] else "disabled",
        "workers": args.workers,
    }
    results = {
        "meta": meta,
        "scenario_1_studio_recall": {
            "score": f"{recall_ok}/{len(recall)}",
            "rows": recall,
        },
        "scenario_2_year_match": {
            "score": f"{year_ok}/{len(year_match)}",
            "rows": year_match,
        },
        "scenario_3_year_repeat": {
            "score": f"{exact_score:.2f}",
            "rows": year_repeat,
        },
        "cache_demo": {"rows": demo},
    }
    eval_summary = {
        "meta": meta,
        "scenario_1_studio_recall": {
            "metric": "manual exact match",
            "score": f"{recall_ok}/{len(recall)}",
            "fraction": round(recall_ok / len(recall), 3) if recall else 0.0,
        },
        "scenario_2_year_match": {
            "metric": f"abs diff <= {YEAR_TOLERANCE}",
            "score": f"{year_ok}/{len(year_match)}",
            "fraction": round(year_ok / len(year_match), 3) if year_match else 0.0,
        },
        "scenario_3_year_repeat": {
            "metric": "deepeval.ExactMatchMetric",
            "threshold": args.threshold,
            "score": round(exact_score, 3),
            "passed": exact_passed,
        },
    }
    os.makedirs(run_dir, exist_ok=True)
    for path, payload in ((run_results, results), (run_eval, eval_summary)):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
    for path in (RESULTS_PATH, EVAL_PATH):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                results if path == RESULTS_PATH else eval_summary,
                fh,
                indent=2,
                ensure_ascii=False,
            )
    print(f"wrote {run_results} and {run_eval}")
    print(f"latest copies at {RESULTS_PATH} and {EVAL_PATH}")


if __name__ == "__main__":
    main()
