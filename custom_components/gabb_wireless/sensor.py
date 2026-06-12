"""Sensor platform for the Gabb Wireless integration."""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import GabbConfigEntry, GabbDataUpdateCoordinator
from .entity import GabbEntity


def _parse_gps_date(value: Any) -> datetime.datetime | None:
    """Parse the gpsDate field into a tz-aware datetime (assume UTC if naive)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 1e11 else value
        try:
            return dt_util.utc_from_timestamp(timestamp)
        except (OverflowError, OSError, ValueError):
            return None
    parsed = dt_util.parse_datetime(str(value))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _string_or_none(value: Any) -> str | None:
    """Return the value as a string, or None when empty/missing."""
    if value is None or value == "":
        return None
    return str(value)


@dataclass(frozen=True, kw_only=True)
class GabbSensorEntityDescription(SensorEntityDescription):
    """Describes a Gabb Wireless sensor."""

    value_fn: Callable[[dict[str, Any]], StateType | datetime.datetime]


SENSOR_DESCRIPTIONS: tuple[GabbSensorEntityDescription, ...] = (
    GabbSensorEntityDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: (
            int(device["batteryLevel"])
            if isinstance(device.get("batteryLevel"), (int, float))
            else None
        ),
    ),
    GabbSensorEntityDescription(
        key="gps_date",
        translation_key="gps_date",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda device: _parse_gps_date(device.get("gpsDate")),
    ),
    GabbSensorEntityDescription(
        key="phone_number",
        translation_key="phone_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: _string_or_none(device.get("phoneNumber")),
    ),
    GabbSensorEntityDescription(
        key="imei",
        translation_key="imei",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: _string_or_none(device.get("imei")),
    ),
    GabbSensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: _string_or_none(device.get("firmwareVersion")),
    ),
    GabbSensorEntityDescription(
        key="device_type",
        translation_key="device_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: _string_or_none(device.get("deviceType")),
    ),
    GabbSensorEntityDescription(
        key="model",
        translation_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: _string_or_none(device.get("model")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GabbConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gabb sensors from a config entry."""
    coordinator = entry.runtime_data
    known_ids: set[str] = set()

    @callback
    def _async_add_new_devices() -> None:
        new_ids = set(coordinator.data or {}) - known_ids
        if new_ids:
            known_ids.update(new_ids)
            async_add_entities(
                GabbSensor(coordinator, device_id, description)
                for device_id in sorted(new_ids)
                for description in SENSOR_DESCRIPTIONS
            )

    _async_add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_devices))


class GabbSensor(GabbEntity, SensorEntity):
    """A sensor backed by one field of a Gabb device."""

    entity_description: GabbSensorEntityDescription

    def __init__(
        self,
        coordinator: GabbDataUpdateCoordinator,
        device_id: str,
        description: GabbSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{device_id}_{description.key}"

    @property
    def native_value(self) -> StateType | datetime.datetime:
        """Return the sensor value from coordinator data."""
        device = self.device
        if device is None:
            return None
        return self.entity_description.value_fn(device)
