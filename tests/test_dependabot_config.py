from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml


def _load_config() -> dict[str, Any]:
    value = yaml.safe_load(Path(".github/dependabot.yml").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def test_docker_updates_are_weekly_bounded_and_review_only() -> None:
    config = _load_config()
    assert config["version"] == 2
    updates = config["updates"]
    assert isinstance(updates, list)
    assert len(updates) == 1
    update = updates[0]
    assert update["package-ecosystem"] == "docker"
    assert update["directory"] == "/"
    assert update["schedule"] == {
        "interval": "weekly",
        "day": "monday",
        "time": "06:00",
        "timezone": "Etc/UTC",
    }
    assert update["open-pull-requests-limit"] == 2
    assert "assignees" not in update
    assert "reviewers" not in update
    assert "target-branch" not in update


def test_python_updates_cannot_move_major_or_minor_series() -> None:
    config = _load_config()
    update = config["updates"][0]
    assert update["ignore"] == [
        {
            "dependency-name": "python",
            "update-types": [
                "version-update:semver-major",
                "version-update:semver-minor",
            ],
        }
    ]
    assert update["labels"] == ["dependencies", "docker"]
    assert update["commit-message"] == {"prefix": "chore(container)"}
