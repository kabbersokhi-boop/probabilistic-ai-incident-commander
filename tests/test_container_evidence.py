from __future__ import annotations

import json
from pathlib import Path

import pytest

from paic.container_evidence import EvidenceError, build_bundle, validate_bundle


def _write_inputs(root: Path) -> tuple[Path, Path, Path]:
    image_path = root / "image.json"
    image_path.write_text(
        json.dumps(
            {
                "Id": "sha256:" + "a" * 64,
                "RepoTags": ["paic:test"],
                "Config": {
                    "User": "10001:10001",
                    "Entrypoint": ["paic"],
                    "Cmd": ["summary", "--spec-dir", "/opt/paic/specs"],
                    "Env": ["SECRET_VALUE=must-not-appear"],
                    "Labels": {
                        "org.opencontainers.image.version": "0.12.0",
                        "org.opencontainers.image.revision": "abc123",
                        "org.opencontainers.image.source": "https://example.invalid/repo",
                        "org.opencontainers.image.licenses": "MIT",
                    },
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    pip_path = root / "pip.json"
    pip_path.write_text(
        json.dumps(
            {
                "version": "1",
                "installed": [
                    {"metadata": {"name": "PyYAML", "version": "6.0.2"}},
                    {
                        "metadata": {
                            "name": "probabilistic-ai-incident-commander",
                            "version": "0.12.0",
                        }
                    },
                ],
                "environment": {"implementation_name": "cpython"},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    debian_path = root / "debian.tsv"
    debian_path.write_text(
        "base-files\t12.4+deb12u12\tamd64\nlibc6\t2.36-9+deb12u13\tamd64\n",
        encoding="utf-8",
    )
    return image_path, pip_path, debian_path


def test_bundle_is_deterministic_and_valid(tmp_path: Path) -> None:
    image_path, pip_path, debian_path = _write_inputs(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    for output in (first, second):
        build_bundle(
            image_inspect=image_path,
            pip_inspect=pip_path,
            debian_packages=debian_path,
            output_dir=output,
        )
        validate_bundle(
            bundle_dir=output,
            expected_revision="abc123",
            expected_image_id="sha256:" + "a" * 64,
        )

    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }


def test_bundle_omits_image_environment_from_derived_evidence(tmp_path: Path) -> None:
    image_path, pip_path, debian_path = _write_inputs(tmp_path)
    output = tmp_path / "bundle"
    build_bundle(
        image_inspect=image_path,
        pip_inspect=pip_path,
        debian_packages=debian_path,
        output_dir=output,
    )

    assert "SECRET_VALUE" not in (output / "container-evidence.json").read_text(encoding="utf-8")
    assert "SECRET_VALUE" not in (output / "sbom.cdx.json").read_text(encoding="utf-8")


def test_validation_rejects_tampered_inventory(tmp_path: Path) -> None:
    image_path, pip_path, debian_path = _write_inputs(tmp_path)
    output = tmp_path / "bundle"
    build_bundle(
        image_inspect=image_path,
        pip_inspect=pip_path,
        debian_packages=debian_path,
        output_dir=output,
    )
    (output / "debian-packages.tsv").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(EvidenceError, match="manifest hash mismatch"):
        validate_bundle(bundle_dir=output)


def test_validation_rejects_wrong_commit_or_image(tmp_path: Path) -> None:
    image_path, pip_path, debian_path = _write_inputs(tmp_path)
    output = tmp_path / "bundle"
    build_bundle(
        image_inspect=image_path,
        pip_inspect=pip_path,
        debian_packages=debian_path,
        output_dir=output,
    )

    with pytest.raises(EvidenceError, match="expected commit"):
        validate_bundle(bundle_dir=output, expected_revision="different")
    with pytest.raises(EvidenceError, match="expected image"):
        validate_bundle(bundle_dir=output, expected_image_id="sha256:" + "b" * 64)


def test_build_rejects_invalid_debian_inventory(tmp_path: Path) -> None:
    image_path, pip_path, debian_path = _write_inputs(tmp_path)
    debian_path.write_text("missing-fields\n", encoding="utf-8")

    with pytest.raises(EvidenceError, match="invalid Debian package row"):
        build_bundle(
            image_inspect=image_path,
            pip_inspect=pip_path,
            debian_packages=debian_path,
            output_dir=tmp_path / "bundle",
        )
