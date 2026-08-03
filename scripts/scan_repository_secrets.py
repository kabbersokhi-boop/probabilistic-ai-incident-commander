"""Scan tracked text for high-confidence credential material."""

from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{24,}\b"),
    re.compile(r"(?i)\b(?:password|api[_-]?key|secret)\s*[:=]\s*['\"][A-Za-z0-9+/=_-]{12,}"),
)


def main() -> int:
    result = subprocess.run(
        [
            "git",
            "grep",
            "-nI",
            "-e",
            "-----BEGIN",
            "-e",
            "ghp_",
            "-e",
            "AKIA",
            "-e",
            "sk-",
            "-e",
            "password=",
            "-e",
            "api_key=",
            "-e",
            "secret=",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    findings = [
        line
        for line in result.stdout.splitlines()
        if any(pattern.search(line) for pattern in PATTERNS)
    ]
    if findings:
        print("credential-like material found in tracked files:", file=sys.stderr)
        print("\n".join(findings), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
