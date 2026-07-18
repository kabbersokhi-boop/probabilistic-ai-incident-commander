"""Locked, local transaction authority for simulated remediation state.

The store is deliberately local.  A single store directory owns one immutable
control-state lineage; callers cannot select an old exported state after the
store has advanced.  A generation becomes current only by atomically replacing
``current.json`` after both the state and its receipt have been validated.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from paic.remediation.approval import ApprovalLedger, evaluate_approval
from paic.remediation.artifact import (
    RemediationArtifactError,
    export_control_state,
    export_execution,
    load_control_state,
    load_execution,
    manifest_sha256,
    validate_execution,
)
from paic.remediation.config import RemediationConfig
from paic.remediation.executor import ExecutionError, execute_plan
from paic.remediation.models import ExecutionReceipt, ExecutionRequest, RemediationPlan
from paic.tools.ledger import canonical, digest

try:  # pragma: no cover - supported CI platforms provide fcntl
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class StateStoreError(RuntimeError):
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


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    staged = path.with_name(f".{path.name}.tmp")
    with staged.open("x", encoding="utf-8") as handle:
        handle.write(canonical(value) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staged, path)
    try:
        _fsync_dir(path.parent)
    except OSError as exc:
        # Replacement is already the commit point.  A successful reread means
        # callers must receive success rather than retry a committed action.
        try:
            if json.loads(path.read_text(encoding="utf-8")) == value:
                return
        except (OSError, ValueError):
            pass
        raise StateStoreError("cannot durably publish state-store pointer") from exc


class ControlStateStore:
    """Canonical authority for exactly-once local simulated execution.

    The guarantee is scoped to processes using the same local store path and
    filesystem locking semantics.  It is intentionally not a distributed
    coordination service.
    """

    def __init__(self, directory: str | Path):
        self.root = Path(directory)
        self.meta_path = self.root / "store.json"
        self.current_path = self.root / "current.json"
        self.generations = self.root / "generations"
        self.lock_path = self.root / ".state.lock"

    def _ensure_regular_root(self) -> None:
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise StateStoreError("control-state store root is not a regular directory")
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.generations.mkdir(exist_ok=True)

    def initialize(self, initial_state_dir: str | Path) -> None:
        """Bind a new store to one validated immutable initial-state artifact."""

        initial = Path(initial_state_dir)
        loaded = load_control_state(initial)
        source_manifest = manifest_sha256(initial)
        self._ensure_regular_root()
        with _lock(self.lock_path):
            if self.meta_path.exists():
                meta = self._read_json(self.meta_path)
                if meta.get("origin_manifest_sha256") != source_manifest:
                    raise StateStoreError("state store is bound to another initial state")
                return
            generation = self.generations / "00000000000000000000"
            if generation.exists():
                raise StateStoreError("state store contains an orphan initial generation")
            staged = Path(tempfile.mkdtemp(prefix=".prepare-", dir=self.root))
            try:
                shutil.copytree(initial, staged / "state", symlinks=True)
                # Revalidate copied bytes before the generation becomes visible.
                copied = load_control_state(staged / "state")
                if copied.state != loaded.state:
                    raise StateStoreError("copied initial state differs from source")
                os.replace(staged, generation)
                _fsync_dir(self.root)
                meta = {
                    "schema_version": "1.0",
                    "origin_manifest_sha256": source_manifest,
                    "state_id": loaded.state.state_id,
                    "incident_id": loaded.state.incident_id,
                }
                _write_json_atomic(self.meta_path, meta)
                _write_json_atomic(
                    self.current_path,
                    {"generation": 0, "directory": generation.name, "receipt_sha256": None},
                )
            except Exception:
                if staged.exists():
                    shutil.rmtree(staged)
                raise

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StateStoreError("control-state store metadata is invalid") from exc
        if not isinstance(value, dict):
            raise StateStoreError("control-state store metadata is invalid")
        return value

    def _current(self) -> tuple[dict[str, Any], Path]:
        if not self.meta_path.exists() or not self.current_path.exists():
            raise StateStoreError("control-state store is not initialized")
        pointer = self._read_json(self.current_path)
        directory = pointer.get("directory")
        generation = pointer.get("generation")
        receipt_sha256 = pointer.get("receipt_sha256")
        if (
            set(pointer) != {"generation", "directory", "receipt_sha256"}
            or not isinstance(directory, str)
            or not isinstance(generation, int)
            or generation < 0
            or (receipt_sha256 is not None and not isinstance(receipt_sha256, str))
        ):
            raise StateStoreError("control-state store pointer is invalid")
        expected_prefix = f"{generation:020d}"
        if (
            Path(directory).name != directory
            or (generation == 0 and directory != expected_prefix)
            or (
                generation > 0 and not re.fullmatch(rf"{expected_prefix}-[0-9a-f]{{16}}", directory)
            )
            or (generation == 0 and receipt_sha256 is not None)
            or (generation > 0 and receipt_sha256 is None)
        ):
            raise StateStoreError("control-state store pointer is unsafe")
        root = self.generations / directory
        if root.is_symlink() or not root.is_dir():
            raise StateStoreError("current control-state generation is missing")
        return pointer, root

    def _generation_roots(self) -> dict[int, Path]:
        """Return committed generations; private prepared directories are inert."""

        roots: dict[int, Path] = {}
        try:
            entries = list(self.generations.iterdir())
        except OSError as exc:
            raise StateStoreError("cannot inspect control-state generations") from exc
        for entry in entries:
            if entry.name.startswith(".prepare-"):
                continue
            if entry.is_symlink() or not entry.is_dir():
                raise StateStoreError("control-state store contains an invalid generation entry")
            if entry.name == "00000000000000000000":
                generation = 0
            else:
                match = re.fullmatch(r"(\d{20})-[0-9a-f]{16}", entry.name)
                if match is None:
                    raise StateStoreError(
                        "control-state store contains an invalid generation entry"
                    )
                generation = int(match.group(1))
                if generation == 0:
                    raise StateStoreError(
                        "control-state store contains an invalid generation entry"
                    )
            if generation in roots:
                raise StateStoreError("control-state store contains duplicate generations")
            roots[generation] = entry
        return roots

    def current_state_dir(self) -> Path:
        self._ensure_regular_root()
        with _lock(self.lock_path):
            _, root = self._current()
            return root / "state"

    def execute(
        self,
        plan: RemediationPlan,
        approval_ledger: ApprovalLedger,
        config: RemediationConfig,
        request: ExecutionRequest,
        *,
        token: str,
        secret: bytes,
    ) -> tuple[Path, ExecutionReceipt]:
        """Commit state and receipt together, after all replay checks under lock."""

        self._ensure_regular_root()
        # Global order is state-store lock then approval lock.  Approval writes
        # only take the latter, so a rejection has one observable ordering.
        with _lock(self.lock_path):
            pointer, before_root = self._current()
            before_dir = before_root / "state"
            before_loaded = load_control_state(before_dir)
            before_manifest = manifest_sha256(before_dir)
            meta = self._read_json(self.meta_path)
            if before_loaded.state.incident_id != meta.get("incident_id"):
                raise StateStoreError("current state does not match store identity")
            if plan.plan_sha256 in before_loaded.state.executed_plan_hashes:
                raise ExecutionError(
                    "remediation plan has already executed against this state lineage"
                )
            if before_manifest != plan.control_state_manifest_sha256 and pointer["generation"] == 0:
                raise StateStoreError("plan is bound to a different initial state")
            if before_loaded.state.generation != pointer["generation"]:
                raise StateStoreError("state generation does not match current pointer")

            # Re-evaluate under the state lock immediately before committing.
            with approval_ledger.locked():
                status = evaluate_approval(plan, approval_ledger, config, at=request.executed_at)
                after_state, receipt = execute_plan(
                    plan,
                    before_loaded.state,
                    status,
                    config,
                    request,
                    token=token,
                    secret=secret,
                    before_state_manifest_sha256=before_manifest,
                )

            next_generation = before_loaded.state.generation + 1
            generation_name = f"{next_generation:020d}-{receipt.receipt_sha256[:16]}"
            destination = self.generations / generation_name
            if destination.exists():
                # A previous attempt may have renamed a fully staged transaction
                # before the current pointer commit.  It is not part of the
                # lineage and can be deterministically discarded under this lock.
                if pointer["generation"] < next_generation and destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    raise StateStoreError("target generation already exists")
            staged = Path(tempfile.mkdtemp(prefix=".prepare-", dir=self.root))
            try:
                state_root = staged / "state"
                receipt_root = staged / "execution"
                export_control_state(after_state, state_root)
                export_execution(receipt, receipt_root)
                if validate_execution(
                    receipt_root,
                    plan_dir=None,
                    before_state_dir=before_dir,
                    after_state_dir=state_root,
                ):
                    raise StateStoreError("prepared transaction does not validate")
                # Include the plan transition check without depending on an
                # external receipt directory after the commit.
                from paic.remediation.executor import verify_execution_transition

                verify_execution_transition(plan, before_loaded.state, after_state, receipt)
                os.replace(staged, destination)
                _fsync_dir(self.generations)
                _write_json_atomic(
                    self.current_path,
                    {
                        "generation": next_generation,
                        "directory": generation_name,
                        "receipt_sha256": receipt.receipt_sha256,
                    },
                )
            except Exception:
                if staged.exists():
                    shutil.rmtree(staged)
                # A generation without a current-pointer update is inert and
                # deterministic recovery ignores it.
                raise
            return destination, receipt

    def validate(self) -> list[str]:
        try:
            self._ensure_regular_root()
            with _lock(self.lock_path):
                pointer, root = self._current()
                roots = self._generation_roots()
                current_generation = pointer["generation"]
                committed = {
                    generation: generation_root
                    for generation, generation_root in roots.items()
                    if generation <= current_generation
                }
                if committed.get(current_generation) != root or set(committed) != set(
                    range(current_generation + 1)
                ):
                    raise StateStoreError("control-state store generations are not contiguous")
                loaded = load_control_state(root / "state")
                if loaded.state.generation != pointer["generation"]:
                    raise StateStoreError("current state generation does not match pointer")
                if pointer.get("receipt_sha256") is not None:
                    receipt = load_execution(root / "execution").receipt
                    if receipt.receipt_sha256 != pointer["receipt_sha256"]:
                        raise StateStoreError("current receipt does not match pointer")
                    if receipt.after_state_payload_sha256 != digest(
                        loaded.state.model_dump(mode="json")
                    ):
                        raise StateStoreError("current receipt does not bind the current state")
        except (StateStoreError, RemediationArtifactError) as exc:
            return [str(exc)]
        return []
