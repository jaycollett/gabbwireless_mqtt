"""Unit tests for the pure functions in gabb_mqtt_publisher.

These tests target the helpers that don't require an actual MQTT broker or
Gabb API connection: humanize_key, normalize_timestamp, generate_mqtt_topics,
generate_homeassistant_discovery_messages, and _refresh_interval_seconds.
"""

import json

import pytest
from dateutil import parser as date_parser

import gabb_mqtt_publisher as publisher


# ---------------------------------------------------------------------------
# humanize_key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("batteryLevel", "Battery Level"),
        ("gpsDate", "Gps Date"),
        ("phoneNumber", "Phone Number"),
        ("last_updated", "Last Updated"),
        ("imei", "Imei"),
        ("id", "Id"),
    ],
)
def test_humanize_key(raw, expected):
    assert publisher.humanize_key(raw) == expected


# ---------------------------------------------------------------------------
# normalize_timestamp
# ---------------------------------------------------------------------------


def test_normalize_timestamp_with_explicit_tz():
    result = publisher.normalize_timestamp("2024-05-19T12:34:56Z")
    assert result is not None
    parsed = date_parser.parse(result)
    assert parsed.tzinfo is not None


def test_normalize_timestamp_assumes_utc_when_naive():
    result = publisher.normalize_timestamp("2024-05-19 12:34:56")
    assert result is not None
    parsed = date_parser.parse(result)
    assert parsed.tzinfo is not None
    # UTC offset is exactly 0.
    assert parsed.utcoffset().total_seconds() == 0


def test_normalize_timestamp_unparsable_returns_none():
    assert publisher.normalize_timestamp("not a date") is None


def test_normalize_timestamp_empty_string_returns_none():
    assert publisher.normalize_timestamp("") is None


def test_normalize_timestamp_none_returns_none():
    assert publisher.normalize_timestamp(None) is None


# ---------------------------------------------------------------------------
# generate_mqtt_topics: sensor-whitelist behavior
# ---------------------------------------------------------------------------


def _fake_map_data(extra_fields: dict | None = None) -> dict:
    """Build a minimal map_data dict with one device.

    Includes a mix of whitelisted (battery, lat/lon, gpsDate) and
    non-whitelisted (appBuild, iccid) fields so tests can exercise the
    sensor-vs-attribute split.
    """
    device: dict = {
        "id": "12345",
        "batteryLevel": 87,
        "latitude": 40.0,
        "longitude": -83.0,
        "gpsDate": "2024-05-19T12:34:56Z",
        "appBuild": "1.2.3",
        "iccid": "8901260000000000000",
    }
    if extra_fields:
        device.update(extra_fields)
    return {"data": {"Devices": [device]}}


def test_generate_mqtt_topics_whitelist():
    map_data = _fake_map_data()
    topics = publisher.generate_mqtt_topics(map_data)

    # Whitelisted fields each get their own topic.
    assert "gabb_device/12345/batteryLevel" in topics
    assert "gabb_device/12345/latitude" in topics
    assert "gabb_device/12345/longitude" in topics
    assert "gabb_device/12345/gpsDate" in topics

    # Non-whitelisted fields do NOT get their own topics.
    assert "gabb_device/12345/appBuild" not in topics
    assert "gabb_device/12345/iccid" not in topics

    # Combined location payload exists and carries the non-whitelisted fields
    # as JSON attributes for state_attr() consumers.
    assert "gabb_device/12345/location" in topics
    location = topics["gabb_device/12345/location"]
    assert isinstance(location, dict)
    assert location["latitude"] == 40.0
    assert location["longitude"] == -83.0
    assert location["appBuild"] == "1.2.3"
    assert location["iccid"] == "8901260000000000000"

    # last_updated is always added.
    assert "gabb_device/12345/last_updated" in topics


# ---------------------------------------------------------------------------
# generate_homeassistant_discovery_messages: payload invariants
# ---------------------------------------------------------------------------


def test_generate_homeassistant_discovery_messages_invariants():
    map_data = _fake_map_data()
    messages = publisher.generate_homeassistant_discovery_messages(map_data)

    # Exactly one device -> exactly one discovery message (device-based).
    assert len(messages) == 1

    topic, payload = next(iter(messages.items()))

    # Discovery topic matches the device-based format.
    assert topic == "homeassistant/device/gabb_device_12345/config"

    # Device identifiers unchanged from Group 1.
    assert payload["device"]["identifiers"] == ["gabb_device_12345"]
    assert payload["device"]["name"] == "Gabb Device 12345"

    # Top-level metadata blocks present.
    assert "origin" in payload
    assert "availability" in payload

    components = payload["components"]

    # Sensor whitelist + tracker + last_updated.
    assert "batteryLevel" in components
    assert "latitude" in components
    assert "longitude" in components
    assert "gpsDate" in components
    assert "tracker" in components
    assert "last_updated" in components

    # Non-whitelisted fields don't get components.
    assert "appBuild" not in components
    assert "iccid" not in components

    # Every component has a unique_id matching the expected format.
    for key, component in components.items():
        assert "unique_id" in component, f"missing unique_id on {key}"
        assert component["unique_id"] == f"gabb_device_12345_{key}", (
            f"unique_id mismatch for {key}: {component['unique_id']!r}"
        )

    # Tracker invariants.
    tracker = components["tracker"]
    assert tracker["name"] is None
    assert tracker["source_type"] == "gps"
    assert tracker["platform"] == "device_tracker"

    # batteryLevel invariants.
    battery = components["batteryLevel"]
    assert battery["state_class"] == "measurement"
    assert battery["device_class"] == "battery"
    assert battery["unit_of_measurement"] == "%"

    # Sanity: payload is JSON-serializable (the publisher will json.dumps it).
    json.dumps(payload)


# ---------------------------------------------------------------------------
# _refresh_interval_seconds: env-var resolution
# ---------------------------------------------------------------------------


def test_refresh_interval_explicit_seconds(monkeypatch):
    monkeypatch.setenv("REFRESH_SECONDS", "900")
    monkeypatch.delenv("REFRESH_RATE", raising=False)
    assert publisher._refresh_interval_seconds() == 900


def test_refresh_interval_below_minimum_is_clamped(monkeypatch):
    monkeypatch.setenv("REFRESH_SECONDS", "10")
    monkeypatch.delenv("REFRESH_RATE", raising=False)
    assert publisher._refresh_interval_seconds() == 60


def test_refresh_interval_invalid_seconds_falls_back_to_rate(monkeypatch):
    monkeypatch.setenv("REFRESH_SECONDS", "notanumber")
    monkeypatch.setenv("REFRESH_RATE", "2")
    # REFRESH_RATE=2 -> 600 seconds.
    assert publisher._refresh_interval_seconds() == 600


def test_refresh_interval_rate_2(monkeypatch):
    monkeypatch.delenv("REFRESH_SECONDS", raising=False)
    monkeypatch.setenv("REFRESH_RATE", "2")
    assert publisher._refresh_interval_seconds() == 600


def test_refresh_interval_invalid_rate_uses_default(monkeypatch):
    monkeypatch.delenv("REFRESH_SECONDS", raising=False)
    monkeypatch.setenv("REFRESH_RATE", "99")
    assert publisher._refresh_interval_seconds() == 1800


def test_refresh_interval_no_env_uses_default(monkeypatch):
    monkeypatch.delenv("REFRESH_SECONDS", raising=False)
    monkeypatch.delenv("REFRESH_RATE", raising=False)
    # When neither env var is set, REFRESH_RATE falls through os.getenv's
    # internal default of "1", which maps to 300 seconds (5 minutes). This
    # is the documented legacy default for the 1..4 ladder.
    assert publisher._refresh_interval_seconds() == 300
