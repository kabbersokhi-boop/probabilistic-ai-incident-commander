from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

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


@pytest.mark.parametrize("kind", ["symlink", "directory"])  # type: ignore[untyped-decorator]
def test_lease_rejects_unsafe_coordination_inode(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    lease = tmp_path / ".artifact.lease"
    if kind == "symlink":
        lease.symlink_to(target)
    else:
        lease.mkdir()
    with (
        pytest.raises(ArtifactLeaseError, match="coordination"),
        artifact_lease(target, exclusive=False),
    ):
        pass


def test_multi_root_order_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    ready = multiprocessing.Event()

    def acquire(roots: list[Path]) -> None:
        with artifact_reader_leases(roots):
            ready.set()
            time.sleep(0.1)

    left = multiprocessing.Process(target=acquire, args=([first, second],))
    right = multiprocessing.Process(target=acquire, args=([second, first],))
    left.start()
    assert ready.wait(5)
    right.start()
    left.join(5)
    right.join(5)
    assert left.exitcode == 0
    assert right.exitcode == 0
