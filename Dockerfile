# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt /app/requirements.txt
COPY gabb /app/gabb
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY gabb_mqtt_publisher.py /app/gabb_mqtt_publisher.py

# Create a non-root user and drop privileges
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

# Unbuffered stdout/stderr so docker logs show output immediately
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

#
# Environment variables for script
#

# Gabb parent account details (required)
#ENV GABB_USERNAME
#ENV GABB_PASSWORD

# MQTT Broker Details (required)
#ENV MQTT_BROKER
#ENV MQTT_PORT
#ENV MQTT_USERNAME
#ENV MQTT_PASSWORD
#ENV MQTT_TOPIC_PREFIX

# Optional TLS for the MQTT connection
#ENV MQTT_TLS=true
#ENV MQTT_CA_CERT=/path/to/ca.pem
#ENV MQTT_TLS_INSECURE=false

# How often to fetch data, 1 = 5 mins, 2 = 10 mins, 3 = 30 mins, 4 = 60 mins
#ENV REFRESH_RATE=1

# Lightweight healthcheck: ensure the Python process can still import the module
HEALTHCHECK --interval=5m --timeout=15s --start-period=30s --retries=3 \
    CMD python -c "import gabb_mqtt_publisher" || exit 1

# Define the command to run the script
CMD ["python", "gabb_mqtt_publisher.py"]
