# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the current directory contents into the container
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

#
# Environment variables for script
#

# Gabb parent account details
#ENV GABB_USERNAME
#ENV GABB_PASSWORD

# MQTT Broker Details
#ENV MQTT_BROKER
#ENV MQTT_PORT
#ENV MQTT_USERNAME
#ENV MQTT_PASSWORD
#ENV MQTT_TOPIC_PREFIX

# How often to fetch data, 1 = 10 mins, 2 = 30 mins, 3 = 60 mins
#ENV REFRESH_RATE=1

# Define the command to run the script
CMD ["python", "gabb_mqtt_publisher.py"]
