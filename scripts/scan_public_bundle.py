"""Reject secrets and machine-private paths in public bundle bytes."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SECRET = re.compile(
    r"(?i)(?:api[_-]?key\s*[:=]|secret\s*[:=]|password\s*[:=]|bearer\s+[A-Za-z0-9._-]+|private[_-]?key\s*[:=])"
)
ABSOLUTE = re.compile(r"(?m)(?:^|[\" ])(?:/home/|/tmp/|/workspace/|[A-Za-z]:[\\/])")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle_dir", type=Path)
    args = parser.parse_args(argv)
    for path in sorted(args.bundle_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        if SECRET.search(text) or ABSOLUTE.search(text):
            print(f"unsafe public content in {path}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
