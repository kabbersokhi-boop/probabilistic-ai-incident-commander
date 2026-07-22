"""Export, load, replay, and validate deterministic investigation artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from paic import __version__
from paic.artifacts.lease import artifact_reader
from paic.artifacts.publication import ArtifactPublicationError, AtomicDirectoryPublisher
from paic.investigation.config import InvestigationConfig, load_investigation_config
from paic.investigation.manifest import InvestigationFileManifest, InvestigationManifest
from paic.investigation.models import InvestigationReport, InvestigationRequest, TranscriptEvent
from paic.investigation.probability import verify_report
from paic.investigation.prompts import SUBMIT_TOOL, gateway_name
from paic.simulator.io import file_sha256
from paic.tools.binding import BindingError, bind_sources
from paic.tools.gateway import Gateway
from paic.tools.ledger import canonical
from paic.tools.models import ToolRequest
from paic.tools.policy import authorize


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
    publisher = AtomicDirectoryPublisher(output_dir, overwrite=overwrite)
    try:
        with publisher as staging:
            manifest = _export_investigation_to_root(report, config, request, transcript, staging)
            publisher.commit()
            return manifest
    except ArtifactPublicationError as exc:
        raise InvestigationArtifactError(str(exc)) from exc


def _export_investigation_to_root(
    report: InvestigationReport,
    config: InvestigationConfig,
    request: InvestigationRequest,
    transcript: list[TranscriptEvent],
    root: Path,
) -> InvestigationManifest:
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


@artifact_reader
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


def _transcript_semantic_issues(loaded: LoadedInvestigation) -> list[str]:
    issues: list[str] = []
    provider_round = 0
    trace_index = 0
    previous: TranscriptEvent | None = None
    for event in loaded.transcript:
        if event.event_type == "provider_response":
            provider_round += 1
            previous = event
            continue
        if event.event_type not in {"tool_result", "proposal_rejected", "proposal_accepted"}:
            previous = event
            continue
        if previous is None or previous.event_type != "provider_response":
            issues.append(f"{event.event_type} is not paired with a provider response")
            previous = event
            continue
        calls = previous.payload.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
            issues.append(f"{event.event_type} requires exactly one provider tool call")
            previous = event
            continue
        call = calls[0]
        call_id = call.get("id")
        name = call.get("name")
        arguments = call.get("arguments")
        if event.payload.get("tool_call_id") != call_id:
            issues.append(f"{event.event_type} provider tool-call identity mismatch")
        if event.event_type == "tool_result":
            if trace_index >= len(loaded.report.tool_trace):
                issues.append("transcript has more tool results than the report trace")
                previous = event
                continue
            trace = loaded.report.tool_trace[trace_index]
            trace_index += 1
            if (
                not isinstance(call_id, str)
                or not isinstance(name, str)
                or not isinstance(arguments, dict)
            ):
                issues.append("provider tool call is malformed")
                previous = event
                continue
            tool = gateway_name(name)
            decision = authorize("investigator", tool, "1.0", arguments)
            expected_call_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{loaded.config.investigation_id}:{provider_round}:{call_id}:"
                    f"{tool}:{canonical(arguments)}",
                )
            )
            if tool != trace.tool:
                issues.append("provider tool name differs from report trace")
            if decision.normalized_arguments != trace.arguments:
                issues.append("provider tool arguments differ from normalized report trace")
            execution_status = getattr(trace, "execution_status", "success")
            error_code = getattr(trace, "error_code", None)
            if execution_status == "success":
                if not decision.allowed:
                    issues.append("successful report trace was not policy-authorized")
                if error_code is not None:
                    issues.append("successful report trace contains an error code")
            else:
                expected_error_code = "request_rejected" if decision.allowed else decision.code
                if error_code != expected_error_code:
                    issues.append("report trace error code differs from governed tool outcome")
            if expected_call_id != trace.call_id:
                issues.append("report trace call ID does not reconstruct from provider event")
        elif name != SUBMIT_TOOL:
            issues.append(f"{event.event_type} is not paired with submit_investigation")
        previous = event
    if trace_index != len(loaded.report.tool_trace):
        issues.append("report trace has no matching transcript tool result")
    return issues


@artifact_reader
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
    transcript_traces = [
        event.payload.get("trace")
        for event in loaded.transcript
        if event.event_type == "tool_result"
    ]
    report_traces = [item.model_dump(mode="json") for item in loaded.report.tool_trace]
    if transcript_traces != report_traces:
        issues.append("transcript tool results differ from report trace")
    issues.extend(_transcript_semantic_issues(loaded))
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


def _replay_governed_tool_trace(
    loaded: LoadedInvestigation,
    *,
    dataset_dir: str | Path,
    analytics_dir: str | Path | None,
    detection_dir: str | Path | None,
    impact_dir: str | Path | None,
    evidence_dir: str | Path | None,
) -> None:
    gateway = Gateway(byte_limit=loaded.config.budget.max_tool_result_bytes)
    observed: set[str] = set()
    provider_arguments = [
        event.payload["tool_calls"][0]["arguments"]
        for index, event in enumerate(loaded.transcript)
        if event.event_type == "provider_response"
        and index + 1 < len(loaded.transcript)
        and loaded.transcript[index + 1].event_type == "tool_result"
        and isinstance(event.payload.get("tool_calls"), list)
        and len(event.payload["tool_calls"]) == 1
        and isinstance(event.payload["tool_calls"][0], dict)
        and isinstance(event.payload["tool_calls"][0].get("arguments"), dict)
    ]
    for expected_sequence, trace in enumerate(loaded.report.tool_trace, 1):
        if trace.sequence != expected_sequence:
            raise InvestigationArtifactError("investigation tool trace sequence is invalid")
        if trace.tool not in loaded.config.allowed_tools:
            raise InvestigationArtifactError(
                "investigation trace uses a tool outside resolved policy"
            )
        try:
            call_id = UUID(trace.call_id)
        except ValueError as exc:
            raise InvestigationArtifactError(
                f"invalid governed tool call ID at trace sequence {trace.sequence}"
            ) from exc
        replayed = gateway.invoke(
            ToolRequest(
                tool=trace.tool,
                incident_id=loaded.report.incident_id,
                role="investigator",
                arguments=(
                    provider_arguments[expected_sequence - 1]
                    if expected_sequence <= len(provider_arguments)
                    else trace.arguments
                ),
                dataset_dir=str(dataset_dir),
                analytics_dir=None if analytics_dir is None else str(analytics_dir),
                detection_dir=None if detection_dir is None else str(detection_dir),
                impact_dir=None if impact_dir is None else str(impact_dir),
                evidence_dir=None if evidence_dir is None else str(evidence_dir),
                call_id=call_id,
            )
        )
        expected = (
            trace.call_id,
            trace.execution_status,
            trace.arguments,
            trace.result_sha256,
            trace.evidence_record_ids,
            trace.truncated,
            trace.error_code,
        )
        actual = (
            replayed.call_id,
            replayed.execution_status,
            replayed.normalized_arguments,
            replayed.result_sha256,
            replayed.evidence_record_ids,
            replayed.truncated,
            replayed.error.code if replayed.error else None,
        )
        if actual != expected:
            raise InvestigationArtifactError(
                f"governed tool semantic replay mismatch at trace sequence {trace.sequence}"
            )
        if (
            replayed.execution_status == "success"
            and replayed.source_manifest_hashes != loaded.report.source_manifest_hashes
        ):
            raise InvestigationArtifactError(
                f"governed tool source binding mismatch at trace sequence {trace.sequence}"
            )
        if replayed.execution_status == "success":
            observed.update(replayed.evidence_record_ids)
    if sorted(observed) != loaded.report.observed_evidence_record_ids:
        raise InvestigationArtifactError(
            "observed evidence does not equal successful governed tool output"
        )


def replay_investigation(
    path: str | Path,
    *,
    dataset_dir: str | Path | None = None,
    analytics_dir: str | Path | None = None,
    detection_dir: str | Path | None = None,
    impact_dir: str | Path | None = None,
    evidence_dir: str | Path | None = None,
    config_path: str | Path | None = None,
    artifact_only: bool = False,
) -> InvestigationReport:
    structural_issues = validate_investigation(path)
    if structural_issues:
        raise InvestigationArtifactError(
            "investigation artifact validation failed before replay: "
            + "; ".join(structural_issues)
        )
    loaded = load_investigation(path)
    optional_presence = loaded.request_receipt.get("source_presence")
    expected_optional = {"analytics", "detection", "impact", "evidence"}
    if not isinstance(optional_presence, dict) or set(optional_presence) != expected_optional:
        raise InvestigationArtifactError(
            "investigation request receipt has invalid source presence"
        )
    source_presence = {"dataset": True, **optional_presence}
    supplied = {
        "dataset": dataset_dir,
        "analytics": analytics_dir,
        "detection": detection_dir,
        "impact": impact_dir,
        "evidence": evidence_dir,
    }
    if artifact_only:
        if any(value is not None for value in supplied.values()) or config_path is not None:
            raise InvestigationArtifactError(
                "artifact-only replay does not accept source directories or config"
            )
        issues: list[str] = []
    else:
        if config_path is None:
            raise InvestigationArtifactError(
                "authoritative replay requires the original investigation config"
            )
        try:
            external_config = load_investigation_config(config_path)
        except Exception as exc:
            raise InvestigationArtifactError(
                f"cannot load authoritative investigation config: {exc}"
            ) from exc
        if external_config != loaded.config:
            raise InvestigationArtifactError("authoritative investigation config mismatch")
        missing = [
            name
            for name, present in source_presence.items()
            if present and supplied.get(name) is None
        ]
        unexpected = [
            name
            for name, value in supplied.items()
            if value is not None and not bool(source_presence.get(name))
        ]
        if missing or unexpected:
            raise InvestigationArtifactError(
                "authoritative replay source set mismatch: "
                f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
            )
        assert dataset_dir is not None
        issues = validate_investigation(
            path,
            dataset_dir=dataset_dir,
            analytics_dir=analytics_dir,
            detection_dir=detection_dir,
            impact_dir=impact_dir,
            evidence_dir=evidence_dir,
        )
        if not issues:
            _replay_governed_tool_trace(
                loaded,
                dataset_dir=dataset_dir,
                analytics_dir=analytics_dir,
                detection_dir=detection_dir,
                impact_dir=impact_dir,
                evidence_dir=evidence_dir,
            )
    if issues:
        raise InvestigationArtifactError(
            "investigation artifact validation failed before replay: " + "; ".join(issues)
        )
    return loaded.report
