"""One snapshot of everything the room UI shows, built only from stored rows (nothing invented).
GET /care/state -> sensor health, last 60 s of movement scores, and the active event with its check-in
timeline and messages. Phone numbers are never included: messages carry roles only."""
import json
from backend.db import now, iso

SERIES_S = 60          # seconds of movement score for the chart
HOLD_OK_S = 60         # keep a closed OK / AWAY check-in on screen this long
HOLD_HELP_S = 120      # keep an escalated check-in on screen this long

# check-in state -> (ui status, pill label, strip title, step, per-step reply text)
STATES = {
    'pending':         ('watch', 'Check-in sent',  'Wellbeing check sent · nurse notified',  2, 'Reply'),
    'ambiguous':       ('watch', 'Unclear reply',  'Unclear reply · nurse reviewing',        4, 'Reply: unclear'),
    'acknowledged':    ('ok',    'Patient OK',     'Patient replied YES · nurse updated',    4, 'Reply: YES'),
    'away':            ('ok',    'Patient away',   'Patient is away · nurse updated',        4, 'Reply: AWAY'),
    'help_requested':  ('help',  'Needs help',     'Patient replied NO · nurse alerted',     4, 'Reply: NO'),
    'timeout':         ('help',  'No reply',       'No reply · nurse alerted',               4, 'No reply'),
    'delivery_failed': ('help',  'Not delivered',  'Check-in not delivered · nurse alerted', 4, 'Not delivered'),
}

def _active_checkin(db):
    c = db.one('SELECT * FROM checkins ORDER BY created_ts DESC LIMIT 1')
    if not c: return None
    if c['state'] in ('pending', 'ambiguous'): return c
    hold = HOLD_OK_S if c['state'] in ('acknowledged', 'away') else HOLD_HELP_S
    return c if now() - (c['updated_ts'] or c['created_ts']) <= hold else None

def snapshot(svc):
    t = now()
    h = svc.health(svc.s.sensor_id)
    obs = svc.db.q('SELECT ts, score FROM observations WHERE sensor_id=? AND ts >= ? ORDER BY ts',
                   (svc.s.sensor_id, t - SERIES_S))
    th = (svc.calibration or {}).get('thresholds', {})
    out = dict(ts_utc=iso(t),
               sensor=dict(status=h['status'], fresh=h['fresh'], last_seen_age_s=h['last_seen_age_s']),
               score=dict(latest=obs[-1]['score'] if obs and h['fresh'] else None,
                          series=[[round(o['ts'] - t, 2), round(o['score'], 3)] for o in obs],
                          high=th.get('high'), low=th.get('low'),
                          moving_at=th.get('high') if svc.s.detector == 'burst' else 0.5),
               event=None, checkin=None, messages=[])
    c = _active_checkin(svc.db)
    if c:
        ev = svc.db.one('SELECT id, event_type, source, created_ts, observations FROM events WHERE id=?', (c['event_id'],))
        status, label, title, step, reply = STATES.get(c['state'], ('watch', c['state'], c['state'], 2, 'Reply'))
        sent = svc.db.one("SELECT 1 FROM messages WHERE checkin_id=? AND role='patient' AND direction='out'", (c['id'],))
        if c['state'] == 'pending' and not sent: step, label = 1, 'Unusual movement'
        out.update(status=status, label=label, title=title, step=step, reply=reply,
                   since_utc=iso(ev['created_ts']) if ev else None)
        if ev:
            out['event'] = dict(id=ev['id'], type=ev['event_type'], source=ev['source'], created_utc=iso(ev['created_ts']),
                                observations=json.loads(ev['observations'] or '{}'))
        out['checkin'] = dict(id=c['id'], state=c['state'], deadline_utc=iso(c['deadline']) if c['deadline'] else None,
                              seconds_left=round(c['deadline'] - t, 1) if c['deadline'] and c['state'] == 'pending' else None)
        out['messages'] = [dict(ts_utc=iso(m['created_ts']), direction=m['direction'], role=m['role'], text=m['text'],
                                status=m['status'])
                           for m in svc.db.q('SELECT * FROM messages WHERE checkin_id=? OR (event_id=? AND checkin_id IS NULL) '
                                             'ORDER BY created_ts', (c['id'], c['event_id']))]
    elif not h['fresh']:
        out.update(status='off', label='Sensor offline', title=None, step=0)
    else:
        out.update(status='ok', label='Monitoring', title=None, step=0)
    return out
