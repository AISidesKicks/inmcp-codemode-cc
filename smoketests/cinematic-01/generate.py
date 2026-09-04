#!/usr/bin/env python3
"""Generate the cinematic-01 micro dataset: real studios -> films -> years.

The 20 studio names are seeded ground truth (see design.md); film titles and
release years are produced by the llama.cpp-backed LFM2.5-2.6B through the
LiteLLM gateway (alias `local-gguf`) using schema-validated structured output.
The dataset lands in a QUOTE_ALL CSV at datasets/cinematic-01/dataset.csv;
per-call cache regime, seconds and llama.cpp timings are checkpointed to
datasets/cinematic-01/generate.json after every studio.

Same title across studios is deduped exactly on the normalized title; years are
guarded to 1900-2023 and films outside that range are left out of the CSV.
"""

import argparse
import csv
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from llm import (
    DEFAULT_BASE_URL,
    MODEL,
    FilmList,
    YearAnswer,
    cache_regime,
    chat,
    completion_text,
    get_repo_root,
    health,
    timings,
    usage_fields,
)

STUDIOS = [
    "Marvel Studios",
    "DC Studios",
    "Walt Disney Pictures",
    "Pixar Animation Studios",
    "Warner Bros. Pictures",
    "Universal Pictures",
    "Paramount Pictures",
    "Sony Pictures",
    "Columbia Pictures",
    "20th Century Studios",
    "DreamWorks Animation",
    "Lionsgate Films",
    "A24",
    "Metro-Goldwyn-Mayer",
    "Legendary Entertainment",
    "New Line Cinema",
    "Studio Ghibli",
    "Focus Features",
    "Searchlight Pictures",
    "Netflix",
]

YEAR_MIN = 1900
YEAR_MAX = 2023
MAX_TRIES = 4
RETRY_SUFFIX = " Please answer again."

DEFAULT_CSV = os.path.join(get_repo_root(), "datasets", "cinematic-01", "dataset.csv")
DEFAULT_LOG = os.path.join(get_repo_root(), "datasets", "cinematic-01", "generate.json")


def normalize_title(title):
    """Exact-match key for dedupe: letters+digits only, lowercase."""
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def film_list_prompt(studio, max_films):
    return (
        f"Name up to {max_films} famous feature films that {studio} produced "
        "or distributed. Reply with only a JSON object shaped like "
        '{"films": ["Title One", "Title Two"]}.'
    )


def year_prompt(title):
    return (
        f'In what year was the movie "{title}" released? Reply with only a JSON '
        'object shaped like {"title": "Title", "year": 1995}.'
    )


def ask_json(content, schema, max_tokens, reasoning):
    """Schema-validated completion, retried; returns (model, text, seconds, resp).

    Retries vary the prompt (suffix) so they skip any Redis entry that cached a
    failed empty/truncated answer for the original prompt.
    """
    last_err = None
    for attempt in range(1, MAX_TRIES + 1):
        payload = content + (RETRY_SUFFIX if attempt > 1 else "")
        try:
            resp, seconds = chat(
                payload,
                max_tokens=max_tokens,
                response_format=schema,
                reasoning=reasoning,
            )
            text = completion_text(resp)
            if not text:
                last_err = "empty completion content"
            else:
                parsed = schema.model_validate_json(text)
                return parsed, text, seconds, resp
        except Exception as exc:  # noqa: BLE001 - retry budget on any failure
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5 * attempt)
    return None, "", 0.0, last_err


def film_entry(studio, title, year, year_ok, details):
    return {
        "studio": studio,
        "title": title,
        "year": year if year_ok else None,
        "year_valid": year_ok,
        **details,
    }


def write_csv(rows, path):
    """QUOTE_ALL CSV with the exact header order; atomic via tmp+replace."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
        writer.writerow(["studio name", "film name", "year"])
        for row in rows:
            year = row["year"] if row["year"] is not None else ""
            writer.writerow([row["studio"], row["title"], year])
    os.replace(tmp, path)


def write_log(meta, entries, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "films": entries}, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def build_meta(target_studios, args, entries, t_start):
    ok = sum(1 for e in entries if e.get("ok"))
    return {
        "name": "cinematic-01",
        "generator": "smoketests/cinematic-01/generate.py",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model_alias": MODEL,
        "base_url": args.base_url,
        "studios": len(target_studios),
        "max_films": args.max_films,
        "skip_year": args.skip_year,
        "reasoning": "enabled" if args.reasoning else "disabled",
        "year_guard": [YEAR_MIN, YEAR_MAX],
        "entries": len(entries),
        "ok": ok,
        "failed": len(entries) - ok,
        "elapsed_s": round(time.perf_counter() - t_start, 1),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate the cinematic-01 micro dataset."
    )
    parser.add_argument(
        "--studios",
        type=int,
        default=len(STUDIOS),
        help="studios to cover (default all)",
    )
    parser.add_argument(
        "--max-films", type=int, default=10, help="film titles per studio (default 10)"
    )
    parser.add_argument(
        "--skip-year",
        action="store_true",
        help="don't ask for release years (fast smoke run)",
    )
    parser.add_argument("--max-tokens", type=int, default=1536)
    parser.add_argument(
        "--reasoning", action="store_true", help="let the model reason first (slower)"
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="LiteLLM gateway URL"
    )
    parser.add_argument("--output", default=DEFAULT_CSV, help="dataset CSV path")
    parser.add_argument("--log", default=DEFAULT_LOG, help="per-call run log JSON path")
    parser.add_argument("--skip-health", action="store_true", help="skip health probes")
    args = parser.parse_args()

    reasoning = {"enabled": True} if args.reasoning else {"enabled": False}
    target_studios = STUDIOS[: args.studios]

    if not args.skip_health:
        if not health(args.base_url + "/health/readiness"):
            sys.exit(f"gateway not ready at {args.base_url}/health/readiness")
        engine = (
            args.base_url.replace(":4000", ":8080")
            if ":4000" in args.base_url
            else "http://localhost:8080"
        )
        if not health(engine + "/health"):
            sys.exit(f"llama.cpp engine not ready at {engine}/health")
    print(
        f"gateway {args.base_url} healthy; generating {len(target_studios)}x"
        f"{args.max_films} films (reasoning {'on' if args.reasoning else 'off'})"
    )

    seen = set()
    entries = []
    t_start = time.perf_counter()
    for studio in target_studios:
        t_studio = time.perf_counter()
        parsed, _, fl_seconds, fresp = ask_json(
            film_list_prompt(studio, args.max_films),
            FilmList,
            args.max_tokens,
            reasoning,
        )
        fl_ok = parsed is not None
        fl_details = {
            "films_seconds": round(fl_seconds, 3),
            "films_cache_regime": cache_regime(fresp) if fl_ok else None,
            "films_timings": timings(fresp) if fl_ok else {},
            "films_usage": usage_fields(fresp) if fl_ok else {},
        }
        if not fl_ok:
            entries.append(
                {
                    "studio": studio,
                    "title": None,
                    "year": None,
                    "year_valid": False,
                    "ok": False,
                    "error": fresp,
                    **fl_details,
                }
            )
            print(f"  {studio}: film list FAIL ({fresp})", flush=True)
            continue
        for title in parsed.films[: args.max_films]:
            norm = normalize_title(title)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            if args.skip_year:
                entries.append(
                    film_entry(studio, title, None, False, {"ok": True, **fl_details})
                )
                continue
            ya, _, y_seconds, yresp = ask_json(
                year_prompt(title), YearAnswer, args.max_tokens, reasoning
            )
            year_ok = bool(ya) and YEAR_MIN <= ya.year <= YEAR_MAX
            details = {
                "ok": bool(ya),
                "error": yresp if not ya else None,
                "year_seconds": round(y_seconds, 3),
                "year_cache_regime": cache_regime(yresp) if ya else None,
                "year_timings": timings(yresp) if ya else {},
                "year_usage": usage_fields(yresp) if ya else {},
                **fl_details,
            }
            entries.append(
                film_entry(studio, title, ya.year if ya else None, year_ok, details)
            )
            state = "ok " if year_ok else "FAIL"
            year = ya.year if ya else "?"
            print(
                f"  {studio:<28} {title:<40} {year!s:<6} {state} "
                f"{round(y_seconds, 2):>6.2f}s",
                flush=True,
            )
        print(
            f"studio {studio} done in {time.perf_counter() - t_studio:.1f}s",
            flush=True,
        )
        meta = build_meta(target_studios, args, entries, t_start)
        write_log(meta, entries, args.log)
        print(
            f"  checkpoint: {meta['ok']} ok / {len(entries)} entries -> {args.log}",
            flush=True,
        )

    rows = [e for e in entries if e.get("ok") and e.get("year_valid") and e["title"]]
    write_csv(rows, args.output)
    meta = build_meta(target_studios, args, entries, t_start)
    write_log(meta, entries, args.log)
    print(
        f"\nwrote {args.output}: {len(rows)} rows, {meta['ok']} ok films, "
        f"{len(entries) - meta['ok']} failed, {meta['elapsed_s']}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
