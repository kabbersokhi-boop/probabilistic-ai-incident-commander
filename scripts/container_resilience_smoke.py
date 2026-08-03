"""Exercise the built image under interruption and bounded resource pressure."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass


class ContainerResilienceError(RuntimeError):
    """Raised when a container resilience scenario fails."""


@dataclass(frozen=True)
class Scenario:
    name: str
    args: tuple[str, ...]


def _hardened_command(
    image: str,
    *command: str,
    tmpfs_size: str = "64m",
    resource_limits: bool = False,
) -> tuple[str, ...]:
    limits = (
        ("--memory=128m", "--cpus=0.5", "--pids-limit=64", "--ulimit", "nofile=128:128")
        if resource_limits
        else ()
    )
    return (
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        *limits,
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={tmpfs_size},mode=1777",
        image,
        *command,
    )


def hardened_prefix(image: str, *extra: str) -> tuple[str, ...]:
    """Return a hardened docker command with ``extra`` as its container command."""
    return _hardened_command(image, *extra)


def scenarios(image: str) -> tuple[Scenario, ...]:
    return (
        Scenario(
            "bounded-memory-cpu-fd",
            _hardened_command(
                image,
                "validate",
                "--spec-dir",
                "/opt/paic/specs",
                resource_limits=True,
            ),
        ),
        Scenario(
            "disk-exhaustion-fails-closed",
            _hardened_command(
                image,
                "python",
                "-c",
                "from pathlib import Path; Path('/tmp/overflow').write_bytes(b'x' * (2 * 1024 * 1024))",
                tmpfs_size="1m",
            ),
        ),
        Scenario(
            "network-unavailable-fails-closed",
            _hardened_command(
                image,
                "python",
                "-c",
                "import socket; socket.create_connection(('127.0.0.1', 9), timeout=1)",
            ),
        ),
    )


def _run(command: tuple[str, ...], *, timeout: float = 45.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContainerResilienceError(f"command failed to complete: {' '.join(command)}") from exc


def _expect_success(command: tuple[str, ...], label: str) -> None:
    result = _run(command)
    if result.returncode != 0:
        raise ContainerResilienceError(
            f"{label} failed with {result.returncode}: {result.stderr[-1000:]}"
        )


def _expect_failure(command: tuple[str, ...], label: str) -> None:
    result = _run(command)
    if result.returncode == 0:
        raise ContainerResilienceError(f"{label} unexpectedly succeeded")


def _signal_term(image: str, name: str) -> None:
    command = (
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
        "--entrypoint",
        "python",
        image,
        "-c",
        "import time; time.sleep(30)",
    )
    started = _run(command)
    if started.returncode != 0:
        raise ContainerResilienceError(f"SIGTERM scenario did not start: {started.stderr[-1000:]}")
    try:
        time.sleep(0.2)
        killed = _run(("docker", "kill", "--signal", "TERM", name))
        if killed.returncode != 0:
            raise ContainerResilienceError(
                f"SIGTERM could not be delivered: {killed.stderr[-1000:]}"
            )
        waited = _run(("docker", "wait", name))
        if waited.returncode != 0 or waited.stdout.strip() not in {"0", "143"}:
            raise ContainerResilienceError(
                f"SIGTERM exit was not controlled: {waited.stdout.strip()}"
            )
    finally:
        _run(("docker", "rm", "--force", name))


def _restart_loop(image: str, name: str) -> None:
    command = (
        "docker",
        "run",
        "--detach",
        "--name",
        name,
        "--restart",
        "on-failure:2",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
        "--entrypoint",
        "python",
        image,
        "-c",
        "raise SystemExit(7)",
    )
    started = _run(command, timeout=15)
    if started.returncode != 0:
        raise ContainerResilienceError(
            f"restart-loop scenario did not start: {started.stderr[-1000:]}"
        )
    try:
        waited = _run(("docker", "wait", name), timeout=30)
        if waited.returncode != 0:
            raise ContainerResilienceError("restart-loop container did not settle")
        deadline = time.monotonic() + 30
        restart_count = 0
        while time.monotonic() < deadline:
            inspected = _run(("docker", "inspect", "--format", "{{.RestartCount}}", name))
            if inspected.returncode == 0:
                restart_count = int(inspected.stdout.strip())
                if restart_count >= 2:
                    break
            time.sleep(0.2)
        if restart_count < 2:
            raise ContainerResilienceError("restart-loop did not honor the bounded restart policy")
    finally:
        _run(("docker", "rm", "--force", name))


def _concurrent_reads(image: str) -> None:
    commands = [
        subprocess.Popen(
            hardened_prefix(image, "validate", "--spec-dir", "/opt/paic/specs"), text=True
        )
        for _ in range(4)
    ]
    results = [process.wait(timeout=60) for process in commands]
    if any(result != 0 for result in results):
        raise ContainerResilienceError(f"concurrent read validation failed: {results}")


def run_smoke(image: str) -> None:
    inspect = _run(("docker", "image", "inspect", image))
    if inspect.returncode != 0:
        raise ContainerResilienceError(f"image does not exist: {image}")
    for scenario in scenarios(image):
        if (
            scenario.name == "disk-exhaustion-fails-closed"
            or scenario.name == "network-unavailable-fails-closed"
        ):
            _expect_failure(scenario.args, scenario.name)
        else:
            _expect_success(scenario.args, scenario.name)
    token = uuid.uuid4().hex[:12]
    _signal_term(image, f"paic-sigterm-{token}")
    _restart_loop(image, f"paic-restart-{token}")
    _concurrent_reads(image)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args(argv)
    try:
        run_smoke(args.image)
    except (ContainerResilienceError, ValueError) as exc:
        print(f"container resilience error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
