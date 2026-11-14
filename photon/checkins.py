"""Check-in state machine. pending -> acknowledged | help_requested | away | ambiguous | timeout | delivery_failed."""
import json, sqlite3
from backend.db import new_id, now, loads

OPEN = ('pending', 'ambiguous')
PROMPT = ("AuraCare check-in (test): we noticed an unusual movement pattern. Are you OK?\n"
          "Reply 1 = OK, 2 = HELP, 3 = AWAY")
CLARIFY = "Sorry, I didn't catch that. Reply 1 = OK, 2 = HELP, 3 = AWAY. A caregiver has also been asked to review."

def _log(db, cid, state, note):
    c = loads(db.one('SELECT history FROM checkins WHERE id=?', (cid,)), 'history')
    h = (c or {}).get('history') or []
    h.append(dict(ts=now(), state=state, note=note))
    db.x('UPDATE checkins SET state=?, updated_ts=?, history=? WHERE id=?', (state, now(), json.dumps(h), cid))

def get(db, cid):
    return loads(db.one('SELECT * FROM checkins WHERE id=?', (cid,)), 'history')

def open_checkin(db, photon, event, timeout_s):
    existing = db.one('SELECT * FROM checkins WHERE event_id=?', (event['id'],))
    if existing:
        return get(db, existing['id']), False
    cid = new_id('chk')
    try:
        db.x('INSERT INTO checkins(id,event_id,state,deadline,created_ts,updated_ts) VALUES(?,?,?,?,?,?)',
             (cid, event['id'], 'pending', now() + timeout_s, now(), now()))
    except sqlite3.IntegrityError:                        # race on UNIQUE(event_id)
        return get(db, db.one('SELECT id FROM checkins WHERE event_id=?', (event['id'],))['id']), False
    db.x('UPDATE events SET checkin_id=? WHERE id=?', (cid, event['id']))
    _log(db, cid, 'pending', 'created by policy')
    m = photon.send('patient', PROMPT, f'checkin:{cid}:prompt', cid, event['id'])
    if m['status'] == 'delivery_failed':
        _log(db, cid, 'delivery_failed', m['detail'])
        notify_caregiver(db, photon, cid, event['id'], 'Check-in message could not be sent. Please check on the participant.')
    return get(db, cid), True

def open_for_patient(db):
    return db.one(f"SELECT * FROM checkins WHERE state IN ({','.join('?'*len(OPEN))}) ORDER BY created_ts DESC LIMIT 1", OPEN)

def notify_caregiver(db, photon, cid, event_id, text):
    db.x("UPDATE events SET review_status='caregiver_review' WHERE id=?", (event_id,))
    return photon.send('caregiver', f'AuraCare (test): {text} Event {event_id}.', f'checkin:{cid}:caregiver:{text[:40]}', cid, event_id)

def apply_reply(db, photon, checkin, intent, text):
    cid, eid = checkin['id'], checkin['event_id']
    if intent == 'acknowledged':
        _log(db, cid, 'acknowledged', 'participant replied OK (acknowledgment, not proof of safety)')
        db.x("UPDATE events SET review_status='acknowledged' WHERE id=?", (eid,))
    elif intent == 'help_requested':
        _log(db, cid, 'help_requested', f'participant asked for help: {text[:200]}')
        notify_caregiver(db, photon, cid, eid, 'The participant replied asking for HELP.')
    elif intent == 'away':
        _log(db, cid, 'away', 'participant reports being away')
        db.x("UPDATE events SET review_status='away' WHERE id=?", (eid,))
    else:
        first = checkin['state'] != 'ambiguous'
        _log(db, cid, 'ambiguous', f'unclear reply needs review: {text[:200]}')
        if first:
            photon.send('patient', CLARIFY, f'checkin:{cid}:clarify', cid, eid)
            notify_caregiver(db, photon, cid, eid, f'Unclear reply needs review: "{text[:120]}".')
    return get(db, cid)

def sweep_timeouts(db, photon):
    due = db.q(f"SELECT * FROM checkins WHERE state IN ({','.join('?'*len(OPEN))}) AND deadline < ?", (*OPEN, now()))
    for c in due:
        _log(db, c['id'], 'timeout', 'no clear reply before deadline')
        notify_caregiver(db, photon, c['id'], c['event_id'], 'No reply to the check-in before the deadline.')
    return len(due)
