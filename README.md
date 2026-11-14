# AuraCare backend, ML and Photon (no paid APIs)

```
RuView/ESP32 motion feed ──► monitoring.runner ──► POST /ingest/observation
                                              │
                         score rules (monitoring/anomaly.py, thresholds from calibration.json)
                                              │ event + evidence IDs
                         deterministic policy ──► check-in + Photon message (immediately)
                                              │
                         evidence investigator (investigation/) ──► report with cited IDs + uncertainty
Raw CSI (3 boards) ──► csi_adapter (64->52, 50 Hz, 3 s) ──► RF+CNN ensemble + conformal set ──► POST /ingest/ml
Photon reply ──► bridge ──► POST /webhooks/photon ──► OK/HELP/AWAY exact, help keywords escalate, else review
```

## ML results (leave-one-participant-out, 926 WiFall clips, 3 s at 50 Hz)
| Model | 5-class macro-F1 | Fall vs rest balanced acc | Fall precision |
|---|---|---|---|
| Always "fall" baseline | 0.13 | 0.50 | - |
| Random Forest (handcrafted features) | 0.41 | 0.69 | 0.76 |
| 1D CNN | 0.50 | 0.78 | 0.87 |
| RF + CNN ensemble | **0.52** | **0.78** | **0.86** |

Conformal prediction (90% target): measured coverage 0.905, average set size 2.7; for 93% of real fall clips
"fall" is in the set (`fall_possible`). When the set has more than one activity the model abstains.
Reproduce: `python -m ml.data data/raw/WiFall data/clips.npz && python -m ml.train_rf data/clips.npz --rate 50 && python -m ml.train_cnn && python -m ml.evaluate`
## Run
```bash
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # fill in test numbers
pytest -q                       # 20 tests

uvicorn backend.main:app --host 127.0.0.1 --port 8000
python -m monitoring.calibrate $ESP_IP          # once: 20 s still, 20 s normal movement
python -m monitoring.runner $ESP_IP             # live
# no board / replay view:
PLAYLIST=data/raw/WiFall/ID8/walk/<file>.csv uvicorn sim.replay_sensor:app --port 62587
python -m monitoring.runner 127.0.0.1 --source wifall_replay
```

### Photon
Photon has no public HTTP send endpoint today, so `photon/bridge/bridge.ts` (spectrum-ts 12.16.0, type-checked)
holds the iMessage connection, exposes local `POST /send`, and forwards inbound texts to `/webhooks/photon`.
No ngrok needed.
```bash
cd photon/bridge && cp .env.example .env && bun install && bun --env-file=.env bridge.ts
# then in backend .env: PHOTON_MODE=bridge
```
Native Spectrum webhooks are also accepted (HMAC `v0:<ts>:<body>`, 5 min window) if you register one.

## Verify on the day
- First line printed by `monitoring.runner` is the raw motion payload. If the score key isn't one of
  `movement, score, motion_score, value`, edit `SCORE_KEYS` in `sensors/espectre_client.py` (RuView feed format TBD).
- `bridge.ts` was type-checked, not run against a live Photon account.

## Guarantees (each has a test)
| Rule | Test |
|---|---|
| Stale/offline sensor = unavailable, never inactivity; gaps reset the detector | `test_stale_sensor_*`, `test_gap_resets_detector` |
| Replay never messages outside demo mode; no opt-in, no message | `test_replay_*`, `test_no_opt_in_*` |
| One check-in per event, no duplicate messages or webhooks | `test_abrupt_*`, `test_duplicate_webhook_ignored` |
| OK acknowledges, HELP or help words notify caregiver, other free text never closes | `test_ok_*`, `test_help_*`, `test_mixed_*`, `test_ambiguous_never_closes` |
| Timeout and delivery failure recorded and escalated | `test_timeout_escalates`, `test_delivery_failure_recorded` |
| Investigator cites only IDs that exist; stale sensor = inspect sensor; replay labelled | `test_report_*`, `test_stale_sensor_report_*`, `test_replay_labelled` |
| Activity model only attached after live validation | `test_ml_only_attached_after_validation` |

Demo thresholds are accelerated settings, not clinical recommendations. No fall detection or diagnosis claims.
Upstream: WiFall (RS2002/WiFall), Photon spectrum-ts, ESPectre/RuView firmware.

## Live CSI (RuView boards, 3 x ESP32)
- Model is trained at **50 Hz** (`python -m ml.train_rf data/clips.npz --rate 50`), matching the boards.
- `sensors/csi_adapter.py`: 64 subcarriers (128 bytes) -> WiFall's 52 in order [-26..-1, +1..+26]
  (`SORTED`). If the invalid-first-word flag is set, subcarrier +1 is filled from its neighbour.
- Laptop receive time is the clock; sequence numbers count drops; windows with gaps > 0.1 s or < 80% coverage are skipped.
- `python -m ml.check_live recording.json`: rate, drops, layout check (correct layout << 1, wrong ~9), predictions.
  Edit `load()` to the RuView JSON format.
- Predictions attach to events only when `LIVE_HAR_VALIDATED=1`; per-board ensemble scores are averaged and a conformal set is attached (`ActivityModel.fuse`).
