"""Shared test fixtures for Watchtower."""

from __future__ import annotations

import pytest

from watchtower.config import WatchtowerConfig


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory for test artifacts."""
    return tmp_path


@pytest.fixture
def default_config():
    """Provide a default WatchtowerConfig."""
    return WatchtowerConfig()


@pytest.fixture
def db_path(tmp_path):
    """Provide a temporary database path."""
    return str(tmp_path / "test_journal.db")
