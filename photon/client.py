"""Outbound messages. Photon has no public HTTP send endpoint today, so sends go through the
spectrum-ts bridge (photon/bridge/bridge.ts). Recipients are resolved by ROLE from server config;
callers (including the agent) can never pass an address."""
import json, requests
from backend.db import new_id, now

class PhotonClient:
    def __init__(self, db, settings):
        self.db, self.s = db, settings

    def _address(self, role):
        return {'patient': self.s.patient_address, 'caregiver': self.s.caregiver_address}.get(role, '')

    def send(self, role, text, idem_key, checkin_id=None, event_id=None):
        prior = self.db.one('SELECT * FROM messages WHERE idem_key=?', (idem_key,))
        if prior and prior['status'] in ('mock_sent', 'accepted', 'delivered'):
            return prior                                  # idempotent: never double-send
        to = self._address(role)
        mid = prior['id'] if prior else new_id('msg')
        if not to:
            status, detail = 'delivery_failed', 'no opted-in test address configured for role'
        elif self.s.photon_mode == 'mock':
            status, detail = 'mock_sent', 'PHOTON_MODE=mock'
        else:
            try:
                r = requests.post(self.s.bridge_url + '/send', timeout=20,
                                  headers={'X-Bridge-Secret': self.s.bridge_secret},
                                  json={'to': to, 'text': text, 'key': idem_key})
                r.raise_for_status()
                status, detail = 'accepted', json.dumps(r.json())   # accepted != delivered
            except requests.RequestException as e:
                status, detail = 'delivery_failed', str(e)[:300]
        if prior:
            self.db.x('UPDATE messages SET status=?, detail=?, created_ts=? WHERE id=?', (status, detail, now(), mid))
        else:
            self.db.x('INSERT INTO messages(id,checkin_id,event_id,direction,role,text,status,idem_key,created_ts,detail)'
                      ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (mid, checkin_id, event_id, 'out', role, text, status, idem_key, now(), detail))
        return self.db.one('SELECT * FROM messages WHERE id=?', (mid,))
