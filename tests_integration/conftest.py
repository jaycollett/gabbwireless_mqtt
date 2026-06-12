"""Fixtures for the Gabb Wireless custom integration tests.

These tests are independent of the MQTT publisher tests in tests/ and
require pytest-homeassistant-custom-component.
"""

import sys
from pathlib import Path

import pytest

# Make the repo root importable so `custom_components.gabb_wireless` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in all tests."""
    yield
