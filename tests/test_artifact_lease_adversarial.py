from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

from paic.artifacts.lease import artifact_lease


def _join(process: multiprocessing.Process) -> None:
    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)
    assert process.exitcode == 0


def test_shared_readers_overlap_without_timeout_release(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    entered = [multiprocessing.Event(), multiprocessing.Event()]
    release = multiprocessing.Event()

    def reader(index: int) -> None:
        with artifact_lease(target, exclusive=False):
            entered[index].set()
            release.wait()

    processes = [multiprocessing.Process(target=reader, args=(index,)) for index in range(2)]
    for process in processes:
        process.start()
    try:
        assert entered[0].wait(5)
        assert entered[1].wait(2)
    finally:
        release.set()
        for process in processes:
            _join(process)


def test_parent_replacement_cannot_create_second_writer_domain(tmp_path: Path) -> None:
    parent = tmp_path / "artifacts"
    target = parent / "artifact"
    target.mkdir(parents=True)
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    acquired = multiprocessing.Event()

    def holder() -> None:
        with artifact_lease(target, exclusive=True):
            ready.set()
            release.wait()

    def contender() -> None:
        with artifact_lease(target, exclusive=True):
            acquired.set()

    first = multiprocessing.Process(target=holder)
    first.start()
    second: multiprocessing.Process | None = None
    try:
        assert ready.wait(5)
        parent.rename(tmp_path / "artifacts-old")
        target.mkdir(parents=True)
        second = multiprocessing.Process(target=contender)
        second.start()
        time.sleep(0.25)
        assert not acquired.is_set()
        release.set()
        _join(second)
        assert acquired.is_set()
    finally:
        release.set()
        if second is not None and second.is_alive():
            second.terminate()
            second.join(5)
        _join(first)


def test_writer_turnstile_covers_sibling_readers(tmp_path: Path) -> None:
    left = tmp_path / "artifacts" / "left"
    right = tmp_path / "artifacts" / "right"
    left.mkdir(parents=True)
    right.mkdir()
    stop = multiprocessing.Event()
    started = [multiprocessing.Event(), multiprocessing.Event()]
    acquired = multiprocessing.Event()

    def reader(index: int) -> None:
        started[index].set()
        while not stop.is_set():
            with artifact_lease(right, exclusive=False):
                time.sleep(0.005)

    def writer() -> None:
        with artifact_lease(left, exclusive=True, timeout_seconds=5):
            acquired.set()

    readers = [multiprocessing.Process(target=reader, args=(index,)) for index in range(2)]
    for process in readers:
        process.start()
    assert all(event.wait(5) for event in started)
    writer_process = multiprocessing.Process(target=writer)
    writer_process.start()
    try:
        _join(writer_process)
        assert acquired.is_set()
    finally:
        stop.set()
        for process in readers:
            _join(process)


def test_replaced_diagnostic_lock_waits_for_active_epoch(tmp_path: Path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    acquired = multiprocessing.Event()

    def holder() -> None:
        with artifact_lease(target, exclusive=False):
            ready.set()
            release.wait()

    def contender() -> None:
        with artifact_lease(target, exclusive=False):
            acquired.set()

    first = multiprocessing.Process(target=holder)
    first.start()
    second: multiprocessing.Process | None = None
    try:
        assert ready.wait(5)
        replacement = tmp_path / "replacement"
        replacement.write_text("replacement", encoding="utf-8")
        os.replace(replacement, tmp_path / ".artifact.lease")
        second = multiprocessing.Process(target=contender)
        second.start()
        time.sleep(0.2)
        assert not acquired.is_set()
        release.set()
        _join(second)
        assert acquired.is_set()
    finally:
        release.set()
        if second is not None and second.is_alive():
            second.terminate()
            second.join(5)
        _join(first)
