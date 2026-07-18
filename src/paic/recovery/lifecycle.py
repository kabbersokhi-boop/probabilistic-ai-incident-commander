"""Locked immutable lifecycle store for recovery and automatic incident reopening."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from paic.recovery.config import RecoveryConfig
from paic.recovery.engine import digest, verify_report
from paic.recovery.models import (
    RecoveryLifecycleEvent,
    RecoveryLifecycleState,
    RecoveryReport,
)

try:  # pragma: no cover - CI platforms provide fcntl
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


ZERO_HASH = "0" * 64


class RecoveryStateStoreError(RuntimeError):
    pass


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.flush()
            os.fsync(handle.fileno())
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    staged = path.with_name(f".{path.name}.tmp")
    if staged.exists():
        staged.unlink()
    with staged.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staged, path)
    try:
        _fsync_dir(path.parent)
    except OSError as exc:
        try:
            if json.loads(path.read_text(encoding="utf-8")) == value:
                return
        except (OSError, ValueError):
            pass
        raise RecoveryStateStoreError("cannot durably publish recovery-state pointer") from exc


def transition_state(
    previous: RecoveryLifecycleState,
    report: RecoveryReport,
    config: RecoveryConfig,
) -> tuple[RecoveryLifecycleState, str]:
    verify_report(report)
    if report.incident_id != previous.incident_id:
        raise RecoveryStateStoreError("recovery report targets another incident")
    if report.execution_receipt_sha256 != previous.execution_receipt_sha256:
        raise RecoveryStateStoreError("recovery report targets another remediation execution")
    if report.report_sha256 in previous.applied_report_sha256s:
        raise RecoveryStateStoreError("recovery report has already been applied")
    if previous.last_evaluated_at is not None and report.evaluated_at <= previous.last_evaluated_at:
        raise RecoveryStateStoreError("recovery reports must be applied in evaluation-time order")

    status: Literal["monitoring", "recovered", "reopened"]
    if previous.status == "reopened":
        status = "reopened"
        failures = previous.consecutive_failed_evaluations
        trigger = "incident_already_reopened"
    elif report.decision == "recovered":
        status = "recovered"
        failures = 0
        trigger = "recovery_verified"
    else:
        failures = previous.consecutive_failed_evaluations + 1 if previous.ever_recovered else 0
        severe = report.severe_guardrail_breach and config.immediate_reopen_on_severe_guardrail
        threshold = previous.ever_recovered and failures >= config.reopen_after_consecutive_failures
        if severe or threshold:
            status = "reopened"
            trigger = "severe_guardrail_regression" if severe else "sustained_recovery_regression"
        else:
            status = "monitoring"
            trigger = (
                "recovery_not_yet_verified"
                if not previous.ever_recovered
                else "regression_observed"
            )

    return (
        RecoveryLifecycleState(
            incident_id=previous.incident_id,
            execution_receipt_sha256=previous.execution_receipt_sha256,
            generation=previous.generation + 1,
            status=status,
            ever_recovered=previous.ever_recovered or report.decision == "recovered",
            consecutive_failed_evaluations=failures,
            applied_report_sha256s=[*previous.applied_report_sha256s, report.report_sha256],
            last_report_sha256=report.report_sha256,
            last_evaluated_at=report.evaluated_at,
        ),
        trigger,
    )


class RecoveryStateStore:
    def __init__(self, directory: str | Path):
        self.root = Path(directory)
        self.generations = self.root / "generations"
        self.current_path = self.root / "current.json"
        self.lock_path = self.root / ".recovery.lock"

    def _ensure_root(self) -> None:
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise RecoveryStateStoreError("recovery-state store root is not a regular directory")
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.generations.mkdir(exist_ok=True)

    def initialize(self, incident_id: str, execution_receipt_sha256: str) -> RecoveryLifecycleState:
        self._ensure_root()
        with _lock(self.lock_path):
            if self.current_path.exists():
                state, _, _ = self._current()
                if (
                    state.incident_id != incident_id
                    or state.execution_receipt_sha256 != execution_receipt_sha256
                ):
                    raise RecoveryStateStoreError(
                        "recovery-state store is bound to another incident execution"
                    )
                return state
            state = RecoveryLifecycleState(
                incident_id=incident_id,
                execution_receipt_sha256=execution_receipt_sha256,
                generation=0,
            )
            destination = self.generations / "00000000000000000000"
            if destination.exists():
                raise RecoveryStateStoreError(
                    "recovery-state store contains an orphan initial generation"
                )
            staged = Path(tempfile.mkdtemp(prefix=".prepare-", dir=self.root))
            try:
                (staged / "state.json").write_text(
                    state.model_dump_json(indent=2) + "\n", encoding="utf-8"
                )
                os.replace(staged, destination)
                _fsync_dir(self.generations)
                _atomic_json(
                    self.current_path,
                    {"generation": 0, "directory": destination.name, "event_sha256": None},
                )
            except Exception:
                if staged.exists():
                    shutil.rmtree(staged)
                raise
            return state

    def _read_pointer(self) -> dict[str, Any]:
        try:
            pointer = json.loads(self.current_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RecoveryStateStoreError("recovery-state pointer is invalid") from exc
        if not isinstance(pointer, dict) or set(pointer) != {
            "generation",
            "directory",
            "event_sha256",
        }:
            raise RecoveryStateStoreError("recovery-state pointer is invalid")
        generation = pointer["generation"]
        directory = pointer["directory"]
        event_hash = pointer["event_sha256"]
        if not isinstance(generation, int) or generation < 0 or not isinstance(directory, str):
            raise RecoveryStateStoreError("recovery-state pointer is invalid")
        expected = f"{generation:020d}"
        if generation == 0:
            valid_name = directory == expected and event_hash is None
        else:
            valid_name = bool(
                re.fullmatch(rf"{expected}-[0-9a-f]{{16}}", directory)
            ) and isinstance(event_hash, str)
        if not valid_name:
            raise RecoveryStateStoreError("recovery-state pointer is unsafe")
        return pointer

    def _current(self) -> tuple[RecoveryLifecycleState, RecoveryLifecycleEvent | None, Path]:
        if not self.current_path.exists():
            raise RecoveryStateStoreError("recovery-state store is not initialized")
        pointer = self._read_pointer()
        root = self.generations / pointer["directory"]
        if root.is_symlink() or not root.is_dir():
            raise RecoveryStateStoreError("current recovery-state generation is missing")
        try:
            state = RecoveryLifecycleState.model_validate_json(
                (root / "state.json").read_text(encoding="utf-8")
            )
            event = None
            if pointer["generation"] > 0:
                event = RecoveryLifecycleEvent.model_validate_json(
                    (root / "event.json").read_text(encoding="utf-8")
                )
        except (OSError, ValueError) as exc:
            raise RecoveryStateStoreError("current recovery-state generation is invalid") from exc
        if state.generation != pointer["generation"]:
            raise RecoveryStateStoreError("recovery-state generation does not match pointer")
        if event is not None and event.event_sha256 != pointer["event_sha256"]:
            raise RecoveryStateStoreError("recovery-state event does not match pointer")
        return state, event, root

    def current(self) -> RecoveryLifecycleState:
        self._ensure_root()
        with _lock(self.lock_path):
            state, _, _ = self._current()
            return state

    def apply(
        self, report: RecoveryReport, config: RecoveryConfig
    ) -> tuple[RecoveryLifecycleState, RecoveryLifecycleEvent]:
        self._ensure_root()
        with _lock(self.lock_path):
            previous, previous_event, _ = self._current()
            state, trigger = transition_state(previous, report, config)
            previous_hash = previous_event.event_sha256 if previous_event is not None else ZERO_HASH
            event_payload = {
                "schema_version": "1.0",
                "incident_id": state.incident_id,
                "generation": state.generation,
                "report_sha256": report.report_sha256,
                "previous_event_sha256": previous_hash,
                "from_status": previous.status,
                "to_status": state.status,
                "trigger": trigger,
                "evaluated_at": report.evaluated_at,
            }
            provisional_event = RecoveryLifecycleEvent.model_validate(
                {**event_payload, "event_sha256": ZERO_HASH}
            )
            normalized_event = provisional_event.model_dump(mode="json")
            normalized_event.pop("event_sha256")
            event = provisional_event.model_copy(update={"event_sha256": digest(normalized_event)})
            destination_name = f"{state.generation:020d}-{event.event_sha256[:16]}"
            destination = self.generations / destination_name
            if destination.exists():
                # A previous attempt may have published the immutable generation
                # but failed before the current-pointer commit. Such a generation
                # is outside the authoritative lineage and is safe to discard
                # under the store lock before a deterministic retry.
                if destination.is_dir() and previous.generation < state.generation:
                    shutil.rmtree(destination)
                else:
                    raise RecoveryStateStoreError("target recovery-state generation already exists")
            staged = Path(tempfile.mkdtemp(prefix=".prepare-", dir=self.root))
            try:
                (staged / "state.json").write_text(
                    state.model_dump_json(indent=2) + "\n", encoding="utf-8"
                )
                (staged / "event.json").write_text(
                    event.model_dump_json(indent=2) + "\n", encoding="utf-8"
                )
                os.replace(staged, destination)
                _fsync_dir(self.generations)
                _atomic_json(
                    self.current_path,
                    {
                        "generation": state.generation,
                        "directory": destination_name,
                        "event_sha256": event.event_sha256,
                    },
                )
            except Exception:
                if staged.exists():
                    shutil.rmtree(staged)
                raise
            return state, event

    def validate(self) -> list[str]:
        try:
            self._ensure_root()
            with _lock(self.lock_path):
                current, _, current_root = self._current()
                roots: dict[int, Path] = {}
                for entry in self.generations.iterdir():
                    if entry.name.startswith(".prepare-"):
                        continue
                    if entry.is_symlink() or not entry.is_dir():
                        raise RecoveryStateStoreError(
                            "recovery-state store contains an invalid entry"
                        )
                    if entry.name == "00000000000000000000":
                        generation = 0
                    else:
                        match = re.fullmatch(r"(\d{20})-[0-9a-f]{16}", entry.name)
                        if match is None:
                            raise RecoveryStateStoreError(
                                "recovery-state store contains an invalid entry"
                            )
                        generation = int(match.group(1))
                    if generation in roots:
                        raise RecoveryStateStoreError(
                            "recovery-state store contains duplicate generations"
                        )
                    roots[generation] = entry
                committed = {k: v for k, v in roots.items() if k <= current.generation}
                if (
                    set(committed) != set(range(current.generation + 1))
                    or committed[current.generation] != current_root
                ):
                    raise RecoveryStateStoreError("recovery-state generations are not contiguous")
                previous_event_hash = ZERO_HASH
                previous_state: RecoveryLifecycleState | None = None
                for generation in range(current.generation + 1):
                    root = committed[generation]
                    state = RecoveryLifecycleState.model_validate_json(
                        (root / "state.json").read_text(encoding="utf-8")
                    )
                    if state.generation != generation:
                        raise RecoveryStateStoreError("stored recovery generation is inconsistent")
                    if generation == 0:
                        if (root / "event.json").exists():
                            raise RecoveryStateStoreError(
                                "initial recovery generation must not contain an event"
                            )
                    else:
                        event = RecoveryLifecycleEvent.model_validate_json(
                            (root / "event.json").read_text(encoding="utf-8")
                        )
                        payload = event.model_dump(mode="json")
                        payload.pop("event_sha256")
                        if event.event_sha256 != digest(payload):
                            raise RecoveryStateStoreError("recovery lifecycle event hash mismatch")
                        if event.previous_event_sha256 != previous_event_hash:
                            raise RecoveryStateStoreError(
                                "recovery lifecycle event chain is broken"
                            )
                        if (
                            previous_state is None
                            or event.from_status != previous_state.status
                            or event.to_status != state.status
                        ):
                            raise RecoveryStateStoreError(
                                "recovery lifecycle event transition is inconsistent"
                            )
                        previous_event_hash = event.event_sha256
                    previous_state = state
        except (RecoveryStateStoreError, OSError, ValueError) as exc:
            return [str(exc)]
        return []
