import os
import time
from datetime import datetime, timezone
from gabb import GabbClient
import json
import paho.mqtt.client as mqtt

# Configurable Variables from Environment
GABB_USERNAME = os.getenv("GABB_USERNAME", "default_username")
GABB_PASSWORD = os.getenv("GABB_PASSWORD", "default_password")

MQTT_BROKER = os.getenv("MQTT_BROKER", "mqtt.example.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "mqtt_user")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "mqtt_password")

DEVICE_MODEL = "Gabb Device"
DEVICE_MANUFACTURER = "Gabb Wireless"

# Calculate LOOP_DELAY based on environment variable value
LOOP_DELAY_SETTING = int(os.getenv("REFRESH_RATE", 1))
LOOP_DELAY = {1: 300, 2: 600, 3: 1800, 4: 3600}.get(LOOP_DELAY_SETTING, 1800)  # Default to 30 minutes if invalid

PUBLISH_DELAY = float(0.1) # Delay in seconds between publishing each topic

# Global MQTT client
mqtt_client = mqtt.Client()

def setup_mqtt_client():
    """
    Setup and connect the MQTT client.
    """
    global mqtt_client
    try:
        mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        print(f"Connected to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}")
    except Exception as e:
        print(f"Failed to connect to MQTT broker: {e}")
        raise e

def ensure_mqtt_connection():
    """
    Ensure that the MQTT client is connected.
    """
    global mqtt_client
    if not mqtt_client.is_connected():
        print("MQTT client disconnected. Attempting to reconnect...")
        try:
            mqtt_client.reconnect()
            print("Reconnected to MQTT broker.")
        except Exception as e:
            print(f"Failed to reconnect to MQTT broker: {e}")
            raise e

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
        print("No devices found in the map data.")
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
        print("No devices found in the map data.")
        return {}

    # Mapping of keys to device_class and unit_of_measurement
    key_to_device_class = {
        "batteryLevel": {"device_class": "battery", "unit_of_measurement": "%"},
        "longitude": {"device_class": None, "unit_of_measurement": "°"},
        "latitude": {"device_class": None, "unit_of_measurement": "°"},
        "gpsDate": {"device_class": "timestamp", "unit_of_measurement": None},
        "deviceStatus": {"device_class": "connectivity", "unit_of_measurement": None},
        "last_updated": {"device_class": "timestamp", "unit_of_measurement": None},
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
            print(f"Published Home Assistant discovery message to {topic}")
            time.sleep(delay)
        except Exception as e:
            print(f"Failed to publish Home Assistant discovery message to {topic}: {e}")

    # Publish regular MQTT topics
    for topic, value in mqtt_topics.items():
        try:
            mqtt_client.publish(topic, json.dumps(value) if isinstance(value, (dict, list)) else str(value))
            print(f"Published to {topic}")
            time.sleep(delay)
        except Exception as e:
            print(f"Failed to publish {topic}: {e}")

def main():
    try:
        setup_mqtt_client()
    except Exception as e:
        print(f"Critical error during MQTT setup: {e}")
        return

    while True:
        print("Starting new iteration...")
        try:
            # Initialize Gabb client
            client = GabbClient(GABB_USERNAME, GABB_PASSWORD)
            print("Initialized Gabb client.")

            # Fetch map data
            print("Fetching map data...")
            map_response = client.get_map()
            try:
                map_data = map_response.json()
            except Exception as e:
                print(f"Failed to parse map data: {e}")
                continue

            # Remove all "SafeZone" entries from the data
            remove_key_recursive(map_data, "SafeZones")

            # Generate MQTT topics
            print("Processing map data...")
            mqtt_topics = generate_mqtt_topics(map_data)

            # Generate Home Assistant discovery messages for all properties
            print("Generating Home Assistant discovery messages...")
            discovery_messages = generate_homeassistant_discovery_messages(map_data)

            if mqtt_topics or discovery_messages:
                # Publish to MQTT broker
                print("Publishing topics to MQTT broker...")
                publish_to_mqtt_broker(mqtt_topics, discovery_messages, delay=PUBLISH_DELAY)
        except Exception as e:
            print(f"Error in iteration: {e}")

        print(f"Iteration complete. Waiting for {LOOP_DELAY} seconds...")
        time.sleep(LOOP_DELAY)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Script interrupted by user.")
    except Exception as e:
        print(f"Unhandled exception: {e}")
