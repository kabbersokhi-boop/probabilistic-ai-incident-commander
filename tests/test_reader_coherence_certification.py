from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from itertools import pairwise
from pathlib import Path
from typing import Any

from paic.artifacts.publication import AtomicDirectoryPublisher
from paic.simulator.io import export_dataset
from paic.simulator.types import SimulationResult
from paic.simulator.validation import validate_dataset_directory


_READER_CODE = r"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

from paic.artifacts.lease import artifact_lease
from paic.simulator.validation import validate_dataset_directory

reader_id = sys.argv[1]
target, ready, stop, result = map(Path, sys.argv[2:])
count = 0
errors = []
identities = {}
first_success = None
last_success = None


def record():
    temporary = result.with_name(result.name + '.tmp')
    payload = {
        'reader_id': reader_id,
        'count': count,
        'errors': errors,
        'identities': identities,
        'first_success': first_success,
        'last_success': last_success,
    }
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.flush()
    os.replace(temporary, result)


def validate_once():
    global count, first_success, last_success
    with artifact_lease(target, exclusive=False):
        report = validate_dataset_directory(target)
        manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
        marker = (target / '_SUCCESS').read_text(encoding='utf-8').strip()
        identity = manifest['config_sha256']
        if not report.valid:
            errors.append({
                'code': 'invalid',
                'issues': [
                    {'code': issue.code, 'message': issue.message}
                    for issue in report.issues
                ],
            })
            return False
        if marker != identity:
            errors.append({
                'code': 'identity_mismatch',
                'manifest': identity,
                'success_marker': marker,
            })
            return False
    now = time.time()
    count += 1
    first_success = first_success or now
    last_success = now
    identities[identity] = identities.get(identity, 0) + 1
    if count == 1 or count % 5 == 0:
        record()
    return True


try:
    if not validate_once():
        record()
        raise SystemExit(2)
    ready.write_text('ready', encoding='utf-8')
    while not stop.exists():
        if not validate_once():
            break
except BaseException as exc:
    if not isinstance(exc, SystemExit):
        errors.append({
            'code': 'exception',
            'type': type(exc).__name__,
            'message': str(exc),
            'traceback': traceback.format_exc(),
        })
finally:
    record()
raise SystemExit(1 if errors else 0)
"""


def _read_result(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _diagnostics(
    readers: list[subprocess.Popen[str]], markers: list[tuple[Path, Path, Path]]
) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for process, (ready, result, _) in zip(readers, markers, strict=True):
        payload: dict[str, Any] | None = None
        if result.exists():
            try:
                payload = _read_result(result)
            except (OSError, json.JSONDecodeError):
                payload = {"raw": result.read_text(encoding="utf-8", errors="replace")}
        details.append(
            {
                "pid": process.pid,
                "returncode": process.poll(),
                "ready": ready.exists(),
                "result": payload,
            }
        )
    return details


def test_continuous_cross_process_validation_certifies_reader_coherence(
    tmp_path: Path,
    smoke_result: SimulationResult,
    rich_result: SimulationResult,
) -> None:
    """Certify 100 exchanges and 1,000 coherent reads with live subprocess readers."""

    first_generation = tmp_path / "generation-a"
    second_generation = tmp_path / "generation-b"
    target = tmp_path / "dataset"
    export_dataset(smoke_result, first_generation)
    export_dataset(rich_result, second_generation)
    shutil.copytree(first_generation, target)

    expected_identities = {
        json.loads((first_generation / "manifest.json").read_text(encoding="utf-8"))["config_sha256"],
        json.loads((second_generation / "manifest.json").read_text(encoding="utf-8"))["config_sha256"],
    }
    assert len(expected_identities) == 2

    readers: list[subprocess.Popen[str]] = []
    markers: list[tuple[Path, Path, Path]] = []
    publication_started = time.time()
    try:
        for index in range(2):
            ready = tmp_path / f"cert-reader-{index}.ready"
            result = tmp_path / f"cert-reader-{index}.json"
            stop = tmp_path / f"cert-reader-{index}.stop"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _READER_CODE,
                    str(index),
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

        startup_deadline = time.monotonic() + 30
        while time.monotonic() < startup_deadline:
            if all(ready.exists() for ready, _, _ in markers):
                break
            if any(process.poll() is not None for process in readers):
                raise AssertionError(f"reader exited during startup: {_diagnostics(readers, markers)}")
            time.sleep(0.02)
        assert all(ready.exists() for ready, _, _ in markers), _diagnostics(readers, markers)

        def aggregate_count() -> int:
            return sum(_read_result(result)["count"] for _, result, _ in markers)

        progress: list[tuple[int, int]] = [(0, aggregate_count())]
        publication_started = time.time()
        for cycle in range(1, 101):
            source = second_generation if cycle % 2 else first_generation
            publisher = AtomicDirectoryPublisher(target, overwrite=True)
            with publisher as staging:
                shutil.copytree(source, staging, dirs_exist_ok=True)
                publisher.commit()
            time.sleep(0.01)
            if cycle in {25, 50, 75, 100}:
                assert all(process.poll() is None for process in readers), _diagnostics(
                    readers, markers
                )
                progress.append((cycle, aggregate_count()))

        read_deadline = time.monotonic() + 120
        while aggregate_count() < 1000 and time.monotonic() < read_deadline:
            if any(process.poll() is not None for process in readers):
                raise AssertionError(f"reader exited before target: {_diagnostics(readers, markers)}")
            time.sleep(0.05)
        assert aggregate_count() >= 1000, {
            "progress": progress,
            "readers": _diagnostics(readers, markers),
        }

        publication_finished = time.time()
        for _, _, stop in markers:
            stop.write_text("stop", encoding="utf-8")
        completed: list[dict[str, Any]] = []
        for process, (_, result, _) in zip(readers, markers, strict=True):
            stdout, stderr = process.communicate(timeout=60)
            payload = _read_result(result)
            assert process.returncode == 0, {
                "returncode": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "result": payload,
            }
            completed.append(payload)

        assert sum(item["count"] for item in completed) >= 1000
        assert all(item["count"] > 0 for item in completed)
        assert all(item["errors"] == [] for item in completed), completed
        assert all(set(item["identities"]).issubset(expected_identities) for item in completed)
        assert all(item["first_success"] <= publication_started for item in completed)
        assert all(item["last_success"] >= publication_started for item in completed)
        assert all(item["last_success"] <= publication_finished for item in completed)

        counts = [count for _, count in progress]
        assert progress[-1][1] > progress[0][1], progress
        assert sum(after > before for before, after in pairwise(counts)) >= 2, progress
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
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
