"""Tests for Gabb Wireless config entry setup."""

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.gabb_wireless.const import DOMAIN

SAMPLE_DEVICE = {
    "id": 123,
    "batteryLevel": 88,
    "latitude": 35.1234,
    "longitude": -80.5678,
    "gpsDate": "2026-06-12T10:00:00Z",
    "online": True,
    "phoneNumber": "+15555555555",
    "imei": "490154203237518",
    "firmwareVersion": "1.2.3",
    "deviceType": "Watch",
    "model": "Gabb Watch 3",
    "appBuild": "1.28",
    "iccid": "89014103211118510720",
    "serialNumber": "SN0001",
    "SafeZones": [{"Name": "Home"}],
}


async def test_setup_creates_entities(hass: HomeAssistant) -> None:
    """A successful setup creates tracker, sensor, and binary sensor entities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="parent@example.com",
        data={"username": "parent@example.com", "password": "hunter2"},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.gabb_wireless.GabbApiClient.async_get_devices",
        return_value={"123": SAMPLE_DEVICE},
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)

    tracker_id = registry.async_get_entity_id(
        "device_tracker", DOMAIN, "gabb_wireless_123_device_tracker"
    )
    assert tracker_id is not None
    tracker_state = hass.states.get(tracker_id)
    assert tracker_state is not None
    assert tracker_state.attributes["latitude"] == 35.1234
    assert tracker_state.attributes["longitude"] == -80.5678
    assert tracker_state.attributes["battery_level"] == 88
    # Extra attributes carry leftover fields but never the nested SafeZones.
    assert tracker_state.attributes["serialNumber"] == "SN0001"
    assert "SafeZones" not in tracker_state.attributes

    battery_id = registry.async_get_entity_id(
        "sensor", DOMAIN, "gabb_wireless_123_battery"
    )
    assert battery_id is not None
    assert hass.states.get(battery_id).state == "88"

    gps_date_id = registry.async_get_entity_id(
        "sensor", DOMAIN, "gabb_wireless_123_gps_date"
    )
    assert gps_date_id is not None
    assert hass.states.get(gps_date_id).state == "2026-06-12T10:00:00+00:00"

    online_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, "gabb_wireless_123_online"
    )
    assert online_id is not None
    assert hass.states.get(online_id).state == "on"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
