from __future__ import annotations

from pathlib import Path

import pytest

from paic.artifacts.lease import ArtifactLeaseError, artifact_path, artifact_reader_leases


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


def test_direct_nested_sibling_parent_replacement_fails_closed(tmp_path: Path) -> None:
    from paic.artifacts.lease import artifact_lease

    first = tmp_path / "artifacts" / "first"
    second = tmp_path / "artifacts" / "second"
    first.mkdir(parents=True)
    second.mkdir()
    with artifact_lease(first, exclusive=False):
        second.parent.rename(tmp_path / "artifacts-old")
        second.mkdir(parents=True)
        with (
            pytest.raises(ArtifactLeaseError, match="parent changed"),
            artifact_lease(second, exclusive=False),
        ):
            pass


def test_decorated_reader_uses_anchored_generation(tmp_path: Path) -> None:
    from paic.artifacts.lease import artifact_reader

    parent = tmp_path / "artifacts"
    root = parent / "artifact"
    root.mkdir(parents=True)
    (root / "value.txt").write_text("old", encoding="utf-8")

    @artifact_reader
    def read_value(path: str | Path) -> str:
        parent.rename(tmp_path / "artifacts-old")
        replacement = parent / "artifact"
        replacement.mkdir(parents=True)
        (replacement / "value.txt").write_text("new", encoding="utf-8")
        return (artifact_path(path) / "value.txt").read_text(encoding="utf-8")

    assert read_value(root) == "old"
