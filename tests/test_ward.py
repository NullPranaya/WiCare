from fastapi.testclient import TestClient
import backend.main as main
from backend import ward as W
from tests.test_webhook import payload, post

def _client(svc, monkeypatch):
    monkeypatch.setattr(main, 'svc', svc)
    return TestClient(main.app)

def _run(w, target, seconds, t0):
    w.set_target(target)
    for i in range(seconds): w.tick(t0 + i)
    return t0 + seconds

def test_calm_rooms_stay_below_alert():
    w = W.Ward(photon=None, db=None)
    assert all(w.current(r, t) < W.LOW for r in range(1, 6) if r != W.LIVE_ROOM for t in range(0, 600, 7))

def test_live_room_high_asks_nurse_once_and_yes_opens_room(svc, monkeypatch):
    c = _client(svc, monkeypatch); w = svc.ward
    t = _run(w, 30, 5, 1000.0)
    assert w.alert is None
    t = _run(w, 90, 10, t)
    assert w.alert['state'] == 'asked'
    asks = svc.db.q("SELECT * FROM messages WHERE event_id=? AND direction='out'", (w.alert['id'],))
    assert len(asks) == 1 and asks[0]['role'] == 'caregiver' and f'Room {W.LIVE_ROOM}' in asks[0]['text']
    _run(w, 90, 10, t)                                     # still high: no second message
    assert len(svc.db.q("SELECT * FROM messages WHERE event_id=?", (w.alert['id'],))) == 1
    r = post(c, payload('m_ward_yes', svc.s.caregiver_address, 'Yes'))
    assert r.json()['ward']['state'] == 'viewing'
    s = c.get('/ward/state').json()
    assert s['alert']['state'] == 'viewing' and [m['direction'] for m in s['messages']] == ['out', 'in', 'out']

def test_no_dismisses_and_rearms_only_after_calm(svc, monkeypatch):
    c = _client(svc, monkeypatch); w = svc.ward
    t = _run(w, 90, 10, 1000.0)
    post(c, payload('m_ward_no', svc.s.caregiver_address, '2'))
    assert w.alert['state'] == 'dismissed'
    t = _run(w, 90, 10, t)
    assert w.alert['state'] == 'dismissed'                 # no re-ask while still high
    t = _run(w, 20, 5, t)
    assert w.alert is None
    _run(w, 90, 10, t)
    assert w.alert['state'] == 'asked'

def test_screen_button_and_close(svc, monkeypatch):
    c = _client(svc, monkeypatch)
    assert c.post('/ward/view').json()['state'] == 'viewing'
    c.post('/ward/close')
    assert c.get('/ward/state').json()['alert'] is None
    assert '/ward/stress' in c.get('/ward/control').text
