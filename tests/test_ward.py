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

def test_wicare_board_drives_stress_and_remote_overrides(svc):
    w = svc.ward
    t = 1000.0
    for i in range(3):                                     # board below its threshold: calm
        w.board_sample(0.3, t + i); w.tick(t + i)
    assert w.source(t + 2) == 'board' and w.alert is None
    t += 3
    for i in range(3):                                     # board at 1.3 (> 0.962): alert after 1 s
        w.board_sample(1.3, t + i); w.tick(t + i)
    assert w.alert and w.alert['state'] == 'asked'
    w.close(); w.armed = True
    w.set_target(20, t=t + 4)                              # remote takes over even with the board streaming
    for i in range(4, 8):
        w.board_sample(1.3, t + i); w.tick(t + i)
    assert w.source(t + 7) == 'remote' and w.alert is None

def test_board_observations_reach_ward(svc):
    svc.observe('esp32_01', 1.2, source='espectre_live')
    assert svc.ward.source() == 'board'
    svc.observe('esp32_01', 1.2, source='live_score')       # other sources do not feed the monitor

def test_patient_checkins_off_leaves_only_the_room_question(svc):
    from tests.conftest import feed_fall_like
    svc.s.patient_checkins = False
    ev = feed_fall_like(svc)
    assert ev and not svc.event(ev['id'])['checkin_id']
    assert not svc.db.q("SELECT * FROM messages WHERE role='patient'")
