import base64, hashlib, hmac, json, time
import pytest
from fastapi.testclient import TestClient
import backend.main as main
from tests.conftest import feed_fall_like, WHSEC

@pytest.fixture
def client(svc, monkeypatch):
    monkeypatch.setattr(main, 'svc', svc)
    return TestClient(main.app)

def payload(mid, sender, text, kind='human'):
    """Photon 2.0 message.received event, shape copied from a real delivery (apiVersion 2026-07-01)."""
    return {'id': 'evt_' + mid, 'apiVersion': '2026-07-01', 'type': 'message.received', 'source': 'imessage.photon',
            'data': {'object': {'id': mid, 'content': {'type': 'text', 'format': 'plain', 'text': text},
                                 'sentBy': {'id': 'pho_usr_' + sender[-4:], 'handle': sender, 'kind': kind},
                                 'space': {'id': 'pho_spc_1', 'kind': 'direct'},
                                 'createdAt': '2026-11-14T00:00:00.000Z'}}}

def sign(raw, wid, ts, secret=WHSEC):
    key = base64.b64decode(secret.removeprefix('whsec_'))
    return 'v1,' + base64.b64encode(hmac.new(key, f'{wid}.{ts}.'.encode() + raw, hashlib.sha256).digest()).decode()

def post(client, p, secret=WHSEC, ts=None):
    raw = json.dumps(p).encode(); ts = str(ts or int(time.time())); wid = p['id']
    h = {'webhook-id': wid, 'webhook-timestamp': ts, 'webhook-signature': sign(raw, wid, ts, secret),
         'content-type': 'application/json'}
    return client.post('/webhooks/photon', content=raw, headers=h)

def state(svc, ev): return svc.checkin_for_event(ev['id'])['checkin']['state']

def test_ok_acknowledges(client, svc):
    ev = feed_fall_like(svc)
    r = post(client, payload('m1', '+15550000001', 'OK')).json()
    assert r['interpretation']['intent'] == 'acknowledged' and state(svc, ev) == 'acknowledged'

def test_help_notifies_caregiver(client, svc):
    ev = feed_fall_like(svc)
    post(client, payload('m1', '+15550000001', '2'))
    assert state(svc, ev) == 'help_requested'
    nurse = [m['text'] for m in svc.db.q('SELECT text FROM messages WHERE role="caregiver" AND direction="out" ORDER BY created_ts')]
    assert len(nurse) == 2 and 'Care Team Notice' in nurse[0] and 'replied NO' in nurse[1]
    pat = [m['text'] for m in svc.db.q('SELECT text FROM messages WHERE role="patient" AND direction="out" ORDER BY created_ts')]
    assert 'Wellbeing Check' in pat[0] and 'nurse know right away' in pat[1]

def test_mixed_reply_with_help_signal_escalates(client, svc):
    ev = feed_fall_like(svc)
    r = post(client, payload('m1', '+15550000001', "I'm fine but cannot get up")).json()
    assert r['interpretation']['intent'] == 'help_requested' and state(svc, ev) == 'help_requested'

def test_ambiguous_never_closes(client, svc):
    ev = feed_fall_like(svc)
    r = post(client, payload('m1', '+15550000001', "hmm maybe later")).json()
    assert r['interpretation']['intent'] == 'ambiguous' and state(svc, ev) == 'ambiguous'
    assert svc.event(ev['id'])['review_status'] == 'caregiver_review'
    post(client, payload('m2', '+15550000001', 'OK'))              # explicit OK still works afterwards
    assert state(svc, ev) == 'acknowledged'

def test_duplicate_webhook_ignored(client, svc):
    feed_fall_like(svc)
    post(client, payload('m1', '+15550000001', '2'))
    assert post(client, payload('m1', '+15550000001', '2')).json() == {'duplicate': True}
    assert svc.db.one('SELECT count(*) n FROM messages WHERE role="caregiver" AND direction="out"')['n'] == 2  # notice + help, once

def test_unknown_sender_and_bad_signature(client, svc):
    feed_fall_like(svc)
    assert post(client, payload('m1', '+19999999999', 'OK')).json()['ignored'] == 'unauthorized sender'
    wrong = 'whsec_' + base64.b64encode(b'x' * 32).decode()
    assert post(client, payload('m2', '+15550000001', 'OK'), secret=wrong).status_code == 400

def test_signature_rotation_and_stale_timestamp(client, svc):
    feed_fall_like(svc)
    p = payload('m9', '+15550000001', 'OK'); raw = json.dumps(p).encode(); ts = str(int(time.time()))
    old = sign(raw, p['id'], ts, 'whsec_' + base64.b64encode(b'o' * 32).decode())
    h = {'webhook-id': p['id'], 'webhook-timestamp': ts, 'webhook-signature': old + ' ' + sign(raw, p['id'], ts)}
    assert client.post('/webhooks/photon', content=raw, headers=h).status_code == 200
    assert post(client, payload('m10', '+15550000001', 'OK'), ts=int(time.time()) - 1000).status_code == 400

def test_own_agent_message_ignored(client, svc):
    feed_fall_like(svc)
    assert 'ignored' in post(client, payload('m11', '+15550000001', 'OK', kind='agent')).json()

def test_caregiver_question_gets_grounded_answer(client, svc):
    ev = feed_fall_like(svc)
    r = post(client, payload('c1', '+15550000002', 'why did this alert?')).json()
    assert r['answered'] and ev['id'] in r['text'] and 'threshold' in r['text']

def test_photon_idempotency_header_is_valid_and_stable():
    import re
    from photon.client import idem_header
    k = 'checkin:chk_1:caregiver:No reply to the check-in before the deadline.'
    h = idem_header(k)
    assert re.fullmatch(r'[\x21-\x7e]{1,255}', h) and h == idem_header(k) and h != idem_header(k + 'x')

@pytest.mark.parametrize('reply,intent,nurse_word', [('1', 'acknowledged', 'replied YES'), ('Yes', 'acknowledged', 'replied YES'),
                                                      ("I\u2019m okay", 'acknowledged', 'replied YES'), ('no', 'help_requested', 'replied NO')])
def test_yes_no_replies_update_nurse_and_thank_patient(client, svc, reply, intent, nurse_word):
    ev = feed_fall_like(svc)
    assert post(client, payload('m1', '+15550000001', reply)).json()['interpretation']['intent'] == intent
    nurse = [m['text'] for m in svc.db.q('SELECT text FROM messages WHERE role="caregiver" AND direction="out" ORDER BY created_ts')]
    assert len(nurse) == 2 and nurse_word in nurse[1] and ev['id'] in nurse[1]
    assert svc.db.one('SELECT count(*) n FROM messages WHERE role="patient" AND direction="out"')['n'] == 2   # check + thanks
