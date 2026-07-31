from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from paic.container_evidence import EvidenceError, build_bundle, main, validate_bundle

EXPECTED_IMAGE_ID = "sha256:" + "a" * 64
EXPECTED_REVISION = "abc123"
HASHED_NAMES = (
    "container-evidence.json",
    "debian-packages.tsv",
    "image-inspect.json",
    "python-packages.json",
    "sbom.cdx.json",
)


def _write_inputs(root: Path) -> tuple[Path, Path, Path]:
    image_path = root / "image.json"
    image_path.write_text(
        json.dumps(
            {
                "Id": EXPECTED_IMAGE_ID,
                "RepoTags": ["paic:test"],
                "Config": {
                    "User": "10001:10001",
                    "Entrypoint": ["paic"],
                    "Cmd": ["summary", "--spec-dir", "/opt/paic/specs"],
                    "Labels": {
                        "org.opencontainers.image.version": "0.12.0",
                        "org.opencontainers.image.revision": EXPECTED_REVISION,
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


def _build(root: Path) -> Path:
    image_path, pip_path, debian_path = _write_inputs(root)
    output = root / "bundle"
    build_bundle(
        image_inspect=image_path,
        pip_inspect=pip_path,
        debian_packages=debian_path,
        output_dir=output,
    )
    return output


def _validate(output: Path) -> None:
    validate_bundle(
        bundle_dir=output,
        expected_revision=EXPECTED_REVISION,
        expected_image_id=EXPECTED_IMAGE_ID,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rehash_bundle(output: Path) -> None:
    manifest_path = output / "container-evidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name in (
        "debian-packages.tsv",
        "image-inspect.json",
        "python-packages.json",
        "sbom.cdx.json",
    ):
        manifest["files"][name]["sha256"] = _sha256(output / name)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(output / name)}  {name}\n" for name in HASHED_NAMES),
        encoding="utf-8",
    )


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
        _validate(output)

    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }


def test_bundle_omits_image_environment_from_derived_evidence(tmp_path: Path) -> None:
    image_path, pip_path, debian_path = _write_inputs(tmp_path)
    image = json.loads(image_path.read_text(encoding="utf-8"))
    image["Config"]["Env"] = ["SECRET_VALUE=must-not-appear"]
    image_path.write_text(json.dumps(image), encoding="utf-8")

    with pytest.raises(EvidenceError, match="image Config is not sanitized"):
        build_bundle(
            image_inspect=image_path,
            pip_inspect=pip_path,
            debian_packages=debian_path,
            output_dir=tmp_path / "bundle",
        )


def test_validation_rejects_tampered_inventory(tmp_path: Path) -> None:
    output = _build(tmp_path)
    (output / "debian-packages.tsv").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(EvidenceError, match="checksum mismatch"):
        _validate(output)


def test_validation_rejects_coordinated_malformed_inventory(tmp_path: Path) -> None:
    output = _build(tmp_path)
    (output / "debian-packages.tsv").write_text("missing-fields\n", encoding="utf-8")
    _rehash_bundle(output)

    with pytest.raises(EvidenceError, match="invalid Debian package row"):
        _validate(output)


def test_validation_rejects_noncanonical_sbom_after_rehash(tmp_path: Path) -> None:
    output = _build(tmp_path)
    sbom_path = output / "sbom.cdx.json"
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    sbom["metadata"]["component"]["version"] = "different"
    sbom_path.write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _rehash_bundle(output)

    with pytest.raises(EvidenceError, match="SBOM does not match"):
        _validate(output)


def test_validation_rejects_mismatched_breakdown_counts_after_rehash(tmp_path: Path) -> None:
    output = _build(tmp_path)
    manifest_path = output / "container-evidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["inventory"]["python_components"] = 0
    manifest["inventory"]["debian_components"] = manifest["inventory"]["total_components"]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(output / name)}  {name}\n" for name in HASHED_NAMES),
        encoding="utf-8",
    )

    with pytest.raises(EvidenceError, match="manifest does not match"):
        _validate(output)


def test_validation_rejects_wrong_commit_or_image(tmp_path: Path) -> None:
    output = _build(tmp_path)

    with pytest.raises(EvidenceError, match="expected commit"):
        validate_bundle(
            bundle_dir=output,
            expected_revision="different",
            expected_image_id=EXPECTED_IMAGE_ID,
        )
    with pytest.raises(EvidenceError, match="expected image"):
        validate_bundle(
            bundle_dir=output,
            expected_revision=EXPECTED_REVISION,
            expected_image_id="sha256:" + "b" * 64,
        )


def test_validate_cli_requires_exact_bindings(tmp_path: Path) -> None:
    output = _build(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        main(["validate", "--bundle-dir", str(output)])

    assert exc_info.value.code == 2


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


def test_build_rejects_invalid_image_digest(tmp_path: Path) -> None:
    image_path, pip_path, debian_path = _write_inputs(tmp_path)
    image = json.loads(image_path.read_text(encoding="utf-8"))
    image["Id"] = "sha256:not-a-digest"
    image_path.write_text(json.dumps(image), encoding="utf-8")

    with pytest.raises(EvidenceError, match="complete lowercase sha256 digest"):
        build_bundle(
            image_inspect=image_path,
            pip_inspect=pip_path,
            debian_packages=debian_path,
            output_dir=tmp_path / "bundle",
        )


def test_validation_rejects_extra_bundle_file(tmp_path: Path) -> None:
    output = _build(tmp_path)
    (output / "unexpected.txt").write_text("not evidence\n", encoding="utf-8")

    with pytest.raises(EvidenceError, match="complete closed-world bundle"):
        _validate(output)


def test_build_rejects_unexpected_output_entry(tmp_path: Path) -> None:
    image_path, pip_path, debian_path = _write_inputs(tmp_path)
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "stale.txt").write_text("stale\n", encoding="utf-8")

    with pytest.raises(EvidenceError, match="unexpected entries"):
        build_bundle(
            image_inspect=image_path,
            pip_inspect=pip_path,
            debian_packages=debian_path,
            output_dir=output,
        )
