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
        # 0 = room demo: the nurse only gets the room's "check on them? 1/2" text (backend/ward.py), no patient check-in
        self.patient_checkins = _b('PATIENT_CHECKINS', '1')
        # photon
        self.photon_mode = os.getenv('PHOTON_MODE', 'mock')          # mock | api
        self.photon_api_base = os.getenv('PHOTON_API_BASE', 'https://api.photon.codes')
        self.photon_project_id = os.getenv('PHOTON_PROJECT_ID', '')
        self.photon_api_key = os.getenv('PHOTON_PROJECT_API_KEY', '')  # pho_sk_...
        self.photon_webhook_secret = os.getenv('PHOTON_WEBHOOK_SECRET', '')  # whsec_...
        self.photon_line = os.getenv('PHOTON_LINE', '')              # optional: our line's E.164; else looked up per assignment
        # live activity model: only attached to events after the live-vs-WiFall check passed
        self.live_har_validated = _b('LIVE_HAR_VALIDATED', '0')
        # detector: 'burst' (default, the live demo) = ESPectre boards + the team's burst-then-stillness rule
        #           (monitoring/burst_rule.py); 'score' = earlier research path, room-trained CSI score + anomaly.py
        self.detector = os.getenv('DETECTOR', 'burst').strip().lower()
        self.burst_threshold = float(os.getenv('BURST_THRESHOLD', '0.962'))
        self.burst_max_s = float(os.getenv('BURST_MAX_S', '3.0'))
        self.burst_still_s = float(os.getenv('BURST_STILL_S', '8.0'))

    def role_of(self, address):
        if address and address == self.patient_address: return 'patient'
        if address and address == self.caregiver_address: return 'caregiver'
        return None

settings = Settings()
