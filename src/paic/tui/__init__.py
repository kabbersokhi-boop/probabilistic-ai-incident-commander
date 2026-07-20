"""Read-only terminal interface for governed incident workspaces."""

from paic.tui.app import TUIApplication
from paic.tui.config import load_workspace_config
from paic.tui.workspace import inspect_workspace

__all__ = ["TUIApplication", "inspect_workspace", "load_workspace_config"]
