
# Gabb Wireless MQTT Publisher
Simple docker image to run a Python script which will used an "undocumented" API for Gabb Wireless account holders. The script will pull down the device details and publish them to a MQTT broker as configured.

For home assistant users, the script publishes MQTT auto-discovery topics so that your Home Assistant instance will automatically pick up and include the devices and sensors. It also creates a device_tracker device for each device with the GPS coordinates and last GPS update.

None of this would be possible if it were not for this amazing repo and the incredible work done to figure out the basic API calls. [Go check it out!](https://github.com/woodsbw/gabb) Thank you @woodsbw!

**NOTE**: As @woodsbw stated on his repo, the API is not a public API, not documented, and you MUST USE AT YOUR OWN RISK. The API leveraged is owned by Smartcom and you may be running afoul of an EULA by using this, you have been warned.

**Docker cli**

    docker run \
    - dit \
    --name gabb-mqtt-publisher \
    --restart unless-stopped \
    -e GABB_USERNAME=<WEBSITE_USERNAME> \
    -e GABB_PASSWORD=<WEBSITE_PASSWORD> \
    -e MQTT_BROKER=<YOUR_MQTT_BROKER_IP> \
    -e MQTT_PORT=1883 \
    -e MQTT_USERNAME=<YOUR_MQTT_USER_NAME> \
    -e MQTT_PASSWORD=<YOUR_MQTT_BROKER_PASSWORD> \
    -e REFRESH_RATE=1 \
    ghcr.io/jaycollett/gabbwireless_mqtt:latestt

**Envioronment Variables ( -e )**

|Env          |Function                                                            |
|-------------|--------------------------------------------------------------------|
|GABB_USERNAME|The username you use to log into your Gabb Wireless web portal.     |
|GABB_PASSWORD|The password you use to log into your Gabb Wireless web portal.     |
|MQTT_BROKER  |Hostname or IP of your local MQTT broker.                           |
|MQTT_USERNAME|The username for the MQTT account on your broker.                   |
|MQTT_PASSWORD|The password for the MQTT account on your broker.                   |
|MQTT_PORT    |*(optional)* The port to use on your MQTT broker.                     |
|REFRESH_RATE |*(optional)* (1-4) 1: 5 min, 2: 10 min, 3: 30 min (default), 4: 1 hour|


