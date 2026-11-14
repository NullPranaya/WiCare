from tests.conftest import feed_fall_like
from photon import checkins

def msgs(svc, role=None):
    q = 'SELECT * FROM messages WHERE direction="out"' + (f' AND role="{role}"' if role else '')
    return svc.db.q(q)

def test_abrupt_then_still_creates_one_checkin_and_one_message(svc):
    ev = feed_fall_like(svc)
    assert ev and ev['event_type'] == 'abrupt_motion_then_inactivity'
    assert ev['policy']['action'] == 'routine_checkin' and ev['checkin_id']
    assert ev['review_status'] == 'unconfirmed'
    assert len(ev['evidence_ids']) > 5 and all(i.startswith('obs_') for i in ev['evidence_ids'])
    assert [m['status'] for m in msgs(svc, 'patient')] == ['mock_sent']
    nurse = msgs(svc, 'caregiver')                                  # nurse hears at the same time, reply or not
    assert len(nurse) == 1 and 'Care Team Notice' in nurse[0]['text'] and ev['id'] in nurse[0]['text']
    assert ev['review_status'] == 'unconfirmed'                     # a heads-up is not an escalation
    # same pattern again while check-in is open: evidence updates, no new message
    ev2 = feed_fall_like(svc, t0=1_000_100.0)
    assert ev2['policy']['action'] == 'update_existing'
    assert len(msgs(svc, 'patient')) == 1 and len(msgs(svc, 'caregiver')) == 1

def test_stale_sensor_is_unavailable_not_inactivity(svc):
    svc.observe('esp32_01', 0.3)
    svc.db.x('UPDATE sensors SET last_seen = last_seen - 60')
    h = svc.health('esp32_01')
    assert h['fresh'] is False and h['status'] == 'stale'
    ev = svc.create_event('esp32_01', 'live_score', 'prolonged_low_activity', 1.0, [], {})
    assert ev['policy']['action'] == 'sensor_unavailable' and ev['checkin_id'] is None
    assert msgs(svc) == []

def test_gap_resets_detector(svc):
    t = 1_000_000.0
    for s in [0.3] * 4 + [0.95]:
        svc.observe('esp32_01', s, ts=t); t += 0.5
    svc.db.x('UPDATE sensors SET last_seen = last_seen - 60')     # outage
    ev = None
    for s in [0.05] * 26:
        ev = svc.observe('esp32_01', s, ts=t) or ev; t += 0.5
    assert ev is None                                             # spike before the gap is forgotten

def test_replay_outside_demo_mode_never_messages(svc):
    svc.s.demo_mode = False
    ev = feed_fall_like(svc, source='wifall_replay')
    assert ev['policy']['action'] == 'monitor' and msgs(svc) == []

def test_no_opt_in_no_message(svc):
    svc.s.participant_opt_in = False
    ev = feed_fall_like(svc)
    assert ev['policy']['action'] == 'monitor' and msgs(svc) == []

def test_delivery_failure_recorded(svc):
    svc.s.patient_address = ''
    ev = feed_fall_like(svc)
    c = checkins.get(svc.db, ev['checkin_id'])
    assert c['state'] == 'delivery_failed'
    assert msgs(svc, 'patient')[0]['status'] == 'delivery_failed'
    assert len(msgs(svc, 'caregiver')) == 1

def test_timeout_escalates(svc):
    ev = feed_fall_like(svc)
    svc.db.x('UPDATE checkins SET deadline = 0')
    assert checkins.sweep_timeouts(svc.db, svc.photon) == 1
    assert checkins.get(svc.db, ev['checkin_id'])['state'] == 'timeout'
    nurse = [m['text'] for m in msgs(svc, 'caregiver')]
    assert len(nurse) == 2 and 'No Reply' in nurse[1]                # notice, then the timeout alert
    assert checkins.sweep_timeouts(svc.db, svc.photon) == 0       # not repeated

def test_ml_only_attached_after_validation(svc):
    from backend.db import now
    svc.latest_ml = (now(), dict(status='ok', activity='walk', model_score=0.5))
    ev = feed_fall_like(svc)
    assert ev['ml']['status'] == 'live_har_not_validated'
    svc.s.live_har_validated = True
    svc.latest_ml = (now(), dict(status='ok', activity='walk', model_score=0.5))
    ev2 = svc.create_event('esp32_01', 'live_score', 'prolonged_low_activity', 5.0, [], {})
    assert ev2['ml']['activity'] == 'walk' and ev2['ml']['source'] == 'live_csi'

def test_care_state_follows_checkin(svc):
    from backend import care_state
    s = care_state.snapshot(svc)
    assert s['step'] == 0 and s['messages'] == []
    ev = feed_fall_like(svc)
    s = care_state.snapshot(svc)
    assert s['event']['id'] == ev['id'] and s['step'] == 2 and s['status'] == 'watch'
    assert [m['role'] for m in s['messages']] == ['patient', 'caregiver'] and '+1' not in str(s)  # roles only
    svc.db.x("UPDATE checkins SET state='timeout'")
    s = care_state.snapshot(svc)
    assert s['status'] == 'help' and s['step'] == 4
