"""Binary sensor platform for the Gabb Wireless integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GabbConfigEntry, GabbDataUpdateCoordinator
from .entity import GabbEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GabbConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gabb binary sensors from a config entry."""
    coordinator = entry.runtime_data
    known_ids: set[str] = set()

    @callback
    def _async_add_new_devices() -> None:
        new_ids = set(coordinator.data or {}) - known_ids
        if new_ids:
            known_ids.update(new_ids)
            async_add_entities(
                GabbOnlineBinarySensor(coordinator, device_id)
                for device_id in sorted(new_ids)
            )

    _async_add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_devices))


class GabbOnlineBinarySensor(GabbEntity, BinarySensorEntity):
    """Connectivity binary sensor for a Gabb device."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: GabbDataUpdateCoordinator, device_id: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{DOMAIN}_{device_id}_online"

    @property
    def is_on(self) -> bool | None:
        """Return True when the device reports itself online."""
        device = self.device
        if device is None or "online" not in device:
            return None
        return bool(device["online"])
