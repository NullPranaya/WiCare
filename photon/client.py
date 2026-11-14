"""Outbound messages over the Photon 2.0 (Beta) REST API (https://api.photon.codes/v1).
Send = create/get User for the recipient handle -> find the shared line assigned to that handle ->
create/get the direct Space (line agent first) -> POST /messages. Recipients are resolved by ROLE from
server config; callers (including the investigator) can never pass an address.
Shared-line rule: Photon only lets us send to a peer who has messaged the line since their Assignment."""
import hashlib, json, requests
from backend.db import new_id, now

def idem_header(key):
    """Photon requires ^[\\x21-\\x7e]{1,255}$ (no spaces). Our keys can contain message text, so send a
    deterministic hash: same logical key -> same header, so retries stay idempotent."""
    return 'ac_' + hashlib.sha256(key.encode()).hexdigest()

class PhotonClient:
    def __init__(self, db, settings):
        self.db, self.s = db, settings
        self._users, self._lines = {}, {}           # handle -> pho_usr id, peer handle -> line handle

    def _address(self, role):
        return {'patient': self.s.patient_address, 'caregiver': self.s.caregiver_address}.get(role, '')

    # --- Photon API ---
    def _call(self, method, path, idem=None, **kw):
        h = {'Authorization': f'Bearer {self.s.photon_api_key}'}
        if idem: h['Idempotency-Key'] = idem_header(idem)
        r = requests.request(method, f'{self.s.photon_api_base}/v1/projects/{self.s.photon_project_id}{path}',
                             headers=h, timeout=20, **kw)
        if r.status_code >= 400:
            raise requests.HTTPError(f'{r.status_code} {r.text[:300]}', response=r)
        return r.json()

    def _user(self, handle):
        if handle not in self._users:
            self._users[handle] = self._call('POST', '/users', idem=f'user:{handle}',
                                             json={'handle': handle, 'platform': 'imessage'})['id']
        return self._users[handle]

    def _line(self, peer):
        if self.s.photon_line: return self.s.photon_line
        if peer not in self._lines:
            page = self._call('GET', '/platforms/imessage/assignments')
            for a in page.get('assignments', []):
                if a.get('handle') == peer and not a.get('releasedAt'):
                    self._lines[peer] = a['assignedPhoneNumber']
            if peer not in self._lines:
                raise LookupError(f'no active iMessage assignment for this {peer[:2]}... handle')
        return self._lines[peer]

    def _send_api(self, to, text, idem_key):
        agent, peer = self._user(self._line(to)), self._user(to)
        space = self._call('POST', '/spaces', idem=f'space:{agent}:{peer}',
                           json={'platform': 'imessage', 'participantIds': [agent, peer]})
        return self._call('POST', '/messages', idem=idem_key,
                          json={'spaceId': space['id'], 'senderId': agent,
                                'content': {'type': 'text', 'format': 'plain', 'text': text}})

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
                m = self._send_api(to, text, idem_key)
                status, detail = 'accepted', json.dumps({'id': m.get('id'), 'status': m.get('status')})  # accepted != delivered
            except (requests.RequestException, LookupError, KeyError, ValueError) as e:
                status, detail = 'delivery_failed', str(e)[:300]
        if prior:
            self.db.x('UPDATE messages SET status=?, detail=?, created_ts=? WHERE id=?', (status, detail, now(), mid))
        else:
            self.db.x('INSERT INTO messages(id,checkin_id,event_id,direction,role,text,status,idem_key,created_ts,detail)'
                      ' VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (mid, checkin_id, event_id, 'out', role, text, status, idem_key, now(), detail))
        return self.db.one('SELECT * FROM messages WHERE id=?', (mid,))
