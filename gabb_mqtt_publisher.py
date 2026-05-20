import logging
import os
import re
import signal
import ssl
import sys
import time
from datetime import datetime, timezone
from gabb import GabbClient
import json
import paho.mqtt.client as mqtt
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
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = _require_env("MQTT_USERNAME")
MQTT_PASSWORD = _require_env("MQTT_PASSWORD")

MQTT_TLS = _bool_env("MQTT_TLS", False)
MQTT_CA_CERT = os.getenv("MQTT_CA_CERT", "").strip() or None
MQTT_TLS_INSECURE = _bool_env("MQTT_TLS_INSECURE", False)

if MQTT_PASSWORD and not MQTT_TLS:
    log.warning(
        "MQTT_PASSWORD is set but MQTT_TLS is not enabled; credentials will traverse the network in plaintext."
    )

DEVICE_MODEL = "Gabb Device"
DEVICE_MANUFACTURER = "Gabb Wireless"
ROOT_TOPIC = "gabb_device"
AVAILABILITY_TOPIC = f"{ROOT_TOPIC}/availability"

# Calculate LOOP_DELAY based on environment variable value
LOOP_DELAY_SETTING = int(os.getenv("REFRESH_RATE", "1"))
LOOP_DELAY = {1: 300, 2: 600, 3: 1800, 4: 3600}.get(LOOP_DELAY_SETTING, 1800)  # Default to 30 minutes if invalid

PUBLISH_DELAY = 0.1  # Delay in seconds between publishing each topic

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

# Tracks which devices we've already published an old-discovery cleanup for in
# this process lifetime, so we only fire the migration sweep once per device.
_cleaned_legacy_discovery: set[str] = set()

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


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    log.warning("Disconnected from MQTT broker (reason_code=%s).", reason_code)


def on_message(client, userdata, message):
    log.debug("Received message on topic %s: %s", message.topic, message.payload.decode(errors="replace"))


def setup_mqtt_client():
    """
    Setup and connect the MQTT client, and start its network loop.
    """
    global mqtt_client
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

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


def ensure_mqtt_connection():
    """
    Ensure that the MQTT client is connected; trigger a reconnect if not.
    The network loop (started in setup_mqtt_client) handles the state machine.
    """
    global mqtt_client
    if not mqtt_client.is_connected():
        log.warning("MQTT client not connected. Attempting reconnect.")
        try:
            mqtt_client.reconnect()
        except Exception:
            log.exception("Failed to reconnect to MQTT broker.")
            raise


def remove_key_recursive(obj, key_to_remove):
    """
    Recursively traverse a nested dictionary/list structure
    and remove all occurrences of `key_to_remove`.
    """
    if isinstance(obj, dict):
        obj.pop(key_to_remove, None)
        for value in obj.values():
            remove_key_recursive(value, key_to_remove)
    elif isinstance(obj, list):
        for item in obj:
            remove_key_recursive(item, key_to_remove)


def generate_mqtt_topics(map_data, root_topic=ROOT_TOPIC):
    """
    Generate MQTT topics for devices and their properties.
    """
    devices = map_data.get("data", {}).get("Devices", [])
    if not devices:
        log.info("No devices found in the map data.")
        return {}

    mqtt_topics = {}
    for device in devices:
        device_id = device.get("id", "unknown")
        topic_prefix = f"{root_topic}/{device_id}"
        for key, value in device.items():
            topic = f"{topic_prefix}/{key}"
            # HA's timestamp device class requires a tz-aware ISO 8601 string;
            # normalize gpsDate before publishing or skip it if unparseable.
            if key == "gpsDate":
                normalized = normalize_timestamp(value)
                if normalized is None:
                    continue
                mqtt_topics[topic] = normalized
            else:
                mqtt_topics[topic] = value

        # Add a combined topic for location
        if "longitude" in device and "latitude" in device:
            location_payload = {
                "latitude": device["latitude"],
                "longitude": device["longitude"]
            }
            if "gpsDate" in device:
                normalized_gps = normalize_timestamp(device["gpsDate"])
                if normalized_gps is not None:
                    location_payload["LastGPSUpdate"] = normalized_gps
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


def clear_legacy_discovery_topics(map_data, root_topic=ROOT_TOPIC, delay=PUBLISH_DELAY):
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
                mqtt_client.publish(topic, payload="", qos=1, retain=True)
                log.debug("Cleared legacy discovery topic %s", topic)
                time.sleep(delay)
            except Exception:
                log.exception("Failed to clear legacy discovery topic %s", topic)

        _cleaned_legacy_discovery.add(device_id)
        log.info("Cleared %d legacy discovery topics for device %s.", len(topics_to_clear), device_id)


def publish_to_mqtt_broker(mqtt_topics, discovery_messages, delay=0.1):
    """
    Publish MQTT topics and Home Assistant discovery messages to the broker.
    All discovery and state messages are published with retain=True so HA
    recovers cleanly after a restart and slow-moving sensor values (battery,
    GPS) survive broker reconnections.
    """
    global mqtt_client
    ensure_mqtt_connection()

    # Publish Home Assistant discovery messages
    for topic, payload in discovery_messages.items():
        try:
            mqtt_client.publish(topic, json.dumps(payload), qos=1, retain=True)
            log.debug("Published Home Assistant discovery message to %s", topic)
            time.sleep(delay)
        except Exception:
            log.exception("Failed to publish Home Assistant discovery message to %s", topic)

    # Publish regular MQTT topics
    for topic, value in mqtt_topics.items():
        try:
            mqtt_client.publish(
                topic,
                json.dumps(value) if isinstance(value, (dict, list)) else str(value),
                qos=1,
                retain=True,
            )
            log.debug("Published to %s", topic)
            time.sleep(delay)
        except Exception:
            log.exception("Failed to publish %s", topic)


_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.info("Received signal %s, shutting down.", signum)
    _shutdown = True


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        setup_mqtt_client()
    except Exception:
        log.exception("Critical error during MQTT setup.")
        return

    try:
        while not _shutdown:
            log.info("Starting new iteration.")
            try:
                client = GabbClient(GABB_USERNAME, GABB_PASSWORD)
                log.debug("Initialized Gabb client.")

                log.info("Fetching map data.")
                map_response = client.get_map()
                try:
                    map_data = map_response.json()
                except Exception:
                    log.exception("Failed to parse map data.")
                    # Fall through to sleep and retry
                    _sleep_interruptible(LOOP_DELAY)
                    continue

                # Remove all "SafeZone" entries from the data
                remove_key_recursive(map_data, "SafeZones")

                # First-run-per-device migration: retract any legacy single-entity
                # discovery messages still retained on the broker before we
                # publish the new device-based discovery. Idempotent per device.
                clear_legacy_discovery_topics(map_data, delay=PUBLISH_DELAY)

                log.debug("Processing map data.")
                mqtt_topics = generate_mqtt_topics(map_data)

                log.debug("Generating Home Assistant discovery messages.")
                discovery_messages = generate_homeassistant_discovery_messages(map_data)

                if mqtt_topics or discovery_messages:
                    log.info("Publishing topics to MQTT broker.")
                    publish_to_mqtt_broker(mqtt_topics, discovery_messages, delay=PUBLISH_DELAY)
            except Exception:
                log.exception("Error in iteration.")

            log.info("Iteration complete. Waiting for %s seconds.", LOOP_DELAY)
            _sleep_interruptible(LOOP_DELAY)
    finally:
        try:
            # Publish a clean "offline" before disconnecting so HA doesn't
            # have to wait for the LWT timeout on a planned shutdown.
            try:
                mqtt_client.publish(AVAILABILITY_TOPIC, "offline", qos=1, retain=True)
            except Exception:
                log.exception("Failed to publish availability=offline on shutdown.")
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        except Exception:
            log.exception("Error during MQTT shutdown.")


def _sleep_interruptible(seconds: float) -> None:
    """Sleep in 1s increments so SIGTERM/SIGINT cause prompt shutdown."""
    end = time.monotonic() + seconds
    while not _shutdown and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Script interrupted by user.")
    except Exception:
        log.exception("Unhandled exception.")
