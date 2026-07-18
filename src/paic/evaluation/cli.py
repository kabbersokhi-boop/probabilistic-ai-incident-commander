"""Public offline evaluation CLI."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from paic.evaluation.adversarial import (
    AdversarialCase,
    check_adversarial_text,
    evaluate_adversarial_suite,
)
from paic.evaluation.artifact import (
    EvaluationArtifactError,
    export_evaluation,
    load_evaluation,
    replay_evaluation,
)
from paic.evaluation.benchmark import (
    BenchmarkError,
    digest_models,
    digest_value,
    load_benchmark,
    load_predictions,
    provider_config_digest,
    resolve_ablation,
    resolve_prediction_ablation,
    tool_policy_digest,
)
from paic.evaluation.comparison import (
    ComparisonArtifactError,
    compare_runs,
    export_comparison,
    replay_comparison,
)
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
    replay = commands.add_parser("replay", help="Semantically replay an evaluation run.")
    replay.add_argument("--run-dir", type=Path, required=True)
    replay.add_argument("--visible-dir", type=Path)
    replay.add_argument("--answers-dir", type=Path)
    replay.add_argument("--predictions", type=Path)
    replay.add_argument("--config", type=Path)
    summary = commands.add_parser("summary", help="Print aggregate evaluation metrics.")
    summary.add_argument("--run-dir", type=Path, required=True)
    summary.add_argument("--visible-dir", type=Path, required=True)
    summary.add_argument("--answers-dir", type=Path, required=True)
    summary.add_argument("--predictions", type=Path, required=True)
    summary.add_argument("--config", type=Path, required=True)
    compare = commands.add_parser("compare", help="Compare two paired evaluation runs.")
    compare.add_argument("--left-dir", type=Path, required=True)
    compare.add_argument("--right-dir", type=Path, required=True)
    compare.add_argument("--output-dir", type=Path)
    compare.add_argument("--visible-dir", type=Path, required=True)
    compare.add_argument("--answers-dir", type=Path, required=True)
    compare.add_argument("--left-predictions", type=Path, required=True)
    compare.add_argument("--left-config", type=Path, required=True)
    compare.add_argument("--right-predictions", type=Path, required=True)
    compare.add_argument("--right-config", type=Path, required=True)
    compare_replay = commands.add_parser(
        "compare-replay", help="Replay a closed-world comparison artifact."
    )
    compare_replay.add_argument("--comparison-dir", type=Path, required=True)
    compare_replay.add_argument("--left-dir", type=Path, required=True)
    compare_replay.add_argument("--right-dir", type=Path, required=True)
    compare_replay.add_argument("--visible-dir", type=Path, required=True)
    compare_replay.add_argument("--answers-dir", type=Path, required=True)
    compare_replay.add_argument("--left-predictions", type=Path, required=True)
    compare_replay.add_argument("--left-config", type=Path, required=True)
    compare_replay.add_argument("--right-predictions", type=Path, required=True)
    compare_replay.add_argument("--right-config", type=Path, required=True)
    adversarial = commands.add_parser(
        "adversarial", help="Run a supplemental untrusted-text detector."
    )
    adversarial.add_argument("--case-id", required=True)
    adversarial.add_argument("--input", required=True)
    suite = commands.add_parser(
        "adversarial-suite", help="Run declared adversarial boundary cases."
    )
    suite.add_argument("--cases", type=Path, required=True)


def _build_run(args: Namespace) -> EvaluationRun:
    source_visible, answers, visible_hash, answer_hash = load_benchmark(
        args.visible_dir, args.answers_dir
    )
    predictions = load_predictions(args.predictions)
    source_ids = [item.case_id for item in source_visible]
    if [item.case_id for item in predictions] != source_ids:
        raise BenchmarkError("prediction IDs must match visible case IDs and order")
    config = EvaluationConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    effective_visible = resolve_ablation(source_visible, config.ablation)
    effective_predictions = resolve_prediction_ablation(
        predictions,
        abstention_enabled=config.ablation.abstention_enabled,
        max_hypotheses=config.ablation.max_hypotheses,
    )
    results = [
        score_case(
            case,
            answer,
            prediction,
            max_tool_calls=config.ablation.max_tool_calls,
        )
        for case, answer, prediction in zip(
            effective_visible, answers, effective_predictions, strict=True
        )
    ]
    return EvaluationRun(
        config=config,
        benchmark_manifest_sha256=visible_hash,
        answer_key_manifest_sha256=answer_hash,
        effective_benchmark_sha256=digest_models(effective_visible),
        prediction_sha256=digest_models(effective_predictions),
        resolved_ablation_sha256=digest_value(config.ablation.model_dump(mode="json")),
        provider_config_sha256=provider_config_digest(config),
        tool_policy_sha256=tool_policy_digest(effective_visible, config),
        source_visible_cases=source_visible,
        effective_visible_cases=effective_visible,
        answer_keys=answers,
        predictions=effective_predictions,
        results=results,
        aggregate=aggregate_results(results, answers, effective_predictions),
    )


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
            run = _build_run(args)
            export_evaluation(run, args.output_dir, overwrite=args.overwrite)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "run_id": run.config.run_id,
                        "metrics": run.aggregate.model_dump(mode="json"),
                    },
                    sort_keys=True,
                )
            )
            return 0
        if command == "validate":
            run = load_evaluation(args.run_dir)
            print(json.dumps({"valid": True, "run_id": run.config.run_id}, sort_keys=True))
            return 0
        if command == "replay":
            run = replay_evaluation(
                args.run_dir,
                visible_dir=getattr(args, "visible_dir", None),
                answers_dir=getattr(args, "answers_dir", None),
                predictions_path=getattr(args, "predictions", None),
                config_path=getattr(args, "config", None),
            )
            print(json.dumps(run.aggregate.model_dump(mode="json"), sort_keys=True))
            return 0
        if command == "summary":
            run = replay_evaluation(
                args.run_dir,
                visible_dir=getattr(args, "visible_dir", None),
                answers_dir=getattr(args, "answers_dir", None),
                predictions_path=getattr(args, "predictions", None),
                config_path=getattr(args, "config", None),
            )
            print(json.dumps(run.aggregate.model_dump(mode="json"), indent=2, sort_keys=True))
            return 0
        if command == "compare":
            report = compare_runs(
                args.left_dir,
                args.right_dir,
                visible_dir=args.visible_dir,
                answers_dir=args.answers_dir,
                left_predictions_path=args.left_predictions,
                left_config_path=args.left_config,
                right_predictions_path=args.right_predictions,
                right_config_path=args.right_config,
            )
            if getattr(args, "output_dir", None) is not None:
                export_comparison(report, args.output_dir)
            print(json.dumps(report.model_dump(mode="json"), sort_keys=True))
            return 0
        if command == "compare-replay":
            report = replay_comparison(
                args.comparison_dir,
                args.left_dir,
                args.right_dir,
                visible_dir=args.visible_dir,
                answers_dir=args.answers_dir,
                left_predictions_path=args.left_predictions,
                left_config_path=args.left_config,
                right_predictions_path=args.right_predictions,
                right_config_path=args.right_config,
            )
            print(json.dumps(report.model_dump(mode="json"), sort_keys=True))
            return 0
        if command == "adversarial":
            result = check_adversarial_text(args.case_id, args.input)
            print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
            return 0 if result.blocked else 1
        if command == "adversarial-suite":
            payload = json.loads(args.cases.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError("adversarial suite must be a JSON list")
            cases = [AdversarialCase.model_validate(item) for item in payload]
            results = evaluate_adversarial_suite(cases)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "case_count": len(results),
                        "authoritative_boundary_count": sum(
                            result.authoritative_control_exercised for result in results
                        ),
                        "results": [item.model_dump(mode="json") for item in results],
                    },
                    sort_keys=True,
                )
            )
            return 0
        raise ValueError(f"unknown evaluation command: {command}")
    except (
        BenchmarkError,
        ComparisonArtifactError,
        EvaluationArtifactError,
        ValueError,
        OSError,
    ) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, sort_keys=True))
        return 1
