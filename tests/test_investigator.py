from tests.conftest import feed_fall_like
from investigation import investigator
from photon.reply_parser import interpret

def test_report_is_evidence_linked(svc):
    ev = feed_fall_like(svc)
    inv = investigator.investigate(svc, ev['id'])
    r = inv['result']
    assert r['data_source'] == 'live_score' and r['sensor_status'] == 'ok'
    assert ev['id'] in r['evidence_ids'] and r['evidence_ids'][1] in ev['evidence_ids']
    assert any('x the calibrated' in o for o in r['observations'])
    assert any('cannot confirm a fall' in u for u in r['uncertainty'])
    assert r['recommendation'] == 'routine_checkin'
    known = {o['id'] for o in svc.db.q('SELECT id FROM observations')} | {ev['id'], ev['checkin_id']}
    assert set(r['evidence_ids']) <= known                       # every cited ID exists

def test_stale_sensor_report_says_unavailable(svc):
    ev = feed_fall_like(svc)
    svc.db.x('UPDATE sensors SET last_seen = last_seen - 60')
    r = investigator.investigate(svc, ev['id'])['result']
    assert r['recommendation'] == 'inspect_sensor' and 'Missing data is not inactivity' in ' '.join(r['uncertainty'])

def test_replay_labelled(svc):
    ev = feed_fall_like(svc, source='wifall_replay')
    assert 'not a live person' in investigator.investigate(svc, ev['id'])['result']['observations'][0]

def test_reply_parser():
    assert interpret('OK')['intent'] == 'acknowledged' and interpret('2')['intent'] == 'help_requested'
    assert interpret('I fell in the kitchen')['intent'] == 'help_requested'
    assert interpret("I'm fine thanks")['intent'] == 'ambiguous'       # free text never closes
