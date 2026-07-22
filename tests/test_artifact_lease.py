from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest

from paic.artifacts.lease import ArtifactLeaseError, artifact_lease


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
    with pytest.raises(ArtifactLeaseError, match="coordination"), artifact_lease(
        target, exclusive=False
    ):
        pass
