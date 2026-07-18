# Governed Remediation and Human Approval

This capability turns a validated, concluded investigation into a tightly bounded remediation workflow. A model or operator may propose an action, but ordinary code remains the sole authority for policy, approval, authorization, state mutation, receipts, and rollback construction.

## Safety boundary

The initial executor is a **simulated control plane**. It does not call Kubernetes, cloud APIs, CI/CD systems, feature-flag vendors, configuration stores, shells, or remote services. The only executable actions are:

- `deployment.rollback`
- `feature_flag.set`
- `configuration.restore`

Every action is reversible and carries exact current-state preconditions. An action is rejected if its target state has changed since the plan was built.

## Workflow

```text
Validated investigation
        |
        v
Untrusted strict proposal
        |
        v
Deterministic policy assessment
        |
        +--> denied
        |
        v
Immutable remediation plan
        |
        v
Append-only human approval ledger
        |
        v
Short-lived HMAC approval token
        |
        v
Atomic simulated execution
        |
        +--> new control-state artifact
        +--> immutable execution receipt
        +--> fresh inverse rollback proposal
```

## Investigation gate

Remediation is denied unless all configured thresholds pass:

- the investigation status is `concluded`;
- the proposal is bound to the exact report hash and selected hypothesis;
- confidence, selected posterior, posterior margin, and entropy satisfy policy;
- the selected hypothesis has enough supporting evidence;
- every action cites only observed evidence supporting that selected hypothesis.

An abstained or failed investigation cannot produce an executable plan.

## Risk and blast radius

Risk is deterministic. The action kind supplies a base risk, while blast radius may escalate it. Critical-risk actions are denied by the reference policy. High-risk plans require two independent approvals from distinct approver groups.

The requester cannot approve their own plan. Any rejection vetoes execution. Decisions outside the plan validity window are invalid.

## Approval token

Once approval quorum is reached, PAIC issues a short-lived HMAC token bound to:

- the exact plan hash;
- incident ID;
- ordered action IDs;
- the validated approval-ledger snapshot;
- issuance and expiry times;
- a one-time nonce.

The secret is loaded from `PAIC_APPROVAL_SECRET` by default and is never persisted in plan, approval, state, or execution artifacts. The token itself is temporary and is not part of an exported artifact. Execution stores only token and nonce hashes.

The reference HMAC mechanism proves the software boundary, not enterprise identity. Cloud deployment should replace asserted approver identities with an authenticated identity provider and managed key service.

## Artifact integrity

Control-state, remediation-plan, and execution artifacts are flat, closed-world exports. Validation rejects:

- missing or undeclared files;
- nested directories;
- symbolic links;
- unsafe paths;
- duplicate manifest entries;
- file hash or byte-size changes;
- success-marker changes;
- semantic plan or receipt hash changes;
- source-binding changes.

A complete plan validation can reconstruct the deterministic plan from the original investigation, state, proposal, and resolved policy.

## Atomic execution

All action preconditions are checked and all state changes are computed in memory before output is written. The source state artifact is immutable. A successful execution produces a new generation of the control-state artifact.

The new state records consumed token-nonce hashes and executed plan hashes. Reusing an approval token or executing the same plan again on that state lineage is rejected.

## Rollback

Execution receipts contain a deterministic inverse action for every executed action. PAIC can generate a fresh rollback proposal in reverse action order. The rollback is **not executed automatically**: it must pass the same policy, approval, token, and execution workflow as any other remediation.

## Example

```bash
read -s -p "Temporary approval secret: " PAIC_APPROVAL_SECRET
echo
export PAIC_APPROVAL_SECRET

paic remediate state build \
  --input .artifacts/remediation-state-input.json \
  --output-dir .artifacts/remediation-state

paic remediate plan build \
  --investigation-dir .artifacts/investigation-smoke \
  --state-dir .artifacts/remediation-state \
  --proposal .artifacts/remediation-proposal.json \
  --config configs/remediation/smoke.yaml \
  --output-dir .artifacts/remediation-plan

paic remediate approval record \
  --plan-dir .artifacts/remediation-plan \
  --approval-dir .artifacts/remediation-approval \
  --decision .artifacts/remediation-decision-one.json

paic remediate token issue \
  --plan-dir .artifacts/remediation-plan \
  --approval-dir .artifacts/remediation-approval \
  --at 2026-07-18T00:07:00+00:00 \
  --output .artifacts/remediation-approval.token

paic remediate execute \
  --plan-dir .artifacts/remediation-plan \
  --state-dir .artifacts/remediation-state \
  --approval-dir .artifacts/remediation-approval \
  --token-file .artifacts/remediation-approval.token \
  --request .artifacts/remediation-execution-request.json \
  --output-state-dir .artifacts/remediation-state-after \
  --output-dir .artifacts/remediation-execution

unset PAIC_APPROVAL_SECRET
rm -f .artifacts/remediation-approval.token
```

## Deliberate limitations

- No production infrastructure is mutated.
- Approver identity is asserted input, not SSO-backed identity.
- HMAC secrets are process-environment inputs, not managed-key-service keys.
- Recovery is not inferred from action completion. Statistical recovery verification and automatic reopening belong to the next capability.
- Docker and cloud deployment remain later capability milestones and must not be pulled into this implementation.
