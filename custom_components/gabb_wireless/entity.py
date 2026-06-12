"""Base entity for the Gabb Wireless integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GabbDataUpdateCoordinator


def device_display_name(device_id: str, device: dict[str, Any] | None) -> str:
    """Return the display name for a Gabb device."""
    if device:
        name = device.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return f"Gabb Device {device_id}"


class GabbEntity(CoordinatorEntity[GabbDataUpdateCoordinator]):
    """Common base for all Gabb Wireless entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GabbDataUpdateCoordinator, device_id: str) -> None:
        """Initialize the entity for one physical device."""
        super().__init__(coordinator)
        self._device_id = device_id
        device = coordinator.data.get(device_id) if coordinator.data else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device_display_name(device_id, device),
            manufacturer="Gabb Wireless",
            model=(device or {}).get("model") or (device or {}).get("deviceType"),
            sw_version=(device or {}).get("firmwareVersion"),
        )

    @property
    def device(self) -> dict[str, Any] | None:
        """Return the raw device dict from coordinator data, if present."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._device_id)

    @property
    def available(self) -> bool:
        """Entity is unavailable when the device vanishes from the API."""
        return super().available and self.device is not None
