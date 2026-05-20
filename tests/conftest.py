"""Pytest configuration: stub required env vars before the publisher module is imported.

The publisher module exits with code 2 if GABB_USERNAME/PASSWORD or
MQTT_BROKER/USERNAME/PASSWORD are missing. Tests don't need real values --
they only exercise pure functions -- so set dummy defaults here, before any
test imports the module.
"""

import os
import sys
from pathlib import Path

# Required-on-import env vars. Use setdefault so a real env (e.g. CI with
# secrets injected) still wins over the test placeholders.
os.environ.setdefault("GABB_USERNAME", "test")
os.environ.setdefault("GABB_PASSWORD", "test")
os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_USERNAME", "test")
os.environ.setdefault("MQTT_PASSWORD", "test")

# Make the publisher module importable as a top-level name. The script lives
# at the repo root, not in a package, so we add the repo root to sys.path.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
