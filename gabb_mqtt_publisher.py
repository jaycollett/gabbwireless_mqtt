import logging
import os
import signal
import ssl
import sys
import time
from datetime import datetime, timezone
from gabb import GabbClient
import json
import paho.mqtt.client as mqtt

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

# Calculate LOOP_DELAY based on environment variable value
LOOP_DELAY_SETTING = int(os.getenv("REFRESH_RATE", "1"))
LOOP_DELAY = {1: 300, 2: 600, 3: 1800, 4: 3600}.get(LOOP_DELAY_SETTING, 1800)  # Default to 30 minutes if invalid

PUBLISH_DELAY = 0.1  # Delay in seconds between publishing each topic

# Global MQTT client (paho-mqtt v2 API)
mqtt_client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    protocol=mqtt.MQTTv5,
)


def on_connect(client, userdata, flags, reason_code, properties=None):
    log.info("Connected to MQTT broker (reason_code=%s).", reason_code)


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


def generate_mqtt_topics(map_data, root_topic="gabb_device"):
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
            mqtt_topics[topic] = value

        # Add a combined topic for location
        if "longitude" in device and "latitude" in device:
            location_payload = {
                "latitude": device["latitude"],
                "longitude": device["longitude"]
            }
            if "gpsDate" in device:
                location_payload["LastGPSUpdate"] = device["gpsDate"]
            mqtt_topics[f"{topic_prefix}/location"] = location_payload

        # Add a current UTC timestamp as a sensor
        current_utc_time = datetime.now(timezone.utc).isoformat()
        mqtt_topics[f"{topic_prefix}/last_updated"] = current_utc_time

    return mqtt_topics


def generate_homeassistant_discovery_messages(map_data, root_topic="gabb_device"):
    """
    Generate Home Assistant MQTT discovery messages for devices.
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
        "weight": {"device_class": "weight", "unit_of_measurement": "kg"}

    }

    discovery_messages = {}
    for device in devices:
        device_id = device.get("id", "unknown")
        device_name = f"Gabb Device {device_id}"
        base_topic = f"homeassistant/sensor/{root_topic}_{device_id}"

        # Generate sensor discovery messages
        for key, value in device.items():
            sensor_name = ''.join(word.capitalize() for word in key.split('_'))
            sensor_topic = f"{base_topic}/{key}/config"
            device_class = key_to_device_class.get(key, {}).get("device_class")
            unit_of_measurement = key_to_device_class.get(key, {}).get("unit_of_measurement")
            discovery_payload = {
                "name": sensor_name,
                "state_topic": f"{root_topic}/{device_id}/{key}",
                "unique_id": f"{root_topic}_{device_id}_{key}",
                "device_class": device_class,
                "unit_of_measurement": unit_of_measurement,
                "device": {
                    "identifiers": [f"{root_topic}_{device_id}"],
                    "name": device_name,
                    "model": DEVICE_MODEL,
                    "manufacturer": DEVICE_MANUFACTURER
                }
            }
            discovery_messages[sensor_topic] = discovery_payload

        # Add discovery message for the last updated sensor
        last_updated_topic = f"{base_topic}/last_updated/config"
        last_updated_payload = {
            "name": "Last Updated",
            "state_topic": f"{root_topic}/{device_id}/last_updated",
            "unique_id": f"{root_topic}_{device_id}_last_updated",
            "device_class": "timestamp",
            "device": {
                "identifiers": [f"{root_topic}_{device_id}"],
                "name": device_name,
                "model": DEVICE_MODEL,
                "manufacturer": DEVICE_MANUFACTURER
            }
        }
        discovery_messages[last_updated_topic] = last_updated_payload

        # Generate device tracker discovery message
        if "longitude" in device and "latitude" in device:
            tracker_topic = f"homeassistant/device_tracker/{root_topic}_{device_id}/config"
            tracker_payload = {
                "name": device_name,
                "unique_id": f"{root_topic}_{device_id}_tracker",
                "json_attributes_topic": f"{root_topic}/{device_id}/location",
                "device": {
                    "identifiers": [f"{root_topic}_{device_id}"],
                    "name": device_name,
                    "model": DEVICE_MODEL,
                    "manufacturer": DEVICE_MANUFACTURER
                }
            }
            discovery_messages[tracker_topic] = tracker_payload

    return discovery_messages


def publish_to_mqtt_broker(mqtt_topics, discovery_messages, delay=0.1):
    """
    Publish MQTT topics and Home Assistant discovery messages to the broker.
    """
    global mqtt_client
    ensure_mqtt_connection()

    # Publish Home Assistant discovery messages
    for topic, payload in discovery_messages.items():
        try:
            mqtt_client.publish(topic, json.dumps(payload))
            log.debug("Published Home Assistant discovery message to %s", topic)
            time.sleep(delay)
        except Exception:
            log.exception("Failed to publish Home Assistant discovery message to %s", topic)

    # Publish regular MQTT topics
    for topic, value in mqtt_topics.items():
        try:
            mqtt_client.publish(topic, json.dumps(value) if isinstance(value, (dict, list)) else str(value))
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
