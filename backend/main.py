"""AuraCare API. Run: uvicorn backend.main:app --host 127.0.0.1 --port 8000"""
import json, sqlite3, threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from backend.service import Service
from backend.db import now
from backend import care_state
from fastapi.middleware.cors import CORSMiddleware
from photon import checkins, webhook
from investigation import investigator
from photon import reply_parser

svc = Service()

# every new event: policy already ran synchronously; the investigator writes an evidence report after it
svc.on_event.append(lambda ev: investigator.investigate(svc, ev['id']))

def _timeout_worker(stop):
    while not stop.is_set():
        try: checkins.sweep_timeouts(svc.db, svc.photon)
        except Exception as e: print('sweep error', e)
        stop.wait(2)

@asynccontextmanager
async def lifespan(app):
    stop = threading.Event()
    threading.Thread(target=_timeout_worker, args=(stop,), daemon=True).start()
    yield
    stop.set()

app = FastAPI(title='AuraCare', lifespan=lifespan)
# the room UI (frontend/, served on :3000) reads /care/state; local origins only, read-only
app.add_middleware(CORSMiddleware, allow_origin_regex=r'http://(localhost|127\.0\.0\.1)(:\d+)?', allow_methods=['GET'])

@app.get('/care/state')
def care(): return care_state.snapshot(svc)

def _404(x, what):
    if not x: raise HTTPException(404, f'{what} not found')
    return x

@app.get('/sensors/{sensor_id}/health')
def health(sensor_id: str): return svc.health(sensor_id)

@app.get('/sensors/{sensor_id}/baseline')
def baseline(sensor_id: str): return svc.baseline(sensor_id)

@app.get('/activity/history')
def history(sensor_id: str, seconds: int = 120): return svc.recent_activity(sensor_id, seconds)

@app.get('/events')
def events(limit: int = 20):
    return [svc.event(r['id']) for r in svc.db.q('SELECT id FROM events ORDER BY created_ts DESC LIMIT ?', (min(limit, 100),))]

@app.get('/events/{event_id}')
def event(event_id: str): return _404(svc.event(event_id), 'event')

@app.get('/events/{event_id}/checkin')
def event_checkin(event_id: str):
    _404(svc.event(event_id), 'event'); return svc.checkin_for_event(event_id)

@app.get('/events/{event_id}/investigations')
def investigations(event_id: str):
    rows = svc.db.q('SELECT * FROM investigations WHERE event_id=? ORDER BY created_ts DESC', (event_id,))
    for r in rows: r['result'], r['trace'] = json.loads(r['result']), json.loads(r['trace'])
    return rows

class EventRef(BaseModel):
    event_id: str

@app.post('/checkins')
def create_checkin(body: EventRef):
    """Policy-validated: returns the existing check-in or creates one only if policy allows."""
    ev = _404(svc.event(body.event_id), 'event')
    if ev.get('checkin_id'):
        return dict(created=False, checkin_id=ev['checkin_id'])
    d = svc.apply_policy(body.event_id)
    return dict(created=d.action == 'routine_checkin', decision=d.__dict__, checkin_id=svc.event(body.event_id)['checkin_id'])

@app.post('/investigate')
def investigate(body: EventRef):
    _404(svc.event(body.event_id), 'event'); return investigator.investigate(svc, body.event_id)

class Observation(BaseModel):
    sensor_id: str
    score: float
    motion: bool | None = None
    source: str = 'live_score'

@app.post('/ingest/observation')
def ingest(o: Observation):
    """Used by the sensor runner (live or replay). Local only."""
    ev = svc.observe(o.sensor_id, o.score, o.motion, o.source)
    return dict(event=ev)

@app.post('/ingest/ml')
def ingest_ml(pred: dict):
    """Latest fused 3 s window prediction from the CSI worker (ml.inference.ActivityModel.fuse)."""
    svc.latest_ml = (now(), pred)
    return dict(ok=True)

@app.post('/webhooks/photon')
async def photon_webhook(request: Request):
    raw = await request.body()
    h = {k.lower(): v for k, v in request.headers.items()}
    if not webhook.verify(h, raw, svc.s.photon_webhook_secret):
        raise HTTPException(400, 'bad signature')                         # Photon docs: 400, do not process
    with open('photon_events.jsonl', 'ab') as f: f.write(raw + b'\n')     # verified raw events, git-ignored
    msg = webhook.normalize(json.loads(raw))
    if not msg or not msg['message_id']:
        return dict(ignored='non-text, own message, or not a message event')
    role = svc.s.role_of(msg['sender_id'])
    if not role:
        return dict(ignored='unauthorized sender')                       # never reply to unknown numbers
    try:
        svc.db.x('INSERT INTO messages(id,direction,role,text,status,platform_id,created_ts) VALUES(?,?,?,?,?,?,?)',
                 (f"in_{msg['message_id']}", 'in', role, msg['text'], 'received', msg['message_id'], now()))
    except sqlite3.IntegrityError:
        return dict(duplicate=True)
    if role == 'patient':
        c = checkins.open_for_patient(svc.db)
        if not c:
            return dict(ignored='no open check-in')
        svc.db.x('UPDATE messages SET checkin_id=?, event_id=? WHERE platform_id=?', (c['id'], c['event_id'], msg['message_id']))
        r = reply_parser.interpret(msg['text'])
        return dict(interpretation=r, checkin=checkins.apply_reply(svc.db, svc.photon, checkins.get(svc.db, c['id']), r['intent'], msg['text'], r['source']))
    # caregiver question -> grounded answer about the latest event with a check-in
    latest = svc.db.one('SELECT id FROM events WHERE checkin_id IS NOT NULL ORDER BY created_ts DESC LIMIT 1')
    if not latest:
        svc.photon.send('caregiver', 'AuraCare (test): no recent events to report.', f"chat:{msg['message_id']}")
        return dict(answered=False)
    text = investigator.answer(svc, latest['id'], msg['text'])
    svc.photon.send('caregiver', text, f"chat:{msg['message_id']}", event_id=latest['id'])
    return dict(answered=True, event_id=latest['id'], text=text)
