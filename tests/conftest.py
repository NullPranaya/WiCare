import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest
from backend.config import Settings
from backend.service import Service
from monitoring.anomaly import Thresholds

WHSEC = 'whsec_' + __import__('base64').b64encode(b'photon-test-secret-32-bytes-long').decode()

@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Settings()
    s.db_path = str(tmp_path / 't.db'); s.photon_mode = 'mock'
    s.participant_opt_in = True; s.demo_mode = True; s.expected_active = True
    s.patient_address, s.caregiver_address = '+15550000001', '+15550000002'
    s.photon_webhook_secret = WHSEC
    s.stale_after_s = 5; s.checkin_timeout_s = 90
    v = Service(s)
    v.set_calibration(Thresholds(high=0.8, low=0.1, still_s=10, inactive_s=60), 'test', path=str(tmp_path / 'cal.json'))
    return v

def feed_fall_like(svc, t0=1_000_000.0, source='live_score', sensor='esp32_01'):
    """normal motion -> abrupt spike -> 12 s low activity at 2 Hz. Returns the event (or None)."""
    ev, t = None, t0
    for s in [0.3, 0.4, 0.35] * 4 + [0.95] + [0.05] * 26:
        ev = svc.observe(sensor, s, source=source, ts=t) or ev
        t += 0.5
    return ev
