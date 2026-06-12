# Gabb Wireless for Home Assistant

[![GitHub release](https://img.shields.io/github/v/release/jaycollett/gabbwireless_mqtt?include_prereleases)](https://github.com/jaycollett/gabbwireless_mqtt/releases)
[![GitHub last commit](https://img.shields.io/github/last-commit/jaycollett/gabbwireless_mqtt)](https://github.com/jaycollett/gabbwireless_mqtt/commits)
[![GitHub issues](https://img.shields.io/github/issues-raw/jaycollett/gabbwireless_mqtt)](https://github.com/jaycollett/gabbwireless_mqtt/issues)
[![GitHub](https://img.shields.io/github/license/jaycollett/gabbwireless_mqtt)](LICENSE)

Bring your kids' Gabb watches and phones into Home Assistant. Sign in with your Gabb parent account and each device on the account shows up in HA with GPS location, battery level, online status, and device details. Put the kids on the map next to everyone else, get a low-battery notification before school pickup, or trigger automations when a watch comes home.

There are two ways to run this:

1. **Home Assistant integration (recommended)**: installed through HACS, set up entirely from the HA UI. No broker, no containers, no YAML.
2. **Docker MQTT publisher**: a standalone container that publishes device data to an MQTT broker with HA auto-discovery. Useful if you already run everything through MQTT or want the data outside Home Assistant.

Both talk to the same Gabb (Smartcom FiLIP) API. Pick one; running both will give you duplicate devices.

> **Heads up:** this project uses an undocumented API owned by Smartcom. It is not sanctioned by Gabb and could break or violate their EULA. Use at your own risk. None of this would exist without the API work in [woodsbw/gabb](https://github.com/woodsbw/gabb). Thanks @woodsbw!

## How to install

HACS is the recommended way to install. It handles updates for you and takes about two minutes. If you don't use HACS you can install manually, and the Docker MQTT publisher further down remains an option for MQTT-centric setups.

### HACS (recommended)

1. Open **HACS** in Home Assistant.
2. Click the three-dot menu in the top right and choose **Custom repositories**.
3. Paste `https://github.com/jaycollett/gabbwireless_mqtt`, set the type to **Integration**, and click **Add**.
4. Search HACS for **Gabb Wireless** and click **Download**.
5. Restart Home Assistant.
6. Go to **Settings → Devices & services → Add integration**, search for **Gabb Wireless**, and sign in with the username and password you use for the Gabb parent app or web portal.

That's it. Every device on your account appears under Settings → Devices & services → Gabb Wireless. When a new version is released, HACS shows the update alongside your other integrations.

### Manual installation

1. Download the [latest release](https://github.com/jaycollett/gabbwireless_mqtt/releases/latest) and copy the `custom_components/gabb_wireless` folder into the `custom_components` directory of your Home Assistant config (create it if it doesn't exist).
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**, search for **Gabb Wireless**, and sign in.

You'll need to repeat step 1 by hand for each update, which is why HACS is the better route.

### What you get

For each watch or phone on the account:

| Entity | Notes |
|---|---|
| Device tracker | GPS location, shows up on the map and works with zones and the `person` entity |
| Battery | Percentage, with history and long-term statistics |
| Last GPS fix | Timestamp of the most recent location report |
| Online | Connectivity status |
| Phone number, IMEI, firmware, device type, model | Diagnostic section of the device page |

Fields the API returns that don't warrant their own entity (ICCID, serial number, app build, and so on) are available as attributes on the device tracker:

```yaml
{{ state_attr('device_tracker.gabb_device_12345', 'iccid') }}
```

### Options

Click **Configure** on the integration to change how often it polls Gabb. The default is every 5 minutes; the minimum is 60 seconds. Polling faster drains the watch battery quicker and hits Gabb's servers harder, so leave it at the default unless you have a reason not to.

If your Gabb password changes, HA will prompt you to re-authenticate rather than silently going stale.

## Docker MQTT publisher (alternative)

The original form of this project: a container that polls the Gabb API and publishes to your MQTT broker using Home Assistant device-based discovery. HA picks the devices up automatically through the MQTT integration.

```sh
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
```

### Environment variables

| Env | Function |
|---|---|
| GABB_USERNAME | The username you use to log into your Gabb Wireless web portal. |
| GABB_PASSWORD | The password you use to log into your Gabb Wireless web portal. |
| MQTT_BROKER | Hostname or IP of your local MQTT broker. |
| MQTT_USERNAME | The username for the MQTT account on your broker. |
| MQTT_PASSWORD | The password for the MQTT account on your broker. |
| MQTT_PORT | *(optional)* Broker port. Defaults to `1883`, or `8883` when `MQTT_TLS=true` and no port is set explicitly. |
| MQTT_TLS | *(optional)* Enable TLS to the broker. Default `false`. |
| MQTT_CA_CERT | *(optional)* Path inside the container to a CA cert file used to verify the broker. Defaults to the system trust store. |
| MQTT_TLS_INSECURE | *(optional)* Disable broker hostname verification. Default `false`. Only for local testing against self-signed certs. |
| REFRESH_SECONDS | *(optional)* Poll interval in seconds (minimum 60). Overrides `REFRESH_RATE` when both are set. |
| REFRESH_RATE | *(optional)* Legacy 1..4 ladder: 1 = 5 min, 2 = 10 min, 3 = 30 min (default), 4 = 1 hour. Ignored when `REFRESH_SECONDS` is set. |

### Sensor whitelist

To keep Home Assistant's entity list manageable, only these device fields are published as individual sensor entities:

`batteryLevel`, `latitude`, `longitude`, `gpsDate`, `online`, `phoneNumber`, `imei`, `firmwareVersion`, `deviceType`, `model`.

Everything else the API returns rides as a JSON attribute on the per-device `device_tracker` entity, readable with `state_attr()` as shown above.

### Healthcheck and failure handling

The publisher touches `/tmp/gabb_heartbeat` after every successful iteration, and the container `HEALTHCHECK` verifies the file has been touched within the last 90 minutes. If you set `REFRESH_SECONDS` above 5400, extend the `-mmin` value in the Dockerfile.

After 10 consecutive failed iterations (expired credentials, sustained API outage) the container exits with code 1 and relies on your restart policy to recover and re-authenticate from scratch.

## Development

Publisher tests:

```sh
pip install -r requirements-dev.txt
pytest tests/ -v
```

Integration tests run against a real Home Assistant install:

```sh
pip install pytest-homeassistant-custom-component
pytest tests_integration --asyncio-mode=auto
```

CI runs ruff, both test suites, hassfest, and HACS validation on every PR. Pushing a `manifest.json` version bump to master creates the GitHub release automatically, which also kicks off the Docker image build, so HACS users and Docker users update from the same release.

## Credits

API reverse engineering by [@woodsbw](https://github.com/woodsbw/gabb). MQTT publisher and Home Assistant integration by [@jaycollett](https://github.com/jaycollett).
