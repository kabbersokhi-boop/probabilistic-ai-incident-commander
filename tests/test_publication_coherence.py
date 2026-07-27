from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path

import pytest

from paic.artifacts.publication import AtomicDirectoryPublisher


def _reader(target: str, stop: str, ready: str, result: str) -> None:
    from paic.artifacts.lease import artifact_lease

    root = Path(target)
    stop_path = Path(stop)
    ready_path = Path(ready)
    result_path = Path(result)
    errors: list[str] = []
    reads = 0
    ready_path.write_text("ready", encoding="utf-8")
    try:
        while not stop_path.exists():
            with artifact_lease(root, exclusive=False):
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                payload = json.loads((root / "payload.json").read_text(encoding="utf-8"))
                if manifest.get("generation") != payload.get("generation"):
                    errors.append("mixed generation")
                    break
                reads += 1
    except Exception as exc:  # diagnostics are returned to the parent process
        errors.append(f"{type(exc).__name__}: {exc}")
    result_path.write_text(json.dumps({"reads": reads, "errors": errors}), encoding="utf-8")


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "artifact_name",
    ["remediation-execution", "recovery-observations", "recovery-report", "evaluation-artifact"],
)
def test_process_reader_observes_only_complete_generations(
    tmp_path: Path, artifact_name: str
) -> None:
    target = tmp_path / artifact_name
    target.mkdir()
    (target / "manifest.json").write_text('{"generation": 0}\n', encoding="utf-8")
    (target / "payload.json").write_text('{"generation": 0}\n', encoding="utf-8")
    stop = tmp_path / "stop"
    ready = tmp_path / "ready"
    result = tmp_path / "result.json"
    process = multiprocessing.Process(
        target=_reader, args=(str(target), str(stop), str(ready), str(result))
    )
    process.start()
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            if not process.is_alive():
                break
            time.sleep(0.01)
        assert ready.exists(), (
            result.read_text(encoding="utf-8") if result.exists() else "reader did not start"
        )
        for generation in range(1, 11):
            publisher = AtomicDirectoryPublisher(target, overwrite=True)
            with publisher as staging:
                (staging / "manifest.json").write_text(
                    json.dumps({"generation": generation}) + "\n", encoding="utf-8"
                )
                (staging / "payload.json").write_text(
                    json.dumps({"generation": generation}) + "\n", encoding="utf-8"
                )
                publisher.commit()
        stop.write_text("stop", encoding="utf-8")
        process.join(10)
        assert not process.is_alive()
        assert process.exitcode == 0
        report = json.loads(result.read_text(encoding="utf-8"))
        assert report["errors"] == []
        assert report["reads"] > 0
    finally:
        stop.touch()
        if process.is_alive():
            process.terminate()
        process.join(5)
        assert not process.is_alive()
