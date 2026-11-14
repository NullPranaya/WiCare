"""Room-trained movement score: P(moving) from the last 3 s of CSI on nodes 2 and 3.
Trained on the 10 unprompted chunks of the room recording (empty / still = 0, moving = 1). Held-out
(train _1 / test _2 and swap, ml/eval_room.py): balanced acc 0.84, fired after 28/28 sit/stand/jump prompts.
This is a movement score for the temporal rules in monitoring/anomaly.py, not a fall classifier.
  python -m ml.room_motion train <dataset dir>      -> models/room_motion.joblib
  python -m ml.room_motion scores <dataset dir>     -> 1 Hz score streams per chunk (evaluation/room_scores.json)"""
import json, sys
import joblib, numpy as np
from sklearn.ensemble import RandomForestClassifier
from ml.features import clip_features
from sensors.csi_adapter import window

NODE_HZ = 29.0
KW = dict(dt=0.02, length_s=2.96, max_gap_s=0.3, min_coverage=0.6, expected_hz=NODE_HZ)
NODES = (2, 3)
PATH = 'models/room_motion.joblib'
BASE_STATES = ('empty', 'p1_still', 'p1_moving', 'p2_still', 'p2_moving')

def features(bufs, end):
    """bufs: {node_id: dict(t, seq, amp)}. Needs a valid window on every node in NODES; else None."""
    f = []
    for n in NODES:
        b = bufs.get(n)
        if b is None or len(b['t']) < 10: return None
        x, _ = window(b['t'], b['seq'], b['amp'], end_t=end, **KW)
        if x is None: return None
        f.append(clip_features(x, KW['dt']))
    return np.concatenate(f)

class RoomMotion:
    def __init__(self, path=PATH):
        self.clf = joblib.load(path)['model']

    def score(self, bufs, end):
        f = features(bufs, end)
        return None if f is None else float(self.clf.predict_proba(f[None])[0, 1])

def _chunk_windows(root, c):
    from ml.auracare_dataset import load_chunk
    d = load_chunk(root, c['id'])
    t0 = max(x['t'][0] for x in d.values()) + 3.0; t1 = min(x['t'][-1] for x in d.values())
    X, E = [], []
    for end in np.arange(t0, t1, 1.0):
        f = features(d, end)
        if f is not None: X.append(f); E.append(end)
    return np.array(X), np.array(E)

def train(root):
    from ml.auracare_dataset import session
    X, y = [], []
    for c in session(root)['chunks']:
        if c['state'] not in BASE_STATES: continue
        Xc, _ = _chunk_windows(root, c)
        X.append(Xc); y += [int('moving' in c['state'])] * len(Xc)
    clf = RandomForestClassifier(400, min_samples_leaf=3, class_weight='balanced', random_state=0, n_jobs=-1)
    clf.fit(np.concatenate(X), np.array(y))
    joblib.dump(dict(model=clf, kw=KW, nodes=NODES, trained_on=session(root)['session']), PATH)
    print('saved', PATH, 'windows', len(y), 'moving frac', round(float(np.mean(y)), 2))

def scores(root):
    from ml.auracare_dataset import session
    m, out = RoomMotion(), {}
    for c in session(root)['chunks']:
        X, E = _chunk_windows(root, c)
        p = m.clf.predict_proba(X)[:, 1]
        out[c['id']] = dict(state=c['state'], ts=E.tolist(), score=np.round(p, 3).tolist(),
                            actions=[dict(t=a['t_unix'], action=a['action']) for a in c.get('actions') or []])
    json.dump(out, open('evaluation/room_scores.json', 'w'))
    return out

if __name__ == '__main__':
    {'train': train, 'scores': scores}[sys.argv[1]](sys.argv[2])
