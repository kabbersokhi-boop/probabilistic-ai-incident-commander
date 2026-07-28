"""Small deterministic terminal renderer with no UI framework dependency."""

from __future__ import annotations

import re
import textwrap
import unicodedata
from dataclasses import dataclass
from typing import Any

from paic.tui.models import StageSnapshot, WorkspaceSnapshot

_ANSI_ESCAPE = re.compile(
    r"(?:\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-Z\\-_])|"
    r"\x9b[0-?]*[ -/]*[@-~])"
)
_CONTROL_REPLACEMENT = "�"


def sanitize_terminal_text(value: Any) -> str:
    """Make untrusted text inert before it is written to a terminal."""

    text = str(value)
    cleaned: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if character in {"\n", "\r", "\t"}:
            cleaned.append(" ")
        elif category in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            cleaned.append(_CONTROL_REPLACEMENT)
        else:
            cleaned.append(character)
    return "".join(cleaned)


@dataclass(frozen=True)
class Palette:
    reset: str
    bold: str
    dim: str
    healthy: str
    warning: str
    error: str
    neutral: str


class Renderer:
    def __init__(self, *, width: int = 88, color: bool = True, unicode: bool = True):
        self._width = 40
        self.width = width
        self.unicode = unicode
        self.palette = (
            Palette(
                reset="\033[0m",
                bold="\033[1m",
                dim="\033[2m",
                healthy="\033[32m",
                warning="\033[33m",
                error="\033[31m",
                neutral="\033[36m",
            )
            if color
            else Palette("", "", "", "", "", "", "")
        )

    @property
    def width(self) -> int:
        return self._width

    @width.setter
    def width(self, value: int) -> None:
        self._width = max(40, value)

    def _status(self, value: str) -> str:
        icon_map = {
            "healthy": "✓" if self.unicode else "OK",
            "warning": "!",
            "error": "✗" if self.unicode else "X",
            "missing": "?",
            "not_configured": "-",
        }
        color = {
            "healthy": self.palette.healthy,
            "warning": self.palette.warning,
            "error": self.palette.error,
            "missing": self.palette.error,
            "not_configured": self.palette.dim,
        }[value]
        label = value.replace("_", " ").upper()
        return f"{color}{icon_map[value]} {label}{self.palette.reset}"

    def _rule(self, char: str | None = None) -> str:
        selected = char or ("─" if self.unicode else "-")
        return selected * self.width

    def _wrap(self, value: Any, *, indent: str = "", width: int | None = None) -> list[str]:
        clean = sanitize_terminal_text(value)
        available = max(1, (width or self.width) - len(indent))
        return [
            indent + line
            for line in textwrap.wrap(
                clean,
                width=available,
                break_long_words=True,
                break_on_hyphens=False,
            )
        ] or [indent]

    def _labelled(self, label: str, value: Any) -> list[str]:
        clean = sanitize_terminal_text(value)
        prefix = f"{label}: "
        first_width = max(1, self.width - len(prefix))
        chunks = textwrap.wrap(
            clean,
            width=first_width,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [""]
        rows = [prefix + chunks[0]]
        rows.extend(" " * len(prefix) + chunk for chunk in chunks[1:])
        return rows

    def _centered(self, value: str) -> list[str]:
        return [line.center(self.width) for line in self._wrap(value)]

    def banner(self, snapshot: WorkspaceSnapshot) -> str:
        title = "PAIC TERMINAL CONTROL ROOM"
        display_name = sanitize_terminal_text(snapshot.display_name)
        lines = [
            self._rule("═" if self.unicode else "="),
            f"{self.palette.bold}{title.center(self.width)}{self.palette.reset}",
        ]
        lines.extend(self._centered(display_name))
        lines.append(self._rule("═" if self.unicode else "="))
        return "\n".join(lines)

    def overview(self, snapshot: WorkspaceSnapshot) -> str:
        rows = [
            self.banner(snapshot),
            f"Overall: {self._status(snapshot.overall_status)}",
            (
                f"Validated: {snapshot.healthy_stage_count}/{snapshot.configured_stage_count} "
                "configured stages"
            ),
            self._rule(),
        ]
        for index, stage in enumerate(snapshot.stages, 1):
            auth = "authoritative" if stage.authoritative else "read-only check"
            title = sanitize_terminal_text(stage.title)
            status = self._status(stage.status)
            compact = f"{index:>2}. {title}  {strip_ansi(status)}  {auth}"
            if len(compact) <= self.width:
                rows.append(
                    f"{index:>2}. {title}  {status}  {self.palette.dim}{auth}{self.palette.reset}"
                )
                rows.extend(self._wrap(stage.summary, indent="    "))
            else:
                rows.extend(self._wrap(f"{index}. {title}"))
                rows.append(f"   {status}")
                rows.extend(self._wrap(auth, indent="   "))
                rows.extend(self._wrap(stage.summary, indent="   "))
        rows.extend(
            [
                self._rule(),
            ]
        )
        rows.extend(self._wrap("Choose a number for details, [R]efresh, [H]elp, or [Q]uit."))
        return "\n".join(rows)

    def detail(self, stage: StageSnapshot) -> str:
        title = sanitize_terminal_text(stage.title)
        rows = [
            self._rule("═" if self.unicode else "="),
            *[f"{self.palette.bold}{line}{self.palette.reset}" for line in self._wrap(title)],
            f"Status: {self._status(stage.status)}",
            *self._labelled(
                "Authority",
                "source-authoritative" if stage.authoritative else "artifact/read-only",
            ),
        ]
        if stage.path:
            rows.extend(self._labelled("Path", stage.path))
        rows.append(self._rule())
        rows.extend(self._wrap(stage.summary))
        if stage.details:
            rows.extend(["", f"{self.palette.bold}What this means{self.palette.reset}"])
            for item in stage.details:
                rows.extend(self._wrap(item, indent="  - "))
        if stage.issues:
            rows.extend(["", f"{self.palette.bold}Problems to fix{self.palette.reset}"])
            for issue in stage.issues:
                rows.extend(self._wrap(issue, indent="  - "))
        rows.append(self._rule())
        rows.extend(self._wrap("Press Enter to return."))
        return "\n".join(rows)

    def help(self) -> str:
        paragraphs = [
            "HEALTHY means the configured authoritative checks passed.",
            "WARNING means the artifact is readable but original source files were not supplied, so the TUI will not claim full provenance.",
            "ERROR or MISSING means the stage needs attention before relying on it.",
            "The TUI is read-only. It cannot approve, execute, reopen, or mutate an incident.",
            "All authority remains in the existing PAIC validators and governed commands.",
        ]
        rows = [
            self._rule("═" if self.unicode else "="),
            *[
                f"{self.palette.bold}{line}{self.palette.reset}"
                for line in self._wrap("How to read this screen")
            ],
            self._rule(),
        ]
        for paragraph in paragraphs:
            rows.extend(self._wrap(paragraph))
        rows.append(self._rule())
        rows.extend(self._wrap("Press Enter to return."))
        return "\n".join(rows)


def strip_ansi(value: str) -> str:
    return _ANSI_ESCAPE.sub("", value)
