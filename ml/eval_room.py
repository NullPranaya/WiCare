"""Held-out check of room-trained coarse models on the room recording (what the hub's people/motion model does,
but measured on recordings it never saw). Each state has two 60 s recordings (_1, _2): train on one, test on the
other, both ways. Windows: 3 s, 1 s hop, features per node (clip_features) concatenated over nodes 2 and 3.
The sit-stand / jump recordings are never trained on; they test whether the motion gate fires after prompts.
  python -m ml.eval_room <dataset dir> [--out evaluation/room_eval.json]"""
import argparse, json
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, confusion_matrix
from ml.auracare_dataset import session, load_chunk, NODE_HZ
from ml.features import clip_features
from sensors.csi_adapter import window

KW = dict(dt=0.02, length_s=2.96, max_gap_s=0.3, min_coverage=0.6, expected_hz=NODE_HZ)

def windows(d, ends):
    X, E = [], []
    for end in ends:
        f = []
        for n in sorted(d):
            x, _ = window(d[n]['t'], d[n]['seq'], d[n]['amp'], end_t=end, **KW)
            if x is None: break
            f.append(clip_features(x, KW['dt']))
        if len(f) == len(d): X.append(np.concatenate(f)); E.append(end)
    return np.array(X), np.array(E)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('--out', default='evaluation/room_eval.json')
    a = ap.parse_args()
    ses = session(a.root)
    rows = []                                   # (chunk, state, people, rep, X, ends)
    for c in ses['chunks']:
        d = load_chunk(a.root, c['id'])
        t0 = max(x['t'][0] for x in d.values()) + 3.0; t1 = min(x['t'][-1] for x in d.values())
        X, E = windows(d, np.arange(t0, t1, 1.0))
        rows.append(dict(c=c, X=X, E=E))
    base = [r for r in rows if r['c']['state'] in ('empty', 'p1_still', 'p1_moving', 'p2_still', 'p2_moving')]
    tasks = {
        'motion (moving vs empty/still)': lambda c: int('moving' in c['state']),
        'occupied (empty vs people)': lambda c: int(c['people'] > 0),
        'people count 0/1/2': lambda c: c['people'],
        'state (5 classes)': lambda c: c['state'],
    }
    res = {}
    for name, lab in tasks.items():
        yt, yp = [], []
        for test_rep in ('_1', '_2'):
            tr = [r for r in base if not r['c']['id'].endswith(test_rep)]
            te = [r for r in base if r['c']['id'].endswith(test_rep)]
            Xtr = np.concatenate([r['X'] for r in tr]); ytr = np.concatenate([[lab(r['c'])] * len(r['X']) for r in tr])
            clf = RandomForestClassifier(400, min_samples_leaf=3, class_weight='balanced', random_state=0, n_jobs=-1).fit(Xtr, ytr)
            for r in te:
                yt += [lab(r['c'])] * len(r['X']); yp += list(clf.predict(r['X']))
        labels = sorted(set(yt), key=str)
        res[name] = dict(balanced_acc=round(balanced_accuracy_score(yt, yp), 3), labels=[str(l) for l in labels],
                         confusion=confusion_matrix(yt, yp, labels=labels).tolist(), n=len(yt))
    # motion gate on the prompted recordings (never trained on): does it fire after each prompt, and how often between?
    lab = tasks['motion (moving vs empty/still)']
    Xall = np.concatenate([r['X'] for r in base]); yall = np.concatenate([[lab(r['c'])] * len(r['X']) for r in base])
    gate = RandomForestClassifier(400, min_samples_leaf=3, class_weight='balanced', random_state=0, n_jobs=-1).fit(Xall, yall)
    prompted = {}
    for r in rows:
        acts = r['c'].get('actions') or []
        if not acts: continue
        p = gate.predict_proba(r['X'])[:, 1]
        hit = []
        for act in acts:                       # any window ending 1.5..4.5 s after the prompt flags motion?
            m = (r['E'] >= act['t_unix'] + 1.5) & (r['E'] <= act['t_unix'] + 4.5)
            hit.append(bool(m.any() and (p[m] >= 0.5).any()))
        prompted[r['c']['id']] = dict(prompts=len(acts), motion_detected_after_prompt=sum(hit),
                                      windows_flagged_moving=round(float((p >= 0.5).mean()), 2))
    res['motion gate on prompted recordings (not trained on)'] = prompted
    json.dump(res, open(a.out, 'w'), indent=1)
    print(json.dumps(res, indent=1))

if __name__ == '__main__':
    main()
