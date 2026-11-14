"""Leave-one-participant-out RF on WiFall clips. Writes evaluation/rf_metrics.json + confusion matrix."""
import json, sys
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, accuracy_score, confusion_matrix, classification_report
import joblib
from ml.data import CLASSES, to_rate
from ml.features import clip_features, NAMES

def make_rf():
    return RandomForestClassifier(n_estimators=300, max_depth=10, min_samples_leaf=2,
                                  class_weight='balanced', random_state=42, n_jobs=-1)

def loso(F, y, g, make=make_rf):
    folds, yt, yp = [], [], []
    for pid in sorted(set(g), key=lambda s: int(s[2:])):
        te = g == pid
        m = make().fit(F[~te], y[~te]); p = m.predict(F[te])
        folds.append(dict(test=pid, n=int(te.sum()), acc=accuracy_score(y[te], p),
                          macro_f1=f1_score(y[te], p, average='macro', labels=range(5), zero_division=0)))
        yt += list(y[te]); yp += list(p)
    return folds, np.array(yt), np.array(yp)

def summarize(folds):
    small = [f['macro_f1'] for f in folds if f['test'] != 'ID2']
    return dict(macro_f1_mean_9small=float(np.mean(small)), macro_f1_std_9small=float(np.std(small)),
                macro_f1_ID2=float(next(f['macro_f1'] for f in folds if f['test'] == 'ID2')))

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument('npz', nargs='?', default='data/clips.npz')
    ap.add_argument('--rate', type=float, default=50, help='train at the live boards rate (Hz); 0 = native 83 Hz')
    a = ap.parse_args()
    d = np.load(a.npz, allow_pickle=True)
    X, dt = to_rate(d['X'], a.rate or None)
    y, g = d['y'], d['groups']
    print('clips', X.shape, 'dt', dt)
    F = np.stack([clip_features(x, dt) for x in X])
    folds, yt, yp = loso(F, y, g)
    s = summarize(folds)
    # majority baseline (always 'fall')
    maj = int(np.bincount(y).argmax())
    base = dict(acc=float((y == maj).mean()),
                macro_f1=float(f1_score(y, np.full_like(y, maj), average='macro', labels=range(5), zero_division=0)))
    # leakage check: only static per-subcarrier mean level (should be ~chance if no session leakage)
    S = X.mean(1)
    sf, syt, syp = loso(S, y, g)
    out = dict(model='rf_dynamics', protocol='leave-one-participant-out', classes=CLASSES,
               n_clips=int(len(y)), **s, pooled_acc=float(accuracy_score(yt, yp)),
               pooled_macro_f1=float(f1_score(yt, yp, average='macro')),
               folds=folds, majority_baseline=base,
               leakage_check_static_level=summarize(sf),
               confusion_pooled=confusion_matrix(yt, yp, labels=range(5)).tolist(),
               report=classification_report(yt, yp, target_names=CLASSES, output_dict=True, zero_division=0))
    json.dump(out, open('evaluation/rf_metrics.json', 'w'), indent=2, default=float)
    print(json.dumps({k: v for k, v in out.items() if k not in ('folds', 'report')}, indent=1, default=float))
    print(classification_report(yt, yp, target_names=CLASSES, zero_division=0))
    for f in folds: print(f)
    final = make_rf().fit(F, y)
    imp = sorted(zip(final.feature_importances_, NAMES), reverse=True)[:8]
    print('top features', [(n, round(v, 3)) for v, n in imp])
    out['rate_hz'] = 1 / dt
    json.dump(out, open('evaluation/rf_metrics.json', 'w'), indent=2, default=float)
    joblib.dump(dict(model=final, classes=CLASSES, features=NAMES, dt=dt, T=X.shape[1]), 'models/activity_rf.joblib')
