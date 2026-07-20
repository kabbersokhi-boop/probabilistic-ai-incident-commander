from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread

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


def test_recursive_payload_durability_flushes_files_and_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    calls: list[int] = []
    real_fsync = os.fsync

    def record_fsync(descriptor: int) -> None:
        calls.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", record_fsync)
    publisher = AtomicDirectoryPublisher(target, overwrite=True)
    with publisher as staging:
        nested = staging / "nested"
        nested.mkdir()
        (nested / "value.txt").write_text("new", encoding="utf-8")
        publisher.commit()
    assert len(calls) >= 5  # payload, nested dir, staging dir, parent, cleanup parent


def test_recursive_payload_durability_rejects_symlink_and_special_entries(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    publisher = AtomicDirectoryPublisher(target, overwrite=True)
    with publisher as staging:
        (staging / "link").symlink_to(target)
        with pytest.raises(ArtifactPublicationError, match="symbolic links"):
            publisher.commit()
    publisher = AtomicDirectoryPublisher(target, overwrite=True)
    with publisher as staging:
        (staging / "fifo").unlink(missing_ok=True)
        os.mkfifo(staging / "fifo")
        with pytest.raises(ArtifactPublicationError, match="non-regular"):
            publisher.commit()


def test_failed_rollback_preserves_backup_and_reports_recovery_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "value.txt").write_text("old", encoding="utf-8")
    publisher = AtomicDirectoryPublisher(target, overwrite=True)
    publisher._lock_fd = os.open(publisher.lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    backup = tmp_path / ".artifact.backup-manual"
    backup.mkdir()
    (backup / "value.txt").write_text("old", encoding="utf-8")
    shutil.rmtree(target)
    publisher.backup = backup
    monkeypatch.setattr(os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("blocked")))
    with pytest.raises(ArtifactPublicationError, match="backup preserved"):
        publisher.__exit__(RuntimeError, RuntimeError("failure"), None)
    assert (backup / "value.txt").read_text(encoding="utf-8") == "old"


def test_exclusive_writer_lock_allows_only_one_writer(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    first = AtomicDirectoryPublisher(target, overwrite=True)
    second = AtomicDirectoryPublisher(target, overwrite=True)
    with first as staging:
        (staging / "value.txt").write_text("new", encoding="utf-8")
        with pytest.raises(ArtifactPublicationError, match="already locked"):
            second.__enter__()
        first.commit()
    assert not first.lock_path.exists()


def test_subprocess_termination_after_staging_preserves_old_generation(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "value.txt").write_text("old", encoding="utf-8")
    script = """
from pathlib import Path
import sys, time
from paic.artifacts.publication import AtomicDirectoryPublisher
target = Path(sys.argv[1])
with AtomicDirectoryPublisher(target, overwrite=True) as staging:
    (staging / 'value.txt').write_text('new', encoding='utf-8')
    time.sleep(30)
    AtomicDirectoryPublisher(target, overwrite=True).commit()
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(target)])
    for _ in range(50):
        if list(tmp_path.glob(".artifact.staging-*")):
            break
        time.sleep(0.02)
    child.terminate()
    child.wait(timeout=5)
    assert (target / "value.txt").read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".artifact.staging-*"))
    assert (tmp_path / ".artifact.lock").exists()
    for orphan in tmp_path.glob(".artifact.staging-*"):
        shutil.rmtree(orphan)
    (tmp_path / ".artifact.lock").unlink()


def test_concurrent_readers_see_only_complete_generations(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "value.txt").write_text("generation-0", encoding="utf-8")
    stop = Event()
    observations: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                value = (target / "value.txt").read_text(encoding="utf-8")
                if value not in {"generation-0", "generation-1"}:
                    observations.append(value)
            except (FileNotFoundError, OSError) as exc:
                observations.append(type(exc).__name__)

    thread = Thread(target=reader)
    thread.start()
    inspections = 0
    try:
        for index in range(30):
            publisher = AtomicDirectoryPublisher(target, overwrite=True)
            with publisher as staging:
                (staging / "value.txt").write_text(f"generation-{index % 2}", encoding="utf-8")
                publisher.commit()
            inspections += 1
    finally:
        stop.set()
        thread.join(timeout=5)
    assert inspections == 30
    assert observations == []


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "checkpoint", ["payload-written", "old-moved", "new-committed", "parent-synced"]
)
def test_subprocess_termination_checkpoints_are_recoverable(
    tmp_path: Path, checkpoint: str
) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "value.txt").write_text("old", encoding="utf-8")
    marker = tmp_path / "marker"
    script = """
from pathlib import Path
import sys, time
from paic.artifacts.publication import AtomicDirectoryPublisher
target, marker, checkpoint = map(Path, sys.argv[1:])
def hook(point):
    if point == checkpoint.name:
        marker.write_text(point, encoding='utf-8')
        time.sleep(30)
publisher = AtomicDirectoryPublisher(target, overwrite=True, failure_hook=hook)
with publisher as staging:
    (staging / 'value.txt').write_text('new', encoding='utf-8')
    publisher.commit()
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(target), str(marker), checkpoint])
    for _ in range(250):
        if marker.exists():
            break
        time.sleep(0.02)
    assert marker.read_text(encoding="utf-8") == checkpoint
    child.terminate()
    child.wait(timeout=5)
    value = (target / "value.txt").read_text(encoding="utf-8")
    assert value in {"old", "new"}
    assert value == "new" or list(tmp_path.glob(".artifact.staging-*"))
    for orphan in tmp_path.glob(".artifact.staging-*"):
        shutil.rmtree(orphan)
    lock = tmp_path / ".artifact.lock"
    if lock.exists():
        lock.unlink()
