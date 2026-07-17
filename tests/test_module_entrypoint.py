from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest


def test_python_m_paic_entrypoint(monkeypatch: pytest.MonkeyPatch, spec_dir: Path) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["paic", "validate", "--spec-dir", str(spec_dir)],
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("paic.__main__", run_name="__main__")
    assert exc.value.code == 0
