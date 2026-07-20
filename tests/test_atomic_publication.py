from __future__ import annotations

from pathlib import Path

import pytest

from paic.artifacts.publication import ArtifactPublicationError, AtomicDirectoryPublisher


def test_atomic_publication_replaces_complete_generation(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "value.txt").write_text("old", encoding="utf-8")
    publisher = AtomicDirectoryPublisher(target, overwrite=True)
    with publisher as staging:
        (staging / "value.txt").write_text("new", encoding="utf-8")
        result = publisher.commit()
    assert result.committed and result.durability_confirmed
    assert (target / "value.txt").read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob(".artifact.staging-*"))
    assert not list(tmp_path.glob(".artifact.backup-*"))


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "point", ["staging-created", "payload-written", "old-moved"]
)
def test_failures_before_commit_preserve_previous_generation(tmp_path: Path, point: str) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "value.txt").write_text("old", encoding="utf-8")

    def hook(current: str) -> None:
        if current == point:
            raise RuntimeError("boom")

    publisher = AtomicDirectoryPublisher(target, overwrite=True, failure_hook=hook)
    with pytest.raises((RuntimeError, ArtifactPublicationError)), publisher as staging:
        (staging / "value.txt").write_text("new", encoding="utf-8")
        publisher.commit()
    assert (target / "value.txt").read_text(encoding="utf-8") == "old"


def test_failure_after_commit_reports_uncertain_durability_but_keeps_new_generation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "value.txt").write_text("old", encoding="utf-8")

    def hook(point: str) -> None:
        if point == "new-committed":
            raise RuntimeError("boom")

    publisher = AtomicDirectoryPublisher(target, overwrite=True, failure_hook=hook)
    with (
        pytest.raises(ArtifactPublicationError, match="committed but durability is uncertain"),
        publisher as staging,
    ):
        (staging / "value.txt").write_text("new", encoding="utf-8")
        publisher.commit()
    assert (target / "value.txt").read_text(encoding="utf-8") == "new"


def test_publication_rejects_symlink_target(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "artifact"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ArtifactPublicationError, match="symbolic link"):
        AtomicDirectoryPublisher(link, overwrite=True).__enter__()
