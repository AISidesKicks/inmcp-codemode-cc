#!/usr/bin/env python3
"""Shared wiring for the cinematic-01 prompt-optimizer sweep and films legs.

Committed smoketests module (formalized from the scratch playground, which
stays the experimentation area). Sibling imports (`import llm`, `import test`)
resolve inside smoketests/cinematic-01/ — `test` is this lab's smoke test
module, not the stdlib `test` package — and provide the plumbing every
optimizer variant shares:

- task_text(prompt, ...) -> str    tested 2.6B direct on :8081
                                   (`local-thinking`), every call run_name-
                                   labelled `<run_id> <opt> eval` so Phoenix
                                   spans stay attributable; per-call token
                                   budget (TASK_MAX_TOKENS sweep legs,
                                   FILMS_MAX_TOKENS corpus legs)
- judge_chat(prompt) -> str        granite judge direct on :8080
                                   (`local-judge`); when a sweep sets a tag,
                                   the client-side turn span lands as
                                   `<run_id> <opt> judge` in Phoenix
- eval_text(row, text)             normalized exact match on the StudioList
                                   schema via test.parse_model (think-block
                                   tolerant); feedback = expected-vs-got
- score_rows / score_val           mean score + per-row results over rendered
                                   rows; score_val is the micro 6/4 wrapper at
                                   the sweep budget, films legs pass their own
- LocalLLM(DeepEvalBaseLLM)        judge wrapper for deepeval reflection and
                                   mutation (schema calls parse JSON via
                                   json_repair)
- dspy_lms / dspy_dataset /        dspy plumbing: engine-routed dspy.LM pair
  dspy_metric                      (calls OTEL-tagged via extra_body.metadata,
                                   cache=False so dspy's memo never hides calls),
                                   6-row dspy.Example split, exact-match metric
- health_all()                     both llama servers, early exit

Budgets: TASK_MAX_TOKENS=1536 (micro sweep), FILMS_MAX_TOKENS=8192 (films
corpus legs, INTENT §3 "reasoning 8192 budget"), JUDGE_MAX_TOKENS=4096.

No lab-config writes; live HTTP probes only.
"""

import asyncio
import random
import sys
import time

import llm
import test
from openai import OpenAI

JUDGE_BASE = llm.ENGINES["local-judge"]
JUDGE_MODEL = "local-judge"
TESTED_MODEL = "local-thinking"
TASK_MAX_TOKENS = 1536
FILMS_MAX_TOKENS = 8192  # INTENT §3: films corpus legs at the 8192 reasoning budget
JUDGE_MAX_TOKENS = 4096
SEED = 0

SEED_PROMPT = (
    'Which studio produced the movie "{film}"? Reply with only a '
    'JSON object shaped like {"studios": ["Studio Name"]}.'
)

JUDGE_CLIENT = OpenAI(base_url=JUDGE_BASE, api_key="unused")

STATS = {"task_calls": 0, "judge_calls": 0, "task_seconds": 0.0, "judge_seconds": 0.0}
JUDGE_STATE = {"tag": None}


def set_judge_tag(tag):
    """Set (or clear with None) the judge-call span tag (run_name)."""
    JUDGE_STATE["tag"] = tag


def stats_reset():
    for key in STATS:
        STATS[key] = 0


def stats_snapshot():
    return dict(STATS)


def fill_template(template, row):
    """Substitute {film} without str.format (optimizer prompts contain JSON braces)."""
    return template.replace("{film}", row["film"])


def ensure_placeholder(template):
    """Repair optimizer outputs that dropped the {film} placeholder."""
    if "{film}" in template:
        return template
    return template + "\n\nMovie: {film}"


def split_dataset(n_train=6, n_val=4, seed=SEED):
    """6 train / 4 val rows with a year, round-robin across studios per test.py."""
    rows = test.load_rows(test.DEFAULT_CSV)
    year_rows = [r for r in rows if r["year"] is not None]
    rng = random.Random(seed)
    rng.shuffle(year_rows)
    train = test.sample_rows(list(year_rows), n_train)
    val_pool = [r for r in year_rows if r not in train]
    val = test.sample_rows(val_pool, n_val)
    return train, val


def eval_text(row, text):
    """(score, feedback) for one tested-model answer against the studio truth."""
    parsed = test.parse_model(text, llm.StudioList)
    if parsed is None or not parsed.studios:
        return 0.0, f"unparseable output; expected studio {row['studio']!r}"
    guess = parsed.studios[0]
    ok = test.normalize(guess) == test.normalize(row["studio"])
    feedback = "correct" if ok else f"expected {row['studio']!r}, got {guess!r}"
    return (1.0 if ok else 0.0), feedback


def task_text(prompt, run_name=None, max_tokens=TASK_MAX_TOKENS):
    """Tested-model call on the thinking engine; returns clean primary text."""
    STATS["task_calls"] += 1
    kwargs = {"max_tokens": max_tokens, "reasoning": {"enabled": True}}
    if run_name:
        kwargs["run_name"] = run_name
    resp, secs = llm.chat(prompt, model=TESTED_MODEL, **kwargs)
    STATS["task_seconds"] += secs
    if resp is None:
        return ""
    return llm.completion_text(resp)


def _judge_plain(prompt):
    STATS["judge_calls"] += 1
    t0 = time.perf_counter()
    kwargs = {}
    if JUDGE_STATE["tag"]:
        kwargs["run_name"] = JUDGE_STATE["tag"]
    resp, _ = llm.chat(prompt, model=JUDGE_MODEL, max_tokens=JUDGE_MAX_TOKENS, **kwargs)
    STATS["judge_seconds"] += time.perf_counter() - t0
    if resp is None:
        raise RuntimeError(f"judge chat failed: {prompt[:80]!r}")
    text = llm.completion_text(resp)
    if "</think>" in text:
        text = text.split("</think>", 1)[-1].strip()
    return text.strip()


def judge_chat(prompt):
    """Judge reflection text; engine `local-judge`, OTEL-tagged when a tag is set."""
    return _judge_plain(prompt)


def _loads_loose(text):
    """First JSON object in `text` via json_repair; None if nothing parses."""
    import json

    try:
        start = text.index("{")
        end = text.rindex("}")
        candidate = text[start : end + 1]
        return json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        pass
    try:
        from json_repair import repair_json

        repaired = repair_json(text, return_objects=True)
        return repaired if isinstance(repaired, dict) else None
    except Exception:  # noqa: BLE001 - loose parsing is best-effort
        return None


def _coerce_str_lists(data):
    """Join list-of-strings fields; the chatty judge answers list-form where
    deepeval diagnosis/rewrite schemas want a single string."""
    if isinstance(data, dict):
        return {
            key: (
                "\n".join(value)
                if isinstance(value, list) and all(isinstance(i, str) for i in value)
                else value
            )
            for key, value in data.items()
        }
    return data


def _judge_generate(prompt, schema):
    text = _judge_plain(prompt)
    if schema is None:
        return text
    data = _loads_loose(text)
    if data is None:
        raise ValueError(f"judge JSON unparseable: {text[:200]!r}")
    return schema.model_validate(_coerce_str_lists(data))


async def _judge_agenerate(prompt, schema):
    return await asyncio.to_thread(_judge_generate, prompt, schema)


try:
    from deepeval.models.base_model import DeepEvalBaseLLM
except ImportError:
    DeepEvalBaseLLM = object


class LocalLLM(DeepEvalBaseLLM):
    """Judge as a real DeepEvalBaseLLM subclass (deepeval isinstance-checks it)."""

    def __init__(self):
        super().__init__(model=None)

    def load_model(self):
        return JUDGE_CLIENT

    def get_model_name(self):
        return "granite-4.0-h-tiny-Q4_K_XL (judge)"

    def generate(self, prompt, schema=None, **kv):
        return _judge_generate(prompt, schema)

    async def a_generate(self, prompt, schema=None, **kv):
        return await _judge_agenerate(prompt, schema)


def gepa_eval_result(score, feedback):
    from gepa.adapters.default_adapter.default_adapter import EvaluationResult

    return EvaluationResult(score=score, feedback=feedback)


def gepa_evaluator(data, response):
    """gepa Evaluator protocol: (dict inst, response str) -> EvaluationResult.

    DefaultDataInst carries the truth in `answer`; our rows use `studio`.
    """
    score, feedback = eval_text({"studio": data.get("answer", "")}, response)
    return gepa_eval_result(score, feedback)


def health_all():
    """Gateway + both llama servers; exits the process on any failure."""
    checks = {
        "judge-8080": llm.health(llm.JUDGE_URL + "/health"),
        "tested-8081": llm.health(llm.THINKING_URL + "/health"),
    }
    for name, ok in checks.items():
        print(f"health {name}: {'ok' if ok else 'FAIL'}")
    if not all(checks.values()):
        sys.exit("lab stack not fully healthy; aborting sweep")


def echo_score_rows(base_run_name, scored):
    """Best-effort Phoenix status stamping: one tiny echo call per scored row
    (`<base_run_name> <film> PASS|FAIL`), stamped as the session's echo turn
    output. Never raises."""
    for row in scored:
        status = "PASS" if row["score"] == 1.0 else "FAIL"
        llm.echo_verdict(f"{base_run_name} {row['film']}", status)


def score_rows(rows, render, run_name, max_tokens, echo=False):
    """Mean score + per-row results; render(row) builds the full prompt and
    max_tokens sets the per-call generation budget (sweep vs films legs).
    echo=True additionally stamps per-row PASS/FAIL into Phoenix."""
    scored = []
    for row in rows:
        text = task_text(render(row), run_name=run_name, max_tokens=max_tokens)
        score, feedback = eval_text(row, text)
        scored.append({"film": row["film"], "score": score, "feedback": feedback})
    mean = sum(r["score"] for r in scored) / len(scored) if scored else 0.0
    if echo:
        echo_score_rows(run_name, scored)
    return mean, scored


def score_val(val, render, run_name, echo=False):
    """Micro 6/4 split leg at the sweep task budget (score_rows wrapper)."""
    return score_rows(val, render, run_name, TASK_MAX_TOKENS, echo=echo)


def deepeval_goldens(rows):
    from deepeval.dataset import Golden

    return [Golden(input=r["film"], expected_output=r["studio"]) for r in rows]


def deepeval_model_callback(run_id, opt):
    """(Prompt, Golden) -> str task-model callback for deepeval optimizers."""

    def callback(prompt, golden):
        template = getattr(prompt, "text_template", None) or str(prompt)
        return task_text(
            template.replace("{film}", golden.input),
            run_name=f"{run_id} {opt} eval",
        )

    return callback


def studio_metric():
    """Programmatic exact-match BaseMetric factory; no LLM cost inside the metric."""
    from deepeval.metrics.base_metric import BaseMetric

    class _StudioExactMatch(BaseMetric):
        threshold = 0.5

        def measure(self, test_case, *args, **kwargs):
            row = {"studio": test_case.expected_output}
            score, feedback = eval_text(row, test_case.actual_output)
            self.score = score
            self.reason = feedback
            self.success = score >= self.threshold
            return score

        def is_successful(self, *args, **kwargs):
            return bool(getattr(self, "success", False))

        async def a_measure(self, test_case, *args, **kwargs):
            return self.measure(test_case, *args, **kwargs)

    return _StudioExactMatch()


def dspy_call_counter():
    """dspy BaseCallback counting LM calls into STATS by engine alias.

    dspy.LM runs its own client-side loop, so its calls never touch
    task_text/_judge_plain; this keeps the sweep table's call columns honest
    for dspy rows (0s would mean the callback wiring broke, not zero calls).
    """
    from dspy.utils.callback import BaseCallback

    class _DspyCounter(BaseCallback):
        def on_lm_start(self, call_id, instance, inputs):
            model = str(getattr(instance, "model", ""))
            if JUDGE_MODEL in model:
                STATS["judge_calls"] += 1
            else:
                STATS["task_calls"] += 1

    return _DspyCounter()


def dspy_lms(run_id, opt):
    """(task_lm, judge_lm) dspy.LM pair routed straight to the llama.cpp
    engines (task -> :8081, judge -> :8080).

    dspy drives its own litellm client, so dspy legs get no client-side
    session spans — the STATS call counter keeps the sweep table honest for
    dspy rows. cache=False keeps dspy's memo from hiding calls; no
    temperature -> engine sampling flags govern.
    """
    import dspy

    def lm(model, max_tokens):
        return dspy.LM(
            f"openai/{model}",
            api_base=llm.ENGINES[model],
            api_key="unused",
            max_tokens=max_tokens,
            cache=False,
            num_retries=2,
            callbacks=[dspy_call_counter()],
        )

    return lm(TESTED_MODEL, TASK_MAX_TOKENS), lm(JUDGE_MODEL, JUDGE_MAX_TOKENS)


def dspy_dataset(rows):
    """dspy.Example list over the sweep's train/val rows (film is the input)."""
    import dspy

    return [
        dspy.Example(film=r["film"], studio=r["studio"]).with_inputs("film")
        for r in rows
    ]


def dspy_metric():
    """Exact-match dspy metric reusing eval_text's normalization guards."""

    def metric(example, pred, trace=None):
        row = {"studio": example.studio}
        studios = getattr(pred, "studios", None)
        if isinstance(studios, str):
            score, _ = eval_text(row, studios)
            return score
        if isinstance(studios, (list, tuple)) and studios:
            ok = test.normalize(str(studios[0])) == test.normalize(row["studio"])
            return 1.0 if ok else 0.0
        return 0.0

    return metric


if __name__ == "__main__":
    health_all()
    train, val = split_dataset()
    print(f"split: train={len(train)} val={len(val)}")
    print("train films:", [r["film"] for r in train])
    print("val films:", [r["film"] for r in val])
    text = task_text(
        fill_template(SEED_PROMPT, train[0]), run_name="promptopt-common-check"
    )
    print("sample task text head:", repr(text[:160]))
    print("sample eval:", eval_text(train[0], text))
    set_judge_tag("promptopt-selfcheck judge")
    judge_head = judge_chat("Say one short sentence praising small LLMs.")
    print("judge head:", repr(judge_head[:160]))
    print("stats:", stats_snapshot())
