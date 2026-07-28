"""Interactive read-only terminal application."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from typing import TextIO

from paic.tui.models import WorkspaceConfig, WorkspaceSnapshot
from paic.tui.render import Renderer
from paic.tui.workspace import inspect_workspace

TerminalWidthProvider = Callable[[], int]


class TUIApplication:
    def __init__(
        self,
        config: WorkspaceConfig,
        *,
        input_stream: TextIO = sys.stdin,
        output_stream: TextIO = sys.stdout,
        snapshot_builder: Callable[[WorkspaceConfig], WorkspaceSnapshot] = inspect_workspace,
        terminal_width_provider: TerminalWidthProvider | None = None,
        color: bool = True,
        unicode: bool = True,
    ):
        self.config = config
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.snapshot_builder = snapshot_builder
        self.terminal_width_provider = terminal_width_provider or (
            lambda: shutil.get_terminal_size(fallback=(88, 24)).columns
        )
        self.renderer = Renderer(width=88, color=color, unicode=unicode)

    def _update_width(self) -> None:
        self.renderer.width = self.terminal_width_provider()

    def _clear(self) -> None:
        if self.output_stream.isatty():
            self.output_stream.write("\033[2J\033[H")

    def _write(self, value: str) -> None:
        self.output_stream.write(value + "\n")
        self.output_stream.flush()

    def _read(self, prompt: str = "> ") -> str:
        self.output_stream.write(prompt)
        self.output_stream.flush()
        value = self.input_stream.readline()
        if value == "":
            raise EOFError
        return value.strip()

    def run(self) -> int:
        try:
            snapshot = self.snapshot_builder(self.config)
            while True:
                self._update_width()
                self._clear()
                self._write(self.renderer.overview(snapshot))
                choice = self._read().lower()
                if choice in {"q", "quit", "exit"}:
                    return 0
                if choice in {"r", "refresh"}:
                    snapshot = self.snapshot_builder(self.config)
                    continue
                if choice in {"h", "help", "?"}:
                    self._update_width()
                    self._clear()
                    self._write(self.renderer.help())
                    self._read("")
                    continue
                try:
                    index = int(choice) - 1
                except ValueError:
                    continue
                if 0 <= index < len(snapshot.stages):
                    self._update_width()
                    self._clear()
                    self._write(self.renderer.detail(snapshot.stages[index]))
                    self._read("")
        except (EOFError, KeyboardInterrupt):
            self.output_stream.write(os.linesep)
            return 0
