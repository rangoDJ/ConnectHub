import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "root" / "app" / "backend"
sys.path.insert(0, str(BACKEND))

# The backend modules read these at import time, so they have to point at throwaway
# directories before any test module imports them -- otherwise a test run would touch
# the real /config and /shared.
os.environ["CONFIG_DIR"] = tempfile.mkdtemp(prefix="connecthub-test-cfg-")
os.environ["SHARED_DIR"] = tempfile.mkdtemp(prefix="connecthub-test-shared-")
os.environ.pop("SECRET_KEY", None)
os.environ.pop("AUTH_MODE", None)
