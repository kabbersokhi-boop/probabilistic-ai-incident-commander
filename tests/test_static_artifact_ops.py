import json
from pathlib import Path

import pytest
from scripts.static_artifact_ops import backup, promote, restore

from paic.web_readiness import WebReadinessError


def _bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    payload = {
        "schema_version": "1.0",
        "bundle_kind": "paic-public-demo",
        "disclaimer": "Synthetic demonstration data only; values are not production claims.",
        "workspace_id": "demo",
        "display_name": "Demo",
        "source_bindings": {},
        "lifecycle": {},
        "detection": {},
        "operations": {},
        "investigation": {},
        "impact": {},
        "remediation": {},
        "recovery": {},
        "evaluation": {},
        "stages": [],
        "files": [],
    }
    (bundle / "bundle.json").write_text(json.dumps(payload, sort_keys=True) + "\n")
    import hashlib

    digest = hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps({"bundle_sha256": digest}))
    mdigest = hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
    (bundle / "SHA256SUMS").write_text(f"{digest}  bundle.json\n{mdigest}  manifest.json\n")
    return bundle


def test_backup_restore_and_promotion_are_validated(tmp_path: Path) -> None:
    source = _bundle(tmp_path)
    archive = tmp_path / "demo.tar.gz"
    backup(source, archive)
    restored = tmp_path / "restored"
    restore(archive, restored)
    target = tmp_path / "current"
    promote(restored, target)
    assert (target / "bundle.json").is_file()


def test_restore_rejects_tampered_backup(tmp_path: Path) -> None:
    source = _bundle(tmp_path)
    archive = tmp_path / "demo.tar.gz"
    backup(source, archive)
    archive.write_bytes(archive.read_bytes() + b"tamper")
    with pytest.raises(WebReadinessError, match="hash mismatch"):
        restore(archive, tmp_path / "restored")
