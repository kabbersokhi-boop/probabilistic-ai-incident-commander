"""CLI registration and standalone entry point for the terminal interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from paic.artifacts.lease import ArtifactLeaseError
from paic.tui.app import TUIApplication
from paic.tui.config import (
    TUIConfigError,
    load_workspace_config,
    write_workspace_template,
)
from paic.tui.models import WorkspaceConfig, WorkspaceSnapshot
from paic.tui.render import Renderer, sanitize_terminal_text
from paic.tui.workspace import inspect_workspace


def _register_commands(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="tui_command", required=True)

    run = commands.add_parser("run", help="Open the interactive terminal control room.")
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--no-color", action="store_true")
    run.add_argument("--ascii", action="store_true")

    snapshot = commands.add_parser(
        "snapshot", help="Print one deterministic workspace status snapshot."
    )
    snapshot.add_argument("--workspace", type=Path, required=True)
    snapshot.add_argument("--format", choices=("text", "json"), default="text")
    snapshot.add_argument("--no-color", action="store_true")
    snapshot.add_argument("--ascii", action="store_true")

    validate = commands.add_parser(
        "validate",
        help="Validate every configured stage and return a CI-friendly status.",
    )
    validate.add_argument("--workspace", type=Path, required=True)
    validate.add_argument("--format", choices=("text", "json"), default="text")

    init = commands.add_parser("init", help="Write a safe starter workspace configuration.")
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--workspace-id", default="local-control-room")
    init.add_argument("--display-name", default="Local incident control room")
    init.add_argument("--root-dir", default="../..")
    init.add_argument("--overwrite", action="store_true")


def register_tui_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "tui",
        help="Open the read-only terminal control room for a validated workspace.",
    )
    _register_commands(parser)


def _load_snapshot(path: Path) -> tuple[WorkspaceConfig, WorkspaceSnapshot]:
    config = load_workspace_config(path)
    return config, inspect_workspace(config)


def dispatch_tui(args: argparse.Namespace) -> int:
    try:
        if args.tui_command == "init":
            target = write_workspace_template(
                args.output,
                workspace_id=args.workspace_id,
                display_name=args.display_name,
                root_dir=args.root_dir,
                overwrite=args.overwrite,
            )
            print(sanitize_terminal_text(target))
            return 0
        config, snapshot = _load_snapshot(args.workspace)
        if args.tui_command == "run":
            coordination_failure = any(
                any(
                    "Workspace artifact leases could not be acquired safely" in issue
                    for issue in stage.issues
                )
                for stage in snapshot.stages
            )
            if coordination_failure:
                print(
                    json.dumps(
                        {
                            "success": False,
                            "error": "workspace artifact coordination failed; interactive UI was not started",
                        },
                        indent=2,
                    ),
                    file=sys.stderr,
                )
                return 2
            return int(
                TUIApplication(
                    config,
                    color=not args.no_color and sys.stdout.isatty(),
                    unicode=not args.ascii,
                ).run()
            )
        if args.format == "json":
            print(snapshot.model_dump_json(indent=2))
        else:
            renderer = Renderer(
                color=False if args.tui_command == "validate" else not args.no_color,
                unicode=False if args.tui_command == "validate" else not args.ascii,
            )
            print(renderer.overview(snapshot))
        return 1 if snapshot.overall_status in {"error", "missing"} else 0
    except (TUIConfigError, ArtifactLeaseError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paic-tui",
        description="Read-only PAIC terminal control room.",
    )
    _register_commands(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return dispatch_tui(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
