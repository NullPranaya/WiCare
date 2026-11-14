"""Compare RF, CNN and RF+CNN ensemble on identical leave-one-participant-out folds, then add split-conformal
prediction sets (for each held-out participant, calibrate on the other participants' out-of-fold scores).
Writes evaluation/metrics.json. Run after ml.train_cnn."""
import json
import numpy as np
from sklearn.metrics import f1_score, balanced_accuracy_score, confusion_matrix
from ml.data import CLASSES, to_rate
from ml.features import clip_features
from ml.train_rf import make_rf

def rf_oof(F, y, g):
    P = np.zeros((len(y), 5))
    for pid in set(g):
        te = g == pid
        m = make_rf().fit(F[~te], y[~te])
        P[np.ix_(te, m.classes_)] = m.predict_proba(F[te])
    return P

def scores(P, y, g):
    pred = P.argmax(1)
    small = [f1_score(y[g == p], pred[g == p], average='macro', labels=range(5), zero_division=0) for p in set(g) if p != 'ID2']
    fall = P[:, 0] >= P[:, 1:].max(1)
    return dict(macro_f1_pooled=round(float(f1_score(y, pred, average='macro')), 3),
                macro_f1_9small_mean=round(float(np.mean(small)), 3), macro_f1_9small_std=round(float(np.std(small)), 3),
                fall_vs_rest_bal_acc=round(float(balanced_accuracy_score(y == 0, fall)), 3),
                fall_recall=round(float(fall[y == 0].mean()), 3), fall_precision=round(float((y[fall] == 0).mean()), 3),
                confusion=confusion_matrix(y, pred, labels=range(5)).tolist())

def conformal(P, y, g, alpha=0.1):
    """Per held-out participant: q from other participants' nonconformity 1 - p(true). Set = {c: 1 - p_c <= q}."""
    cover, size, single, fall_in = [], [], [], []
    for pid in set(g):
        te, cal = g == pid, g != pid
        s = 1 - P[cal, y[cal]]
        n = len(s); q = np.quantile(s, min(1, np.ceil((n + 1) * (1 - alpha)) / n), method='higher')
        sets = (1 - P[te]) <= q
        cover += list(sets[np.arange(te.sum()), y[te]]); size += list(sets.sum(1)); single += list(sets.sum(1) == 1)
        fall_in += list(sets[y[te] == 0, 0])
    return dict(alpha=alpha, target_coverage=1 - alpha, coverage=round(float(np.mean(cover)), 3),
                avg_set_size=round(float(np.mean(size)), 2), confident_single_label_frac=round(float(np.mean(single)), 3),
                true_falls_with_fall_in_set=round(float(np.mean(fall_in)), 3))

def q_hat(P, y, alpha=0.1):
    s = 1 - P[np.arange(len(y)), y]; n = len(s)
    return float(np.quantile(s, min(1, np.ceil((n + 1) * (1 - alpha)) / n), method='higher'))

if __name__ == '__main__':
    d = np.load('data/clips.npz', allow_pickle=True)
    X, dt = to_rate(d['X'], 50); y, g = d['y'], d['groups']
    F = np.stack([clip_features(x, dt) for x in X])
    Prf = rf_oof(F, y, g)
    c = np.load('evaluation/oof_cnn.npz', allow_pickle=True); Pcnn = c['P']
    assert (c['y'] == y).all()
    Pens = (Prf + Pcnn) / 2
    out = dict(protocol='leave-one-participant-out, 926 clips, 3 s at 50 Hz', classes=CLASSES,
               majority_baseline_macro_f1=0.133,
               rf=scores(Prf, y, g), cnn=scores(Pcnn, y, g), ensemble=scores(Pens, y, g),
               conformal_ensemble=conformal(Pens, y, g, 0.1),
               deployed_q_hat_alpha_0_1=q_hat(Pens, y, 0.1))
    json.dump(out, open('evaluation/metrics.json', 'w'), indent=2)
    for k in ('rf', 'cnn', 'ensemble'):
        print(k, {a: b for a, b in out[k].items() if a != 'confusion'})
    print('conformal', out['conformal_ensemble'], 'q_hat', round(out['deployed_q_hat_alpha_0_1'], 3))
