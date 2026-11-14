import json, sqlite3, threading, time, uuid
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS sensors(id TEXT PRIMARY KEY, room_id TEXT, mode TEXT, ready INTEGER,
  last_seen REAL, last_payload TEXT);
CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, sensor_id TEXT, ts REAL, score REAL,
  motion INTEGER, source TEXT);
CREATE INDEX IF NOT EXISTS obs_sensor_ts ON observations(sensor_id, ts);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, sensor_id TEXT, room_id TEXT, source TEXT,
  event_type TEXT, start_ts REAL, created_ts REAL, evidence_ids TEXT, observations TEXT, ml TEXT,
  review_status TEXT, policy TEXT, flags TEXT DEFAULT '[]', checkin_id TEXT, dedup_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS checkins(id TEXT PRIMARY KEY, event_id TEXT UNIQUE, state TEXT,
  deadline REAL, created_ts REAL, updated_ts REAL, history TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, checkin_id TEXT, event_id TEXT, direction TEXT,
  role TEXT, text TEXT, status TEXT, idem_key TEXT UNIQUE, platform_id TEXT UNIQUE, created_ts REAL, detail TEXT);
CREATE TABLE IF NOT EXISTS investigations(id TEXT PRIMARY KEY, event_id TEXT, status TEXT, result TEXT,
  trace TEXT, created_ts REAL);
"""

def now(): return time.time()
def iso(ts): return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
def new_id(prefix): return f'{prefix}_{uuid.uuid4().hex[:10]}'

class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with self.lock:
            return self.conn.execute(sql, args).rowcount

def loads(row, *cols):
    if row:
        for c in cols:
            if isinstance(row.get(c), str): row[c] = json.loads(row[c])
    return row
