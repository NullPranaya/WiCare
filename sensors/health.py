from backend.db import now

def sensor_health(db, sensor_id, stale_after_s):
    s = db.one('SELECT * FROM sensors WHERE id=?', (sensor_id,))
    if not s:
        return dict(sensor_id=sensor_id, ready=False, fresh=False, mode=None, status='never_seen', last_seen_age_s=None)
    age = now() - s['last_seen'] if s['last_seen'] else None
    fresh = age is not None and age <= stale_after_s
    status = 'capture_mode' if s['mode'] == 'csi_capture' else ('ok' if fresh and s['ready'] else
             ('stale' if s['ready'] else 'not_ready'))
    return dict(sensor_id=sensor_id, ready=bool(s['ready']), fresh=fresh, mode=s['mode'], status=status,
                last_seen_age_s=None if age is None else round(age, 2))
