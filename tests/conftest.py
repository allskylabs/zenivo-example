"""
Pytest configuration and shared fixtures for the IMX335 AllSky E2E Test Suite.
"""

import os
import sys
import tempfile
import sqlite3
from typing import Generator, Dict, Any, List

import pytest
import numpy as np

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.fixtures.synthetic_generator import SyntheticAllSkyGenerator


@pytest.fixture(scope="session")
def synthetic_gen() -> SyntheticAllSkyGenerator:
    """Fast scaled synthetic generator (640x480) for rapid unit/E2E assertions."""
    return SyntheticAllSkyGenerator(width=640, height=480, seed=42)


@pytest.fixture(scope="session")
def fullres_synthetic_gen() -> SyntheticAllSkyGenerator:
    """Full-resolution 2592x1944 synthetic generator for exact sensor format testing."""
    return SyntheticAllSkyGenerator(width=2592, height=1944, seed=42)


@pytest.fixture
def sample_dark_frame_10s(synthetic_gen) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Single 10.0s synthetic dark frame with ~150 hot pixels (DN >= 100)."""
    return synthetic_gen.generate_dark_frame(exposure_s=10.0, gain_db=36.0, num_hot_pixels=150)


@pytest.fixture
def sample_dark_stack_9(synthetic_gen) -> tuple[List[np.ndarray], np.ndarray]:
    """Stack of 9 dark frames for Master Dark median estimation."""
    return synthetic_gen.generate_dark_stack(count=9, exposure_s=10.0, gain_db=36.0, num_hot_pixels=150)


@pytest.fixture
def sample_night_frame(synthetic_gen) -> tuple[np.ndarray, List[Dict[str, Any]]]:
    """Pristine night sky with ~120 point sources."""
    return synthetic_gen.generate_night_sky(num_stars=120)


@pytest.fixture
def sample_cloudy_frame(synthetic_gen) -> tuple[np.ndarray, Dict[str, Any]]:
    """Cloudy night sky with 70% cloud cover."""
    return synthetic_gen.generate_cloudy_sky(cloud_cover_pct=70.0)


@pytest.fixture
def sample_meteor_frame(synthetic_gen) -> tuple[np.ndarray, Dict[str, Any]]:
    """Night sky with injected meteor streak."""
    return synthetic_gen.generate_meteor_frame()


@pytest.fixture
def temp_output_dir() -> Generator[str, None, None]:
    """Provides a fresh temporary directory for file product testing."""
    with tempfile.TemporaryDirectory(prefix="allsky_test_") as tmpdir:
        yield tmpdir


@pytest.fixture
def sqlite_queue_db(temp_output_dir) -> Generator[str, None, None]:
    """Initializes a temporary SQLite WAL queue database adhering to PROJECT.md schema."""
    db_path = os.path.join(temp_output_dir, "test_sync_queue.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            priority INTEGER DEFAULT 5,
            payload_json TEXT,
            file_path TEXT,
            status TEXT DEFAULT 'pending',
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 50,
            next_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_error TEXT
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sync_queue_status ON sync_queue (status, next_retry_at, priority);")
    conn.commit()
    conn.close()
    yield db_path


# ---------------------------------------------------------------------------
# Admin login in tests: every TestClient is signed in as admin by default (existing tests exercise write routes);
# `guest_client` is the explicit not-signed-in client. Auth state never touches the real data/ folder.
# ---------------------------------------------------------------------------
TEST_ADMIN_TOKEN = "test-admin-session-token"


@pytest.fixture(autouse=True, scope="session")
def _admin_cookie_for_test_clients(tmp_path_factory):
    from starlette.testclient import TestClient
    from src.auth.manager import AuthManager, COOKIE_NAME

    real_data = os.path.join(PROJECT_ROOT, "data")
    auth_dir = str(tmp_path_factory.mktemp("auth"))
    orig_init, orig_valid, orig_client = AuthManager.__init__, AuthManager.session_valid, TestClient.__init__

    def init(self, data_dir, *a, **k):
        orig_init(self, auth_dir if os.path.abspath(data_dir) == real_data else data_dir, *a, **k)

    def valid(self, token):
        return token == TEST_ADMIN_TOKEN or orig_valid(self, token)

    def client_init(self, *a, **k):
        orig_client(self, *a, **k)
        self.cookies.set(COOKIE_NAME, TEST_ADMIN_TOKEN)

    AuthManager.__init__, AuthManager.session_valid, TestClient.__init__ = init, valid, client_init
    yield
    AuthManager.__init__, AuthManager.session_valid, TestClient.__init__ = orig_init, orig_valid, orig_client


@pytest.fixture(scope="module")
def guest_client(tmp_path_factory):
    """TestClient with no session cookie, on a service whose admin password is not set yet."""
    from starlette.testclient import TestClient
    from src.api.main import create_app
    from src.api.service import AllSkyService
    from src.auth.manager import AuthManager
    from src.hardware.simulator import SimulatedIMX335Controller

    cam = SimulatedIMX335Controller(width=640, height=480, seed=42)
    cam.open()
    AllSkyService.reset_instance()
    service = AllSkyService.get_instance(camera=cam)
    service.community.data_dir = str(tmp_path_factory.mktemp("community"))
    auth_dir = str(tmp_path_factory.mktemp("guest_auth"))
    service.auth = AuthManager(auth_dir)
    app = create_app(camera=cam)
    with TestClient(app) as c:
        c.cookies.clear()
        c.service, c.auth_dir = service, auth_dir
        yield c
    service.close()
    AllSkyService.reset_instance()
