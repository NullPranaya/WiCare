"""Application service: the only place that touches events, observations and check-ins.
API routes, the sensor runner and the investigator all go through here."""
import json, os, sqlite3
from backend.db import DB, new_id, now, iso, loads
from backend.config import settings as default_settings
from monitoring import policy
from monitoring.anomaly import Detector, Thresholds
from monitoring.burst_rule import BurstDetector, BurstRule
from photon.client import PhotonClient
from photon import checkins
from sensors.health import sensor_health
from backend.ward import Ward

class Service:
    def __init__(self, settings=None, db=None):
        self.s = settings or default_settings
        self.db = db or DB(self.s.db_path)
        self.photon = PhotonClient(self.db, self.s)
        self.ward = Ward(self.photon, self.db)      # 5-room stress demo (backend/ward.py)
        if getattr(self.s, 'detector', 'score') == 'burst':
            rule = BurstRule(threshold=self.s.burst_threshold, burst_max_s=self.s.burst_max_s, still_s=self.s.burst_still_s)
            self.calibration = dict(thresholds=dict(high=rule.threshold, low=rule.threshold, still_s=rule.still_s,
                                                    burst_max_s=rule.burst_max_s),
                                    note='ESPectre board motion score, burst-then-stillness rule tuned in the room (WiCare)')
            self.detector = BurstDetector(rule)
        else:
            self.calibration = self._load_calibration()
            self.detector = Detector(Thresholds(**self.calibration['thresholds']),
                                     expected_active=self.s.expected_active) if self.calibration else None
        self.on_event = []        # callbacks, e.g. the evidence investigator
        self.latest_ml = None     # (ts, fused prediction) from the CSI worker

    # ---- calibration / baseline
    def _load_calibration(self, path='calibration.json'):
        if os.path.exists(path):
            return json.load(open(path))
        return None

    def set_calibration(self, thresholds: Thresholds, note, path='calibration.json'):
        self.calibration = dict(thresholds=thresholds.__dict__, note=note, created_utc=iso(now()))
        json.dump(self.calibration, open(path, 'w'), indent=2)
        self.detector = Detector(thresholds, expected_active=self.s.expected_active)

    def baseline(self, sensor_id):
        if not self.calibration:
            return dict(sensor_id=sensor_id, available=False, reason='no local calibration recorded')
        return dict(sensor_id=sensor_id, available=True, **self.calibration,
                    limitations='short consented calibration session, not a long-term personal routine')

    # ---- sensor
    def heartbeat(self, sensor_id, mode='motion', ready=True, payload=None):
        self.db.x('INSERT INTO sensors(id,room_id,mode,ready,last_seen,last_payload) VALUES(?,?,?,?,?,?) '
                  'ON CONFLICT(id) DO UPDATE SET mode=excluded.mode, ready=excluded.ready, '
                  'last_seen=excluded.last_seen, last_payload=excluded.last_payload',
                  (sensor_id, self.s.room_id, mode, int(ready), now(), json.dumps(payload or {})))

    def mark_offline(self, sensor_id, reason):
        self.db.x('UPDATE sensors SET ready=0 WHERE id=?', (sensor_id,))
        if self.detector: self.detector.reset()

    def health(self, sensor_id):
        return sensor_health(self.db, sensor_id, self.s.stale_after_s)

    # ---- observations -> events
    def observe(self, sensor_id, score, motion=None, source='live_score', ts=None, payload=None, level=None):
        """Ingest one movement-score observation. Returns created event or None."""
        if score is None:
            return None
        h_before = self.health(sensor_id)
        self.heartbeat(sensor_id, payload=payload)
        if self.detector and h_before['last_seen_age_s'] is not None and h_before['last_seen_age_s'] > self.s.stale_after_s:
            self.detector.reset()                       # gap: never treat missing data as inactivity
        oid = new_id('obs')
        ts = ts or now()
        self.db.x('INSERT INTO observations(id,sensor_id,ts,score,motion,source) VALUES(?,?,?,?,?,?)',
                  (oid, sensor_id, ts, float(score), None if motion is None else int(motion), source))
        if not self.detector:
            return None
        ev = self.detector.feed(dict(id=oid, ts=ts, score=float(score), level=level))
        return self.create_event(sensor_id, source, **ev) if ev else None

    def create_event(self, sensor_id, source, event_type, start_ts, evidence_ids, observations, ml=None):
        eid = new_id('evt')
        dedup = f'{sensor_id}:{event_type}:{int(start_ts)}'
        if ml is None:
            if not self.s.live_har_validated:
                ml = dict(activity=None, model_score=None, status='live_har_not_validated', model_version=None)
            elif self.latest_ml and now() - self.latest_ml[0] < 5:
                ml = {**self.latest_ml[1], 'source': 'live_csi', 'model_version': 'activity_rf_50hz'}
            else:
                ml = dict(activity=None, model_score=None, status='raw_csi_unavailable', model_version=None)
        try:
            self.db.x('INSERT INTO events(id,sensor_id,room_id,source,event_type,start_ts,created_ts,evidence_ids,'
                      'observations,ml,review_status,dedup_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (eid, sensor_id, self.s.room_id, source, event_type, start_ts, now(),
                       json.dumps(evidence_ids), json.dumps(observations), json.dumps(ml), 'unconfirmed', dedup))
        except sqlite3.IntegrityError:                   # duplicate event
            return self.event(self.db.one('SELECT id FROM events WHERE dedup_key=?', (dedup,))['id'])
        self.apply_policy(eid)
        ev = self.event(eid)
        for cb in self.on_event: cb(ev)
        return ev

    def apply_policy(self, event_id):
        ev = self.event(event_id)
        d = policy.decide(ev, self.health(ev['sensor_id']), opt_in=self.s.participant_opt_in,
                          demo_mode=self.s.demo_mode, expected_active=self.s.expected_active,
                          open_checkin=checkins.open_for_patient(self.db))
        self.db.x('UPDATE events SET policy=? WHERE id=?', (json.dumps(d.__dict__), event_id))
        if d.action == 'routine_checkin':
            checkins.open_checkin(self.db, self.photon, ev, self.s.checkin_timeout_s)
        elif d.action == 'sensor_unavailable':
            self.db.x("UPDATE events SET review_status='sensor_unavailable' WHERE id=?", (event_id,))
        return d

    # ---- reads (used by API + investigator)
    def event(self, event_id):
        e = loads(self.db.one('SELECT * FROM events WHERE id=?', (event_id,)), 'evidence_ids', 'observations', 'ml', 'policy', 'flags')
        if e:
            e['start_utc'] = iso(e['start_ts'])
        return e

    def recent_activity(self, sensor_id, seconds=120, around_ts=None):
        seconds = max(1, min(int(seconds), 600))
        end = (around_ts + seconds / 2) if around_ts else now()
        rows = self.db.q('SELECT id,ts,score,motion,source FROM observations WHERE sensor_id=? AND ts BETWEEN ? AND ? ORDER BY ts',
                         (sensor_id, end - seconds, end))
        for r in rows: r['utc'] = iso(r.pop('ts'))
        return dict(sensor_id=sensor_id, window_s=seconds, n=len(rows), observations=rows[-300:],
                    truncated=len(rows) > 300)

    def checkin_for_event(self, event_id):
        c = self.db.one('SELECT * FROM checkins WHERE event_id=?', (event_id,))
        if not c:
            return dict(event_id=event_id, checkin=None)
        c = checkins.get(self.db, c['id'])
        msgs = self.db.q('SELECT id,direction,role,status,created_ts,text FROM messages WHERE checkin_id=? ORDER BY created_ts', (c['id'],))
        for m in msgs: m['utc'] = iso(m.pop('created_ts'))
        c['deadline_utc'] = iso(c.pop('deadline'))
        return dict(event_id=event_id, checkin=c, messages=msgs)

    def add_flag(self, event_id, flag):
        ev = self.event(event_id)
        flags = sorted(set(ev['flags'] or []) | {flag})
        self.db.x('UPDATE events SET flags=? WHERE id=?', (json.dumps(flags), event_id))
