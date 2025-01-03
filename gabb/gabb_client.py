from gabb import GabbClient
import json

# Hardcoded Gabb credentials
username = "jay@collett.us"
password = "eDtI9QCL4Qt&lez"



def format_map_data(map_data):
    """
    Helper function to format and combine map data into a dictionary format.
    """
    combined_data = {}

    # Check if 'Devices' key exists and process it
    if 'Devices' in map_data:
        devices = map_data['Devices']
        if devices:
            for device in devices:
                device_id = device.get('id')
                combined_data[device_id] = {
                    'deviceInfo': {
                        'id': device.get('id'),
                        'firstName': device.get('firstName'),
                        'lastName': device.get('lastName'),
                        'type': device.get('type'),
                        'batteryLevel': device.get('batteryLevel'),
                        'deviceStatus': device.get('deviceStatus'),
                        'longitude': device.get('longitude'),
                        'latitude': device.get('latitude'),
                        'gpsDate': device.get('gpsDate'),
                        'emergencyMode': device.get('emergencyMode'),
                        'shutdown': device.get('shutdown'),
                        'firmwareVersion': device.get('firmwareVersion'),
                        'hardwareVersion': device.get('hardwareVersion'),
                        'imei': device.get('imei')
                    }
                }
        else:
            print("No devices found in map data.")
    else:
        print("No 'Devices' key found in map data.")

    # Check and combine SafeZones data
    if 'SafeZones' in map_data:
        for zone in map_data['SafeZones']:
            for device_id in zone['devices']:
                if device_id in combined_data:
                    if 'safeZones' not in combined_data[device_id]:
                        combined_data[device_id]['safeZones'] = []
                    combined_data[device_id]['safeZones'].append({
                        'zoneName': zone['name'],
                        'latitude': zone['latitude'],
                        'longitude': zone['longitude'],
                        'radius': zone['radius']
                    })
    return combined_data


def main():
    try:
        # Initialize the Gabb client with credentials
        client = GabbClient(username, password)
        print("Successfully initialized Gabb client.")
    except Exception as e:
        print(f"Failed to initialize Gabb client: {e}")
        return

    # Prepare the combined data dictionary
    combined_device_data = {}

    # Fetch and combine user profile data
    try:
        response = client.get_user_profile()
        user_profile = response.json()  # Extract JSON data from the response
        print("\nRetrieved user profile:")
        
        if user_profile.get('data'):
            user_data = user_profile['data']
            for key, value in user_data.items():
                print(f"{key}: {value}")
        else:
            print("No user profile data found.")
    except Exception as e:
        print(f"Failed to fetch user profile: {e}")

    # Fetch and combine contacts data
    try:
        response = client.get_contacts()
        contacts = response.json()  # Extract JSON data from the response
        print("\nRetrieved contacts:")

        if contacts.get('data'):
            contact_data = contacts['data']['contacts']
            for contact in contact_data:
                print("\nContact:")
                for key, value in contact.items():
                    print(f"{key}: {value}")
        else:
            print("No contacts data found.")
    except Exception as e:
        print(f"Failed to fetch contacts: {e}")

    # Fetch and combine device profiles and map data for each device
    try:
        response = client.get_contacts()
        contacts = response.json()  # Extract JSON data from the response
        device_ids = set()

        if contacts.get('data'):
            contact_data = contacts['data']['contacts']
            for contact in contact_data:
                if contact.get('devices'):
                    device_ids.update(contact['devices'])  # Add the device IDs

            if device_ids:
                for device_id in device_ids:
                    print(f"\nRetrieving device profile for Device ID {device_id}...")
                    device_profile_response = client.get_device_profile(device_id)
                    device_profile = device_profile_response.json()

                    # Initialize combined data for the device if not already present
                    if device_id not in combined_device_data:
                        combined_device_data[device_id] = {}

                    # Merge device profile data into combined data
                    combined_device_data[device_id]['deviceProfile'] = device_profile

                    # Fetch map data for the device
                    print(f"\nRetrieving map data for Device ID {device_id}...")
                    map_response = client.get_map()  # Call without device_id
                    map_data = map_response.json()  # Extract map data from the response
                    if map_data.get('data'):
                        formatted_map_data = format_map_data(map_data['data'])
                        # Merge map data into combined data
                        if device_id in formatted_map_data:
                            combined_device_data[device_id].update(formatted_map_data[device_id])
                    else:
                        print(f"No map data found for Device ID {device_id}.")
            else:
                print("No device IDs found.")
        else:
            print("No contacts data found.")
    except Exception as e:
        print(f"Failed to fetch device profiles or map data: {e}")

    # Output the combined data as JSON
    try:
        print("\nCombined Device Data:")
        print(json.dumps(combined_device_data, indent=4))
    except Exception as e:
        print(f"Failed to output combined data as JSON: {e}")


if __name__ == "__main__":
    main()
