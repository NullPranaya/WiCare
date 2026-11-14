"""Run the WiFall-trained activity ensemble on the room recording. No falls were recorded, so this measures
(1) how often 'fall' shows up on normal data (false-alarm pressure) and (2) what the model says right after
the 28 timed sit_down / stand_up / jump prompts. Windows: 3 s, fused over nodes 2 and 3, resampled to 50 Hz.
  python -m ml.eval_auracare <dataset dir> [--out evaluation/auracare_eval.json]"""
import argparse, json
from collections import Counter
import numpy as np
from ml.auracare_dataset import session, load_chunk, NODE_HZ
from ml.inference import ActivityModel
from sensors.csi_adapter import window

REACT_S = 0.5              # people react ~0.5-1 s after the prompt
WIN_KW = dict(dt=0.02, length_s=2.96, max_gap_s=0.3, min_coverage=0.6, expected_hz=NODE_HZ)

def fused(m, data, end):
    wins, infos = {}, {}
    for n, x in data.items():
        wins[n], infos[n] = window(x['t'], x['seq'], x['amp'], end_t=end, **WIN_KW)
    return m.fuse(wins), infos

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('--out', default='evaluation/auracare_eval.json')
    a = ap.parse_args()
    m, ses = ActivityModel(), session(a.root)
    per_state, actions, valid = {}, [], Counter()
    for c in ses['chunks']:
        d = load_chunk(a.root, c['id'])
        t0 = max(x['t'][0] for x in d.values()) + 3.0; t1 = min(x['t'][-1] for x in d.values())
        for end in np.arange(t0, t1, 1.0):                                   # sliding, 1 s hop
            r, _ = fused(m, d, end)
            valid['ok' if r['status'] == 'ok' else 'no_window'] += 1
            if r['status'] != 'ok': continue
            s = per_state.setdefault(c['state'], dict(n=0, top=Counter(), fall_in_set=0, abstain=0, fall_score=[]))
            s['n'] += 1; s['top'][r['activity']] += 1; s['fall_in_set'] += r['fall_possible']
            s['abstain'] += r['abstain']; s['fall_score'].append(r['scores']['fall'])
        for act in c.get('actions') or []:
            r, inf = fused(m, d, act['t_unix'] + REACT_S + 2.96)            # window covers prompt+0.5 .. +3.5 s
            actions.append(dict(chunk=c['id'], action=act['action'], status=r['status'],
                                top=r.get('activity'), fall_in_set=r.get('fall_possible'), scores=r.get('scores'),
                                n_boards=r.get('n_boards', 0)))
    summ = {k: dict(windows=v['n'], top=dict(v['top']), fall_in_set_rate=round(v['fall_in_set'] / v['n'], 3),
                    fall_top_rate=round(v['top']['fall'] / v['n'], 3), abstain_rate=round(v['abstain'] / v['n'], 3),
                    fall_score_mean=round(float(np.mean(v['fall_score'])), 3)) for k, v in per_state.items()}
    by_act = {}
    for x in actions:
        b = by_act.setdefault(x['action'], dict(n=0, top=Counter(), fall_in_set=0))
        b['n'] += 1; b['top'][x['top']] += 1; b['fall_in_set'] += bool(x['fall_in_set'])
    by_act = {k: dict(n=v['n'], top=dict(v['top']), fall_in_set=v['fall_in_set']) for k, v in by_act.items()}
    out = dict(session=ses['session'], window=WIN_KW, windows=dict(valid), per_state=summ, per_action=by_act, actions=actions)
    json.dump(out, open(a.out, 'w'), indent=1)
    print(json.dumps(dict(windows=dict(valid), per_state=summ, per_action=by_act), indent=1))

if __name__ == '__main__':
    main()
