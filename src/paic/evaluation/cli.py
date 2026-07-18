"""Public offline evaluation CLI."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from paic.evaluation.adversarial import check_adversarial_text
from paic.evaluation.artifact import EvaluationArtifactError, export_evaluation, replay_evaluation
from paic.evaluation.benchmark import BenchmarkError, load_benchmark, load_predictions
from paic.evaluation.comparison import compare_runs
from paic.evaluation.models import EvaluationConfig, EvaluationRun
from paic.evaluation.scoring import aggregate_results, score_case


def register_evaluation_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("evaluation", help="Run deterministic hidden benchmarks.")
    commands = parser.add_subparsers(dest="evaluation_command", required=True)
    validate = commands.add_parser(
        "benchmark-validate", help="Validate visible and hidden benchmark separation."
    )
    validate.add_argument("--visible-dir", type=Path, required=True)
    validate.add_argument("--answers-dir", type=Path, required=True)
    run = commands.add_parser("run", help="Score scripted predictions without a model call.")
    run.add_argument("--visible-dir", type=Path, required=True)
    run.add_argument("--answers-dir", type=Path, required=True)
    run.add_argument("--predictions", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--overwrite", action="store_true")
    artifact = commands.add_parser("validate", help="Validate a closed-world evaluation run.")
    artifact.add_argument("--run-dir", type=Path, required=True)
    replay = commands.add_parser("replay", help="Replay a closed-world evaluation run.")
    replay.add_argument("--run-dir", type=Path, required=True)
    summary = commands.add_parser("summary", help="Print aggregate evaluation metrics.")
    summary.add_argument("--run-dir", type=Path, required=True)
    compare = commands.add_parser("compare", help="Compare two deterministic evaluation runs.")
    compare.add_argument("--left-dir", type=Path, required=True)
    compare.add_argument("--right-dir", type=Path, required=True)
    adversarial = commands.add_parser(
        "adversarial", help="Check untrusted text against safety boundaries."
    )
    adversarial.add_argument("--case-id", required=True)
    adversarial.add_argument("--input", required=True)


def dispatch_evaluation(args: Namespace) -> int:
    command = args.evaluation_command
    try:
        if command == "benchmark-validate":
            visible, answers, visible_hash, answer_hash = load_benchmark(
                args.visible_dir, args.answers_dir
            )
            print(
                json.dumps(
                    {
                        "valid": True,
                        "case_count": len(visible),
                        "visible_sha256": visible_hash,
                        "answer_key_sha256": answer_hash,
                        "hidden_answer_keys_loaded": len(answers),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if command == "run":
            visible, answers, visible_hash, answer_hash = load_benchmark(
                args.visible_dir, args.answers_dir
            )
            predictions = load_predictions(args.predictions)
            if [item.case_id for item in predictions] != [item.case_id for item in visible]:
                raise BenchmarkError("prediction IDs must match visible case IDs and order")
            config = EvaluationConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
            results = [
                score_case(case, answer, prediction)
                for case, answer, prediction in zip(visible, answers, predictions, strict=True)
            ]
            aggregate = aggregate_results(results, answers, predictions)
            run = EvaluationRun(
                config=config,
                benchmark_manifest_sha256=visible_hash,
                answer_key_manifest_sha256=answer_hash,
                results=results,
                aggregate=aggregate,
            )
            export_evaluation(run, args.output_dir, overwrite=args.overwrite)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "run_id": config.run_id,
                        "metrics": aggregate.model_dump(mode="json"),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if command == "compare":
            print(
                json.dumps(
                    compare_runs(str(args.left_dir), str(args.right_dir)).model_dump(mode="json"),
                    sort_keys=True,
                )
            )
            return 0
        if command == "adversarial":
            result = check_adversarial_text(args.case_id, args.input)
            print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
            return 0 if result.blocked else 1
        run = replay_evaluation(args.run_dir)
        print(json.dumps(run.aggregate.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0
    except (BenchmarkError, EvaluationArtifactError, ValueError, OSError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 1
