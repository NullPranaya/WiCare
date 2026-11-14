"""All settings from env (.env locally). Demo values only, not clinical thresholds."""
import os
from dotenv import load_dotenv

load_dotenv()

def _b(name, default='0'):
    return os.getenv(name, default).strip().lower() in ('1', 'true', 'yes')

class Settings:
    def __init__(self):
        self.db_path = os.getenv('AURACARE_DB', 'auracare.db')
        self.demo_mode = _b('DEMO_MODE', '1')              # replay/synthetic may message TEST recipients only
        self.sensor_id = os.getenv('SENSOR_ID', 'esp32_01')
        self.room_id = os.getenv('ROOM_ID', 'demo_room')
        self.stale_after_s = float(os.getenv('SENSOR_STALE_S', '5'))
        # consent + test contacts: resolved server-side by role only
        self.participant_opt_in = _b('PARTICIPANT_OPT_IN', '0')
        self.expected_active = _b('EXPECTED_ACTIVE', '1')
        self.patient_address = os.getenv('PATIENT_ADDRESS', '')      # E.164 for iMessage
        self.caregiver_address = os.getenv('CAREGIVER_ADDRESS', '')
        self.checkin_timeout_s = float(os.getenv('CHECKIN_TIMEOUT_S', '90'))
        # photon
        self.photon_mode = os.getenv('PHOTON_MODE', 'mock')          # mock | bridge
        self.bridge_url = os.getenv('PHOTON_BRIDGE_URL', 'http://127.0.0.1:8787')
        self.bridge_secret = os.getenv('BRIDGE_SECRET', '')
        self.spectrum_webhook_secret = os.getenv('SPECTRUM_WEBHOOK_SECRET', '')
        # live activity model: only attached to events after the live-vs-WiFall check passed
        self.live_har_validated = _b('LIVE_HAR_VALIDATED', '0')

    def role_of(self, address):
        if address and address == self.patient_address: return 'patient'
        if address and address == self.caregiver_address: return 'caregiver'
        return None

settings = Settings()
