"""Export, load, replay, and validate deterministic investigation artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paic import __version__
from paic.investigation.config import InvestigationConfig
from paic.investigation.manifest import InvestigationFileManifest, InvestigationManifest
from paic.investigation.models import InvestigationReport, InvestigationRequest, TranscriptEvent
from paic.investigation.probability import verify_report
from paic.simulator.io import file_sha256
from paic.tools.binding import BindingError, bind_sources
from paic.tools.ledger import canonical


class InvestigationArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedInvestigation:
    manifest: InvestigationManifest
    config: InvestigationConfig
    report: InvestigationReport
    transcript: list[TranscriptEvent]
    request_receipt: dict[str, Any]


def _request_receipt(request: InvestigationRequest) -> dict[str, Any]:
    return {
        "incident_id": request.incident_id,
        "question": request.question,
        "role": request.role,
        "source_presence": {
            "analytics": request.analytics_dir is not None,
            "detection": request.detection_dir is not None,
            "impact": request.impact_dir is not None,
            "evidence": request.evidence_dir is not None,
        },
    }


def export_investigation(
    report: InvestigationReport,
    config: InvestigationConfig,
    request: InvestigationRequest,
    transcript: list[TranscriptEvent],
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> InvestigationManifest:
    root = Path(output_dir)
    if root.exists():
        if not overwrite:
            raise InvestigationArtifactError(f"output directory already exists: {root}")
        if root.is_file():
            raise InvestigationArtifactError(f"output path is a file: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    payloads: dict[str, str] = {
        "investigation.config.resolved.json": json.dumps(
            config.model_dump(mode="json"), indent=2, sort_keys=True
        )
        + "\n",
        "request.receipt.json": json.dumps(_request_receipt(request), indent=2, sort_keys=True)
        + "\n",
        "report.json": report.model_dump_json(indent=2) + "\n",
        "transcript.jsonl": "".join(
            canonical(item.model_dump(mode="json")) + "\n" for item in transcript
        ),
    }
    files: list[InvestigationFileManifest] = []
    for relative, content in payloads.items():
        path = root / relative
        path.write_text(content, encoding="utf-8")
        files.append(
            InvestigationFileManifest(
                relative_path=relative,
                byte_size=path.stat().st_size,
                sha256=file_sha256(path),
            )
        )
    manifest = InvestigationManifest(
        schema_version="1.0",
        investigation_id=report.investigation_id,
        incident_id=report.incident_id,
        generator_version=__version__,
        status=report.status,
        selected_hypothesis_id=report.selected_hypothesis_id,
        report_sha256=report.report_sha256,
        source_manifest_hashes=report.source_manifest_hashes,
        model_attempt_count=len(report.model_attempts),
        tool_call_count=len(report.tool_trace),
        transcript_event_count=len(transcript),
        files=sorted(files, key=lambda item: item.relative_path),
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (root / "_SUCCESS").write_text(file_sha256(manifest_path) + "\n", encoding="utf-8")
    return manifest


def _safe_path(root: Path, relative: str) -> Path:
    if Path(relative).name != relative or relative in {"", ".", ".."}:
        raise InvestigationArtifactError("artifact file path is unsafe")
    candidate = (root / relative).resolve()
    resolved = root.resolve()
    if candidate != resolved and resolved not in candidate.parents:
        raise InvestigationArtifactError("artifact file path escapes root")
    return candidate


_PAYLOAD_FILES = {
    "investigation.config.resolved.json",
    "request.receipt.json",
    "report.json",
    "transcript.jsonl",
}
_ARTIFACT_FILES = _PAYLOAD_FILES | {"manifest.json", "_SUCCESS"}


def _closed_world_issues(root: Path) -> list[str]:
    """Reject anything other than the six flat, regular export files."""

    issues: list[str] = []
    try:
        if not root.is_dir() or root.is_symlink():
            return ["investigation artifact root is not a regular directory"]
        entries = list(root.iterdir())
    except OSError as exc:
        return [f"cannot inspect investigation artifact: {exc}"]
    names = {entry.name for entry in entries}
    if names != _ARTIFACT_FILES:
        issues.append("investigation artifact contains missing or undeclared paths")
    for entry in entries:
        if entry.is_symlink():
            issues.append(f"investigation artifact contains symbolic link: {entry.name}")
        elif not entry.is_file():
            issues.append(f"investigation artifact contains nested or non-file path: {entry.name}")
    return issues


def load_investigation(path: str | Path) -> LoadedInvestigation:
    root = Path(path)
    layout_issues = _closed_world_issues(root)
    if layout_issues:
        raise InvestigationArtifactError("; ".join(layout_issues))
    try:
        manifest = InvestigationManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        config = InvestigationConfig.model_validate_json(
            (root / "investigation.config.resolved.json").read_text(encoding="utf-8")
        )
        report = InvestigationReport.model_validate_json(
            (root / "report.json").read_text(encoding="utf-8")
        )
        request_receipt = json.loads((root / "request.receipt.json").read_text(encoding="utf-8"))
        transcript = [
            TranscriptEvent.model_validate_json(line)
            for line in (root / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise InvestigationArtifactError(f"cannot load investigation artifact: {exc}") from exc
    return LoadedInvestigation(manifest, config, report, transcript, request_receipt)


def validate_investigation(
    path: str | Path,
    *,
    dataset_dir: str | Path | None = None,
    analytics_dir: str | Path | None = None,
    detection_dir: str | Path | None = None,
    impact_dir: str | Path | None = None,
    evidence_dir: str | Path | None = None,
) -> list[str]:
    root = Path(path)
    issues: list[str] = []
    issues.extend(_closed_world_issues(root))
    if issues:
        return issues
    try:
        loaded = load_investigation(root)
    except InvestigationArtifactError as exc:
        return [str(exc)]
    declared_paths = [item.relative_path for item in loaded.manifest.files]
    declared_files = set(declared_paths)
    if len(declared_files) != len(declared_paths):
        issues.append("investigation manifest contains duplicate file paths")
    if declared_files != _PAYLOAD_FILES:
        issues.append("investigation manifest file set is incomplete or unexpected")
    for item in loaded.manifest.files:
        try:
            target = _safe_path(root, item.relative_path)
        except InvestigationArtifactError as exc:
            issues.append(str(exc))
            continue
        if not target.is_file():
            issues.append(f"missing file: {item.relative_path}")
            continue
        if target.stat().st_size != item.byte_size or file_sha256(target) != item.sha256:
            issues.append(f"file metadata mismatch: {item.relative_path}")
    manifest_path = root / "manifest.json"
    marker = root / "_SUCCESS"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != file_sha256(
        manifest_path
    ):
        issues.append("success marker mismatch")
    if loaded.report.report_sha256 != loaded.manifest.report_sha256:
        issues.append("report hash differs from manifest")
    if (
        loaded.report.investigation_id != loaded.manifest.investigation_id
        or loaded.report.incident_id != loaded.manifest.incident_id
        or loaded.report.status != loaded.manifest.status
        or loaded.report.selected_hypothesis_id != loaded.manifest.selected_hypothesis_id
    ):
        issues.append("report identity or status differs from manifest")
    if len(loaded.report.model_attempts) != loaded.manifest.model_attempt_count:
        issues.append("model attempt count differs from manifest")
    if len(loaded.report.tool_trace) != loaded.manifest.tool_call_count:
        issues.append("tool call count differs from manifest")
    if loaded.report.source_manifest_hashes != loaded.manifest.source_manifest_hashes:
        issues.append("report source hashes differ from manifest")
    receipt = loaded.request_receipt
    if not isinstance(receipt, dict) or {
        "dataset_dir",
        "analytics_dir",
        "detection_dir",
        "impact_dir",
        "evidence_dir",
        "audit_dir",
    }.intersection(receipt):
        issues.append("request receipt is malformed or contains source paths")
    elif (
        receipt.get("incident_id") != loaded.report.incident_id
        or receipt.get("question") != loaded.report.question
        or receipt.get("role") != "investigator"
    ):
        issues.append("request receipt differs from report")
    try:
        verify_report(loaded.report, loaded.config.decision)
    except ValueError as exc:
        issues.append(str(exc))
    previous = "0" * 64
    for expected, event in enumerate(loaded.transcript, 1):
        base = {
            "sequence": event.sequence,
            "event_type": event.event_type,
            "payload": event.payload,
            "previous_event_sha256": event.previous_event_sha256,
        }
        expected_hash = hashlib.sha256(canonical(base).encode()).hexdigest()
        if (
            event.sequence != expected
            or event.previous_event_sha256 != previous
            or event.event_sha256 != expected_hash
        ):
            issues.append("transcript hash chain is invalid")
            break
        previous = event.event_sha256
    if len(loaded.transcript) != loaded.manifest.transcript_event_count:
        issues.append("transcript event count differs from manifest")
    accepted = [event for event in loaded.transcript if event.event_type == "proposal_accepted"]
    if (
        len(accepted) != 1
        or accepted[0].payload.get("report_sha256") != loaded.report.report_sha256
    ):
        issues.append("transcript does not contain exactly one matching accepted proposal")
    if dataset_dir is not None:
        try:
            bound = bind_sources(
                dataset_dir,
                analytics_dir,
                detection_dir,
                impact_dir,
                evidence_dir,
            )
        except BindingError as exc:
            issues.append(str(exc))
        else:
            if bound.hashes != loaded.manifest.source_manifest_hashes:
                issues.append("source manifest hashes do not match investigation")
    return issues


def replay_investigation(path: str | Path) -> InvestigationReport:
    issues = validate_investigation(path)
    if issues:
        raise InvestigationArtifactError(
            "investigation artifact validation failed before replay: " + "; ".join(issues)
        )
    loaded = load_investigation(path)
    return loaded.report
