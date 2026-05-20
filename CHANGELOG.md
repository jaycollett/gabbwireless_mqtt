# Changelog

## [0.2.0] - Unreleased

### Breaking changes

**Display names will change.** Entity IDs (`sensor.gabb_device_<id>_batterylevel`, etc.) are preserved, but friendly names now follow HA conventions:
- Camel-cased names are split (e.g., "Batterylevel" → "Battery Level")
- `has_entity_name: true` means the device name is auto-prepended (e.g., "Gabb Device 12345 Battery Level")

*Mitigation:* automations that reference `entity_id` keep working. Automations that key off `friendly_name` or `state_attr(..., 'friendly_name')` need updating. The device tracker now inherits the device name (no more "Gabb Device 12345 Gabb Device 12345").

**MQTT discovery switched to device-based format.** The publisher now sends one combined discovery message per device at `homeassistant/device/gabb_device_<id>/config`. Old per-entity discovery topics (`homeassistant/sensor/gabb_device_<id>/<key>/config` and `homeassistant/device_tracker/gabb_device_<id>/config`) are cleared with an empty retained payload on first startup of the new version.

*Mitigation:* HA migrates automatically because `unique_id` and `device.identifiers` are unchanged — entity IDs persist across the format change. If you see duplicated entities after upgrade, restart HA once.

**Sensors now have `expire_after` set to `2 × refresh_interval`.** When the publisher hasn't reported in for that long (e.g., container down, Gabb API outage), HA will show entities as Unavailable instead of stuck-stale data.

*Mitigation:* none needed — this is generally desirable. If you have automations triggered by state changes, add a `not in ['unavailable', 'unknown']` condition for safety.

**Diagnostic entities** (IMEI, firmware version, phone number, etc.) are moved to the device's Diagnostic section in the HA UI. They keep the same entity IDs.

*Mitigation:* they're still available in automations by entity_id. To re-add them to a dashboard card, add them explicitly.

### Added

- HA availability topic with Last Will Testament (`gabb_device/availability`) — HA shows entities as Unavailable when the publisher process is down.
- `origin` block in discovery payload — easier troubleshooting from the HA logs/UI.
- `state_class: measurement` on battery level — enables HA long-term statistics / nice history graphs.
- `source_type: gps` on the device tracker — proper categorization in HA UI.
- `has_entity_name: true` on all entities — names follow current HA naming conventions.
- Removed dead `weight` device class (Gabb devices don't report weight).

### Fixed

- Camel-case entity names were collapsing to single-cap (e.g., "Batterylevel"). Now correctly split ("Battery Level").
- `gpsDate` is normalized to RFC3339 with timezone before publishing so HA's `timestamp` device class accepts it.
- Discovery and state messages are now published with `retain=True`. HA no longer loses Gabb entities when it restarts.
