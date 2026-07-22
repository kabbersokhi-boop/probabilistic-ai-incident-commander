from __future__ import annotations

import ctypes
import errno
import json
import os
import shutil
import subprocess
import sys
import time
from itertools import pairwise
from pathlib import Path
from threading import Event, Thread

import pytest

from paic.artifacts import publication
from paic.artifacts.publication import ArtifactPublicationError, AtomicDirectoryPublisher
from paic.simulator.io import export_dataset
from paic.simulator.types import SimulationResult
from paic.simulator.validation import validate_dataset_directory


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


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "failure", [OSError("renameat2 unavailable"), OSError(errno.EXDEV, "cross-device")]
)
def test_atomic_exchange_unavailable_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: OSError
) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "value.txt").write_text("old", encoding="utf-8")

    def unavailable(_left: Path, _right: Path) -> None:
        raise failure

    monkeypatch.setattr(publication, "_rename_exchange", unavailable)
    publisher = AtomicDirectoryPublisher(target, overwrite=True)
    with pytest.raises(ArtifactPublicationError, match="not committed"), publisher as staging:
        (staging / "value.txt").write_text("new", encoding="utf-8")
        publisher.commit()
    assert (target / "value.txt").read_text(encoding="utf-8") == "old"
    assert not publisher.lock_path.exists()
    assert not list(tmp_path.glob(".artifact.staging-*"))


def test_stale_writer_lock_fails_closed_without_mutating_target(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "value.txt").write_text("old", encoding="utf-8")
    lock = tmp_path / ".artifact.lock"
    lock.write_text("999999\n", encoding="utf-8")
    with pytest.raises(ArtifactPublicationError, match="already locked"):
        AtomicDirectoryPublisher(target, overwrite=True).__enter__()
    assert (target / "value.txt").read_text(encoding="utf-8") == "old"
    assert lock.read_text(encoding="utf-8") == "999999\n"


def test_rename_exchange_reports_unavailable_and_errno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class NoExchange:
        renameat2 = None

    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: NoExchange())
    with pytest.raises(OSError, match="unavailable"):
        publication._rename_exchange(tmp_path / "left", tmp_path / "right")

    class FailingExchange:
        argtypes: object = None
        restype: object = None

        def __call__(self, *_args: object) -> int:
            return -1

    class ErrnoExchange:
        renameat2 = FailingExchange()

    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: ErrnoExchange())
    monkeypatch.setattr(ctypes, "get_errno", lambda: errno.EXDEV)
    with pytest.raises(OSError, match="cross-device"):
        publication._rename_exchange(tmp_path / "left", tmp_path / "right")


def test_parent_and_lock_safety_reject_symlink_and_nonregular_components(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ArtifactPublicationError, match="parent traverses"):
        AtomicDirectoryPublisher(parent_link / "artifact", overwrite=True).__enter__()
    parent_file = tmp_path / "parent-file"
    parent_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ArtifactPublicationError, match="non-directory component"):
        AtomicDirectoryPublisher(parent_file / "artifact", overwrite=True).__enter__()
    target = tmp_path / "target"
    target.mkdir()
    lock = tmp_path / ".target.lock"
    lock.mkdir()
    with pytest.raises(ArtifactPublicationError, match="lock is not a regular"):
        AtomicDirectoryPublisher(target, overwrite=True).__enter__()


def test_lock_acquisition_oserror_is_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    monkeypatch.setattr(
        os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied"))
    )
    with pytest.raises(ArtifactPublicationError, match="cannot acquire"):
        AtomicDirectoryPublisher(target, overwrite=True).__enter__()


def test_commit_without_entering_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactPublicationError, match="has not been entered"):
        AtomicDirectoryPublisher(tmp_path / "artifact", overwrite=True).commit()


def test_commit_failure_restores_backup_when_target_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher = AtomicDirectoryPublisher(tmp_path / "artifact", overwrite=True)
    staging = tmp_path / ".artifact.staging-manual"
    staging.mkdir()
    backup = tmp_path / ".artifact.backup-manual"
    backup.mkdir()
    (backup / "value.txt").write_text("old", encoding="utf-8")
    publisher.staging = staging
    publisher.backup = backup
    monkeypatch.setattr(
        publication,
        "_fsync_payload_tree",
        lambda _root: (_ for _ in ()).throw(OSError("before exchange")),
    )
    with pytest.raises(ArtifactPublicationError, match="not committed"):
        publisher.commit()
    assert (publisher.target / "value.txt").read_text(encoding="utf-8") == "old"


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


def test_high_frequency_dataset_validator_reads_see_complete_generations(
    tmp_path: Path,
    smoke_result: SimulationResult,
    rich_result: SimulationResult,
) -> None:
    """Exercise the real dataset loader/validator while 100 generations publish."""
    old_generation = tmp_path / "old-generation"
    new_generation = tmp_path / "new-generation"
    target = tmp_path / "dataset"
    export_dataset(smoke_result, old_generation)
    export_dataset(rich_result, new_generation)
    shutil.copytree(old_generation, target)

    generation_ready = Event()
    generation_checked = Event()
    successful_reads = 0
    observations: list[str] = []

    def reader() -> None:
        nonlocal successful_reads
        for _ in range(100):
            assert generation_ready.wait(timeout=30)
            generation_ready.clear()
            try:
                for _ in range(10):
                    report = validate_dataset_directory(target)
                    if not report.valid:
                        observations.append("invalid")
                        return
                    successful_reads += 1
            except Exception as exc:  # The assertion below retains controlled diagnostics.
                observations.append(type(exc).__name__)
                return
            finally:
                generation_checked.set()

    thread = Thread(target=reader)
    thread.start()
    try:
        for index in range(100):
            source = new_generation if index % 2 else old_generation
            publisher = AtomicDirectoryPublisher(target, overwrite=True)
            with publisher as staging:
                shutil.copytree(source, staging, dirs_exist_ok=True)
                publisher.commit()
            generation_checked.clear()
            generation_ready.set()
            assert generation_checked.wait(timeout=30)
    finally:
        generation_ready.set()
        thread.join(timeout=120)

    assert not thread.is_alive()
    assert observations == []
    assert successful_reads >= 1000
    assert validate_dataset_directory(target).valid
    assert not list(tmp_path.glob(".dataset.staging-*"))
    assert not list(tmp_path.glob(".dataset.backup-*"))
    assert not list(tmp_path.glob(".dataset.lock"))


def test_cross_process_validator_progress_overlaps_publication(
    tmp_path: Path,
    smoke_result: SimulationResult,
    rich_result: SimulationResult,
) -> None:
    """Readers continuously validate while alternating generations are exchanged."""
    old_generation = tmp_path / "old-generation"
    new_generation = tmp_path / "new-generation"
    target = tmp_path / "dataset"
    export_dataset(smoke_result, old_generation)
    export_dataset(rich_result, new_generation)
    shutil.copytree(old_generation, target)
    reader_code = """
import json, os, sys, time, traceback
from pathlib import Path
from paic.simulator.validation import validate_dataset_directory
target, ready, stop, result = map(Path, sys.argv[1:])
count = 0
errors = []
first_success = None
def record():
    temporary = result.with_name(result.name + '.tmp')
    temporary.write_text(json.dumps({'count': count, 'errors': errors,
        'first_success': first_success, 'last_success': time.time()}, sort_keys=True), encoding='utf-8')
    with temporary.open('rb') as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, result)
try:
    initial = validate_dataset_directory(target)
    if not initial.valid:
        errors.append({'code': 'initial_invalid', 'issues': [i.code for i in initial.issues]})
        record()
        raise SystemExit(2)
    count = 1
    first_success = time.time()
    record()
    ready.write_text('ready', encoding='utf-8')
    while not stop.exists():
        try:
            report = validate_dataset_directory(target)
            if not report.valid:
                errors.append({'code': 'invalid', 'issues': [i.code for i in report.issues]})
                break
            count += 1
            if count % 10 == 0:
                record()
        except Exception as exc:
            errors.append({'code': 'exception', 'type': type(exc).__name__, 'message': str(exc),
                           'traceback': traceback.format_exc()})
            break
except Exception as exc:
    errors.append({'code': 'exception', 'type': type(exc).__name__, 'message': str(exc),
                   'traceback': traceback.format_exc()})
record()
"""
    readers: list[subprocess.Popen[str]] = []
    markers: list[tuple[Path, Path, Path]] = []
    try:
        for index in range(2):
            ready = tmp_path / f"reader-{index}.ready"
            result = tmp_path / f"reader-{index}.json"
            stop = tmp_path / f"reader-{index}.stop"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    reader_code,
                    str(target),
                    str(ready),
                    str(stop),
                    str(result),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            readers.append(process)
            markers.append((ready, result, stop))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not all(item[0].exists() for item in markers):
            time.sleep(0.01)
        if not all(item[0].exists() for item in markers):
            diagnostics = []
            for process, (ready, result, _) in zip(readers, markers, strict=True):
                if process.poll() is None:
                    process.kill()
                stdout, stderr = process.communicate(timeout=5)
                diagnostics.append(
                    {
                        "ready": ready.exists(),
                        "result": result.read_text(encoding="utf-8") if result.exists() else None,
                        "returncode": process.returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                )
            raise AssertionError(f"reader startup timed out: {diagnostics}")

        def read_count(path: Path) -> int:
            try:
                return int(json.loads(path.read_text(encoding="utf-8"))["count"])
            except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                return 0

        progress: list[int] = [sum(read_count(item[1]) for item in markers)]
        checkpoints: list[tuple[int, int]] = []
        for cycle in range(1, 101):
            source = new_generation if cycle % 2 else old_generation
            publisher = AtomicDirectoryPublisher(target, overwrite=True)
            with publisher as staging:
                shutil.copytree(source, staging, dirs_exist_ok=True)
                publisher.commit()
            if cycle in {1, 25, 50, 75, 100}:
                value = sum(read_count(item[1]) for item in markers)
                progress.append(value)
                checkpoints.append((cycle, value))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and sum(read_count(item[1]) for item in markers) < 1000:
            if any(process.poll() is not None for process in readers):
                break
            time.sleep(0.05)
        for _, _, stop in markers:
            stop.write_text("stop", encoding="utf-8")
        for process in readers:
            process.wait(timeout=120)
        results = [json.loads(result.read_text(encoding="utf-8")) for _, result, _ in markers]
        assert all(item["errors"] == [] for item in results), results
        assert sum(item["count"] for item in results) >= 1000
        assert progress[-1] > progress[0], checkpoints
        assert sum(after > before for before, after in pairwise(progress)) >= 2, checkpoints
        assert validate_dataset_directory(target).valid
        assert (tmp_path / ".dataset.lease").is_file()
        assert not list(tmp_path.glob(".dataset.staging-*"))
        assert not list(tmp_path.glob(".dataset.backup-*"))
        assert not (tmp_path / ".dataset.lock").exists()
    finally:
        for _, _, stop in markers:
            stop.touch()
        for process in readers:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


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
