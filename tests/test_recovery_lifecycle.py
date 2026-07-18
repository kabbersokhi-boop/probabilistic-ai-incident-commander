from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from paic.recovery.lifecycle import RecoveryStateStore, RecoveryStateStoreError, transition_state
from test_recovery_unit import config, report, sha


def test_recovered_then_severe_regression_reopens() -> None:
    from paic.recovery.models import RecoveryLifecycleState

    initial = RecoveryLifecycleState(
        incident_id="incident-smoke",
        execution_receipt_sha256=sha("receipt"),
        generation=0,
    )
    recovered, trigger = transition_state(initial, report(True, 6), config())
    assert recovered.status == "recovered"
    assert trigger == "recovery_verified"
    reopened, trigger = transition_state(recovered, report(False, 12), config())
    assert reopened.status == "reopened"
    assert trigger == "severe_guardrail_regression"


def test_two_nonsevere_failures_after_recovery_reopen() -> None:
    from paic.recovery.models import RecoveryLifecycleState

    initial = RecoveryLifecycleState(
        incident_id="incident-smoke",
        execution_receipt_sha256=sha("receipt"),
        generation=0,
    )
    recovered, _ = transition_state(initial, report(True, 6), config(reopen=2))
    value = report(False, 12).model_copy(update={"severe_guardrail_breach": False})
    # Keep the guardrail evaluation non-severe as well, then recompute through model construction.
    metrics = [
        item.model_copy(update={"severe_breach": False}) for item in value.metric_evaluations
    ]
    from paic.recovery.engine import digest
    from paic.recovery.models import RecoveryReport

    payload = value.model_dump(mode="json")
    payload["metric_evaluations"] = [item.model_dump(mode="json") for item in metrics]
    payload["severe_guardrail_breach"] = False
    payload.pop("report_sha256")
    first = RecoveryReport.model_validate({**payload, "report_sha256": digest(payload)})
    payload["observation_set_id"] = "later-failure"
    payload["evaluated_at"] = "2026-01-02T02:00:00Z"
    payload["report_sha256"] = digest({k: v for k, v in payload.items() if k != "report_sha256"})
    second = RecoveryReport.model_validate(payload)
    monitoring, _ = transition_state(recovered, first, config(reopen=2))
    assert monitoring.status == "monitoring"
    reopened, trigger = transition_state(monitoring, second, config(reopen=2))
    assert reopened.status == "reopened"
    assert trigger == "sustained_recovery_regression"


def test_store_is_exactly_once_and_validates_chain(tmp_path: Path) -> None:
    store = RecoveryStateStore(tmp_path / "store")
    store.initialize("incident-smoke", sha("receipt"))
    state, event = store.apply(report(True, 6), config())
    assert state.generation == 1
    assert event.to_status == "recovered"
    assert store.validate() == []
    with pytest.raises(RecoveryStateStoreError, match="already been applied"):
        store.apply(report(True, 6), config())


def test_concurrent_apply_has_one_winner(tmp_path: Path) -> None:
    store = RecoveryStateStore(tmp_path / "store")
    store.initialize("incident-smoke", sha("receipt"))
    candidate = report(True, 6)

    def apply_once() -> str:
        try:
            store.apply(candidate, config())
            return "success"
        except RecoveryStateStoreError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: apply_once(), range(2)))
    assert sorted(results) == ["rejected", "success"]
    assert store.current().generation == 1
    assert store.validate() == []


def test_orphan_prepared_generation_is_inert(tmp_path: Path) -> None:
    store = RecoveryStateStore(tmp_path / "store")
    store.initialize("incident-smoke", sha("receipt"))
    orphan = store.generations / ".prepare-crash"
    orphan.mkdir()
    (orphan / "garbage").write_text("partial", encoding="utf-8")
    assert store.current().generation == 0
    assert store.validate() == []


def test_pointer_failure_leaves_inert_generation_and_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import paic.recovery.lifecycle as lifecycle

    store = RecoveryStateStore(tmp_path / "store")
    store.initialize("incident-smoke", sha("receipt"))
    original = lifecycle._atomic_json
    calls = 0

    def fail_once(path: Path, value: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RecoveryStateStoreError("injected pointer failure")
        original(path, value)

    monkeypatch.setattr(lifecycle, "_atomic_json", fail_once)
    with pytest.raises(RecoveryStateStoreError, match="injected"):
        store.apply(report(True, 6), config())
    assert store.current().generation == 0
    state, _ = store.apply(report(True, 6), config())
    assert state.generation == 1
    assert store.validate() == []


def test_lifecycle_rejects_uninitialized_mismatches_and_stale_reports(tmp_path: Path) -> None:
    store = RecoveryStateStore(tmp_path / "store")
    with pytest.raises(RecoveryStateStoreError, match="not initialized"):
        store.current()
    assert store.validate() == ["recovery-state store is not initialized"]
    store.initialize("incident-smoke", sha("receipt"))
    with pytest.raises(RecoveryStateStoreError, match="another incident execution"):
        store.initialize("other-incident", sha("other-receipt"))
    candidate = report(True, 6)
    with pytest.raises(RecoveryStateStoreError, match="another incident"):
        transition_state(
            store.current().model_copy(update={"incident_id": "other-incident"}),
            candidate,
            config(),
        )
    with pytest.raises(RecoveryStateStoreError, match="another remediation"):
        transition_state(
            store.current().model_copy(update={"execution_receipt_sha256": sha("other")}),
            candidate,
            config(),
        )
    recovered, _ = transition_state(store.current(), candidate, config())
    with pytest.raises(RecoveryStateStoreError, match="already been applied"):
        transition_state(recovered, candidate, config())


def test_reopened_lifecycle_remains_reopened(tmp_path: Path) -> None:
    from paic.recovery.models import RecoveryLifecycleState

    previous = RecoveryLifecycleState(
        incident_id="incident-smoke",
        execution_receipt_sha256=sha("receipt"),
        generation=1,
        status="reopened",
        ever_recovered=True,
    )
    next_state, trigger = transition_state(previous, report(True, 6), config())
    assert next_state.status == "reopened"
    assert trigger == "incident_already_reopened"
