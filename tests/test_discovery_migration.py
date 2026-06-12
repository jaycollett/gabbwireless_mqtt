"""Tests for the HA discovery publish/migration flow using a fake MQTT client.

Covers the official migrate_discovery sequencing (signal -> publish new
device discovery -> clear legacy topics) and the once-per-device gating of
both the migration and the device-based discovery publish.
"""

import json

import paho.mqtt.client as mqtt
import pytest

import gabb_mqtt_publisher as publisher


class FakeMessageInfo:
    rc = mqtt.MQTT_ERR_SUCCESS

    def wait_for_publish(self, timeout=None):
        return None


class FakeMqttClient:
    def __init__(self):
        self.published: list[tuple[str, str, int, bool]] = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        return FakeMessageInfo()

    def is_connected(self):
        return True


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Module-level once-per-process sets must not leak between tests."""
    publisher._cleaned_legacy_discovery.clear()
    publisher._discovery_published_for.clear()
    yield
    publisher._cleaned_legacy_discovery.clear()
    publisher._discovery_published_for.clear()


def _fake_map_data() -> dict:
    return {
        "data": {
            "Devices": [
                {
                    "id": 12345,
                    "batteryLevel": 87,
                    "latitude": 40.0,
                    "longitude": -83.0,
                }
            ]
        }
    }


def test_signal_legacy_migration_publishes_migrate_discovery():
    client = FakeMqttClient()
    pending = publisher.signal_legacy_discovery_migration(client, _fake_map_data())

    assert list(pending.keys()) == ["12345"]
    topics = pending["12345"]
    assert "homeassistant/device_tracker/gabb_device_12345/config" in topics
    assert "homeassistant/sensor/gabb_device_12345/batteryLevel/config" in topics
    assert "homeassistant/sensor/gabb_device_12345/last_updated/config" in topics

    # Every legacy topic got the official migrate_discovery payload, retained.
    assert len(client.published) == len(topics)
    for topic, payload, qos, retain in client.published:
        assert topic in topics
        assert json.loads(payload) == {"migrate_discovery": True}
        assert retain is True

    # Signaling alone must NOT mark the device as cleaned; that only happens
    # after the legacy topics are actually cleared.
    assert "12345" not in publisher._cleaned_legacy_discovery


def test_clear_legacy_topics_publishes_empty_and_marks_cleaned():
    client = FakeMqttClient()
    pending = {"12345": ["homeassistant/sensor/gabb_device_12345/batteryLevel/config"]}

    publisher.clear_legacy_discovery_topics(client, pending)

    assert client.published == [
        ("homeassistant/sensor/gabb_device_12345/batteryLevel/config", "", 1, True)
    ]
    assert "12345" in publisher._cleaned_legacy_discovery


def test_migration_runs_once_per_device():
    client = FakeMqttClient()
    map_data = _fake_map_data()

    pending = publisher.signal_legacy_discovery_migration(client, map_data)
    publisher.clear_legacy_discovery_topics(client, pending)

    # Second sweep is a no-op: nothing pending, nothing published.
    client.published.clear()
    assert publisher.signal_legacy_discovery_migration(client, map_data) == {}
    assert client.published == []


def test_full_migration_sequence_orders_correctly():
    """migrate_discovery -> new device discovery -> legacy clear (HA docs order)."""
    client = FakeMqttClient()
    map_data = _fake_map_data()

    pending = publisher.signal_legacy_discovery_migration(client, map_data)
    publisher.publish_discovery_for_new_devices(client, map_data)
    publisher.clear_legacy_discovery_topics(client, pending)

    n_legacy = len(pending["12345"])
    publishes = client.published

    # Phase 1: migrate_discovery on all legacy topics.
    for topic, payload, _, _ in publishes[:n_legacy]:
        assert json.loads(payload) == {"migrate_discovery": True}

    # Phase 2: exactly one device-based discovery publish.
    topic, payload, qos, retain = publishes[n_legacy]
    assert topic == "homeassistant/device/gabb_device_12345/config"
    assert "components" in json.loads(payload)
    assert retain is True

    # Phase 3: empty retained payloads clear the same legacy topics.
    clears = publishes[n_legacy + 1 :]
    assert sorted(t for t, *_ in clears) == sorted(pending["12345"])
    assert all(payload == "" for _, payload, _, _ in clears)


def test_publish_discovery_only_once_per_device():
    client = FakeMqttClient()
    map_data = _fake_map_data()

    assert publisher.publish_discovery_for_new_devices(client, map_data) == 1
    assert "12345" in publisher._discovery_published_for

    # Already published: no new publishes.
    client.published.clear()
    assert publisher.publish_discovery_for_new_devices(client, map_data) == 0
    assert client.published == []

    # HA birth message clears the cache -> republished.
    publisher._discovery_published_for.clear()
    assert publisher.publish_discovery_for_new_devices(client, map_data) == 1


def test_publish_state_topics_counts_successes():
    client = FakeMqttClient()
    topics = {"gabb_device/12345/batteryLevel": 87, "gabb_device/12345/location": {"a": 1}}
    assert publisher.publish_state_topics(client, topics) == 2
    payloads = {t: p for t, p, _, _ in client.published}
    assert payloads["gabb_device/12345/batteryLevel"] == "87"
    assert json.loads(payloads["gabb_device/12345/location"]) == {"a": 1}
