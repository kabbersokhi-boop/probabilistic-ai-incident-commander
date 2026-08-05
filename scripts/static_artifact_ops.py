"""Backup, restore, validate, and promote immutable static web bundles."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

from paic.web_readiness import WebReadinessError, validate_bundle


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_exchange(left: Path, right: Path) -> None:
    """Exchange two sibling directories or fail closed when unsupported."""
    if left.parent != right.parent:
        raise WebReadinessError("atomic deployment entries must share a parent")
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise WebReadinessError("atomic directory exchange is unavailable")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        parent_fd = os.open(left.parent, flags)
        try:
            result = renameat2(
                parent_fd,
                os.fsencode(left.name),
                parent_fd,
                os.fsencode(right.name),
                2,
            )
            if result != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except OSError as exc:
        raise WebReadinessError(f"atomic directory exchange failed: {exc}") from exc


def backup(bundle_dir: Path, archive: Path) -> None:
    validate_bundle(bundle_dir)
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(bundle_dir, arcname="bundle", recursive=True)
    manifest = {"archive": archive.name, "sha256": _sha256(archive), "source": "paic-public-demo"}
    archive.with_suffix(archive.suffix + ".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def restore(archive: Path, output_dir: Path) -> None:
    manifest_path = archive.with_suffix(archive.suffix + ".manifest.json")
    if not manifest_path.is_file():
        raise WebReadinessError("backup integrity manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("sha256") != _sha256(archive):
        raise WebReadinessError("backup archive hash mismatch")
    if output_dir.exists() or output_dir.is_symlink():
        raise WebReadinessError("restore destination must be new")
    staging = output_dir.with_name(output_dir.name + ".staging")
    staging.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            target = (staging / member.name).resolve()
            if staging.resolve() not in target.parents and target != staging.resolve():
                raise WebReadinessError("backup contains a path traversal member")
            if not member.isfile() and not member.isdir():
                raise WebReadinessError("backup contains an unsafe or non-file member")
        handle.extractall(staging, filter="data")
    restored = staging / "bundle"
    validate_bundle(restored)
    restored.rename(output_dir)
    staging.rmdir()


def promote(bundle_dir: Path, target_dir: Path) -> None:
    validate_bundle(bundle_dir)
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = target_dir.with_name(target_dir.name + ".staging")
    if staging.exists() or staging.is_symlink():
        shutil.rmtree(staging)
    shutil.copytree(bundle_dir, staging)
    validate_bundle(staging)
    previous = target_dir.with_name(target_dir.name + ".previous")
    if previous.exists() or previous.is_symlink():
        shutil.rmtree(previous)
    if target_dir.exists():
        _atomic_exchange(target_dir, staging)
        staging.rename(previous)
    else:
        staging.rename(target_dir)


def rollback(target_dir: Path) -> None:
    """Restore the validated previous directory while retaining the current one."""
    previous = target_dir.with_name(target_dir.name + ".previous")
    if not target_dir.is_dir() or target_dir.is_symlink():
        raise WebReadinessError("rollback target is missing or unsafe")
    if not previous.is_dir() or previous.is_symlink():
        raise WebReadinessError("validated previous bundle is missing")
    validate_bundle(target_dir)
    validate_bundle(previous)
    _atomic_exchange(target_dir, previous)
    validate_bundle(target_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup")
    create.add_argument("--bundle", type=Path, required=True)
    create.add_argument("--archive", type=Path, required=True)
    recover = commands.add_parser("restore")
    recover.add_argument("--archive", type=Path, required=True)
    recover.add_argument("--output", type=Path, required=True)
    publish = commands.add_parser("promote")
    publish.add_argument("--bundle", type=Path, required=True)
    publish.add_argument("--target", type=Path, required=True)
    revert = commands.add_parser("rollback")
    revert.add_argument("--target", type=Path, required=True)
    check = commands.add_parser("validate")
    check.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            backup(args.bundle, args.archive)
        elif args.command == "restore":
            restore(args.archive, args.output)
        elif args.command == "promote":
            promote(args.bundle, args.target)
        elif args.command == "rollback":
            rollback(args.target)
        else:
            validate_bundle(args.bundle)
    except (OSError, ValueError, WebReadinessError, tarfile.TarError) as exc:
        print(f"static artifact operation error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
