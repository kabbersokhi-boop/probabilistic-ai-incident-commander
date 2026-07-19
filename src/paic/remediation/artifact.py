"""Export, load, and validate remediation-related artifacts."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from paic import __version__
from paic.investigation.artifact import replay_investigation
from paic.remediation.config import RemediationConfig
from paic.remediation.executor import ExecutionError, verify_execution_transition
from paic.remediation.manifest import ArtifactFileManifest, RemediationArtifactManifest
from paic.remediation.models import (
    ControlState,
    ExecutionReceipt,
    RemediationPlan,
    RemediationProposal,
)
from paic.remediation.policy import build_plan, verify_plan
from paic.simulator.io import file_sha256
from paic.tools.ledger import digest


class RemediationArtifactError(RuntimeError):
    pass


T = TypeVar("T")


@dataclass(frozen=True)
class LoadedControlState:
    manifest: RemediationArtifactManifest
    state: ControlState


@dataclass(frozen=True)
class LoadedPlan:
    manifest: RemediationArtifactManifest
    config: RemediationConfig
    proposal: RemediationProposal
    plan: RemediationPlan


@dataclass(frozen=True)
class LoadedExecution:
    manifest: RemediationArtifactManifest
    receipt: ExecutionReceipt


def _stage_root(path: str | Path, *, overwrite: bool) -> tuple[Path, Path, bool]:
    """Create a private sibling staging directory without touching the destination."""

    destination = Path(path)
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    exists = destination.exists() or destination.is_symlink()
    if exists and not overwrite:
        raise RemediationArtifactError(f"output directory already exists: {destination}")
    if exists and (destination.is_file() or destination.is_symlink() or not destination.is_dir()):
        raise RemediationArtifactError(f"output path is not a regular directory: {destination}")
    staged = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=parent))
    os.chmod(staged, 0o700)
    return destination, staged, exists


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(root: Path, destination: Path, *, replace: bool) -> None:
    """Atomically publish a fully validated staged artifact, restoring on failure."""

    backup: Path | None = None
    committed = False
    try:
        if replace:
            backup = destination.with_name(f".{destination.name}.previous-{uuid4().hex}")
            os.replace(destination, backup)
        os.replace(root, destination)
        committed = True
        try:
            _fsync_directory(destination.parent)
        except OSError as exc:
            # The rename is the visibility/commit point.  Do not report a
            # committed artifact as failed solely because durability
            # confirmation is interrupted; a later validator can inspect it.
            if not destination.is_dir() or destination.is_symlink():
                raise RemediationArtifactError("published artifact is unavailable") from exc
    except OSError as exc:
        if not committed and backup is not None and backup.exists() and not destination.exists():
            os.replace(backup, destination)
            _fsync_directory(destination.parent)
        raise RemediationArtifactError(f"cannot atomically publish artifact: {exc}") from exc
    if backup is not None:
        with suppress(OSError):
            shutil.rmtree(backup)


def _export(
    output_dir: str | Path,
    *,
    overwrite: bool,
    writer: Callable[[Path], RemediationArtifactManifest],
    validator: Callable[[str | Path], T],
) -> RemediationArtifactManifest:
    destination, staged, replace = _stage_root(output_dir, overwrite=overwrite)
    try:
        manifest = writer(staged)
        validator(staged)
        _publish(staged, destination, replace=replace)
        return manifest
    except Exception:
        if staged.exists():
            shutil.rmtree(staged)
        raise


def _write_manifest(
    root: Path,
    *,
    artifact_type: str,
    artifact_id: str,
    incident_id: str,
    status: str,
    payload_sha256: str,
    bindings: dict[str, str],
    payloads: dict[str, str],
) -> RemediationArtifactManifest:
    files: list[ArtifactFileManifest] = []
    for name, content in payloads.items():
        path = root / name
        path.write_text(content, encoding="utf-8")
        os.chmod(path, 0o600)
        files.append(
            ArtifactFileManifest(
                relative_path=name,
                byte_size=path.stat().st_size,
                sha256=file_sha256(path),
            )
        )
    manifest = RemediationArtifactManifest.model_validate(
        {
            "schema_version": "1.0",
            "artifact_type": artifact_type,
            "artifact_id": artifact_id,
            "incident_id": incident_id,
            "generator_version": __version__,
            "status": status,
            "payload_sha256": payload_sha256,
            "bindings": dict(sorted(bindings.items())),
            "files": sorted(files, key=lambda item: item.relative_path),
        }
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    marker = root / "_SUCCESS"
    marker.write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    os.chmod(marker, 0o600)
    _fsync_directory(root)
    return manifest


def export_control_state(
    state: ControlState,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> RemediationArtifactManifest:
    return _export(
        output_dir,
        overwrite=overwrite,
        writer=lambda root: _write_manifest(
            root,
            artifact_type="control_state",
            artifact_id=state.state_id,
            incident_id=state.incident_id,
            status="ready",
            payload_sha256=digest(state.model_dump(mode="json")),
            bindings={},
            payloads={"state.json": state.model_dump_json(indent=2) + "\n"},
        ),
        validator=load_control_state,
    )


def export_plan(
    config: RemediationConfig,
    proposal: RemediationProposal,
    plan: RemediationPlan,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> RemediationArtifactManifest:
    return _export(
        output_dir,
        overwrite=overwrite,
        writer=lambda root: _write_manifest(
            root,
            artifact_type="remediation_plan",
            artifact_id=plan.remediation_id,
            incident_id=plan.incident_id,
            status=plan.status,
            payload_sha256=digest(plan.model_dump(mode="json")),
            bindings={
                "investigation_manifest": plan.investigation_manifest_sha256,
                "control_state_manifest": plan.control_state_manifest_sha256,
                "investigation_report": plan.investigation_report_sha256,
            },
            payloads={
                "remediation.config.resolved.json": config.model_dump_json(indent=2) + "\n",
                "proposal.json": proposal.model_dump_json(indent=2) + "\n",
                "plan.json": plan.model_dump_json(indent=2) + "\n",
            },
        ),
        validator=load_plan,
    )


def export_execution(
    receipt: ExecutionReceipt,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> RemediationArtifactManifest:
    return _export(
        output_dir,
        overwrite=overwrite,
        writer=lambda root: _write_manifest(
            root,
            artifact_type="execution_receipt",
            artifact_id=receipt.execution_id,
            incident_id=receipt.incident_id,
            status=receipt.status,
            payload_sha256=digest(receipt.model_dump(mode="json")),
            bindings={
                "plan": receipt.plan_sha256,
                "approval_snapshot": receipt.approval_snapshot_sha256,
                "before_state_manifest": receipt.before_state_manifest_sha256,
                "after_state_payload": receipt.after_state_payload_sha256,
            },
            payloads={"receipt.json": receipt.model_dump_json(indent=2) + "\n"},
        ),
        validator=load_execution,
    )


def _safe_path(root: Path, relative: str) -> Path:
    if Path(relative).name != relative or relative in {"", ".", ".."}:
        raise RemediationArtifactError("artifact file path is unsafe")
    candidate = (root / relative).resolve()
    resolved = root.resolve()
    if candidate != resolved and resolved not in candidate.parents:
        raise RemediationArtifactError("artifact file path escapes root")
    return candidate


def _layout_issues(root: Path, expected: set[str]) -> list[str]:
    try:
        if not root.is_dir() or root.is_symlink():
            return ["artifact root is not a regular directory"]
        entries = list(root.iterdir())
    except OSError as exc:
        return [f"cannot inspect artifact: {exc}"]
    issues: list[str] = []
    names = {entry.name for entry in entries}
    if names != expected:
        issues.append("artifact contains missing or undeclared paths")
    for entry in entries:
        if entry.is_symlink():
            issues.append(f"artifact contains symbolic link: {entry.name}")
        elif not entry.is_file():
            issues.append(f"artifact contains nested or non-file path: {entry.name}")
    return issues


def _load_manifest(root: Path, expected_files: set[str]) -> RemediationArtifactManifest:
    issues = _layout_issues(root, expected_files | {"manifest.json", "_SUCCESS"})
    if issues:
        raise RemediationArtifactError("; ".join(issues))
    try:
        manifest = RemediationArtifactManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise RemediationArtifactError(f"cannot load artifact manifest: {exc}") from exc
    declared = [item.relative_path for item in manifest.files]
    if len(declared) != len(set(declared)) or set(declared) != expected_files:
        raise RemediationArtifactError("artifact manifest file set is invalid")
    for item in manifest.files:
        target = _safe_path(root, item.relative_path)
        if not target.is_file() or target.is_symlink():
            raise RemediationArtifactError(f"missing regular file: {item.relative_path}")
        if target.stat().st_size != item.byte_size or file_sha256(target) != item.sha256:
            raise RemediationArtifactError(f"file metadata mismatch: {item.relative_path}")
    manifest_path = root / "manifest.json"
    marker = root / "_SUCCESS"
    if marker.read_text(encoding="utf-8").strip() != file_sha256(manifest_path):
        raise RemediationArtifactError("success marker mismatch")
    return manifest


def load_control_state(path: str | Path) -> LoadedControlState:
    root = Path(path)
    manifest = _load_manifest(root, {"state.json"})
    if manifest.artifact_type != "control_state":
        raise RemediationArtifactError("artifact is not a control-state export")
    try:
        state = ControlState.model_validate_json((root / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RemediationArtifactError(f"cannot load control state: {exc}") from exc
    if manifest.payload_sha256 != digest(state.model_dump(mode="json")):
        raise RemediationArtifactError("control-state payload hash mismatch")
    if manifest.artifact_id != state.state_id or manifest.incident_id != state.incident_id:
        raise RemediationArtifactError("control-state identity differs from manifest")
    if manifest.status != "ready" or manifest.bindings:
        raise RemediationArtifactError("control-state status or bindings differ from manifest")
    return LoadedControlState(manifest, state)


def load_plan(path: str | Path) -> LoadedPlan:
    root = Path(path)
    expected = {"remediation.config.resolved.json", "proposal.json", "plan.json"}
    manifest = _load_manifest(root, expected)
    if manifest.artifact_type != "remediation_plan":
        raise RemediationArtifactError("artifact is not a remediation-plan export")
    try:
        config = RemediationConfig.model_validate_json(
            (root / "remediation.config.resolved.json").read_text(encoding="utf-8")
        )
        proposal = RemediationProposal.model_validate_json(
            (root / "proposal.json").read_text(encoding="utf-8")
        )
        plan = RemediationPlan.model_validate_json((root / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RemediationArtifactError(f"cannot load remediation plan: {exc}") from exc
    try:
        verify_plan(plan)
    except RuntimeError as exc:
        raise RemediationArtifactError(str(exc)) from exc
    if manifest.payload_sha256 != digest(plan.model_dump(mode="json")):
        raise RemediationArtifactError("remediation-plan payload hash mismatch")
    if manifest.artifact_id != plan.remediation_id or manifest.incident_id != plan.incident_id:
        raise RemediationArtifactError("remediation-plan identity differs from manifest")
    if manifest.status != plan.status:
        raise RemediationArtifactError("remediation-plan status differs from manifest")
    if plan.proposal_sha256 != digest(proposal.model_dump(mode="json")):
        raise RemediationArtifactError("proposal hash differs from remediation plan")
    expected_bindings = {
        "investigation_manifest": plan.investigation_manifest_sha256,
        "control_state_manifest": plan.control_state_manifest_sha256,
        "investigation_report": plan.investigation_report_sha256,
    }
    if manifest.bindings != expected_bindings:
        raise RemediationArtifactError("remediation-plan bindings differ from manifest")
    return LoadedPlan(manifest, config, proposal, plan)


def load_execution(path: str | Path) -> LoadedExecution:
    root = Path(path)
    manifest = _load_manifest(root, {"receipt.json"})
    if manifest.artifact_type != "execution_receipt":
        raise RemediationArtifactError("artifact is not an execution export")
    try:
        receipt = ExecutionReceipt.model_validate_json(
            (root / "receipt.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise RemediationArtifactError(f"cannot load execution receipt: {exc}") from exc
    unsigned = receipt.model_dump(mode="json")
    supplied = unsigned.pop("receipt_sha256")
    if digest(unsigned) != supplied:
        raise RemediationArtifactError("execution receipt hash is invalid")
    if manifest.payload_sha256 != digest(receipt.model_dump(mode="json")):
        raise RemediationArtifactError("execution payload hash mismatch")
    if manifest.artifact_id != receipt.execution_id or manifest.incident_id != receipt.incident_id:
        raise RemediationArtifactError("execution identity differs from manifest")
    if manifest.status != receipt.status:
        raise RemediationArtifactError("execution status differs from manifest")
    expected_bindings = {
        "plan": receipt.plan_sha256,
        "approval_snapshot": receipt.approval_snapshot_sha256,
        "before_state_manifest": receipt.before_state_manifest_sha256,
        "after_state_payload": receipt.after_state_payload_sha256,
    }
    if manifest.bindings != expected_bindings:
        raise RemediationArtifactError("execution bindings differ from manifest")
    return LoadedExecution(manifest, receipt)


def manifest_sha256(path: str | Path) -> str:
    return str(file_sha256(Path(path) / "manifest.json"))


def validate_control_state(path: str | Path) -> list[str]:
    try:
        load_control_state(path)
    except RemediationArtifactError as exc:
        return [str(exc)]
    return []


def validate_plan(
    path: str | Path,
    *,
    investigation_dir: str | Path | None = None,
    control_state_dir: str | Path | None = None,
    dataset_dir: str | Path | None = None,
    analytics_dir: str | Path | None = None,
    detection_dir: str | Path | None = None,
    impact_dir: str | Path | None = None,
    evidence_dir: str | Path | None = None,
    investigation_config_path: str | Path | None = None,
) -> list[str]:
    try:
        loaded = load_plan(path)
    except RemediationArtifactError as exc:
        return [str(exc)]
    issues: list[str] = []
    report = None
    if investigation_dir is not None:
        try:
            report = replay_investigation(
                investigation_dir,
                dataset_dir=dataset_dir,
                analytics_dir=analytics_dir,
                detection_dir=detection_dir,
                impact_dir=impact_dir,
                evidence_dir=evidence_dir,
                config_path=investigation_config_path,
            )
        except Exception as exc:
            issues.append(f"cannot validate bound investigation: {exc}")
        else:
            if manifest_sha256(investigation_dir) != loaded.plan.investigation_manifest_sha256:
                issues.append("investigation manifest hash differs from remediation plan")
            if report.report_sha256 != loaded.plan.investigation_report_sha256:
                issues.append("investigation report hash differs from remediation plan")
    state = None
    if control_state_dir is not None:
        try:
            state = load_control_state(control_state_dir).state
        except RemediationArtifactError as exc:
            issues.append(f"cannot validate bound control state: {exc}")
        else:
            if manifest_sha256(control_state_dir) != loaded.plan.control_state_manifest_sha256:
                issues.append("control-state manifest hash differs from remediation plan")
    if report is not None and state is not None:
        assert investigation_dir is not None
        assert control_state_dir is not None
        rebuilt = build_plan(
            report,
            state,
            loaded.proposal,
            loaded.config,
            investigation_manifest_sha256=manifest_sha256(investigation_dir),
            control_state_manifest_sha256=manifest_sha256(control_state_dir),
        )
        if rebuilt != loaded.plan:
            issues.append("remediation plan does not match deterministic policy reconstruction")
    return issues


def validate_execution(
    path: str | Path,
    *,
    plan_dir: str | Path | None = None,
    before_state_dir: str | Path | None = None,
    after_state_dir: str | Path | None = None,
) -> list[str]:
    try:
        loaded = load_execution(path)
    except RemediationArtifactError as exc:
        return [str(exc)]
    issues: list[str] = []
    plan = None
    before_state = None
    after_state = None
    if plan_dir is not None:
        try:
            plan = load_plan(plan_dir).plan
        except RemediationArtifactError as exc:
            issues.append(f"cannot validate bound plan: {exc}")
        else:
            if loaded.receipt.plan_sha256 != plan.plan_sha256:
                issues.append("execution receipt plan hash differs from the bound plan")
    if before_state_dir is not None:
        try:
            before_state = load_control_state(before_state_dir).state
        except RemediationArtifactError as exc:
            issues.append(f"cannot validate before state: {exc}")
        else:
            if manifest_sha256(before_state_dir) != loaded.receipt.before_state_manifest_sha256:
                issues.append("before-state manifest hash differs from the execution receipt")
    if after_state_dir is not None:
        try:
            after_state = load_control_state(after_state_dir).state
        except RemediationArtifactError as exc:
            issues.append(f"cannot validate after state: {exc}")
        else:
            if (
                digest(after_state.model_dump(mode="json"))
                != loaded.receipt.after_state_payload_sha256
            ):
                issues.append("after-state payload hash differs from the execution receipt")
    if plan is not None and before_state is not None and after_state is not None:
        try:
            verify_execution_transition(plan, before_state, after_state, loaded.receipt)
        except ExecutionError as exc:
            issues.append(str(exc))
    return issues
