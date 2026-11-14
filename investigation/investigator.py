"""Deterministic event investigator (no LLM, free, no hallucination). Gathers the same evidence a caregiver
would ask for, computes signal-quality checks, and writes an evidence-linked report. Every statement is
generated from stored numbers and cites the IDs it came from. It never sends messages or changes policy."""
import json
import numpy as np
from backend.db import new_id, now, iso

def _coverage(obs, window_s, expected_hz):
    if len(obs) < 2:
        return 0.0, None
    ts = np.array([o['ts'] for o in obs])
    return min(1.0, len(obs) / max(1, window_s * expected_hz)), float(np.diff(ts).max())

def investigate(svc, event_id, expected_hz=2.0):
    steps = []
    def step(name, fn):
        out = fn(); steps.append(dict(step=name, utc=iso(now()))); return out

    ev = step('event', lambda: svc.event(event_id))
    if not ev:
        raise ValueError('unknown event')
    health = step('sensor_health', lambda: svc.health(ev['sensor_id']))
    base = step('baseline', lambda: svc.baseline(ev['sensor_id']))
    chk = step('checkin', lambda: svc.checkin_for_event(event_id))
    pre = step('activity_before', lambda: svc.db.q(
        'SELECT id,ts,score FROM observations WHERE sensor_id=? AND ts BETWEEN ? AND ? ORDER BY ts',
        (ev['sensor_id'], ev['start_ts'] - 30, ev['start_ts'])))
    post = step('activity_after', lambda: svc.db.q(
        'SELECT id,ts,score FROM observations WHERE sensor_id=? AND ts BETWEEN ? AND ? ORDER BY ts',
        (ev['sensor_id'], ev['start_ts'], ev['start_ts'] + 30)))

    obs, ev_ids, unc = [], [ev['id']] + list(ev['evidence_ids'] or [])[:5], []
    o = ev['observations'] or {}
    th = (base.get('thresholds') or {}) if base.get('available') else {}

    obs.append(f"Source: {ev['source']}" + (' (recorded replay, not a live person)' if ev['source'] in ('wifall_replay', 'synthetic') else ''))
    if 'peak_score' in o and th.get('high'):
        obs.append(f"Peak movement score {o['peak_score']} = {o['peak_score'] / th['high']:.1f}x the calibrated 'abrupt' threshold {th['high']:.3g}.")
    if 'idle_seconds' in o:
        obs.append(f"Low activity for {o['idle_seconds']} s after the event (threshold {th.get('low', o.get('low_threshold'))}).")
    if pre:
        s = np.array([p['score'] for p in pre])
        obs.append(f"30 s before: median score {np.median(s):.3g}, max {s.max():.3g} ({len(pre)} readings).")
        ev_ids += [pre[0]['id'], pre[-1]['id']]
    cov_pre, gap_pre = _coverage(pre, 30, expected_hz)
    cov_post, gap_post = _coverage(post, 30, expected_hz)
    if min(cov_pre, cov_post) < 0.8 or max(gap_pre or 0, gap_post or 0) > 3:
        unc.append(f'Data coverage around the event is incomplete (before {cov_pre:.0%}, after {cov_post:.0%}).')

    ml = ev['ml'] or {}
    if ml.get('activity'):
        obs.append(f"Experimental activity model (3 s window, {ml.get('n_boards', '?')} boards): "
                   f"top '{ml['activity']}' score {ml.get('model_score')}" +
                   (f", prediction set {ml['prediction_set']}" if ml.get('prediction_set') else '') + '.')
        if ml.get('abstain'):
            unc.append('Activity model abstained: more than one activity is plausible at 90% confidence.')
    else:
        unc.append(f"No activity classification ({ml.get('status', 'unavailable')}); movement score only.")

    if health['status'] != 'ok':
        unc.append(f"Sensor is {health['status']}: data is unavailable. Missing data is not inactivity.")
    c = chk.get('checkin')
    if c:
        delivered = [m['status'] for m in chk.get('messages', []) if m['direction'] == 'out' and m['role'] == 'patient']
        obs.append(f"Check-in {c['id']} is '{c['state']}' (patient message status: {', '.join(delivered) or 'none'}).")
        ev_ids.append(c['id'])
        if c['state'] == 'acknowledged':
            unc.append('An OK reply is an acknowledgment, not proof of safety.')
    unc.append('Wi-Fi movement sensing cannot confirm a fall, identity or injury.')

    recommendation = ('inspect_sensor' if health['status'] != 'ok' else
                      'caregiver_review' if (c and c['state'] in ('help_requested', 'ambiguous', 'timeout', 'delivery_failed'))
                      else 'routine_checkin' if c else 'monitor')
    report = dict(event_id=event_id, data_source=ev['source'], sensor_status=health['status'],
                  observations=obs, evidence_ids=list(dict.fromkeys(ev_ids)), uncertainty=unc,
                  recommendation=recommendation, policy=ev.get('policy'),
                  summary=summary(ev, obs, c, health))
    iid = new_id('inv')
    svc.db.x('INSERT INTO investigations(id,event_id,status,result,trace,created_ts) VALUES(?,?,?,?,?,?)',
             (iid, event_id, 'ok', json.dumps(report), json.dumps(steps), now()))
    return dict(id=iid, status='ok', result=report, trace=steps)

def summary(ev, obs, c, health):
    if health['status'] != 'ok':
        return f"Sensor {health['status']}; AuraCare can't tell what is happening right now. Please check the sensor."
    what = ev['event_type'].replace('_', ' ')
    s = f"{what.capitalize()} detected at {ev['start_utc'][11:19]} UTC ({ev['source']}). "
    if c: s += f"Check-in is {c['state'].replace('_', ' ')}. "
    return s + 'This is a movement pattern, not a confirmed fall.'

def answer(svc, event_id, question):
    """Caregiver text -> grounded answer from the latest investigation (keyword routing, no LLM)."""
    inv = investigate(svc, event_id)['result']
    q = question.lower()
    if any(w in q for w in ('sensor', 'working', 'offline', 'battery')):
        body = f"Sensor status: {inv['sensor_status']}."
    elif any(w in q for w in ('status', 'reply', 'replied', 'answer', 'ok?')):
        body = next((o for o in inv['observations'] if o.startswith('Check-in')), 'No check-in for this event.')
    elif any(w in q for w in ('why', 'what happened', 'explain', 'evidence', 'reason')):
        body = ' '.join(inv['observations'][1:3]) or inv['summary']
    else:
        body = inv['summary']
    return f"{body} [{', '.join(inv['evidence_ids'][:3])}]"
