"""Regenerate the committed JSON Schema for the public bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from paic.web_readiness import WebBundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(WebBundle.model_json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
