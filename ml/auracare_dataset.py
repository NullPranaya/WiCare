"""Loader for the room recording zip (session S20261114_1541 and later), decoded/<chunk>.jsonl.gz.
Per packet: raw = int8 [imag, real] pairs; raw[0:128] = L-LTF, 64 subcarriers in native order 0..31, -32..-1
(the WiFall layout source); raw[128:256] = HT-LTF (ignored). first_word_sanitized zeroes subcarriers 0 and +1.
Measured on S20261114_1541: ~29 Hz per node (not 45), ~34% packets lost (seq gaps), gaps up to ~0.8 s,
first_word_sanitized on 100% of packets. Use ts_unix + seq, never a fixed rate.
  python -m ml.auracare_dataset <dataset dir>      # per chunk/node summary"""
import gzip, json, os, sys
import numpy as np
from sensors.csi_adapter import to_wifall

NODE_HZ = 29.0          # measured delivered rate per node on the 2026-11-14 recording

def session(root):
    return json.load(open(os.path.join(root, 'session.json')))

def load_chunk(root, chunk_id):
    """-> {node_id: dict(t, seq, amp (N x 52 WiFall order), sanitized)} sorted by ts_unix."""
    per = {}
    with gzip.open(os.path.join(root, 'decoded', f'{chunk_id}.jsonl.gz'), 'rt') as f:
        for line in f:
            r = json.loads(line)
            if r['raw_len'] < 128: continue
            per.setdefault(r['node_id'], []).append(r)
    out = {}
    for n, rows in per.items():
        rows.sort(key=lambda r: r['ts_unix'])
        out[n] = dict(t=np.array([r['ts_unix'] for r in rows]), seq=np.array([r['seq'] for r in rows], np.int64),
                      amp=np.stack([to_wifall(r['raw'][:128], r['first_word_sanitized']) for r in rows]),
                      sanitized=np.array([r['first_word_sanitized'] for r in rows], bool))
    return out

if __name__ == '__main__':
    root = sys.argv[1]
    for c in session(root)['chunks']:
        d = load_chunk(root, c['id'])
        for n, x in sorted(d.items()):
            ds = np.diff(x['seq']); lost = int(np.clip(ds - 1, 0, None).sum())
            print(f"{c['id']:14s} node {n}: {len(x['t'])} pkts {len(x['t']) / (x['t'][-1] - x['t'][0]):.1f} Hz "
                  f"lost {lost / (lost + len(x['t'])):.0%} max gap {np.diff(x['t']).max():.2f}s")
