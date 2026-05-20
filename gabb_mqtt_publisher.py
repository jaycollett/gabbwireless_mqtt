import logging
import os
import re
import signal
import ssl
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from gabb import GabbClient
import json
import paho.mqtt.client as mqtt
import requests
from dateutil import parser as date_parser

__version__ = "0.2.0"

# Logging setup
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("gabb_mqtt_publisher")


def _require_env(name: str) -> str:
    """Return the env var value, or exit with a clear error if missing/empty."""
    val = os.getenv(name, "").strip()
    if not val:
        log.error("Required environment variable %s is not set.", name)
        sys.exit(2)
    return val


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Configurable Variables from Environment (fail fast on required creds)
GABB_USERNAME = _require_env("GABB_USERNAME")
GABB_PASSWORD = _require_env("GABB_PASSWORD")

MQTT_BROKER = _require_env("MQTT_BROKER")
MQTT_USERNAME = _require_env("MQTT_USERNAME")
MQTT_PASSWORD = _require_env("MQTT_PASSWORD")

MQTT_TLS = _bool_env("MQTT_TLS", False)
MQTT_CA_CERT = os.getenv("MQTT_CA_CERT", "").strip() or None
MQTT_TLS_INSECURE = _bool_env("MQTT_TLS_INSECURE", False)


def _mqtt_default_port() -> int:
    """Default MQTT_PORT to 8883 when TLS is on and no explicit port was set."""
    explicit = os.getenv("MQTT_PORT", "").strip()
    if explicit:
        return int(explicit)
    return 8883 if MQTT_TLS else 1883


MQTT_PORT = _mqtt_default_port()

if MQTT_PASSWORD and not MQTT_TLS:
    log.warning(
        "MQTT_PASSWORD is set but MQTT_TLS is not enabled; credentials will traverse the network in plaintext."
    )

DEVICE_MODEL = "Gabb Device"
DEVICE_MANUFACTURER = "Gabb Wireless"
ROOT_TOPIC = "gabb_device"
AVAILABILITY_TOPIC = f"{ROOT_TOPIC}/availability"
HA_STATUS_TOPIC = "homeassistant/status"
HEARTBEAT_FILE = Path("/tmp/gabb_heartbeat")
MAX_CONSECUTIVE_FAILURES = 10


def _refresh_interval_seconds() -> int:
    """Resolve the polling interval, preferring REFRESH_SECONDS when set.

    REFRESH_SECONDS is the explicit, human-friendly knob. REFRESH_RATE
    (1..4) is kept for backward compatibility with older deployments.
    """
    explicit = os.getenv("REFRESH_SECONDS")
    if explicit:
        try:
            value = int(explicit)
            if value < 60:
                log.warning("REFRESH_SECONDS=%d below minimum 60; clamping to 60.", value)
                return 60
            return value
        except ValueError:
            log.warning("REFRESH_SECONDS=%r is not an integer; falling back to REFRESH_RATE.", explicit)
    legacy = int(os.getenv("REFRESH_RATE", "1"))
    return {1: 300, 2: 600, 3: 1800, 4: 3600}.get(legacy, 1800)


LOOP_DELAY = _refresh_interval_seconds()

# Keys that are slow-moving identity/firmware data and should be filed under
# the device's "Diagnostic" UI section rather than the main card.
DIAGNOSTIC_KEYS = {
    "imei",
    "firmwareVersion",
    "appBuild",
    "deviceType",
    "iccid",
    "phoneNumber",
    "serialNumber",
    "mac",
    "model",
    "manufacturer",
    "id",
}

# Whitelist of device fields exposed as individual HA sensor entities. Fields
# outside this set are still published, but they ride as JSON attributes on the
# device_tracker rather than cluttering HA's entity list. Keep in sync with the
# CHANGELOG entry that documents the breaking change.
SENSOR_FIELDS = {
    "batteryLevel",
    "latitude",
    "longitude",
    "gpsDate",
    "online",
    "phoneNumber",
    "imei",
    "firmwareVersion",
    "deviceType",
    "model",
}

# Tracks which devices we've already published an old-discovery cleanup for in
# this process lifetime, so we only fire the migration sweep once per device.
_cleaned_legacy_discovery: set[str] = set()

# Tracks which devices we've already published the new device-based discovery
# for in this process lifetime. Cleared when HA sends the "online" birth
# message so HA picks discovery back up after a restart, and updated when a
# new device appears mid-process.
_discovery_published_for: set[str] = set()
_discovery_lock = threading.Lock()

# Shutdown coordinator. threading.Event lets us wait()-with-timeout and break
# out of the sleep immediately on SIGTERM/SIGINT.
shutdown = threading.Event()

# Global MQTT client (paho-mqtt v2 API)
mqtt_client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    protocol=mqtt.MQTTv5,
)


def humanize_key(key: str) -> str:
    """Convert a camelCase or snake_case key into a Title Cased label.

    e.g. ``batteryLevel`` -> ``"Battery Level"``, ``gpsDate`` -> ``"Gps Date"``.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key)
    spaced = re.sub(r"_", " ", spaced)
    return spaced.title()


def normalize_timestamp(value):
    """Return an ISO 8601 / RFC3339 string with tz info, or None on failure.

    HA's ``timestamp`` device class rejects payloads without a timezone offset.
    """
    if value is None or value == "":
        return None
    try:
        dt = date_parser.parse(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


def on_connect(client, userdata, flags, reason_code, properties=None):
    log.info("Connected to MQTT broker (reason_code=%s).", reason_code)
    # Publish online availability so HA knows the publisher is up. Retained so
    # late-subscribing HA instances pick it up immediately.
    try:
        client.publish(AVAILABILITY_TOPIC, "online", qos=1, retain=True)
    except Exception:
        log.exception("Failed to publish availability=online on connect.")

    # (Re)subscribe to HA's birth/will topic so we can re-publish discovery
    # whenever HA restarts. Subscribing in on_connect ensures the subscription
    # is restored automatically after any reconnect.
    try:
        result, _ = client.subscribe(HA_STATUS_TOPIC, qos=0)
        if result == mqtt.MQTT_ERR_SUCCESS:
            log.debug("Subscribed to %s for HA birth notifications.", HA_STATUS_TOPIC)
        else:
            log.warning("Failed to subscribe to %s (rc=%s).", HA_STATUS_TOPIC, result)
    except Exception:
        log.exception("Failed to subscribe to %s.", HA_STATUS_TOPIC)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    log.warning("Disconnected from MQTT broker (reason_code=%s).", reason_code)


def on_message(client, userdata, message):
    try:
        payload = message.payload.decode(errors="replace")
    except Exception:
        payload = "<undecodable>"
    log.debug("Received message on topic %s: %s", message.topic, payload)

    # HA birth message: when HA comes (back) online it publishes "online" on
    # homeassistant/status. Clear our discovery cache so the next iteration
    # republishes device-based discovery and HA repopulates its entity list.
    if message.topic == HA_STATUS_TOPIC and payload.strip().lower() == "online":
        with _discovery_lock:
            n = len(_discovery_published_for)
            _discovery_published_for.clear()
        log.info("HA birth message received; cleared discovery cache for %d device(s).", n)


def setup_mqtt_client():
    """
    Setup and connect the MQTT client, and start its network loop.
    """
    global mqtt_client
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    # Let paho's network thread manage reconnection backoff for us. Must be
    # set BEFORE connect()/loop_start() so the first reconnect attempt picks
    # up the configured bounds.
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)

    # Last Will & Testament: if the client drops without a clean disconnect,
    # the broker publishes "offline" on the availability topic so HA can mark
    # all gabb entities Unavailable. Must be set BEFORE connect().
    mqtt_client.will_set(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)

    if MQTT_TLS:
        try:
            mqtt_client.tls_set(
                ca_certs=MQTT_CA_CERT,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
            if MQTT_TLS_INSECURE:
                mqtt_client.tls_insecure_set(True)
                log.warning("MQTT_TLS_INSECURE=true: peer certificate hostname will not be verified.")
        except Exception:
            log.exception("Failed to configure MQTT TLS.")
            raise

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        log.info("Connecting to MQTT broker at %s:%s (tls=%s).", MQTT_BROKER, MQTT_PORT, MQTT_TLS)
    except Exception:
        log.exception("Failed to connect to MQTT broker.")
        raise

    # Start the network loop in a background thread so keepalives, reconnects,
    # and callbacks (on_connect/on_disconnect) fire correctly.
    mqtt_client.loop_start()


def fetch_map_with_retry(client, max_attempts=3, backoff=(0, 5, 20)):
    """Fetch map data with bounded exponential backoff on transient failures.

    Returns the parsed JSON dict on success, or ``None`` if every attempt
    failed (the caller falls through to its usual long sleep). Shutdown is
    honored between attempts so SIGTERM doesn't get blocked behind a retry.
    """
    last_err = None
    for attempt in range(max_attempts):
        if attempt > 0:
            # Bail early if a shutdown was requested mid-backoff.
            if shutdown.wait(backoff[attempt]):
                return None
        try:
            resp = client.get_map()
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            log.warning("get_map attempt %d/%d failed: %s", attempt + 1, max_attempts, e)
    log.error("get_map failed after %d attempts: %s", max_attempts, last_err)
    return None


def _is_auth_failure(exc: BaseException) -> bool:
    """Return True if exc looks like an HTTP 401/403 from the Gabb API.

    These auth failures usually indicate token/credential rot and warrant
    re-creating the GabbClient on the next iteration to force a fresh login.
    """
    if isinstance(exc, requests.exceptions.HTTPError):
        resp = getattr(exc, "response", None)
        if resp is not None and resp.status_code in (401, 403):
            return True
    return False


def _publish_with_rc(client, topic, payload, *, qos=1, retain=True, wait=False, wait_timeout=5):
    """Publish and log non-success return codes.

    Returns the MQTTMessageInfo so callers can inspect/wait if they need to.
    """
    info = client.publish(topic, payload, qos=qos, retain=retain)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        log.warning("Publish to %s returned rc=%s", topic, info.rc)
        return info
    if wait and qos > 0:
        try:
            info.wait_for_publish(timeout=wait_timeout)
        except (ValueError, RuntimeError) as e:
            log.warning("wait_for_publish on %s failed: %s", topic, e)
    return info


def generate_mqtt_topics(map_data, root_topic=ROOT_TOPIC):
    """
    Generate MQTT topics for devices and their properties.

    Whitelisted sensor fields each get their own topic. Non-sensor fields are
    bundled into the device's ``location`` payload as JSON attributes so users
    can still surface them via ``state_attr()`` templates without polluting
    HA's entity list with one entity per Gabb field.
    """
    devices = map_data.get("data", {}).get("Devices", [])
    if not devices:
        log.info("No devices found in the map data.")
        return {}

    mqtt_topics = {}
    for device in devices:
        device_id = device.get("id", "unknown")
        topic_prefix = f"{root_topic}/{device_id}"

        # SafeZones is a nested list we never publish. Pop it at the known
        # location instead of walking the entire tree.
        device.pop("SafeZones", None)

        # Sensor-eligible fields each get their own topic (preserving the
        # original gabb_device/<id>/<key> format for entity_id stability).
        for key, value in device.items():
            if key not in SENSOR_FIELDS:
                continue
            topic = f"{topic_prefix}/{key}"
            if key == "gpsDate":
                normalized = normalize_timestamp(value)
                if normalized is None:
                    continue
                mqtt_topics[topic] = normalized
            else:
                mqtt_topics[topic] = value

        # Combined location topic doubles as the tracker's json_attributes_topic.
        # Extra (non-sensor) fields ride along so users can read them as
        # tracker attributes via state_attr(device_tracker.<id>, 'appBuild').
        if "longitude" in device and "latitude" in device:
            location_payload: dict = {
                "latitude": device["latitude"],
                "longitude": device["longitude"],
            }
            if "gpsDate" in device:
                normalized_gps = normalize_timestamp(device["gpsDate"])
                if normalized_gps is not None:
                    location_payload["LastGPSUpdate"] = normalized_gps
            for k, v in device.items():
                if k in SENSOR_FIELDS or k in {"latitude", "longitude", "gpsDate"}:
                    continue
                # Only carry JSON-serializable scalars / containers; the
                # location payload is shipped as a single JSON document.
                try:
                    json.dumps(v)
                except (TypeError, ValueError):
                    continue
                location_payload[k] = v
            mqtt_topics[f"{topic_prefix}/location"] = location_payload

        # Add a current UTC timestamp as a sensor
        current_utc_time = datetime.now(timezone.utc).isoformat()
        mqtt_topics[f"{topic_prefix}/last_updated"] = current_utc_time

    return mqtt_topics


def _build_component(
    *,
    platform: str,
    unique_id: str,
    name,
    state_topic: str | None = None,
    device_class=None,
    unit_of_measurement=None,
    state_class=None,
    entity_category=None,
    source_type=None,
    json_attributes_topic=None,
    expire_after=None,
) -> dict:
    """Construct a component entry for the device-based discovery payload.

    Only includes keys with non-None values so HA doesn't see e.g.
    ``"unit_of_measurement": null`` and treat the entity as malformed.
    ``name`` is intentionally always included (None serializes to JSON ``null``)
    so device_tracker components can inherit the device name.
    """
    component: dict = {
        "platform": platform,
        "name": name,
        "unique_id": unique_id,
        "has_entity_name": True,
    }
    # state_topic is required for sensors but optional for device_tracker when
    # json_attributes_topic provides lat/lon (HA derives state from coords).
    if state_topic is not None:
        component["state_topic"] = state_topic
    if device_class is not None:
        component["device_class"] = device_class
    if unit_of_measurement is not None:
        component["unit_of_measurement"] = unit_of_measurement
    if state_class is not None:
        component["state_class"] = state_class
    if entity_category is not None:
        component["entity_category"] = entity_category
    if source_type is not None:
        component["source_type"] = source_type
    if json_attributes_topic is not None:
        component["json_attributes_topic"] = json_attributes_topic
    if expire_after is not None:
        component["expire_after"] = expire_after
    return component


def generate_homeassistant_discovery_messages(map_data, root_topic=ROOT_TOPIC):
    """
    Generate Home Assistant MQTT device-based discovery messages.

    Emits one combined payload per device at
    ``homeassistant/device/{root_topic}_{device_id}/config`` containing all
    sensors and the device tracker as components. unique_ids and
    device.identifiers match the legacy per-entity format byte-for-byte so HA's
    entity registry preserves existing entity_ids across the migration.

    Only fields in ``SENSOR_FIELDS`` produce sensor components. Other fields
    ride as JSON attributes on the device_tracker.
    """
    devices = map_data.get("data", {}).get("Devices", [])
    if not devices:
        log.info("No devices found in the map data.")
        return {}

    # Mapping of keys to device_class and unit_of_measurement
    key_to_device_class = {
        "batteryLevel": {"device_class": "battery", "unit_of_measurement": "%"},
        "longitude": {"device_class": None, "unit_of_measurement": "°"},
        "latitude": {"device_class": None, "unit_of_measurement": "°"},
        "gpsDate": {"device_class": "timestamp", "unit_of_measurement": None},
        "last_updated": {"device_class": "timestamp", "unit_of_measurement": None},
    }

    # HA marks an entity Unavailable if no state update arrives within
    # expire_after seconds. Use 2x the poll interval so a single missed
    # iteration doesn't flap, but a real outage shows up promptly.
    default_expire_after = 2 * LOOP_DELAY

    availability_block = [
        {
            "topic": AVAILABILITY_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
        }
    ]
    origin_block = {
        "name": "gabbwireless_mqtt",
        "sw": __version__,
        "url": "https://github.com/jaycollett/gabbwireless_mqtt",
    }

    discovery_messages = {}
    for device in devices:
        device_id = device.get("id", "unknown")
        device_name = f"Gabb Device {device_id}"
        discovery_topic = f"homeassistant/device/{root_topic}_{device_id}/config"

        components: dict[str, dict] = {}

        for key in device.keys():
            # Only emit a sensor component for whitelisted fields. Non-sensor
            # fields are carried on the device_tracker's json_attributes_topic
            # so the data isn't lost, just not promoted to an entity.
            if key not in SENSOR_FIELDS:
                continue

            mapping = key_to_device_class.get(key, {})
            device_class = mapping.get("device_class")
            unit_of_measurement = mapping.get("unit_of_measurement")
            is_diagnostic = key in DIAGNOSTIC_KEYS
            entity_category = "diagnostic" if is_diagnostic else None

            # state_class: measurement enables HA long-term stats / history
            # graphs on numeric sensors. Battery is the obvious candidate.
            state_class = "measurement" if key == "batteryLevel" else None

            # Diagnostic identity fields rarely change; skip expire_after so
            # they don't go Unavailable just because we haven't polled.
            expire_after = None if is_diagnostic else default_expire_after

            components[key] = _build_component(
                platform="sensor",
                unique_id=f"{root_topic}_{device_id}_{key}",
                state_topic=f"{root_topic}/{device_id}/{key}",
                name=humanize_key(key),
                device_class=device_class,
                unit_of_measurement=unit_of_measurement,
                state_class=state_class,
                entity_category=entity_category,
                expire_after=expire_after,
            )

        # Publisher-side "last updated" timestamp gives users a freshness
        # signal even when individual sensors haven't changed value.
        components["last_updated"] = _build_component(
            platform="sensor",
            unique_id=f"{root_topic}_{device_id}_last_updated",
            state_topic=f"{root_topic}/{device_id}/last_updated",
            name="Last Updated",
            device_class="timestamp",
            entity_category="diagnostic",
        )

        # Device tracker: name=None lets it inherit the device name so the UI
        # doesn't render "Gabb Device 12345 Gabb Device 12345". No state_topic
        # is set — HA derives the state from the lat/lon in
        # json_attributes_topic, matching the original behavior.
        if "longitude" in device and "latitude" in device:
            components["tracker"] = _build_component(
                platform="device_tracker",
                unique_id=f"{root_topic}_{device_id}_tracker",
                name=None,
                source_type="gps",
                json_attributes_topic=f"{root_topic}/{device_id}/location",
            )

        payload = {
            "device": {
                "identifiers": [f"{root_topic}_{device_id}"],
                "name": device_name,
                "model": DEVICE_MODEL,
                "manufacturer": DEVICE_MANUFACTURER,
            },
            "origin": origin_block,
            "availability": availability_block,
            "components": components,
        }
        discovery_messages[discovery_topic] = payload

    return discovery_messages


def clear_legacy_discovery_topics(map_data, root_topic=ROOT_TOPIC):
    """Publish empty retained payloads on the old per-entity discovery topics.

    The old format placed each sensor/tracker under its own ``config`` topic.
    The new device-based discovery uses a single combined topic, so the legacy
    ones need to be retracted from the broker or HA will keep recreating ghost
    entities on every restart.

    Runs at most once per device per process lifetime (tracked via
    ``_cleaned_legacy_discovery``) so we don't spam the broker on every poll.
    """
    global mqtt_client
    devices = map_data.get("data", {}).get("Devices", [])
    if not devices:
        return

    for device in devices:
        device_id = device.get("id", "unknown")
        if device_id in _cleaned_legacy_discovery:
            continue

        topics_to_clear = [
            f"homeassistant/sensor/{root_topic}_{device_id}/{key}/config"
            for key in device.keys()
        ]
        topics_to_clear.append(f"homeassistant/sensor/{root_topic}_{device_id}/last_updated/config")
        topics_to_clear.append(f"homeassistant/device_tracker/{root_topic}_{device_id}/config")

        for topic in topics_to_clear:
            try:
                # Empty payload + retain=True tells the broker to drop the
                # retained message entirely (per MQTT spec).
                _publish_with_rc(mqtt_client, topic, "", qos=1, retain=True)
                log.debug("Cleared legacy discovery topic %s", topic)
            except Exception:
                log.exception("Failed to clear legacy discovery topic %s", topic)

        _cleaned_legacy_discovery.add(device_id)
        log.info("Cleared %d legacy discovery topics for device %s.", len(topics_to_clear), device_id)


def publish_discovery_for_new_devices(map_data):
    """Publish device-based discovery for any device not yet seen this run.

    Discovery is retained on the broker, so we only need to publish once per
    device per process lifetime (re-publish is triggered separately when HA
    sends the ``homeassistant/status: online`` birth message). Returns the
    number of devices whose discovery was (re)published.
    """
    global mqtt_client

    discovery_messages = generate_homeassistant_discovery_messages(map_data)
    if not discovery_messages:
        return 0

    devices = map_data.get("data", {}).get("Devices", [])
    new_devices = []
    for device in devices:
        device_id = str(device.get("id", "unknown"))
        with _discovery_lock:
            already_published = device_id in _discovery_published_for
        if already_published:
            continue
        new_devices.append(device_id)

    if not new_devices:
        return 0

    log.info("Publishing discovery for %d device(s): %s", len(new_devices), new_devices)
    published = 0
    for topic, payload in discovery_messages.items():
        # Map the topic back to a device_id so we only mark the ones we
        # actually managed to publish.
        # Topic format: homeassistant/device/{root_topic}_{device_id}/config
        try:
            device_id = topic.split("/")[2].split("_", 2)[-1]
        except IndexError:
            device_id = None

        with _discovery_lock:
            if device_id and device_id in _discovery_published_for:
                continue

        try:
            info = _publish_with_rc(
                mqtt_client,
                topic,
                json.dumps(payload),
                qos=1,
                retain=True,
                wait=True,
                wait_timeout=5,
            )
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                if device_id:
                    with _discovery_lock:
                        _discovery_published_for.add(device_id)
                published += 1
                log.debug("Published Home Assistant discovery message to %s", topic)
            else:
                log.warning("Discovery publish to %s did not succeed; will retry next iteration.", topic)
        except Exception:
            log.exception("Failed to publish Home Assistant discovery message to %s", topic)

    return published


def publish_state_topics(mqtt_topics):
    """Publish state topics. Returns count of topics whose publish enqueued OK."""
    global mqtt_client

    if not mqtt_client.is_connected():
        log.warning("MQTT client not connected; skipping state publish (paho will reconnect in background).")
        return 0

    succeeded = 0
    for topic, value in mqtt_topics.items():
        try:
            info = _publish_with_rc(
                mqtt_client,
                topic,
                json.dumps(value) if isinstance(value, (dict, list)) else str(value),
                qos=1,
                retain=True,
            )
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                succeeded += 1
                log.debug("Published to %s", topic)
        except Exception:
            log.exception("Failed to publish %s", topic)
    return succeeded


def _handle_signal(signum, frame):
    log.info("Received signal %s, shutting down.", signum)
    shutdown.set()


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        setup_mqtt_client()
    except Exception:
        log.exception("Critical error during MQTT setup.")
        return

    client: GabbClient | None = None
    consecutive_failures = 0

    try:
        while not shutdown.is_set():
            log.info("Starting new iteration.")
            iteration_published_anything = False
            try:
                if client is None:
                    client = GabbClient(GABB_USERNAME, GABB_PASSWORD)
                    log.debug("Initialized Gabb client.")

                log.info("Fetching map data.")
                map_data = fetch_map_with_retry(client)

                if shutdown.is_set():
                    break

                if map_data is None:
                    log.warning("Skipping iteration: map data unavailable.")
                else:
                    # First-run-per-device migration: retract any legacy single-entity
                    # discovery messages still retained on the broker before we
                    # publish the new device-based discovery. Idempotent per device.
                    clear_legacy_discovery_topics(map_data)

                    # Publish discovery only for devices we haven't seen yet (or
                    # all of them after an HA birth message reset). Retained, so
                    # this is a no-op for HA after the first publish.
                    publish_discovery_for_new_devices(map_data)

                    log.debug("Processing map data.")
                    mqtt_topics = generate_mqtt_topics(map_data)

                    if mqtt_topics:
                        log.info("Publishing topics to MQTT broker.")
                        n = publish_state_topics(mqtt_topics)
                        if n > 0:
                            iteration_published_anything = True
                            log.debug("Published %d state topic(s).", n)
            except requests.exceptions.HTTPError as e:
                log.exception("HTTP error during iteration.")
                if _is_auth_failure(e):
                    log.warning("Auth failure detected; recreating GabbClient on next iteration.")
                    client = None
            except requests.RequestException:
                log.exception("Network error during iteration.")
            except Exception:
                log.exception("Error in iteration.")

            if iteration_published_anything:
                consecutive_failures = 0
                try:
                    HEARTBEAT_FILE.touch()
                except OSError:
                    log.exception("Failed to touch heartbeat file %s", HEARTBEAT_FILE)
            else:
                consecutive_failures += 1
                log.warning(
                    "No state published this iteration (consecutive failures: %d/%d).",
                    consecutive_failures,
                    MAX_CONSECUTIVE_FAILURES,
                )
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    log.error(
                        "Exiting after %d consecutive failed iterations; the orchestrator should restart us.",
                        consecutive_failures,
                    )
                    sys.exit(1)

            log.info("Iteration complete. Waiting for %s seconds.", LOOP_DELAY)
            # Event.wait() returns True if the event was set during the wait,
            # so this is both our sleep and our shutdown check.
            if shutdown.wait(LOOP_DELAY):
                break
    finally:
        try:
            # Publish a clean "offline" before disconnecting so HA doesn't
            # have to wait for the LWT timeout on a planned shutdown.
            try:
                _publish_with_rc(mqtt_client, AVAILABILITY_TOPIC, "offline", qos=1, retain=True, wait=True, wait_timeout=2)
            except Exception:
                log.exception("Failed to publish availability=offline on shutdown.")
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            log.exception("Error during MQTT shutdown.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Script interrupted by user.")
    except Exception:
        log.exception("Unhandled exception.")
