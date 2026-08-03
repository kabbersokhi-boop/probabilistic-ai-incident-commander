from pathlib import Path

import pytest

from paic.dependency_lock import (
    LockValidationError,
    declared_dependency_names,
    main,
    parse_lock,
    validate_lock_set,
)


def _lock(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "requirements.lock"
    path.write_text(body, encoding="utf-8")
    return path


def _project(tmp_path: Path, deps: str = '"Example>=1,<2"') -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text("[project]" + chr(10) + f"dependencies = [{deps}]" + chr(10), encoding="utf-8")
    return path


def _entry(name: str, version: str, digest: str, marker: str | None = None) -> str:
    suffix = f" ; {marker}" if marker else ""
    return f"{name}=={version}{suffix} \\" + chr(10) + "    --hash=sha256:" + digest + chr(10)


def test_lock_requires_hashes_and_rejects_duplicates(tmp_path: Path) -> None:
    with pytest.raises(LockValidationError, match="no archive hashes"):
        parse_lock(_lock(tmp_path, "example==1.0" + chr(10)))
    with pytest.raises(LockValidationError, match="duplicate"):
        parse_lock(
            _lock(tmp_path, _entry("example", "1.0", "0" * 64) + _entry("example", "1.0", "1" * 64))
        )


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "entry", ["-e ." + chr(10), "example @ https://example.invalid/a.whl" + chr(10)]
)
def test_lock_rejects_mutable_or_local_entries(tmp_path: Path, entry: str) -> None:
    with pytest.raises(LockValidationError):
        parse_lock(_lock(tmp_path, entry))


def test_lock_requires_declared_dependency(tmp_path: Path) -> None:
    lock = _lock(tmp_path, _entry("other", "1.0", "0" * 64))
    with pytest.raises(LockValidationError, match="missing"):
        validate_lock_set(pyproject=_project(tmp_path), lock_paths=(lock,))


def test_marker_split_is_supported(tmp_path: Path) -> None:
    path = _lock(
        tmp_path,
        _entry("example", "1.0", "0" * 64, "python_version < '3.12'")
        + _entry("example", "1.1", "1" * 64, "python_version >= '3.12'"),
    )
    assert len(parse_lock(path)) == 2


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "body, message",
    [
        ("    --hash=sha256:" + "0" * 64 + "\n", "hash without"),
        ("example==1.0\n    not-a-hash\n", "malformed"),
        ("-r other.txt\n", "malformed"),
    ],
)
def test_lock_rejects_malformed_entries(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(LockValidationError, match=message):
        parse_lock(_lock(tmp_path, body))


def test_lock_rejects_invalid_project_and_cli_returns_failure(tmp_path: Path) -> None:
    project = tmp_path / "bad.toml"
    project.write_text("[tool.other]\nvalue = true\n", encoding="utf-8")
    with pytest.raises(LockValidationError, match=r"no \[project\]"):
        declared_dependency_names(project)
    assert main([str(tmp_path / "missing.lock")]) == 1


def test_lock_parser_and_project_validation_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(LockValidationError, match="cannot read"):
        parse_lock(tmp_path / "missing.lock")
    with pytest.raises(LockValidationError, match="no requirements"):
        parse_lock(_lock(tmp_path, "# generated\n"))
    with pytest.raises(LockValidationError, match="hash without"):
        parse_lock(_lock(tmp_path, "    --hash=sha256:" + "0" * 64 + " \\\n"))

    project = tmp_path / "invalid-deps.toml"
    project.write_text("[project]\ndependencies = true\n", encoding="utf-8")
    with pytest.raises(LockValidationError, match="must be a list"):
        declared_dependency_names(project)
    project.write_text("[project]\ndependencies = [1]\n", encoding="utf-8")
    with pytest.raises(LockValidationError, match="must be strings"):
        declared_dependency_names(project)
    project.write_text('[project]\ndependencies = ["@broken"]\n', encoding="utf-8")
    with pytest.raises(LockValidationError, match="malformed"):
        declared_dependency_names(project)
    project.write_text("[project\n", encoding="utf-8")
    with pytest.raises(LockValidationError, match="cannot read pyproject"):
        declared_dependency_names(project)


def test_lock_set_rejects_unexpected_and_cli_accepts_valid_input(tmp_path: Path) -> None:
    project = _project(tmp_path)
    lock = _lock(tmp_path, _entry("example", "1.0", "0" * 64))
    with pytest.raises(LockValidationError, match="unexpected"):
        validate_lock_set(pyproject=project, lock_paths=(lock,), allowed_names={"other"})
    assert main(["--pyproject", str(project), str(lock)]) == 0
