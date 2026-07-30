from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

SCHEMA_VERSION = 1
CYCLONEDX_SPEC_VERSION = "1.6"
TOOL_NAME = "paic-container-evidence"
TOOL_VERSION = "1"


class EvidenceError(ValueError):
    """Raised when container evidence is malformed or fails validation."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read JSON from {path}: {exc}") from exc


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_mapping(value: object, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{context} must be a JSON object")
    return cast(dict[str, Any], value)


def _require_string(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{context} must be a non-empty string")
    return value


def _canonical_python_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _image_metadata(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    if isinstance(value, list):
        if len(value) != 1:
            raise EvidenceError("image inspect JSON must describe exactly one image")
        value = value[0]
    image = _require_mapping(value, context="image inspect")
    image_id = _require_string(image.get("Id"), context="image Id")
    if not image_id.startswith("sha256:"):
        raise EvidenceError("image Id must use a sha256 digest")

    config = _require_mapping(image.get("Config"), context="image Config")
    labels = _require_mapping(config.get("Labels") or {}, context="image labels")

    def optional_string(key: str, default: str = "unknown") -> str:
        item = labels.get(key, default)
        return item if isinstance(item, str) and item else default

    entrypoint = config.get("Entrypoint")
    cmd = config.get("Cmd")
    if not isinstance(entrypoint, list) or not all(isinstance(item, str) for item in entrypoint):
        raise EvidenceError("image Entrypoint must be a string list")
    if not isinstance(cmd, list) or not all(isinstance(item, str) for item in cmd):
        raise EvidenceError("image Cmd must be a string list")

    return {
        "id": image_id,
        "repository_tags": sorted(
            item for item in image.get("RepoTags", []) if isinstance(item, str)
        ),
        "user": _require_string(config.get("User"), context="image user"),
        "entrypoint": entrypoint,
        "cmd": cmd,
        "version": optional_string("org.opencontainers.image.version"),
        "revision": optional_string("org.opencontainers.image.revision"),
        "source": optional_string("org.opencontainers.image.source"),
        "license": optional_string("org.opencontainers.image.licenses"),
    }


def _python_components(path: Path) -> list[dict[str, Any]]:
    payload = _require_mapping(_read_json(path), context="pip inspect")
    installed = payload.get("installed")
    if not isinstance(installed, list):
        raise EvidenceError("pip inspect installed must be a list")

    components: list[dict[str, Any]] = []
    for index, item in enumerate(installed):
        package = _require_mapping(item, context=f"pip package {index}")
        metadata = _require_mapping(
            package.get("metadata"), context=f"pip package {index} metadata"
        )
        name = _require_string(metadata.get("name"), context=f"pip package {index} name")
        version = _require_string(metadata.get("version"), context=f"pip package {index} version")
        canonical_name = _canonical_python_name(name)
        purl = f"pkg:pypi/{quote(canonical_name, safe='')}@{quote(version, safe='')}"
        components.append(
            {
                "type": "library",
                "bom-ref": purl,
                "name": name,
                "version": version,
                "purl": purl,
                "properties": [{"name": "paic:ecosystem", "value": "python"}],
            }
        )
    return components


def _debian_components(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvidenceError(f"cannot read Debian package inventory from {path}: {exc}") from exc

    components: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 3 or any(not field for field in fields):
            raise EvidenceError(f"invalid Debian package row at line {line_number}")
        name, version, architecture = fields
        purl = (
            f"pkg:deb/debian/{quote(name, safe='')}@{quote(version, safe='')}"
            f"?arch={quote(architecture, safe='')}"
        )
        components.append(
            {
                "type": "library",
                "bom-ref": purl,
                "name": name,
                "version": version,
                "purl": purl,
                "properties": [
                    {"name": "paic:ecosystem", "value": "debian"},
                    {"name": "paic:architecture", "value": architecture},
                ],
            }
        )
    return components


def _deduplicate_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_reference: dict[str, dict[str, Any]] = {}
    for component in components:
        reference = _require_string(component.get("bom-ref"), context="component bom-ref")
        if reference in by_reference:
            raise EvidenceError(f"duplicate component reference: {reference}")
        by_reference[reference] = component
    return [by_reference[key] for key in sorted(by_reference)]


def _container_purl(image: dict[str, Any]) -> str:
    digest = cast(str, image["id"]).removeprefix("sha256:")
    source = quote(cast(str, image["source"]), safe="")
    return f"pkg:oci/paic@{digest}?repository_url={source}"


def _build_sbom(image: dict[str, Any], components: list[dict[str, Any]]) -> dict[str, Any]:
    container_ref = _container_purl(image)
    component_refs = [cast(str, item["bom-ref"]) for item in components]
    serial = uuid.uuid5(uuid.NAMESPACE_URL, cast(str, image["id"]))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": CYCLONEDX_SPEC_VERSION,
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "tools": {
                "components": [{"type": "application", "name": TOOL_NAME, "version": TOOL_VERSION}]
            },
            "component": {
                "type": "container",
                "bom-ref": container_ref,
                "name": "paic",
                "version": image["version"],
                "purl": container_ref,
                "properties": [
                    {"name": "paic:image-id", "value": image["id"]},
                    {"name": "paic:revision", "value": image["revision"]},
                    {"name": "paic:source", "value": image["source"]},
                    {"name": "paic:user", "value": image["user"]},
                ],
            },
        },
        "components": components,
        "dependencies": [{"ref": container_ref, "dependsOn": component_refs}],
    }


def _write_checksums(bundle_dir: Path, names: list[str]) -> None:
    rows = [f"{_sha256(bundle_dir / name)}  {name}" for name in sorted(names)]
    (bundle_dir / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")


def build_bundle(
    *,
    image_inspect: Path,
    pip_inspect: Path,
    debian_packages: Path,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    image = _image_metadata(image_inspect)
    python_components = _python_components(pip_inspect)
    debian_components = _debian_components(debian_packages)
    components = _deduplicate_components(python_components + debian_components)

    sbom_path = output_dir / "sbom.cdx.json"
    _write_json(sbom_path, _build_sbom(image, components))

    source_files = {
        "image-inspect.json": image_inspect,
        "python-packages.json": pip_inspect,
        "debian-packages.tsv": debian_packages,
    }
    for name, source in source_files.items():
        destination = output_dir / name
        if source.resolve() != destination.resolve():
            destination.write_bytes(source.read_bytes())

    evidence = {
        "schema_version": SCHEMA_VERSION,
        "image": image,
        "runtime_boundary": {
            "network": "none",
            "read_only_root_filesystem": True,
            "capabilities": [],
            "no_new_privileges": True,
            "user": "10001:10001",
        },
        "inventory": {
            "python_components": len(python_components),
            "debian_components": len(debian_components),
            "total_components": len(components),
        },
        "files": {
            name: {"sha256": _sha256(output_dir / name)}
            for name in sorted([*source_files, "sbom.cdx.json"])
        },
    }
    _write_json(output_dir / "container-evidence.json", evidence)
    _write_checksums(
        output_dir,
        [*source_files, "sbom.cdx.json", "container-evidence.json"],
    )


def _validate_checksums(bundle_dir: Path) -> None:
    checksum_path = bundle_dir / "SHA256SUMS"
    try:
        rows = checksum_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvidenceError(f"cannot read {checksum_path}: {exc}") from exc
    expected_names = {
        "container-evidence.json",
        "debian-packages.tsv",
        "image-inspect.json",
        "python-packages.json",
        "sbom.cdx.json",
    }
    observed_names: set[str] = set()
    for row in rows:
        digest, separator, name = row.partition("  ")
        if separator != "  " or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise EvidenceError("SHA256SUMS contains an invalid row")
        if name in observed_names:
            raise EvidenceError(f"SHA256SUMS contains duplicate entry: {name}")
        observed_names.add(name)
        path = bundle_dir / name
        if not path.is_file() or _sha256(path) != digest:
            raise EvidenceError(f"checksum mismatch for {name}")
    if observed_names != expected_names:
        raise EvidenceError("SHA256SUMS does not list the complete evidence bundle")


def validate_bundle(
    *,
    bundle_dir: Path,
    expected_revision: str | None = None,
    expected_image_id: str | None = None,
) -> None:
    manifest = _require_mapping(
        _read_json(bundle_dir / "container-evidence.json"), context="evidence manifest"
    )
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise EvidenceError("unsupported evidence schema version")
    image = _require_mapping(manifest.get("image"), context="evidence image")
    image_id = _require_string(image.get("id"), context="evidence image id")
    revision = _require_string(image.get("revision"), context="evidence revision")
    if expected_revision is not None and revision != expected_revision:
        raise EvidenceError("evidence revision does not match the expected commit")
    if expected_image_id is not None and image_id != expected_image_id:
        raise EvidenceError("evidence image id does not match the expected image")

    files = _require_mapping(manifest.get("files"), context="evidence files")
    expected_files = {
        "debian-packages.tsv",
        "image-inspect.json",
        "python-packages.json",
        "sbom.cdx.json",
    }
    if set(files) != expected_files:
        raise EvidenceError("evidence manifest does not list the expected files")
    for name in sorted(expected_files):
        metadata = _require_mapping(files[name], context=f"evidence file {name}")
        digest = _require_string(metadata.get("sha256"), context=f"evidence file {name} hash")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise EvidenceError(f"invalid sha256 for {name}")
        if _sha256(bundle_dir / name) != digest:
            raise EvidenceError(f"manifest hash mismatch for {name}")

    sbom = _require_mapping(_read_json(bundle_dir / "sbom.cdx.json"), context="SBOM")
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != CYCLONEDX_SPEC_VERSION:
        raise EvidenceError("SBOM format or version is invalid")
    metadata = _require_mapping(sbom.get("metadata"), context="SBOM metadata")
    container = _require_mapping(metadata.get("component"), context="SBOM container component")
    properties = container.get("properties")
    if not isinstance(properties, list):
        raise EvidenceError("SBOM container properties must be a list")
    property_map = {
        str(item.get("name")): str(item.get("value"))
        for item in properties
        if isinstance(item, dict)
    }
    if property_map.get("paic:image-id") != image_id:
        raise EvidenceError("SBOM image id does not match the evidence manifest")
    if property_map.get("paic:revision") != revision:
        raise EvidenceError("SBOM revision does not match the evidence manifest")

    components = sbom.get("components")
    inventory = _require_mapping(manifest.get("inventory"), context="evidence inventory")
    if not isinstance(components, list) or len(components) != inventory.get("total_components"):
        raise EvidenceError("SBOM component count does not match the evidence manifest")
    references = [item.get("bom-ref") for item in components if isinstance(item, dict)]
    if len(references) != len(set(references)) or any(
        not isinstance(item, str) for item in references
    ):
        raise EvidenceError("SBOM component references must be unique strings")

    _validate_checksums(bundle_dir)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate PAIC container evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="build a deterministic evidence bundle")
    build.add_argument("--image-inspect", type=Path, required=True)
    build.add_argument("--pip-inspect", type=Path, required=True)
    build.add_argument("--debian-packages", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="validate an evidence bundle")
    validate.add_argument("--bundle-dir", type=Path, required=True)
    validate.add_argument("--expected-revision")
    validate.add_argument("--expected-image-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "build":
            build_bundle(
                image_inspect=arguments.image_inspect,
                pip_inspect=arguments.pip_inspect,
                debian_packages=arguments.debian_packages,
                output_dir=arguments.output_dir,
            )
        else:
            validate_bundle(
                bundle_dir=arguments.bundle_dir,
                expected_revision=arguments.expected_revision,
                expected_image_id=arguments.expected_image_id,
            )
    except EvidenceError as exc:
        print(f"container evidence error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
