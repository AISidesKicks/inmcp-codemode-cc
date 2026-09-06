#!/usr/bin/env python3
"""cinematic-01 smoke test: studio recall, film+year match, year repeat.

Reads the generated CSV (datasets/cinematic-01/dataset.csv) as ground truth
(studio names seeded, films/years model-generated — see design.md) and evaluates
the model against it:

1. studio recall   — which studio produced a film (schema answer via StudioList)
  2. film+year match — model's year for a film within +/-2 of the dataset year
  3. year repeat     — deepeval ExactMatchMetric (threshold 0.8) over reworded
                       year prompts
   (`--model` picks the engine alias)

Writes per-run datasets/cinematic-01/runs/<run-id>/results.json (raw rows) and
datasets/cinematic-01/runs/<run-id>/eval.json (scored scenarios), plus refreshed
"latest" copies at datasets/cinematic-01/results.json and eval.json.

Every model call also emits a client-side OpenInference LLM span direct to
Phoenix project `cdmd-lab` with session.id = run-id, so the run groups into one
Phoenix Session (user edu-harness, one turn per call). `--no-session` skips it.
"""

import argparse
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
    StudioList,
    YearAnswer,
    chat,
    completion_text,
    get_repo_root,
    health,
    reasoning_content,
    usage_fields,
)

YEAR_TOLERANCE = 2
REMINDER = "Please answer again:"
MAX_TRIES = 3

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
    fenced block is stripped before validation. The tested LFM2.5-2.6B splits
    its reasoning into `reasoning_content` on llama.cpp; a closed inline
    `</think>` block is still dropped defensively and an unclosed one counts
    as a miss.
    """
    if not text:
        return None
    stripped = text.strip()
    if "</think>" in stripped:
        stripped = stripped.split("</think>", 1)[1].strip()
    elif stripped.startswith("<think>"):
        return None
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        last = stripped.rfind("```")
        if first_nl != -1 and last > first_nl:
            stripped = stripped[first_nl + 1 : last].strip()
    try:
        return schema.model_validate_json(stripped)
    except Exception:  # noqa: BLE001 - malformed answers count as misses
        return None


def query_schema(content, schema, max_tokens, reasoning, run_name=None):
    """Schema answer with retries; returns (model, text, seconds, resp, span_id).

    Retries prepend a reminder so a failed empty/truncated generation for the
    original prompt is regenerated, not repeated verbatim. Empty completions
    degrade to a miss instead of failing the run. Guided decoding is skipped
    for vLLM (returns empty completions); parse-then-retry covers it instead.
    Every attempt's Phoenix generation span is named run_name. The whole test
    runs inside an AGENT turn root (llm.turn) so the trace reads
    agent -> LLM and the session row's first-input/last-output fill; the turn
    root's span id comes back for the `eval` span annotation.
    """
    guided = "vllm" not in llm.MODEL
    last_resp = None
    seconds = 0.0
    span_id = None
    with llm.turn(run_name, content) as sp:
        if sp is not None:
            span_id = format(sp.get_span_context().span_id, "016x")
        for attempt in range(1, MAX_TRIES + 1):
            payload = (REMINDER + " " + content) if attempt > 1 else content
            resp, seconds = chat(
                payload,
                max_tokens=max_tokens,
                response_format=schema,
                reasoning=reasoning,
                guided=guided,
                run_name=run_name,
            )
            if resp is None:
                continue
            last_resp = resp
            parsed = parse_model(completion_text(resp), schema)
            if parsed is not None:
                if sp is not None:
                    sp.set_attribute(
                        llm.SpanAttributes.OUTPUT_VALUE, completion_text(resp)
                    )
                return parsed, completion_text(resp), seconds, resp, span_id
        if sp is not None:
            sp.set_attribute(
                llm.SpanAttributes.OUTPUT_VALUE, completion_text(last_resp)
            )
    return None, completion_text(last_resp), seconds, last_resp, span_id


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


def scenario_studio_recall(sample, args):
    """Which studio produced each sampled film; exact normalized name match."""

    def one(row):
        prompt = (
            f'Which studio produced the movie "{row["film"]}"? Reply with only a '
            'JSON object shaped like {"studios": ["Studio Name"]}.'
        )
        run_name = f"{args.run_id} recall {row['film']}"
        parsed, text, seconds, resp, span_id = query_schema(
            prompt, StudioList, args.max_tokens, args.reasoning, run_name
        )
        guess = parsed.studios[0] if parsed else None
        correct = guess is not None and normalize(guess) == normalize(row["studio"])
        return {
            "studio": row["studio"],
            "film": row["film"],
            "guess": guess,
            "answer": text[:200],
            "correct": correct,
            "reasoning": reasoning_content(resp),
            "seconds": round(seconds, 3),
            "usage": usage_fields(resp),
            "run_name": run_name,
            "span_id": span_id,
        }

    rows = [one(row) for row in sample]
    for row in rows:
        print(
            f"  recall {row['studio']:<28} -> {row.get('guess') or '?'}"
            f"{'  OK' if row['correct'] else '  miss'} {row['seconds']}s",
            flush=True,
        )
    correct = sum(1 for r in rows if r["correct"])
    return rows, correct


def scenario_year_match(sample, args):
    """Model's year for each film, within +/-2 of the dataset year."""

    def one(row):
        if row["year"] is None:
            return None
        prompt = (
            f'In what year was the movie "{row["film"]}" released? Reply with '
            'only a JSON object shaped like {"title": "Title", "year": 1995}.'
        )
        run_name = f"{args.run_id} year {row['film']}"
        parsed, text, seconds, resp, span_id = query_schema(
            prompt, YearAnswer, args.max_tokens, args.reasoning, run_name
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
            "reasoning": reasoning_content(resp),
            "seconds": round(seconds, 3),
            "usage": usage_fields(resp),
            "run_name": run_name,
            "span_id": span_id,
        }

    rows = [r for r in (one(row) for row in sample) if r is not None]
    for row in rows:
        print(
            f"  year   {row['film']:<40} {row['expected']} vs {row.get('predicted')}"
            f" {'OK' if row['correct'] else 'miss'} {row['seconds']}s",
            flush=True,
        )
    correct = sum(1 for r in rows if r["correct"])
    return rows, correct


def scenario_year_repeat(sample, args, threshold):
    """Reworded year re-answer scored with deepeval ExactMatchMetric."""

    def one(row):
        if row["year"] is None:
            return None
        prompt = (
            f'{REMINDER} what year was the movie "{row["film"]}" released? '
            'Reply with only a JSON object shaped like {"title": "Title", "year": 1995}.'
        )
        run_name = f"{args.run_id} repeat {row['film']}"
        parsed, text, seconds, resp, span_id = query_schema(
            prompt, YearAnswer, args.max_tokens, args.reasoning, run_name
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
            "reasoning": reasoning_content(resp),
            "seconds": round(seconds, 3),
            "usage": usage_fields(resp),
            "run_name": run_name,
            "span_id": span_id,
        }

    rows = [r for r in (one(row) for row in sample) if r is not None]
    for row in rows:
        print(
            f"  repeat {row['film']:<40} {row['expected']} vs {row['predicted']}"
            f" {'OK' if row['metric_score'] == 1.0 else 'miss'} {row['seconds']}s",
            flush=True,
        )
    score = sum(r["metric_score"] for r in rows) / len(rows) if rows else 0.0
    return rows, score, score >= threshold


def default_run_id(model_alias):
    """Unique per invocation: timestamp + model alias."""
    return f"run-{time.strftime('%Y%m%d-%H%M%S')}-{model_alias}"


def annotate_eval_links(args, scenario_rows):
    """Best-effort Phoenix span annotations (`eval` = ok/miss) on each test's
    AGENT turn root in project cdmd-lab. Span ids come from the client-side
    turn spans, so no lookup is needed; any failure only prints a warning so
    the smoke test never depends on Phoenix being up.
    """
    if args.no_annotate:
        return
    links = []
    for kind, rows in scenario_rows:
        for row in rows:
            span_id = row.get("span_id")
            if not span_id:
                continue
            ok = row["metric_score"] == 1.0 if kind == "repeat" else row["correct"]
            links.append((span_id, ok))
    if not links:
        return
    try:
        from phoenix.client import Client

        client = Client()
        for span_id, ok in links:
            client.spans.add_span_annotation(
                span_id=span_id,
                annotation_name="eval",
                annotator_kind="CODE",
                label="ok" if ok else "miss",
                score=1.0 if ok else 0.0,
                sync=True,
            )
        print(f"phoenix annotations: {len(links)} written")
    except ImportError:
        print("warning: phoenix client not available; span annotations skipped")
    except Exception as exc:  # noqa: BLE001 - annotations are best-effort
        print(f"warning: span annotations skipped: {type(exc).__name__}: {str(exc)[:200]}")


def echo_verdicts(args, scenario_rows):
    """Best-effort Phoenix status stamping: one tiny echo call per scored row
    (`<run_name> PASS|FAIL`), stamped as the session's echo turn output. Never
    fails the run.
    """
    if args.no_echo:
        return
    fired = 0
    for kind, rows in scenario_rows:
        for row in rows:
            run_name = row.get("run_name")
            if not run_name:
                continue
            ok = row["metric_score"] == 1.0 if kind == "repeat" else row["correct"]
            if llm.echo_verdict(run_name, "PASS" if ok else "FAIL") is not None:
                fired += 1
    print(f"echo verdicts: {fired} stamped")


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
        "--threshold", type=float, default=0.8, help="ExactMatchMetric threshold"
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="run identifier, defaults to timestamp+model (e.g. run-20260822-120000-local-thinking)",
    )
    parser.add_argument(
        "--model",
        default="local-thinking",
        help="engine model alias (default local-thinking)",
    )
    parser.add_argument("--skip-health", action="store_true", help="skip health probes")
    parser.add_argument(
        "--no-annotate",
        action="store_true",
        help="skip Phoenix span annotations",
    )
    parser.add_argument(
        "--no-echo",
        action="store_true",
        help="skip Phoenix verdict-echo status stamping",
    )
    parser.add_argument(
        "--no-session",
        action="store_true",
        help="skip client-side Phoenix session tracing (project cdmd-lab)",
    )
    args = parser.parse_args()
    llm.MODEL = args.model  # chat() defaults to MODEL when no model kwarg given
    if args.model not in llm.ENGINES:
        sys.exit(f"unknown model alias {args.model!r}; known: {sorted(llm.ENGINES)}")
    llm.TRACING["enabled"] = not args.no_session

    if args.max_tokens is None:
        args.max_tokens = 1536  # thinking always on needs the headroom
    args.reasoning = {"enabled": True}

    if not args.skip_health:
        for name, url in (
            ("tested-8081", llm.THINKING_URL),
            ("judge-8080", llm.JUDGE_URL),
        ):
            if not health(url + "/health"):
                sys.exit(f"engine not ready at {url}/health")
    print(f"engines healthy; dataset {args.csv}")

    run_id = args.run_id or default_run_id(args.model)
    args.run_id = run_id

    run_dir = os.path.join(RUNS_DIR, run_id)
    run_results = os.path.join(run_dir, "results.json")
    run_eval = os.path.join(run_dir, "eval.json")
    print(f"run id: {run_id}")

    rows = load_rows(args.csv)
    if not rows:
        sys.exit("empty dataset; run cinematic-01-generate first")
    sample = sample_rows(rows, args.sample)
    print(f"{len(rows)} rows loaded, sampling {len(sample)} ({args.sample})")

    # single-slot llama.cpp engines: strictly serial, no client thread pool
    with llm.session(run_id):
        recall, recall_ok = scenario_studio_recall(sample, args)
        print(f"scenario 1 studio recall: {recall_ok}/{len(recall)}")

        year_match, year_ok = scenario_year_match(sample, args)
        print(
            f"scenario 2 year match (+/-{YEAR_TOLERANCE}): {year_ok}/{len(year_match)}"
        )

        year_repeat, exact_score, exact_passed = scenario_year_repeat(
            sample, args, args.threshold
        )
        print(
            f"scenario 3 year repeat ExactMatchMetric: {exact_score:.2f} "
            f"({'PASS' if exact_passed else 'FAIL'})"
        )
    meta = {
        "name": "cinematic-01",
        "test": "smoketests/cinematic-01/test.py",
        "run_id": run_id,
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model_alias": args.model,
        "base_url": llm.ENGINES[args.model],
        "dataset": args.csv,
        "sample": len(sample),
        "year_tolerance": YEAR_TOLERANCE,
        "reasoning": "enabled",
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
    annotate_rows = (
        ("recall", [dict(row) for row in recall]),
        ("year", [dict(row) for row in year_match]),
        ("repeat", [dict(row) for row in year_repeat]),
    )
    for rows in (recall, year_match, year_repeat):
        for row in rows:
            row.pop("run_name", None)
            row.pop("span_id", None)
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
    with llm.session(run_id):
        echo_verdicts(args, annotate_rows)
    # flush the batch exporter first — annotations target span ids Phoenix
    # does not know about until the turn roots are exported
    llm.flush()
    annotate_eval_links(args, annotate_rows)


if __name__ == "__main__":
    main()
