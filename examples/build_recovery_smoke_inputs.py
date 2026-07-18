"""Create deterministic healthy and regression recovery observation sets.

The execution receipt hash is supplied by the caller so generated inputs remain
bound to a real Phase 8 execution artifact without embedding credentials or tokens.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path


def source_hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def observation(
    metric: str, cohort: str, at: datetime, value: float, index: int
) -> dict[str, object]:
    return {
        "metric_id": metric,
        "cohort": cohort,
        "observed_at": at.isoformat(),
        "value": value,
        "sample_size": 500,
        "source_sha256": source_hash(f"{metric}:{cohort}:{index}:{value}"),
    }


def build(receipt_hash: str, output_dir: Path) -> None:
    executed_at = datetime(2026, 1, 15, 12, tzinfo=UTC)
    baseline_conversion = [0.719, 0.721, 0.718, 0.722, 0.720, 0.719, 0.721, 0.720]
    baseline_payment = [0.961, 0.960, 0.962, 0.961, 0.959, 0.960, 0.961, 0.960]
    healthy_conversion = [0.665, 0.700, 0.717, 0.720, 0.721]
    healthy_payment = [0.959, 0.960, 0.961, 0.960, 0.961]
    regression_conversion = [0.718, 0.719, 0.700, 0.645, 0.610]
    regression_payment = [0.960, 0.960, 0.955, 0.880, 0.820]

    def payload(
        identifier: str, conversion: list[float], payment: list[float], generated: datetime
    ) -> dict[str, object]:
        rows: list[dict[str, object]] = []
        for index, value in enumerate(baseline_conversion):
            rows.append(
                observation(
                    "checkout_conversion",
                    "eu_android",
                    executed_at - timedelta(hours=8 - index),
                    value,
                    index,
                )
            )
        for index, value in enumerate(baseline_payment):
            rows.append(
                observation(
                    "payment_approval",
                    "eu_android",
                    executed_at - timedelta(hours=8 - index),
                    value,
                    index,
                )
            )
        for index, value in enumerate(conversion):
            rows.append(
                observation(
                    "checkout_conversion",
                    "eu_android",
                    executed_at + timedelta(hours=index + 1),
                    value,
                    100 + index,
                )
            )
        for index, value in enumerate(payment):
            rows.append(
                observation(
                    "payment_approval",
                    "eu_android",
                    executed_at + timedelta(hours=index + 1),
                    value,
                    200 + index,
                )
            )
        return {
            "schema_version": "1.0",
            "observation_set_id": identifier,
            "incident_id": "checkout-address-validation-smoke",
            "execution_receipt_sha256": receipt_hash,
            "executed_at": executed_at.isoformat(),
            "generated_at": generated.isoformat(),
            "observations": rows,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    healthy = payload(
        "checkout-recovery-healthy",
        healthy_conversion,
        healthy_payment,
        executed_at + timedelta(hours=6),
    )
    regression = payload(
        "checkout-recovery-regression",
        regression_conversion,
        regression_payment,
        executed_at + timedelta(hours=12),
    )
    (output_dir / "healthy.json").write_text(
        json.dumps(healthy, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "regression.json").write_text(
        json.dumps(regression, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-receipt-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.execution_receipt_sha256, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
