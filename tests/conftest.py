"""Pytest configuration: make the repo-root modules importable.

The publisher module no longer reads the environment at import time (all env
resolution lives in Config.from_env), so no env stubbing is needed here. The
script lives at the repo root, not in a package, so we add the repo root to
sys.path.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
