"""DataUpdateCoordinator for the Gabb Wireless integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GabbApiClient, GabbAuthError, GabbConnectionError
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type GabbConfigEntry = ConfigEntry[GabbDataUpdateCoordinator]


class GabbDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator that polls the Gabb map endpoint for all devices."""

    config_entry: GabbConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: GabbConfigEntry,
        client: GabbApiClient,
    ) -> None:
        """Initialize the coordinator with the configured update interval."""
        scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        try:
            scan_interval = max(int(scan_interval), MIN_SCAN_INTERVAL)
        except (TypeError, ValueError):
            scan_interval = DEFAULT_SCAN_INTERVAL
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch the latest device data from the Gabb API."""
        try:
            return await self.client.async_get_devices()
        except GabbAuthError as err:
            raise ConfigEntryAuthFailed(
                "Gabb Wireless credentials are no longer valid"
            ) from err
        except GabbConnectionError as err:
            raise UpdateFailed(f"Error communicating with the Gabb API: {err}") from err
