
# Gabb Wireless MQTT Publisher
[![s.io/github/v/GitHub release (latest by date including pre-releases)](https://img.shields.io/github/v/release/jaycollett/gabbwireless_mqtt?include_prereleases)](https://img.shields.io/github/v/release/jaycollett/gabbwireless_mqtt?include_prereleases)
[![GitHub last commit](https://img.shields.io/github/last-commit/jaycollett/gabbwireless_mqtt)](https://img.shields.io/github/last-commit/jaycollett/gabbwireless_mqtt)
[![GitHub issues](https://img.shields.io/github/issues-raw/jaycollett/gabbwireless_mqtt)](https://img.shields.io/github/issues-raw/jaycollett/gabbwireless_mqtt)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/jaycollett/gabbwireless_mqtt)](https://img.shields.io/github/issues-pr/jaycollett/gabbwireless_mqtt)
[![GitHub](https://img.shields.io/github/license/jaycollett/gabbwireless_mqtt)](https://img.shields.io/github/license/jaycollett/gabbwireless_mqtt)

Simple docker image to run a Python script which will used an "undocumented" API for Gabb Wireless account holders. The script will pull down the device details and publish them to a MQTT broker as configured.

For home assistant users, the script publishes MQTT auto-discovery topics so that your Home Assistant instance will automatically pick up and include the devices and sensors. It also creates a device_tracker device for each device with the GPS coordinates and last GPS update.

None of this would be possible if it were not for this amazing repo and the incredible work done to figure out the basic API calls. [Go check it out!](https://github.com/woodsbw/gabb) Thank you @woodsbw!

**NOTE**: As @woodsbw stated on his repo, the API is not a public API, not documented, and you MUST USE AT YOUR OWN RISK. The API leveraged is owned by Smartcom and you may be running afoul of an EULA by using this, you have been warned.

**Docker cli**

    docker run \
    -dit \
    --name gabb-mqtt-publisher \
    --restart unless-stopped \
    -e GABB_USERNAME=<WEBSITE_USERNAME> \
    -e GABB_PASSWORD=<WEBSITE_PASSWORD> \
    -e MQTT_BROKER=<YOUR_MQTT_BROKER_IP> \
    -e MQTT_PORT=1883 \
    -e MQTT_USERNAME=<YOUR_MQTT_USER_NAME> \
    -e MQTT_PASSWORD=<YOUR_MQTT_BROKER_PASSWORD> \
    -e REFRESH_SECONDS=600 \
    ghcr.io/jaycollett/gabbwireless_mqtt:latest

**Environment Variables ( -e )**

| Env                | Function                                                            |
|--------------------|---------------------------------------------------------------------|
| GABB_USERNAME      | The username you use to log into your Gabb Wireless web portal.     |
| GABB_PASSWORD      | The password you use to log into your Gabb Wireless web portal.     |
| MQTT_BROKER        | Hostname or IP of your local MQTT broker.                           |
| MQTT_USERNAME      | The username for the MQTT account on your broker.                   |
| MQTT_PASSWORD      | The password for the MQTT account on your broker.                   |
| MQTT_PORT          | *(optional)* Broker port. Defaults to `1883`, or `8883` when `MQTT_TLS=true` and no port is set explicitly. |
| MQTT_TLS           | *(optional)* Enable TLS to the broker. Default `false`. When `true`, `MQTT_PORT` defaults to `8883` if not set. |
| MQTT_CA_CERT       | *(optional)* Path inside the container to a CA cert file used to verify the broker. Defaults to the system trust store. |
| MQTT_TLS_INSECURE  | *(optional)* Disable broker hostname verification. Default `false`. Only use this for local testing against self-signed certs. |
| REFRESH_SECONDS    | *(optional)* Poll interval in seconds (minimum 60). Overrides `REFRESH_RATE` when both are set. |
| REFRESH_RATE       | *(optional)* Legacy 1..4 ladder: 1 = 5 min, 2 = 10 min, 3 = 30 min (default), 4 = 1 hour. Ignored when `REFRESH_SECONDS` is set. |

### Sensor whitelist (0.3.0+)

To keep Home Assistant's entity list manageable, only the following Gabb device fields are published as individual sensor entities:

`batteryLevel`, `latitude`, `longitude`, `gpsDate`, `online`, `phoneNumber`, `imei`, `firmwareVersion`, `deviceType`, `model`.

Any other field returned by the Gabb API (for example `appBuild`, `iccid`, `serialNumber`, internal IDs) is still published, but it rides as a JSON attribute on the per-device `device_tracker` entity. Read it from a template or automation with:

```
{{ state_attr('device_tracker.gabb_device_12345', 'appBuild') }}
```

### Heartbeat-based healthcheck

The publisher touches `/tmp/gabb_heartbeat` after every successful iteration. The container `HEALTHCHECK` verifies that this file has been touched within the last 90 minutes. The 90-minute window covers `REFRESH_RATE=4` (60 min) plus one missed iteration.

If you set `REFRESH_SECONDS` higher than 5400 (90 minutes), the healthcheck will false-positive. Either lower the refresh interval or extend the `-mmin` value in the Dockerfile's healthcheck command.

### Failure handling

The script exits with code `1` after 10 consecutive failed iterations (no state published — e.g. expired credentials or sustained Gabb API outage). It expects Docker's restart policy (`--restart unless-stopped`) or Kubernetes' restart policy to recover the container, which will re-authenticate from scratch.

### Development

Install the development dependencies and run the unit tests:

```
pip install -r requirements-dev.txt
pytest tests/ -v
```

The tests cover the pure helpers (`humanize_key`, `normalize_timestamp`, sensor-whitelist behavior, discovery payload invariants, and refresh-interval resolution) and do not require an MQTT broker or Gabb credentials.
