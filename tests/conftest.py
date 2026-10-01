"""Plugin test fixtures and configuration."""

import json
from pathlib import Path

import pytest

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifest.json"


@pytest.fixture
def manifest():
    """The plugin's real manifest."""
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sample_config():
    """Sample configuration for testing."""
    return {
        "username": "octocat",
        "active_color": "green",
        "empty_color": "blank",
        "refresh_seconds": 3600,
        "enabled": True,
    }
