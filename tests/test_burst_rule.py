"""The team's ESPectre burst-then-stillness rule (monitoring/burst_rule.py) and its wiring into AuraCare."""
from monitoring.burst_rule import BurstDetector, BurstRule, Fusion
from backend.config import Settings
from backend.service import Service

HZ = 4.0

def run(det, segments, t0=1000.0):
    """segments: [(seconds, score)] fed at 4 Hz. Returns the events produced."""
    t, i, events = t0, 0, []
    for secs, score in segments:
        for _ in range(int(secs * HZ)):
            e = det.feed(dict(id=f'obs_{i}', ts=t, score=score))
            if e: events.append(e)
            t += 1 / HZ; i += 1
    return events

def test_short_burst_then_stillness_is_one_event():
    ev = run(BurstDetector(BurstRule()), [(2, 0.2), (1.5, 1.3), (10, 0.1)])
    assert len(ev) == 1 and ev[0]['event_type'] == 'burst_then_stillness'
    o = ev[0]['observations']
    assert 1.0 <= o['burst_seconds'] <= 2.0 and o['idle_seconds'] >= 8 and o['peak_score'] == 1.3

def test_long_burst_is_walking_not_a_fall():
    assert run(BurstDetector(BurstRule()), [(1, 0.2), (5, 1.3), (12, 0.1)]) == []

def test_movement_during_stillness_cancels():
    assert run(BurstDetector(BurstRule()), [(1, 1.3), (4, 0.1), (1, 1.3), (1, 0.1)]) == []

def test_brief_dip_inside_a_burst_is_the_same_burst():
    ev = run(BurstDetector(BurstRule()), [(0.75, 1.3), (0.5, 0.1), (0.75, 1.3), (9, 0.1)])
    assert len(ev) == 1

def test_signal_gap_during_stillness_never_counts_as_still():
    det = BurstDetector(BurstRule())
    run(det, [(1, 1.3), (4, 0.1)], t0=1000.0)
    assert run(det, [(6, 0.1)], t0=1000.0 + 5 + 3.0) == []         # 3 s silence > 1.5 s max gap

def test_fusion_needs_every_board_for_a_burst():
    f = Fusion(['b1', 'b2'], 0.962)
    assert f.update('b1', 0.0, 1.3) is None                          # b2 has not reported yet
    assert f.update('b2', 0.1, 0.2) == 'mixed'                       # only b1 saw the burst
    assert f.update('b2', 0.3, 1.1) == 'motion'                      # both within 1 s
    assert f.update('b1', 0.5, 0.1) == 'motion'                      # both crossed within the last 1 s
    assert f.update('b2', 0.6, 0.1) == 'still'                       # every board below the threshold
    assert f.update('b1', 2.0, 1.3) == 'mixed'                       # b1 alone, b2's crossing is > 1 s old
    assert f.update('b1', 3.0, 0.1) is None                          # b2 silent > 1.5 s

def test_burst_mode_service_opens_checkin_and_tells_nurse(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Settings()
    s.db_path = str(tmp_path / 'b.db'); s.photon_mode = 'mock'; s.detector = 'burst'
    s.participant_opt_in = True; s.demo_mode = True; s.expected_active = False
    s.patient_address, s.caregiver_address = '+15550000001', '+15550000002'
    svc = Service(s)
    assert svc.baseline(s.sensor_id)['thresholds']['high'] == 0.962
    t, ev = 2000.0, None
    for secs, score in [(1, 0.2), (1.5, 1.3), (10, 0.1)]:
        for _ in range(int(secs * HZ)):
            ev = svc.observe(s.sensor_id, score, source='espectre_live', ts=t) or ev
            t += 1 / HZ
    assert ev and ev['event_type'] == 'burst_then_stillness' and ev['policy']['action'] == 'routine_checkin'
    roles = [m['role'] for m in svc.db.q("SELECT role FROM messages WHERE direction='out' ORDER BY created_ts")]
    assert roles == ['patient', 'caregiver']
