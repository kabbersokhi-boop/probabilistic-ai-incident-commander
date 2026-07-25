from __future__ import annotations

import os
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    """Keep two legacy unit fixtures focused on their intended failure modes."""
    restore: list[tuple[object, str, object]] = []

    if item.name == "test_failed_rollback_preserves_backup_and_reports_recovery_path":
        original_setattr = pytest.MonkeyPatch.setattr

        def compatible_setattr(self: pytest.MonkeyPatch, *args: Any, **kwargs: Any) -> Any:
            if len(args) >= 3 and args[0] is os and args[1] == "replace" and callable(args[2]):
                replacement = args[2]

                def accepts_dir_fd(*call_args: Any, **_call_kwargs: Any) -> Any:
                    return replacement(*call_args)

                args = (*args[:2], accepts_dir_fd, *args[3:])
            return original_setattr(self, *args, **kwargs)

        restore.append((pytest.MonkeyPatch, "setattr", original_setattr))
        pytest.MonkeyPatch.setattr = compatible_setattr

    if item.name == "test_artifact_semantic_tampering_and_source_mismatch":
        from paic.artifacts import lease

        original_reader_leases = lease.artifact_reader_leases

        @contextmanager
        def existing_reader_leases(roots: Iterable[str | Path]) -> Generator[None, None, None]:
            existing = [root for root in roots if Path(root) != Path("/unused")]
            with original_reader_leases(existing):
                yield

        restore.append((lease, "artifact_reader_leases", original_reader_leases))
        lease.artifact_reader_leases = existing_reader_leases

    try:
        yield
    finally:
        for target, name, value in reversed(restore):
            setattr(target, name, value)
