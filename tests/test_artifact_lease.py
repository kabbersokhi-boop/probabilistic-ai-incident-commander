from __future__ import annotations

import contextlib
import multiprocessing
import os
import stat
import time
from pathlib import Path
from typing import Any

import pytest

from paic.artifacts.lease import ArtifactLeaseError, artifact_lease, artifact_reader_leases


def test_shared_leases_overlap_and_writer_waits(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    ready = multiprocessing.Event()
    release = multiprocessing.Event()

    def reader() -> None:
        with artifact_lease(target, exclusive=False):
            ready.set()
            release.wait(5)

    process = multiprocessing.Process(target=reader)
    process.start()
    assert ready.wait(5)
    acquired = multiprocessing.Event()

    def writer() -> None:
        with artifact_lease(target, exclusive=True):
            acquired.set()

    writer_process = multiprocessing.Process(target=writer)
    writer_process.start()
    time.sleep(0.1)
    assert not acquired.is_set()
    release.set()
    writer_process.join(5)
    process.join(5)
    assert acquired.is_set()
    assert process.exitcode == 0
    assert writer_process.exitcode == 0
    assert (tmp_path / ".artifact.lease").is_file()


def test_killed_reader_releases_lease_for_writer(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    ready = multiprocessing.Event()

    def reader() -> None:
        with artifact_lease(target, exclusive=False):
            ready.set()
            time.sleep(30)

    process = multiprocessing.Process(target=reader)
    process.start()
    assert ready.wait(5)
    process.kill()
    process.join(5)
    acquired = multiprocessing.Event()

    def writer() -> None:
        with artifact_lease(target, exclusive=True):
            acquired.set()

    writer_process = multiprocessing.Process(target=writer)
    writer_process.start()
    writer_process.join(5)
    assert writer_process.exitcode == 0
    assert acquired.is_set()
    assert (tmp_path / ".artifact.lease").is_file()


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])  # type: ignore[untyped-decorator]
def test_lease_rejects_unsafe_coordination_inode(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    lease = tmp_path / ".artifact.lease"
    if kind == "symlink":
        lease.symlink_to(target)
    elif kind == "directory":
        lease.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        os.mkfifo(lease)
    with (
        pytest.raises(ArtifactLeaseError, match="coordination"),
        artifact_lease(target, exclusive=False),
    ):
        pass


@pytest.mark.parametrize("failure", ["fstat", "regular", "flock"])  # type: ignore[untyped-decorator]
def test_lease_closes_descriptor_on_acquisition_failure(
    tmp_path: Path, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    opened = 91
    closed: list[int] = []
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: opened)
    monkeypatch.setattr(os, "close", lambda fd: closed.append(fd))
    if failure == "fstat":

        def fail_fstat(fd: int) -> os.stat_result:
            raise OSError("fstat failed")

        monkeypatch.setattr(os, "fstat", fail_fstat)
    elif failure == "regular":
        monkeypatch.setattr(os, "fstat", lambda fd: type("Info", (), {"st_mode": stat.S_IFIFO})())
    else:
        monkeypatch.setattr(
            os, "fstat", lambda fd: os.stat_result((stat.S_IFREG, 0, 0, 0, 0, 0, 0, 0, 0, 0))
        )
        monkeypatch.setattr(
            "paic.artifacts.lease.fcntl.flock",
            lambda *args: (_ for _ in ()).throw(OSError("flock failed")),
        )
    with pytest.raises(ArtifactLeaseError), artifact_lease(target, exclusive=False):
        pass
    assert closed == [opened]


def test_multi_root_order_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    ready_left = multiprocessing.Event()
    ready_right = multiprocessing.Event()

    def acquire(roots: list[Path], ready: Any) -> None:
        with artifact_reader_leases(roots):
            ready.set()
            time.sleep(0.2)

    left = multiprocessing.Process(target=acquire, args=([first, second], ready_left))
    right = multiprocessing.Process(target=acquire, args=([second, first], ready_right))
    left.start()
    right.start()
    assert ready_left.wait(5)
    assert ready_right.wait(5)
    left.join(5)
    right.join(5)
    assert left.exitcode == 0
    assert right.exitcode == 0


def test_shared_readers_hold_lease_concurrently(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    acquired: list[Any] = [multiprocessing.Event(), multiprocessing.Event()]
    release = multiprocessing.Event()

    def reader(index: int) -> None:
        with artifact_lease(target, exclusive=False):
            acquired[index].set()
            release.wait(5)

    processes = [multiprocessing.Process(target=reader, args=(index,)) for index in range(2)]
    for process in processes:
        process.start()
    assert acquired[0].wait(5)
    assert acquired[1].wait(5)
    release.set()
    for process in processes:
        process.join(5)
        assert process.exitcode == 0


def test_multi_root_lease_deduplicates_equivalent_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    calls: list[Path] = []

    @contextlib.contextmanager
    def spy(root: str | Path, *, exclusive: bool) -> Any:
        calls.append(Path(root))
        yield

    monkeypatch.setattr("paic.artifacts.lease.artifact_lease", spy)
    with artifact_reader_leases([target, target.absolute(), Path(".") / target, target]):
        pass
    assert len(calls) == 1
