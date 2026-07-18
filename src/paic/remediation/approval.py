"""Append-only approval decisions and short-lived HMAC authorization tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from paic.remediation.config import ApproverIdentity, RemediationConfig
from paic.remediation.models import (
    ApprovalAttestation,
    ApprovalDecision,
    ApprovalLedgerRecord,
    ApprovalStatus,
    ApprovalTokenClaims,
    RemediationPlan,
)
from paic.tools.ledger import canonical, digest

try:  # pragma: no cover - available on supported Linux/macOS runners
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class ApprovalError(RuntimeError):
    pass


def _registered_identity(config: RemediationConfig, approver_id: str) -> ApproverIdentity:
    for identity in config.approval.approver_registry:
        if identity.approver_id == approver_id:
            return identity
    raise ApprovalError("approver identity is not in the trusted registry")


def _attestation_payload(decision: ApprovalDecision, group: str, key_id: str, nonce: str) -> bytes:
    return canonical(
        {
            "schema_version": "1.0",
            "plan_sha256": decision.plan_sha256,
            "approver_id": decision.approver_id,
            "approver_group": group,
            "decision": decision.decision,
            "decided_at": decision.decided_at,
            "nonce": nonce,
            "key_id": key_id,
        }
    ).encode("utf-8")


def attest_decision(
    decision: ApprovalDecision,
    config: RemediationConfig,
    *,
    secret: bytes,
    nonce: str | None = None,
) -> ApprovalDecision:
    """Attach a per-identity HMAC attestation; group input is never trusted."""

    identity = _registered_identity(config, decision.approver_id)
    if len(secret) < config.approval.minimum_secret_bytes:
        raise ApprovalError("approval attestation secret is shorter than the configured minimum")
    issued_nonce = nonce or secrets.token_urlsafe(24)
    signature = hmac.new(
        secret,
        _attestation_payload(decision, identity.approver_group, identity.key_id, issued_nonce),
        hashlib.sha256,
    ).hexdigest()
    return decision.model_copy(
        update={
            "approver_group": identity.approver_group,
            "attestation": ApprovalAttestation(
                key_id=identity.key_id, nonce=issued_nonce, signature=signature
            ),
        }
    )


def _verify_attestation(
    decision: ApprovalDecision, config: RemediationConfig, seen_nonces: set[str]
) -> None:
    if not config.approval.approver_registry:
        raise ApprovalError("approval identity registry is required")
    identity = _registered_identity(config, decision.approver_id)
    attestation = decision.attestation
    if attestation is None or attestation.key_id != identity.key_id:
        raise ApprovalError("approval attestation is missing or uses an unknown key")
    if decision.approver_group != identity.approver_group:
        raise ApprovalError("approval group differs from the trusted registry")
    if attestation.nonce in seen_nonces:
        raise ApprovalError("approval attestation nonce is duplicated")
    value = os.environ.get(identity.key_env)
    if value is None or len(value.encode("utf-8")) < config.approval.minimum_secret_bytes:
        raise ApprovalError("approval attestation key is unavailable")
    expected = hmac.new(
        value.encode("utf-8"),
        _attestation_payload(decision, identity.approver_group, identity.key_id, attestation.nonce),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(attestation.signature, expected):
        raise ApprovalError("approval attestation signature is invalid")
    seen_nonces.add(attestation.nonce)


@contextmanager
def _locked(path: Path) -> Iterator[None]:
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


class ApprovalLedger:
    def __init__(self, directory: str | Path):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "decisions.jsonl"
        self.lock_path = self.root / ".approval.lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the approval ledger lock for a read/commit decision boundary.

        Execution always obtains the state-store lock before this lock.  The
        ledger itself never obtains the state-store lock, which gives rejection
        and execution a single observable ordering without deadlocks.
        """

        with _locked(self.lock_path):
            yield

    def records(self) -> list[ApprovalLedgerRecord]:
        if not self.path.exists():
            return []
        records: list[ApprovalLedgerRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                records.append(ApprovalLedgerRecord.model_validate_json(line))
            except ValueError as exc:
                raise ApprovalError("approval ledger contains an invalid record") from exc
        return records

    def validate(self) -> list[ApprovalLedgerRecord]:
        records = self.records()
        previous = "0" * 64
        seen_hashes: set[str] = set()
        for expected, record in enumerate(records, 1):
            if record.sequence != expected or record.previous_record_sha256 != previous:
                raise ApprovalError("approval ledger sequence or chain is invalid")
            if record.record_sha256 in seen_hashes:
                raise ApprovalError("approval ledger record hash is duplicated")
            if record.decision_sha256 != digest(record.decision.model_dump(mode="json")):
                raise ApprovalError("approval ledger decision hash is invalid")
            unsigned = record.model_dump(mode="json")
            supplied = unsigned.pop("record_sha256")
            if digest(unsigned) != supplied:
                raise ApprovalError("approval ledger record hash is invalid")
            seen_hashes.add(record.record_sha256)
            previous = record.record_sha256
        return records

    def append(self, decision: ApprovalDecision) -> ApprovalLedgerRecord:
        with _locked(self.lock_path):
            records = self.validate()
            if any(item.decision.approver_id == decision.approver_id for item in records):
                raise ApprovalError("an approver may record only one decision per approval ledger")
            previous = records[-1].record_sha256 if records else "0" * 64
            values = {
                "sequence": len(records) + 1,
                "previous_record_sha256": previous,
                "decision": decision,
                "decision_sha256": digest(decision.model_dump(mode="json")),
            }
            record_sha256 = digest(
                ApprovalLedgerRecord.model_validate(
                    {**values, "record_sha256": "0" * 64}
                ).model_dump(mode="json", exclude={"record_sha256"})
            )
            record = ApprovalLedgerRecord.model_validate({**values, "record_sha256": record_sha256})
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(canonical(record.model_dump(mode="json")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return record


def evaluate_approval(
    plan: RemediationPlan,
    ledger: ApprovalLedger,
    config: RemediationConfig,
    *,
    at: datetime,
) -> ApprovalStatus:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ApprovalError("approval evaluation time must include a timezone offset")
    records = ledger.validate()
    decisions: list[ApprovalDecision] = []
    seen_nonces: set[str] = set()
    for record in records:
        decision = record.decision
        _verify_attestation(decision, config, seen_nonces)
        if decision.plan_sha256 != plan.plan_sha256:
            raise ApprovalError("approval decision is bound to a different plan")
        if decision.decided_at < plan.requested_at or decision.decided_at >= plan.expires_at:
            raise ApprovalError("approval decision falls outside the plan validity window")
        if decision.decided_at > at:
            raise ApprovalError("approval decision occurs after the evaluation time")
        if (
            not config.approval.allow_requester_approval
            and decision.approver_id == plan.requested_by
        ):
            raise ApprovalError("the remediation requester may not approve the same plan")
        decisions.append(decision)

    approvals = [item for item in decisions if item.decision == "approve"]
    rejections = [item for item in decisions if item.decision == "reject"]
    approver_ids = [item.approver_id for item in approvals]
    groups = [item.approver_group for item in approvals]
    if len(approver_ids) != len(set(approver_ids)):
        raise ApprovalError("approval identities must be unique")
    if (
        plan.risk_level == "high"
        and config.approval.require_distinct_groups_for_high_risk
        and len(set(groups)) < min(plan.required_approvals, len(approvals))
    ):
        raise ApprovalError("high-risk approvals must come from distinct groups")

    if plan.status == "denied" or rejections:
        status: Literal["pending", "approved", "rejected", "expired"] = "rejected"
    elif at >= plan.expires_at:
        status = "expired"
    elif len(approvals) >= plan.required_approvals:
        status = "approved"
    else:
        status = "pending"
    # The append-only ledger retains event order in its hash chain, while an
    # authorization snapshot represents the approval decision set.  Sorting
    # canonical decisions prevents harmless submission-order differences from
    # changing a token's binding.
    snapshot = digest(
        sorted(
            (decision.model_dump(mode="json") for decision in decisions),
            key=canonical,
        )
    )
    return ApprovalStatus(
        plan_sha256=plan.plan_sha256,
        status=status,
        required_approvals=plan.required_approvals,
        approval_count=len(approvals),
        rejection_count=len(rejections),
        approver_ids=sorted(approver_ids),
        approver_groups=sorted(groups),
        evaluated_at=at,
        approval_snapshot_sha256=snapshot,
    )


def load_approval_secret(config: RemediationConfig) -> bytes:
    value = os.environ.get(config.approval.secret_env)
    if value is None:
        raise ApprovalError(f"environment variable {config.approval.secret_env} is not set")
    secret = value.encode("utf-8")
    if len(secret) < config.approval.minimum_secret_bytes:
        raise ApprovalError("approval secret is shorter than the configured minimum")
    return secret


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
        # Reject alternate encodings that differ only in unused trailing bits.
        # HMAC verification is over decoded bytes, so accepting them would make
        # a modified token text indistinguishable from the issued token.
        if _b64encode(decoded) != value:
            raise ValueError("non-canonical base64url")
        return decoded
    except (ValueError, TypeError) as exc:
        raise ApprovalError("approval token signature or encoding is invalid") from exc


def issue_token(
    plan: RemediationPlan,
    status: ApprovalStatus,
    config: RemediationConfig,
    *,
    at: datetime,
    secret: bytes,
    nonce: str | None = None,
    token_id: str | None = None,
) -> str:
    if status.status != "approved":
        raise ApprovalError("approval token requires an approved plan")
    if status.plan_sha256 != plan.plan_sha256:
        raise ApprovalError("approval status is bound to a different plan")
    if at >= plan.expires_at:
        raise ApprovalError("remediation plan has expired")
    if len(secret) < config.approval.minimum_secret_bytes:
        raise ApprovalError("approval secret is shorter than the configured minimum")
    expires_at = min(
        plan.expires_at,
        at + timedelta(minutes=config.approval.token_ttl_minutes),
    )
    claims = ApprovalTokenClaims(
        token_id=token_id or f"approval-{secrets.token_hex(12)}",
        plan_sha256=plan.plan_sha256,
        incident_id=plan.incident_id,
        action_ids=[item.action_id for item in plan.actions],
        approval_snapshot_sha256=status.approval_snapshot_sha256,
        issued_at=at,
        expires_at=expires_at,
        nonce=nonce or secrets.token_urlsafe(24),
    )
    payload = canonical(claims.model_dump(mode="json")).encode("utf-8")
    signature = hmac.new(secret, payload, hashlib.sha256).digest()
    return _b64encode(payload) + "." + _b64encode(signature)


def verify_token(
    token: str,
    plan: RemediationPlan,
    status: ApprovalStatus,
    config: RemediationConfig,
    *,
    at: datetime,
    secret: bytes,
) -> ApprovalTokenClaims:
    if len(secret) < config.approval.minimum_secret_bytes:
        raise ApprovalError("approval secret is shorter than the configured minimum")
    parts = token.split(".")
    if len(parts) != 2:
        raise ApprovalError("approval token structure is invalid")
    payload = _b64decode(parts[0])
    supplied = _b64decode(parts[1])
    expected = hmac.new(secret, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied, expected):
        raise ApprovalError("approval token signature is invalid")
    try:
        raw = json.loads(payload, object_pairs_hook=_unique_object)
        claims = ApprovalTokenClaims.model_validate(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ApprovalError("approval token payload is invalid") from exc
    if status.status != "approved":
        raise ApprovalError("approval status is no longer approved")
    if claims.plan_sha256 != plan.plan_sha256 or claims.incident_id != plan.incident_id:
        raise ApprovalError("approval token is bound to a different plan")
    if claims.approval_snapshot_sha256 != status.approval_snapshot_sha256:
        raise ApprovalError("approval token is bound to a stale approval snapshot")
    if claims.action_ids != [item.action_id for item in plan.actions]:
        raise ApprovalError("approval token action set differs from the plan")
    if at < claims.issued_at or at >= claims.expires_at or at >= plan.expires_at:
        raise ApprovalError("approval token is outside its validity window")
    return claims


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
