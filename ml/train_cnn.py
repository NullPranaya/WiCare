"""1D CNN on 3 s CSI windows (50 Hz, 52 subcarriers), leave-one-participant-out.
Input is robust-normalised per subcarrier (removes static level -> less session leakage).
Saves out-of-fold class scores to evaluation/oof_cnn.npz and a final model to models/activity_cnn.pt."""
import argparse, json, time
import numpy as np, torch, torch.nn as nn
from ml.data import CLASSES, to_rate
from ml.features import robust_z

torch.set_num_threads(2)

class CSINet(nn.Module):
    def __init__(self, k=52, classes=5):
        super().__init__()
        def block(i, o, ks): return nn.Sequential(nn.Conv1d(i, o, ks, padding=ks // 2), nn.BatchNorm1d(o), nn.ReLU())
        self.f = nn.Sequential(block(2 * k, 64, 7), nn.MaxPool1d(2), block(64, 96, 5), nn.MaxPool1d(2),
                               block(96, 128, 3), nn.Dropout(0.2))
        self.head = nn.Sequential(nn.Linear(256, 64), nn.ReLU(), nn.Dropout(0.3), nn.Linear(64, classes))

    def forward(self, x):                       # x: B x T x 52
        x = x.transpose(1, 2)
        x = torch.cat([x, torch.diff(x, dim=2, prepend=x[:, :, :1])], 1)   # level + first difference
        z = self.f(x)
        return self.head(torch.cat([z.mean(2), z.amax(2)], 1))

def prep(X):
    return np.stack([robust_z(x) for x in X]).astype(np.float32)

def augment(xb):
    shift = np.random.randint(-15, 16)
    xb = torch.roll(xb, shift, dims=1)
    return xb * (1 + 0.1 * torch.randn(xb.shape[0], 1, xb.shape[2])) + 0.05 * torch.randn_like(xb)

def fit(Xtr, ytr, epochs=30, seed=0, bs=32):
    torch.manual_seed(seed); np.random.seed(seed)
    m = CSINet()
    opt = torch.optim.AdamW(m.parameters(), lr=2e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=epochs * ((len(Xtr) + bs - 1) // bs))
    w = torch.tensor(len(ytr) / (5 * np.bincount(ytr, minlength=5).clip(1)), dtype=torch.float32)
    lossf = nn.CrossEntropyLoss(weight=w, label_smoothing=0.05)
    X, y = torch.from_numpy(Xtr), torch.from_numpy(ytr).long()
    for _ in range(epochs):
        m.train()
        for i in torch.randperm(len(X)).split(bs):
            opt.zero_grad(); lossf(m(augment(X[i])), y[i]).backward(); opt.step(); sched.step()
    return m.eval()

@torch.no_grad()
def proba(m, X):
    return torch.softmax(m(torch.from_numpy(X)), 1).numpy()

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('npz', nargs='?', default='data/clips.npz')
    ap.add_argument('--rate', type=float, default=50); ap.add_argument('--epochs', type=int, default=30)
    a = ap.parse_args()
    d = np.load(a.npz, allow_pickle=True)
    X, dt = to_rate(d['X'], a.rate); X = prep(X); y, g = d['y'], d['groups']
    P = np.zeros((len(y), 5), np.float32)
    for pid in sorted(set(g), key=lambda s: int(s[2:])):
        t0 = time.time(); te = g == pid
        P[te] = proba(fit(X[~te], y[~te], a.epochs), X[te])
        print(pid, int(te.sum()), f'{time.time() - t0:.0f}s', 'acc', round(float((P[te].argmax(1) == y[te]).mean()), 3), flush=True)
    np.savez('evaluation/oof_cnn.npz', P=P, y=y, groups=g)
    m = fit(X, y, a.epochs)
    torch.save(dict(state=m.state_dict(), classes=CLASSES, dt=dt, T=X.shape[1], norm='robust_z'), 'models/activity_cnn.pt')
    print('saved')
