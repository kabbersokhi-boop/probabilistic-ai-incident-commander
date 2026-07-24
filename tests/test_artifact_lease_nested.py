from __future__ import annotations

from pathlib import Path

import pytest

from paic.artifacts.lease import ArtifactLeaseError, artifact_reader_leases


def test_nested_same_domain_parent_replacement_fails_closed(tmp_path: Path) -> None:
    first = tmp_path / "artifacts" / "first"
    second = tmp_path / "artifacts" / "second"
    first.mkdir(parents=True)
    second.mkdir()

    with (
        pytest.raises(ArtifactLeaseError, match="parent changed"),
        artifact_reader_leases([first, second]),
    ):
        second.parent.rename(tmp_path / "artifacts-old")
        second.mkdir(parents=True)
