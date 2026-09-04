#!/usr/bin/env python3
"""Prompt-optimizer sweep + films corpus legs for cinematic-01.

Formalized from the scratch playground (scratch/promptopt_sweep.py stays the
experimentation area). Every optimizer reflects/mutates through the local
granite-4.0-h-tiny judge (non-thinking, gateway `local-judge`, judge spans
labelled `<run_id> <opt> judge`) and evaluates through the LFM2.5-2.6B
tested model via the LiteLLM gateway (`local-thinking`, spans labelled
`<run_id> <opt> eval`):

  gepa            standalone gepa.optimize (reflection_lm = judge,
                  DefaultAdapter via task_lm + evaluator)
  depeval-gepa    deepeval PromptOptimizer x GEPA (reflection+mutation = judge)
  depeval-miprov2 deepeval PromptOptimizer x MIPROV2
  depeval-copro   deepeval PromptOptimizer x COPRO
  depeval-simba   deepeval PromptOptimizer x SIMBA (introspective mini-batch)
  dspy-bootstrap  dspy BootstrapFewShot (judge-as-teacher demo bootstrapping,
                  no LLM proposal calls — cheapest)
  dspy-simba      dspy SIMBA (introspective mini-batch ascent, judge proposals)
  dspy-miprov2    dspy MIPROv2 (bayesian instruction/demos proposals)
  adalflow-tgd    adalflow TGDOptimizer text-grad (EvalFnToTextLoss +
                  BackwardEngine over the gateway; AdalComponent/Trainer path
                  deliberately skipped)
  refiner         promptrefiner BaseStrategy.refine rewrite-only baseline
                  (explicit opt-in only; excluded from `--optimizer all`)

Two stages (`--stage`, default all):

  sweep   the proven micro 6/4 split (fixed seed) per optimizer; each result
          records the `render` kind its best prompt needs for the films leg
          (`system` for gepa/dspy instructions that expect the film as the
          user message, `filled` for {film} placeholder templates) plus the
          full best_prompt
  films   each best prompt scored over the corpus slice (--csv, --films N,
          0 = all rows) at FILMS_MAX_TOKENS (8192, INTENT §3), one Phoenix
          span per film (`<run_id> <opt> films`), strictly sequential
          (parallel-1 lab, no thread pools anywhere); per-optimizer films
          jsons (meta + per-film score/feedback) land in
          datasets/cinematic-01/runs/<run_id>-<opt>-films.json

Resume-safe: `--optimizer all` skips optimizers already recorded in the out
json (sweep) / already carrying a films entry (films legs); an explicit
single --optimizer always re-runs. Per-optimizer failures are recorded as
findings and never abort the run. Prints a comparison table and writes the
out json (default datasets/cinematic-01/runs/<run_id>-optimize.json).
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_common as common

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNS_DIR = os.path.join(REPO, "datasets", "cinematic-01", "runs")
DEFAULT_CSV = os.path.join(REPO, "datasets", "cinematic-01", "dataset.csv")

GEPA_SEED = (
    "You will receive a movie title as the user message. Which studio "
    'produced the movie? Reply with only a JSON object shaped like '
    '{"studios": ["Studio Name"]}.'
)

DSPY_SEED = (
    "You will receive a movie title as the film field. Which studio produced "
    'the movie? Reply with only a JSON object shaped like '
    '{"studios": ["Studio Name"]}.'
)

SWEEP_ALL = [
    "gepa",
    "depeval-gepa",
    "depeval-miprov2",
    "depeval-copro",
    "dspy-bootstrap",
    "depeval-simba",
    "dspy-simba",
    "dspy-miprov2",
    "adalflow-tgd",
]

# Best prompts of these optimizers are system-style instructions (the film
# arrives as the user message); every other runner yields a {film} template.
SYSTEM_RENDER = {"gepa", "dspy-bootstrap", "dspy-simba", "dspy-miprov2"}


def render_kind(name):
    """Render kind the films leg needs for this optimizer's best prompt."""
    return "system" if name in SYSTEM_RENDER else "filled"


def render_filled(template):
    """deepeval/refiner-style templates: {film} placeholder inside the text."""
    return lambda row: common.fill_template(template, row)


def render_system(template):
    """gepa DefaultAdapter-style templates: system prompt + film user message."""
    return lambda row: f"{template}\n\nMovie: {row['film']}"


def insts(rows):
    return [
        {"input": r["film"], "additional_context": {}, "answer": r["studio"]}
        for r in rows
    ]


def record(out, name, t0, before, after, best_prompt, error=None):
    stats = common.stats_snapshot()
    out[name] = {
        "status": "ok" if error is None else "failed",
        "wall_seconds": round(time.perf_counter() - t0, 1),
        "val_score_before": before,
        "val_score_after": after,
        "task_calls": stats["task_calls"],
        "judge_calls": stats["judge_calls"],
        "render": render_kind(name),
        "best_prompt": best_prompt if error is None else None,
        "best_prompt_head": (best_prompt or "")[:220] if error is None else None,
        "error": error,
    }
    tag = "ok" if error is None else f"FAILED: {error}"
    print(f"== {name}: {tag} ({out[name]['wall_seconds']}s)", flush=True)


def run_gepa(val, run_id, out):
    import gepa

    t0 = time.perf_counter()
    name = "gepa"
    common.stats_reset()
    common.set_judge_tag(f"{run_id} gepa judge")
    try:
        before, _ = common.score_val(
            val, render_system(GEPA_SEED), f"{run_id} gepa before"
        )

        def task_lm(messages):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            return common.task_text(
                f"{system}\n\n{user}", run_name=f"{run_id} gepa eval"
            )

        result = gepa.optimize(
            seed_candidate={"studio_prompt": GEPA_SEED},
            trainset=insts(common.split_dataset()[0]),
            valset=insts(val),
            task_lm=task_lm,
            evaluator=common.gepa_evaluator,
            reflection_lm=common.judge_chat,
            max_metric_calls=16,
            seed=0,
            display_progress_bar=False,
        )
        best = result.best_candidate["studio_prompt"]
        after, _ = common.score_val(
            val, render_system(best), f"{run_id} gepa after"
        )
        record(out, name, t0, before, after, best)
    except Exception as exc:  # noqa: BLE001 - findings, not aborts
        record(out, name, t0, None, None, None, f"{type(exc).__name__}: {exc}")
        return
    return best


def run_deepeval(val, run_id, out, algo_name, algorithm):
    from deepeval.evaluate.configs import AsyncConfig, DisplayConfig
    from deepeval.optimizer import PromptOptimizer
    from deepeval.prompt.prompt import Prompt

    t0 = time.perf_counter()
    name = f"depeval-{algo_name}"
    common.stats_reset()
    common.set_judge_tag(f"{run_id} {name} judge")
    try:
        before, _ = common.score_val(
            val, render_filled(common.SEED_PROMPT), f"{run_id} {name} before"
        )
        judge_llm = common.LocalLLM()
        optimizer = PromptOptimizer(
            model_callback=common.deepeval_model_callback(run_id, name),
            metrics=[common.studio_metric()],
            optimizer_model=judge_llm,
            algorithm=algorithm,
            async_config=AsyncConfig(run_async=False),
            display_config=DisplayConfig(show_indicator=False),
        )
        best_prompt = optimizer.optimize(
            prompt=Prompt(text_template=common.SEED_PROMPT),
            goldens=common.deepeval_goldens(common.split_dataset()[0]),
        )
        template = common.ensure_placeholder(best_prompt.text_template)
        after, _ = common.score_val(
            val, render_filled(template), f"{run_id} {name} after"
        )
        record(out, name, t0, before, after, template)
    except Exception as exc:  # noqa: BLE001 - findings, not aborts
        record(out, name, t0, None, None, None, f"{type(exc).__name__}: {exc}")
        return
    return template


def run_dspy(val, run_id, out, name, make_opt):
    """Shared dspy runner: compile the student under the tagged task LM, then
    score the winning instruction via score_val (demos stay inside the dspy
    program and never reach the prompt-only val eval — a finding itself)."""
    import dspy

    class _StudioSignature(dspy.Signature):
        """Instruction is injected as DSPY_SEED so before/after stay comparable."""

        film: str = dspy.InputField(desc="movie title")
        studios: list[str] = dspy.OutputField(desc="producing studios, best first")

    StudioSignature = _StudioSignature.with_instructions(DSPY_SEED)

    class StudioProg(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.Predict(StudioSignature)

        def forward(self, film):
            return self.predict(film=film)

    t0 = time.perf_counter()
    common.stats_reset()
    common.set_judge_tag(f"{run_id} {name} judge")
    try:
        before, _ = common.score_val(
            val, render_system(DSPY_SEED), f"{run_id} {name} before"
        )
        task_lm, judge_lm = common.dspy_lms(run_id, name)
        optimizer, compile_kwargs, teacher = make_opt(task_lm, judge_lm, StudioProg)
        with dspy.context(lm=task_lm):
            compiled = optimizer.compile(
                StudioProg(),
                trainset=common.dspy_dataset(common.split_dataset()[0]),
                **({"teacher": teacher} if teacher is not None else {}),
                **compile_kwargs,
            )
        instruction = compiled.predict.signature.instructions
        after, _ = common.score_val(
            val, render_system(instruction), f"{run_id} {name} after"
        )
        record(out, name, t0, before, after, instruction)
    except Exception as exc:  # noqa: BLE001 - findings, not aborts
        record(out, name, t0, None, None, None, f"{type(exc).__name__}: {exc}")
        return
    return instruction


def run_dspy_bootstrap(val, run_id, out):
    """BootstrapFewShot: judge-as-teacher demo bootstrapping, no proposal LLM.

    dspy deepcopies the teacher and drops per-predictor lms, so the judge LM
    rides via teacher_settings (settings.lm fallback) — demo generation then
    produces `<run_id> dspy-bootstrap judge` spans while the student stays on
    the task LM.
    """

    def make_opt(task_lm, judge_lm, studio_prog):
        from dspy import BootstrapFewShot

        return (
            BootstrapFewShot(
                metric=common.dspy_metric(),
                max_bootstrapped_demos=1,
                max_labeled_demos=1,
                teacher_settings={"lm": judge_lm},
            ),
            {},
            None,
        )

    return run_dspy(val, run_id, out, "dspy-bootstrap", make_opt)


def run_dspy_simba(val, run_id, out):
    """dspy SIMBA: introspective mini-batch ascent; bsize must fit the 6-row
    trainset (compile asserts len(trainset) >= bsize). SIMBA's resampled
    rollout models are library-copied at temperature 1.0 — engine flags govern
    everything else."""

    def make_opt(task_lm, judge_lm, studio_prog):
        from dspy import SIMBA

        return (
            SIMBA(
                metric=common.dspy_metric(),
                bsize=3,
                num_candidates=2,
                max_steps=2,
                max_demos=1,
                num_threads=1,
                prompt_model=judge_lm,
            ),
            {"seed": 0},
            None,
        )

    return run_dspy(val, run_id, out, "dspy-simba", make_opt)


def run_dspy_miprov2(val, run_id, out):
    """dspy MIPROv2: bayesian instruction/demos proposals. auto=None requires
    both init num_candidates and compile num_trials; minibatch=False keeps the
    6-row trainset whole."""

    def make_opt(task_lm, judge_lm, studio_prog):
        from dspy import MIPROv2

        return (
            MIPROv2(
                metric=common.dspy_metric(),
                prompt_model=judge_lm,
                task_model=task_lm,
                max_bootstrapped_demos=1,
                max_labeled_demos=1,
                auto=None,
                num_candidates=2,
                num_threads=1,
            ),
            {"num_trials": 2, "minibatch": False, "seed": 0, "provide_traceback": False},
            None,
        )

    return run_dspy(val, run_id, out, "dspy-miprov2", make_opt)


def run_adalflow(val, run_id, out):
    """adalflow TGDOptimizer lean text-grad loop (no AdalComponent/Trainer).

    The prompt Parameter is the {film} template; gradients come from
    EvalFnToTextLoss + BackwardEngine over the gateway (judge-role calls,
    tagged via extra_body.metadata riding model_kwargs through the Responses
    API). Each round: attach gradients from the 6 train rows, propose, score
    the proposal via score_val, keep the best (manual accept loop).
    """
    from adalflow.components.model_client.openai_client import OpenAIClient
    from adalflow.core.generator import BackwardEngine
    from adalflow.core.types import ModelType
    from adalflow.optim.parameter import Parameter, ParameterType
    from adalflow.optim.text_grad.text_loss_with_eval_fn import EvalFnToTextLoss
    from adalflow.optim.text_grad.tgd_optimizer import TGDOptimizer

    t0 = time.perf_counter()
    name = "adalflow-tgd"
    common.stats_reset()
    common.set_judge_tag(f"{run_id} {name} judge")
    try:
        # LazyImport (package-level) forbids subclassing; import the class
        # directly and count its judge-role calls with a thin wrapper.
        class _CountedOpenAIClient(OpenAIClient):
            def call(self, api_kwargs=None, model_type=ModelType.UNDEFINED):
                common.STATS["judge_calls"] += 1
                return super().call(
                    api_kwargs=api_kwargs or {}, model_type=model_type
                )

        before, _ = common.score_val(
            val, render_filled(common.SEED_PROMPT), f"{run_id} {name} before"
        )
        judge_kwargs = {
            "model": common.JUDGE_MODEL,
            "max_tokens": common.JUDGE_MAX_TOKENS,
            "extra_body": {
                "metadata": {"generation_name": f"{run_id} {name} judge"}
            },
        }
        client = _CountedOpenAIClient(
            api_key=common.llm.get_master_key(), base_url=common.JUDGE_BASE
        )

        def eval_triplet(task_prompt, y_pred, y_gt):
            score, _ = common.eval_text({"studio": y_gt}, y_pred)
            return score

        # BackwardEngine(**kwargs) only — the positional set_backward_engine
        # fallback inside EvalFnToTextLoss is broken in adalflow 1.1.3.
        backward_engine = BackwardEngine(
            model_client=client, model_kwargs=judge_kwargs
        )
        loss_fn = EvalFnToTextLoss(
            eval_fn=eval_triplet,
            eval_fn_desc=(
                "exact match of the predicted studio against the ground-truth "
                "studio; 1.0 when equal (case/punctuation-insensitive), else 0.0"
            ),
            backward_engine=backward_engine,
        )
        prompt_param = Parameter(
            data=common.SEED_PROMPT,
            requires_opt=True,
            role_desc=(
                "prompt template for a small LLM: the literal {film} is replaced "
                "by the movie title at call time; the answer must be a JSON "
                "object with a studios list"
            ),
            param_type=ParameterType.PROMPT,
        )
        optimizer = TGDOptimizer(
            params=[prompt_param],
            model_client=client,
            model_kwargs=judge_kwargs,
            constraints=[
                "Keep the {film} placeholder so the movie title is injected.",
                'Keep the JSON answer format {"studios": ["Studio Name"]}.',
            ],
        )
        optimizer.add_score_to_params(before)
        train, _ = common.split_dataset()
        best_prompt, best_score, after = prompt_param.data, before, before
        for step_idx in range(2):
            optimizer.zero_grad()
            for row in train:
                prompt_text = common.fill_template(prompt_param.data, row)
                prompt_param.set_eval_fn_input(prompt_text)
                answer = common.task_text(
                    prompt_text, run_name=f"{run_id} {name} eval"
                )
                y_pred = Parameter(
                    data=answer,
                    requires_opt=False,
                    role_desc="the tested model's studio answer",
                    eval_input=answer,
                )
                gt = Parameter(
                    data=row["studio"],
                    requires_opt=False,
                    role_desc="ground-truth studio",
                    eval_input=row["studio"],
                )
                loss = loss_fn(
                    {"task_prompt": prompt_param, "y_pred": y_pred, "y_gt": gt},
                    response_desc="studio recall answer",
                    id=row["film"],
                    gt=row["studio"],
                    input=row["film"],
                )
                loss.backward()
            optimizer.propose()
            proposal_score, _ = common.score_val(
                val,
                render_filled(prompt_param.data),
                f"{run_id} {name} proposal-{step_idx}",
            )
            optimizer.add_score_to_params(proposal_score)
            if proposal_score > best_score:
                best_score, best_prompt = proposal_score, prompt_param.data
                optimizer.step()
            else:
                optimizer.revert()
        record(out, name, t0, before, after, best_prompt)
    except Exception as exc:  # noqa: BLE001 - findings, not aborts
        record(out, name, t0, None, None, None, f"{type(exc).__name__}: {exc}")
        return
    return best_prompt


def run_refiner(val, run_id, out):
    from promptrefiner.strategies import BaseStrategy

    class _PlainRefiner(BaseStrategy):
        def get_system_prompt(self) -> str:
            return (
                "You are a prompt engineering expert. Rewrite the given prompt so a "
                "small local LLM answers it correctly. Keep any JSON output format "
                "specification and the {film} placeholder intact. Return only the "
                "rewritten prompt."
            )

    t0 = time.perf_counter()
    name = "refiner"
    common.stats_reset()
    common.set_judge_tag(f"{run_id} refiner judge")
    try:
        before, _ = common.score_val(
            val, render_filled(common.SEED_PROMPT), f"{run_id} refiner before"
        )
        strategy = _PlainRefiner(
            llm_client=lambda system, user: common.judge_chat(f"{system}\n\n{user}")
        )
        refined = strategy.refine(common.SEED_PROMPT)
        template = common.ensure_placeholder(refined)
        after, _ = common.score_val(
            val, render_filled(template), f"{run_id} refiner after"
        )
        record(out, name, t0, before, after, template)
    except Exception as exc:  # noqa: BLE001 - findings, not aborts
        record(out, name, t0, None, None, None, f"{type(exc).__name__}: {exc}")
        return
    return template


def build_algorithms():
    from deepeval.optimizer.algorithms import COPRO, GEPA, MIPROV2, SIMBA

    return {
        "gepa": lambda: GEPA(
            iterations=2,
            minibatch_size=4,
            pareto_size=2,
            patience=2,
            reflection_model=common.LocalLLM(),
            mutation_model=common.LocalLLM(),
            random_seed=0,
        ),
        "miprov2": lambda: MIPROV2(
            num_trials=2,
            num_candidates=2,
            minibatch_size=4,
            max_bootstrapped_demonstrations=1,
            max_labeled_demonstrations=1,
            num_demonstration_sets=1,
            random_state=0,
        ),
        "copro": lambda: COPRO(depth=1, breadth=2, minibatch_size=4, random_state=0),
        "simba": lambda: SIMBA(
            iterations=2,
            minibatch_size=6,
            num_candidates=2,
            num_samples=2,
            minibatch_full_eval_steps=1,
            random_state=0,
        ),
    }


def out_dump(out_path, out):
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)


def films_path(run_id, name):
    return os.path.join(RUNS_DIR, f"{run_id}-{name}-films.json")


def run_films_leg(corpus, name, entry, run_id):
    """One films leg: score this optimizer's best prompt over the corpus
    slice at FILMS_MAX_TOKENS, strictly sequential; writes the per-optimizer
    films json (meta + per-film score/feedback) and returns its summary."""
    template = entry["best_prompt"]
    kind = entry.get("render") or render_kind(name)
    t0 = time.perf_counter()
    common.stats_reset()
    try:
        render = render_system(template) if kind == "system" else render_filled(template)
        score, rows = common.score_rows(
            corpus, render, f"{run_id} {name} films", common.FILMS_MAX_TOKENS
        )
        stats = common.stats_snapshot()
        path = films_path(run_id, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "meta": {
                        "run_id": run_id,
                        "optimizer": name,
                        "render": kind,
                        "max_tokens": common.FILMS_MAX_TOKENS,
                        "run_name": f"{run_id} {name} films",
                        "tested_model": common.TESTED_MODEL,
                        "judge_model": common.JUDGE_MODEL,
                    },
                    "score": score,
                    "rows": rows,
                },
                fh,
                indent=2,
            )
        summary = {
            "score": score,
            "films": len(rows),
            "max_tokens": common.FILMS_MAX_TOKENS,
            "wall_seconds": round(time.perf_counter() - t0, 1),
            "task_calls": stats["task_calls"],
            "judge_calls": stats["judge_calls"],
            "file": os.path.relpath(path, REPO),
        }
        print(
            f"== films {name}: score {score:.3f} over {len(rows)} films "
            f"({summary['wall_seconds']}s, {stats['task_calls']} task calls)",
            flush=True,
        )
        return summary
    except Exception as exc:  # noqa: BLE001 - findings, not aborts
        print(f"== films {name}: FAILED: {type(exc).__name__}: {exc}", flush=True)
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def run_films(args, run_id, out, out_path):
    """Films stage: corpus legs for optimizers with a best_prompt."""
    if not out["results"]:
        sys.exit(f"no sweep results in {out_path}; run --stage sweep first")
    corpus = common.test.load_rows(args.csv)
    if args.films > 0:
        corpus = corpus[: args.films]
    if not corpus:
        sys.exit("empty films corpus; check --csv/--films")
    print(
        f"films corpus: {len(corpus)} films "
        f"(max_tokens={common.FILMS_MAX_TOKENS}, strictly sequential)",
        flush=True,
    )
    if args.optimizer == "all":
        targets = [
            name
            for name in SWEEP_ALL
            if (out["results"].get(name) or {}).get("best_prompt")
        ]
    else:
        targets = [args.optimizer]
    for name in targets:
        entry = out["results"].get(name)
        if entry is None or not entry.get("best_prompt"):
            print(
                f"== films {name}: no best_prompt (failed/missing sweep), skipping",
                flush=True,
            )
            continue
        if args.optimizer == "all" and entry.get("films"):
            print(f"== films {name}: already recorded, skipping (resume)", flush=True)
            continue
        entry["films"] = run_films_leg(corpus, name, entry, run_id)
        out_dump(out_path, out)


def run_sweep(args, run_id, out, out_path):
    """Sweep stage: the micro 6/4 legs, checkpointing the out json per optimizer."""
    _, val = common.split_dataset()
    selected = SWEEP_ALL if args.optimizer == "all" else [args.optimizer]
    algorithms = build_algorithms()
    for name in selected:
        if args.optimizer == "all" and name in out["results"]:
            print(f"== {name}: already recorded, skipping (resume)", flush=True)
            continue
        if name == "gepa":
            run_gepa(val, run_id, out["results"])
        elif name == "refiner":
            run_refiner(val, run_id, out["results"])
        elif name == "dspy-bootstrap":
            run_dspy_bootstrap(val, run_id, out["results"])
        elif name == "dspy-simba":
            run_dspy_simba(val, run_id, out["results"])
        elif name == "dspy-miprov2":
            run_dspy_miprov2(val, run_id, out["results"])
        elif name == "adalflow-tgd":
            run_adalflow(val, run_id, out["results"])
        else:
            run_deepeval(
                val,
                run_id,
                out["results"],
                name.replace("depeval-", ""),
                algorithms[name.replace("depeval-", "")](),
            )
        out_dump(out_path, out)


def print_table(out):
    cols = ("optimizer", "status", "before", "after", "films", "task", "judge", "sec")
    print()
    print(
        f"{cols[0]:<16} {cols[1]:<8} {cols[2]:>6} {cols[3]:>6} "
        f"{cols[4]:>6} {cols[5]:>5} {cols[6]:>5} {cols[7]:>7}"
    )
    print("-" * 68)
    for name, row in out["results"].items():
        films_score = (row.get("films") or {}).get("score")
        print(
            f"{name:<16} {row['status']:<8} "
            f"{row['val_score_before'] if row['val_score_before'] is not None else '—':>6} "
            f"{row['val_score_after'] if row['val_score_after'] is not None else '—':>6} "
            f"{round(films_score, 3) if isinstance(films_score, float) else '—':>6} "
            f"{row['task_calls']:>5} {row['judge_calls']:>5} {row['wall_seconds']:>7}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Prompt-optimizer sweep + films corpus legs (cinematic-01)"
    )
    parser.add_argument(
        "--stage",
        default="all",
        choices=["sweep", "films", "all"],
        help="sweep = micro 6/4 legs, films = corpus legs (default all)",
    )
    parser.add_argument(
        "--optimizer",
        default="all",
        choices=SWEEP_ALL + ["refiner", "all"],
        help="which optimizer to run (all = everything but the rewrite-only refiner)",
    )
    parser.add_argument(
        "--csv", default=DEFAULT_CSV, help="dataset CSV path (films corpus)"
    )
    parser.add_argument(
        "--films", type=int, default=0, help="films corpus slice, 0 = all rows"
    )
    parser.add_argument("--run-id", required=True, help="Phoenix run label prefix")
    parser.add_argument(
        "--out",
        default=None,
        help="output json (default datasets/cinematic-01/runs/<run-id>-optimize.json)",
    )
    args = parser.parse_args()
    run_id = args.run_id
    out_path = args.out or os.path.join(RUNS_DIR, f"{run_id}-optimize.json")

    common.health_all()
    os.makedirs(RUNS_DIR, exist_ok=True)
    train, val = common.split_dataset()
    print(f"run_id: {run_id}")
    print(f"split: train={len(train)} val={len(val)}", flush=True)

    out = None
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as fh:
                out = json.load(fh)
        except (OSError, json.JSONDecodeError):
            out = None
    if not isinstance(out, dict) or "meta" not in out or "results" not in out:
        out = {
            "meta": {
                "run_id": run_id,
                "tested_model": common.TESTED_MODEL,
                "judge_model": common.JUDGE_MODEL,
                "task_max_tokens": common.TASK_MAX_TOKENS,
                "films_max_tokens": common.FILMS_MAX_TOKENS,
                "seed_prompt": common.SEED_PROMPT,
                "gepa_seed": GEPA_SEED,
                "dspy_seed": DSPY_SEED,
                "train_films": [r["film"] for r in train],
                "val_films": [r["film"] for r in val],
            },
            "results": {},
        }

    if args.stage in ("sweep", "all"):
        run_sweep(args, run_id, out, out_path)
    if args.stage in ("films", "all"):
        run_films(args, run_id, out, out_path)

    print_table(out)
    out_dump(out_path, out)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
