import hashlib, hmac, json, time
import pytest
from fastapi.testclient import TestClient
import backend.main as main
from tests.conftest import feed_fall_like

@pytest.fixture
def client(svc, monkeypatch):
    monkeypatch.setattr(main, 'svc', svc)
    return TestClient(main.app)

def payload(mid, sender, text):
    return {'event': 'messages', 'space': {'id': 'any;-;' + sender},
            'message': {'id': mid, 'sender': {'id': sender}, 'content': {'type': 'text', 'text': text}}}

def post(client, p, secret='bridge-test'):
    return client.post('/webhooks/photon', json=p, headers={'x-bridge-secret': secret})

def state(svc, ev): return svc.checkin_for_event(ev['id'])['checkin']['state']

def test_ok_acknowledges(client, svc):
    ev = feed_fall_like(svc)
    r = post(client, payload('m1', '+15550000001', 'OK')).json()
    assert r['interpretation']['intent'] == 'acknowledged' and state(svc, ev) == 'acknowledged'

def test_help_notifies_caregiver(client, svc):
    ev = feed_fall_like(svc)
    post(client, payload('m1', '+15550000001', '2'))
    assert state(svc, ev) == 'help_requested'
    assert svc.db.one('SELECT count(*) n FROM messages WHERE role="caregiver" AND direction="out"')['n'] == 1

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
    assert svc.db.one('SELECT count(*) n FROM messages WHERE role="caregiver" AND direction="out"')['n'] == 1

def test_unknown_sender_and_bad_secret(client, svc):
    feed_fall_like(svc)
    assert post(client, payload('m1', '+19999999999', 'OK')).json()['ignored'] == 'unauthorized sender'
    assert post(client, payload('m2', '+15550000001', 'OK'), secret='wrong').status_code == 401

def test_native_spectrum_signature(client, svc):
    feed_fall_like(svc)
    raw = json.dumps(payload('m9', '+15550000001', 'OK')).encode()
    ts = str(int(time.time()))
    sig = 'v0=' + hmac.new(b'spectrum-test', b'v0:' + ts.encode() + b':' + raw, hashlib.sha256).hexdigest()
    h = {'x-spectrum-event': 'messages', 'x-spectrum-timestamp': ts, 'x-spectrum-signature': sig,
         'content-type': 'application/json'}
    assert client.post('/webhooks/photon', content=raw, headers=h).status_code == 200
    h['x-spectrum-timestamp'] = str(int(time.time()) - 1000)
    assert client.post('/webhooks/photon', content=raw, headers=h).status_code == 401

def test_caregiver_question_gets_grounded_answer(client, svc):
    ev = feed_fall_like(svc)
    r = post(client, payload('c1', '+15550000002', 'why did this alert?')).json()
    assert r['answered'] and ev['id'] in r['text'] and 'threshold' in r['text']
