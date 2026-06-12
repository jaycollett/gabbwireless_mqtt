"""Device tracker platform for the Gabb Wireless integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GabbConfigEntry, GabbDataUpdateCoordinator
from .entity import GabbEntity

# Fields surfaced as dedicated entities (or core tracker state) that should
# not be duplicated in extra_state_attributes. SafeZones is a nested list and
# is excluded entirely.
EXCLUDED_ATTRIBUTES = {
    "id",
    "latitude",
    "longitude",
    "batteryLevel",
    "gpsDate",
    "online",
    "phoneNumber",
    "imei",
    "firmwareVersion",
    "deviceType",
    "model",
    "SafeZones",
}

ACCURACY_KEYS = ("accuracy", "gpsAccuracy", "locationAccuracy")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GabbConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gabb device trackers from a config entry."""
    coordinator = entry.runtime_data
    known_ids: set[str] = set()

    @callback
    def _async_add_new_devices() -> None:
        new_ids = set(coordinator.data or {}) - known_ids
        if new_ids:
            known_ids.update(new_ids)
            async_add_entities(
                GabbDeviceTracker(coordinator, device_id)
                for device_id in sorted(new_ids)
            )

    _async_add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_devices))


class GabbDeviceTracker(GabbEntity, TrackerEntity):
    """GPS tracker for a single Gabb device."""

    _attr_name = None
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: GabbDataUpdateCoordinator, device_id: str) -> None:
        """Initialize the tracker."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{DOMAIN}_{device_id}_device_tracker"

    @property
    def latitude(self) -> float | None:
        """Return the device latitude."""
        device = self.device or {}
        value = device.get("latitude")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def longitude(self) -> float | None:
        """Return the device longitude."""
        device = self.device or {}
        value = device.get("longitude")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def battery_level(self) -> int | None:
        """Return the device battery level."""
        device = self.device or {}
        value = device.get("batteryLevel")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def location_accuracy(self) -> float:
        """Return the GPS accuracy if the API provides one."""
        device = self.device or {}
        for key in ACCURACY_KEYS:
            value = device.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose remaining simple device fields as attributes."""
        device = self.device or {}
        return {
            key: value
            for key, value in device.items()
            if key not in EXCLUDED_ATTRIBUTES
            and isinstance(value, (str, int, float, bool, type(None)))
        }
