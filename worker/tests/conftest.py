"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_inbox(tmp_path: Path) -> Path:
    """An empty inbox directory in a temp location."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return inbox
