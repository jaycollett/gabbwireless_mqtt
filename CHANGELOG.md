# Changelog

## [0.3.0] - 2026-05-20

A modernization release: HA MQTT discovery brought up to current standards, reliability hardened, and dev tooling added. Existing automations that reference `entity_id` continue to work (all `unique_id` values are byte-identical to 0.1.x). Read the breaking-changes section before upgrading.

### Breaking changes

Each item lists what changes and what (if anything) you should do.

#### 1. Display names and friendly names change

Entity IDs are preserved (e.g. `sensor.gabb_device_12345_batterylevel` still works in automations), but the **display / friendly names** change in two ways:

- Camel-cased field names are now correctly split. "Batterylevel" becomes "Battery Level", "Gpsdate" becomes "Gps Date", etc.
- `has_entity_name: true` is set on every entity, so HA automatically prepends the device name in the UI. Friendly name becomes "Gabb Device 12345 Battery Level" instead of just "Batterylevel".
- The device tracker's name is now `null`, so HA shows it as just "Gabb Device 12345" instead of "Gabb Device 12345 Gabb Device 12345".

**Mitigation:** automations that reference `entity_id` are unaffected. Automations that key off `friendly_name` or `state_attr(..., 'friendly_name')` may need their string match updated. Lovelace dashboards that hard-coded the old display name will need a refresh.

#### 2. MQTT discovery moved to device-based format

The publisher now sends one combined discovery payload per device at `homeassistant/device/gabb_device_<id>/config`. Old per-entity discovery topics are cleared with an empty retained payload on first startup of the new version.

**Mitigation:** automatic. `unique_id` and `device.identifiers` are unchanged, so HA's entity registry matches existing entities and keeps the same entity IDs. If you see duplicated entities after upgrade (rare), restart HA once to reconcile.

#### 3. Some fields move from "sensor" to "device tracker attribute"

To reduce HA entity-list clutter, only these fields are now exposed as individual sensors:

```
batteryLevel, latitude, longitude, gpsDate, online,
phoneNumber, imei, firmwareVersion, deviceType, model
```

Other fields from the Gabb API (e.g. `appBuild`, `iccid`, `serialNumber`, internal IDs) are still published, but as JSON attributes on the device_tracker rather than as standalone sensors.

**Mitigation:** if an automation referenced a removed sensor like `sensor.gabb_device_12345_appbuild`, change it to read the value as a tracker attribute:

```yaml
# Old
{{ states('sensor.gabb_device_12345_appbuild') }}

# New
{{ state_attr('device_tracker.gabb_device_12345', 'appBuild') }}
```

The data is unchanged. Only its presentation moved.

#### 4. Diagnostic entities move off the main device card

Identity fields (`imei`, `firmwareVersion`, `phoneNumber`, `appBuild`, `deviceType`, `iccid`, `serialNumber`, `mac`, `model`, `manufacturer`, `id`) are marked `entity_category: diagnostic`. They appear in the device's "Diagnostic" section in the HA UI instead of the main card.

**Mitigation:** entity IDs unchanged, so automations are unaffected. To put them back on a dashboard card, add them explicitly.

#### 5. Entities go "Unavailable" on stale data

Sensors now have `expire_after` set to `2 × refresh_interval`. When the publisher hasn't reported in for that long (container down, Gabb API outage, etc.), HA marks the entities Unavailable instead of leaving stuck-stale values. There is also a new availability topic and Last Will, so the entire device goes Unavailable when the publisher is offline.

**Mitigation:** generally desirable. If you have automations triggered by state changes, add `not in ['unavailable', 'unknown']` to the trigger condition or a `state` condition guarding the action.

### Added

- HA availability topic with Last Will Testament (`gabb_device/availability`). HA shows the device as Unavailable cleanly when the publisher dies.
- `origin` block in discovery payload (`name: gabbwireless_mqtt`, software version, repo URL). Easier troubleshooting from HA logs.
- `state_class: measurement` on battery level. Enables HA long-term statistics and history graphs.
- `source_type: gps` on the device tracker. Correct UI categorization.
- `has_entity_name: true` on every entity. Names follow HA conventions.
- `expire_after = 2 × refresh_interval` on non-diagnostic sensors.
- `REFRESH_SECONDS` env var. Set the poll interval directly in seconds (minimum 60).
- Heartbeat file `/tmp/gabb_heartbeat`. Touched after every successful iteration. The Dockerfile `HEALTHCHECK` verifies the file's mtime is within 90 minutes instead of doing a meaningless `import` check.
- MQTT_PORT auto-defaults to `8883` when `MQTT_TLS=true` and port is unset.
- Subscribed to `homeassistant/status`; on HA `online` birth message, the publisher republishes discovery so HA picks up everything cleanly after a restart.
- Pytest unit tests covering name humanization, timestamp normalization, sensor whitelisting, discovery payload invariants, and refresh-interval env-var resolution.

### Fixed

- Camel-case entity names collapsing to single-cap (e.g., "Batterylevel"). Now correctly split.
- `gpsDate` is normalized to RFC3339 with timezone before publishing so HA's `timestamp` device class accepts it.
- Discovery and state messages are now published with `retain=True`. HA no longer loses Gabb entities across a restart.
- Removed dead `weight` device class (Gabb devices don't report weight).

### Reliability

- `GabbClient` is now reused across iterations instead of fresh-login every poll. Reduces auth load and Gabb-side rate-limit risk.
- Discovery is published only when needed (on connect, on new device, or on HA `online` birth message) rather than every iteration.
- `get_map()` is retried with exponential backoff (0s, 5s, 20s) on transient errors before giving up to the next poll cycle.
- Manual `reconnect()` removed (it competed with paho-mqtt's internal loop). Configured `reconnect_delay_set(1, 120)` instead.
- `mqtt_client.publish()` return codes are now logged when non-success (queue full, not connected, etc.).
- Container exits with code 1 after 10 consecutive failed iterations. Docker/Kubernetes restart policy recovers from situations like expired credentials.
- Removed `PUBLISH_DELAY` artificial sleep. Each poll iteration is ~4 seconds faster.
- Replaced global `_shutdown` flag and `_sleep_interruptible` polling with a `threading.Event`. Cleaner shutdown.

### Changed

- `REFRESH_RATE=1..4` is still supported for backward compatibility. New deployments should use `REFRESH_SECONDS=<n>` instead.
- Transitive dependencies (`certifi`, `charset-normalizer`, `idna`, `urllib3`, `six`) pruned from `requirements.txt`. They still install via `requests` and `python-dateutil`. Reduces Dependabot noise.

### Dev / hygiene

- Dockerfile base image bumped to `python:3.13-slim`.
- Added `requirements-dev.txt` and pytest test suite under `tests/`. Run with `pip install -r requirements-dev.txt && pytest`.
- `__pycache__/` directories removed from the repo.
- README documents `MQTT_TLS`, `MQTT_CA_CERT`, `MQTT_TLS_INSECURE`, `REFRESH_SECONDS`, the heartbeat healthcheck, and the sensor whitelist breaking change.
- GitHub Actions pins moved to floating major tags (`actions/checkout@v6`, etc.).

### Verified invariants (for users with existing automations)

All of these are byte-identical to 0.1.x. If you have automations or templates that reference any of these, they continue to work without modification:

- Per-sensor `unique_id`: `gabb_device_<device_id>_<key>` (key in original camelCase)
- `last_updated` sensor `unique_id`: `gabb_device_<device_id>_last_updated`
- Device tracker `unique_id`: `gabb_device_<device_id>_tracker`
- Device identifiers: `["gabb_device_<device_id>"]`
- State topics: `gabb_device/<device_id>/<key>`, `gabb_device/<device_id>/location`, `gabb_device/<device_id>/last_updated`
