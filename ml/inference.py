"""Live/replay inference: RF + CNN ensemble on a 3 s, 50 Hz, 52-subcarrier window, with a split-conformal
prediction set. If more than one activity is in the 90% set the model abstains. Scores are model scores,
not probabilities of a fall."""
import json, joblib, numpy as np, torch
from ml.features import clip_features, robust_z
from ml.train_cnn import CSINet

class ActivityModel:
    def __init__(self, rf='models/activity_rf.joblib', cnn='models/activity_cnn.pt', conformal='models/conformal.json'):
        b = joblib.load(rf)
        self.rf, self.classes, self.dt, self.T = b['model'], list(b['classes']), b['dt'], b['T']
        c = torch.load(cnn, weights_only=False)
        self.cnn = CSINet(); self.cnn.load_state_dict(c['state']); self.cnn.eval()
        assert abs(c['dt'] - self.dt) < 1e-9 and c['T'] == self.T
        self.q = json.load(open(conformal))['q_hat']

    def _fit_len(self, x):
        if x.shape[1] != 52: raise ValueError('expected 52 subcarriers in WiFall order')
        return x[-self.T:] if len(x) >= self.T else np.pad(x, ((self.T - len(x), 0), (0, 0)), mode='edge')

    @torch.no_grad()
    def proba(self, x):
        x = self._fit_len(np.asarray(x, np.float32))
        p_rf = np.zeros(5); p_rf[self.rf.classes_] = self.rf.predict_proba(clip_features(x, self.dt)[None])[0]
        p_cnn = torch.softmax(self.cnn(torch.from_numpy(robust_z(x).astype(np.float32))[None]), 1)[0].numpy()
        return (p_rf + p_cnn) / 2

    def predict(self, x):
        p = self.proba(x)
        return self._result(p)

    def _result(self, p):
        pset = [self.classes[i] for i in np.where(1 - p <= self.q)[0]]
        top = int(p.argmax())
        return dict(status='ok', activity=self.classes[top], model_score=round(float(p[top]), 3),
                    scores={c: round(float(v), 3) for c, v in zip(self.classes, p)},
                    prediction_set=pset, abstain=len(pset) != 1, fall_possible='fall' in pset,
                    prediction_unit='3s_window', experimental=True)

    def fuse(self, windows):
        """windows: {board_id: T x 52 or None}. Averages ensemble scores over boards with a valid window."""
        per = {b: self.proba(x) for b, x in windows.items() if x is not None}
        if not per:
            return dict(status='no_valid_window', n_boards=0)
        r = self._result(np.mean(list(per.values()), 0))
        r.update(n_boards=len(per), per_board={b: dict(zip(self.classes, np.round(p, 3).tolist())) for b, p in per.items()})
        return r
