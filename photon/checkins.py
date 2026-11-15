"""Check-in state machine. pending -> acknowledged | help_requested | away | ambiguous | timeout | delivery_failed."""
import json, sqlite3
from backend.db import new_id, now, loads

OPEN = ('pending', 'ambiguous')

# ---- patient messages ----
PROMPT = ("\U0001F499 WiCare | Wellbeing Check\n\n"
          "Hi! We noticed some unusual movement in your room and wanted to check on you.\n\n"
          "Are you okay?\n\n"
          "Reply 1: YES\nReply 2: NO")
CLARIFY = ("\U0001F499 WiCare\n\n"
           "Sorry, we didn't quite catch that.\n\n"
           "Reply 1: YES, I'm okay\nReply 2: NO, I need help\n\n"
           "We've also let your nurse know so they can check in.")
THANKS = {
    'acknowledged':   "\U0001F499 WiCare\n\nThank you! We're so glad you're okay. Your nurse has been updated. Take care!",
    'help_requested': "\U0001F499 WiCare\n\nWe've let your nurse know right away so they can check on you.",
    'away':           "\U0001F499 WiCare\n\nThanks for letting us know! Your nurse has been updated.",
}

# ---- nurse messages (role 'caregiver' in code). The nurse hears about every check-in, whatever the reply ----
NURSE = {
    'notice':  ("\U0001FA7A WiCare | Care Team Notice\n\n"
                "We noticed unusual movement in your patient's room and just sent them a wellbeing check.\n\n"
                "No action needed yet. We'll update you as soon as they reply."),
    'ok':      ("\U0001F49A WiCare | Update\n\n"
                "Good news: your patient replied YES, they're okay.\n\n"
                "No action needed."),
    'help':    ("\U0001F6A8 WiCare | Please Check In\n\n"
                "Your patient replied NO to the wellbeing check and may need help.\n\n"
                "Please go check on them now."),
    'help_text': ("\U0001F6A8 WiCare | Please Check In\n\n"
                  "Your patient replied: \"{text}\"\n\n"
                  "They may need help. Please go check on them now."),
    'timeout': ("\u23F0 WiCare | No Reply\n\n"
                "Your patient hasn't answered the wellbeing check yet.\n\n"
                "Please check on them."),
    'unclear': ("\U0001F4AC WiCare | Unclear Reply\n\n"
                "Your patient replied: \"{text}\"\n\n"
                "We've asked them again. You may want to check in."),
    'away':    ("\U0001FA7A WiCare | Update\n\n"
                "Your patient says they're away from the room right now."),
    'undelivered': ("\u26A0\uFE0F WiCare | Couldn't Reach Patient\n\n"
                    "The wellbeing check couldn't be delivered to your patient.\n\n"
                    "Please check on them directly."),
}
ESCALATE = {'help', 'help_text', 'timeout', 'unclear', 'undelivered'}     # these flag the event for nurse review

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
        notify_caregiver(db, photon, cid, event['id'], 'undelivered')
    else:
        notify_caregiver(db, photon, cid, event['id'], 'notice')       # nurse hears at the same time, reply or not
    return get(db, cid), True

def open_for_patient(db):
    return db.one(f"SELECT * FROM checkins WHERE state IN ({','.join('?'*len(OPEN))}) ORDER BY created_ts DESC LIMIT 1", OPEN)

def notify_caregiver(db, photon, cid, event_id, kind, text=''):
    """Message the nurse (role 'caregiver'). kind is a key of NURSE; the event ID is appended for grounded follow-ups."""
    if kind in ESCALATE:
        db.x("UPDATE events SET review_status='caregiver_review' WHERE id=?", (event_id,))
    body = NURSE[kind].format(text=text[:120]) + f'\n\nEvent {event_id}'
    return photon.send('caregiver', body, f'checkin:{cid}:nurse:{kind}', cid, event_id)

def apply_reply(db, photon, checkin, intent, text, source='exact'):
    cid, eid = checkin['id'], checkin['event_id']
    exact = source == 'exact'
    if intent == 'acknowledged':
        _log(db, cid, 'acknowledged', 'participant replied YES (acknowledgment, not proof of safety)')
        db.x("UPDATE events SET review_status='acknowledged' WHERE id=?", (eid,))
        notify_caregiver(db, photon, cid, eid, 'ok')
    elif intent == 'help_requested':
        _log(db, cid, 'help_requested', f'participant asked for help: {text[:200]}')
        notify_caregiver(db, photon, cid, eid, 'help' if exact else 'help_text', text)
    elif intent == 'away':
        _log(db, cid, 'away', 'participant reports being away')
        db.x("UPDATE events SET review_status='away' WHERE id=?", (eid,))
        notify_caregiver(db, photon, cid, eid, 'away')
    else:
        first = checkin['state'] != 'ambiguous'
        _log(db, cid, 'ambiguous', f'unclear reply needs review: {text[:200]}')
        if first:
            photon.send('patient', CLARIFY, f'checkin:{cid}:clarify', cid, eid)
            notify_caregiver(db, photon, cid, eid, 'unclear', text)
        return get(db, cid)
    photon.send('patient', THANKS[intent], f'checkin:{cid}:thanks', cid, eid)
    return get(db, cid)

def sweep_timeouts(db, photon):
    due = db.q(f"SELECT * FROM checkins WHERE state IN ({','.join('?'*len(OPEN))}) AND deadline < ?", (*OPEN, now()))
    for c in due:
        _log(db, c['id'], 'timeout', 'no clear reply before deadline')
        notify_caregiver(db, photon, c['id'], c['event_id'], 'timeout')
    return len(due)
