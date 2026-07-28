from __future__ import annotations

import io
from pathlib import Path

import pytest

from paic.tui.app import TUIApplication
from paic.tui.models import StageSnapshot, WorkspaceConfig, WorkspacePaths, WorkspaceSnapshot
from paic.tui.render import Renderer, strip_ansi


def _snapshot() -> WorkspaceSnapshot:
    stage = StageSnapshot(
        key="dataset",
        title="A very long stage title for terminal boundary coverage",
        status="error",
        summary="A long summary that must wrap without losing any information.",
        authoritative=True,
        path="workspace-relative/artifacts/dataset/manifest.json",
        details=["A detail entry that is deliberately long enough to wrap."],
        issues=["An issue entry that is deliberately long enough to wrap."],
    )
    return WorkspaceSnapshot(
        workspace_id="boundary-room",
        display_name="A workspace display name that needs wrapping",
        root_dir="/workspace",
        overall_status="error",
        configured_stage_count=1,
        healthy_stage_count=0,
        stages=[stage],
    )


def _long_snapshot() -> WorkspaceSnapshot:
    stage = StageSnapshot(
        key="long-stage",
        title="stage-" + "x" * 120,
        status="warning",
        summary="summary-" + "s" * 180,
        authoritative=False,
        path="artifacts/" + "segment-" * 30 + "manifest.json",
        details=["detail-" + "d" * 140],
        issues=["issue-" + "i" * 140],
    )
    return WorkspaceSnapshot(
        workspace_id="long-room",
        display_name="workspace-" + "w" * 140,
        root_dir="/workspace",
        overall_status="warning",
        configured_stage_count=1,
        healthy_stage_count=0,
        stages=[stage],
    )


def _assert_lines_fit(value: str, width: int) -> None:
    assert all(len(strip_ansi(line)) <= width for line in value.splitlines())


@pytest.mark.parametrize("width", [40, 48, 60, 88])  # type: ignore[untyped-decorator]
@pytest.mark.parametrize(
    ("color", "unicode"),
    [(True, True), (True, False), (False, True), (False, False)],
)  # type: ignore[untyped-decorator]
def test_every_renderer_view_respects_narrow_widths(width: int, color: bool, unicode: bool) -> None:
    renderer = Renderer(width=width, color=color, unicode=unicode)
    snapshot = _long_snapshot()

    for rendered in (
        renderer.overview(snapshot),
        renderer.detail(snapshot.stages[0]),
        renderer.help(),
        renderer.banner(snapshot),
    ):
        _assert_lines_fit(rendered, width)
    if color:
        assert "\033[" in renderer.overview(snapshot)


def test_dynamic_resize_updates_later_screens(tmp_path: Path) -> None:
    config = WorkspaceConfig(
        workspace_id="boundary-room",
        display_name="Boundary room",
        root_dir=tmp_path,
        paths=WorkspacePaths(),
    )
    widths = [100, 42, 72, 88]
    provider_calls = 0
    seen_widths: list[int] = []

    def width_provider() -> int:
        nonlocal provider_calls
        value = widths[min(provider_calls, len(widths) - 1)]
        provider_calls += 1
        seen_widths.append(value)
        return value

    output = io.StringIO()
    app = TUIApplication(
        config,
        input_stream=io.StringIO("1\n\nh\n\nr\nq\n"),
        output_stream=output,
        snapshot_builder=lambda _: _snapshot(),
        terminal_width_provider=width_provider,
        color=False,
        unicode=False,
    )

    assert app.run() == 0
    assert seen_widths[:4] == widths
    assert app.renderer.width == 88


class _Stream(io.StringIO):
    def __init__(self, value: str = "", *, tty: bool) -> None:
        super().__init__(value)
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def test_clear_screen_is_tty_only(tmp_path: Path) -> None:
    config = WorkspaceConfig(
        workspace_id="boundary-room",
        display_name="Boundary room",
        root_dir=tmp_path,
        paths=WorkspacePaths(),
    )
    for tty, expected in [(True, True), (False, False)]:
        output = _Stream(tty=tty)
        app = TUIApplication(
            config,
            input_stream=io.StringIO("q\n"),
            output_stream=output,
            snapshot_builder=lambda _: _snapshot(),
            terminal_width_provider=lambda: 48,
            color=False,
            unicode=False,
        )
        assert app.run() == 0
        assert ("\033[2J\033[H" in output.getvalue()) is expected


@pytest.mark.parametrize("input_value", ["", "1\n", "h\n\n"])  # type: ignore[untyped-decorator]
def test_eof_is_clean_from_every_waiting_screen(tmp_path: Path, input_value: str) -> None:
    config = WorkspaceConfig(
        workspace_id="boundary-room",
        display_name="Boundary room",
        root_dir=tmp_path,
        paths=WorkspacePaths(),
    )
    app = TUIApplication(
        config,
        input_stream=io.StringIO(input_value),
        output_stream=io.StringIO(),
        snapshot_builder=lambda _: _snapshot(),
        terminal_width_provider=lambda: 40,
        color=False,
        unicode=False,
    )
    assert app.run() == 0


class _InterruptingInput:
    def readline(self) -> str:
        raise KeyboardInterrupt


def test_keyboard_interrupt_is_clean(tmp_path: Path) -> None:
    config = WorkspaceConfig(
        workspace_id="boundary-room",
        display_name="Boundary room",
        root_dir=tmp_path,
        paths=WorkspacePaths(),
    )
    app = TUIApplication(
        config,
        input_stream=_InterruptingInput(),  # type: ignore[arg-type]
        output_stream=io.StringIO(),
        snapshot_builder=lambda _: _snapshot(),
        terminal_width_provider=lambda: 40,
        color=False,
        unicode=False,
    )
    assert app.run() == 0
