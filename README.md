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
cp .env.example .env                              # test numbers, Photon keys (see below)
cp models/room_calibration.json calibration.json  # detector thresholds for the room model
pytest -q                                         # 24 tests
uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

### Demo runbook (2 nodes + hub.py on the sensing laptop)
```bash
# terminal 1: backend (above). terminal 2: public URL for Photon replies
cloudflared tunnel --url http://127.0.0.1:8000     # paste <url>/webhooks/photon into Photon > Webhooks
# terminal 3: live movement score from the boards (hub.py serves /v1/csi on :8770)
python -m monitoring.csi_worker --hub ws://<hub laptop>:8770/v1/csi
# no boards: replay the room recording (labelled room_replay, demo mode only)
python -m monitoring.csi_worker --replay <dataset dir> --chunks p1_moving_1,p1_still_1
# last-resort fallback, labelled synthetic: burst then stillness, no CSI
python -m monitoring.csi_worker --synthetic
```
Flow: movement burst (score >= 0.8) then 8 s still (score <= 0.4, median of 3) -> event -> iMessage check-in to the
patient -> "OK" closes it; "2"/help or no reply in `CHECKIN_TIMEOUT_S` (15 s) -> caregiver alert; caregiver can
ask "why did this alert?" and gets the evidence. `EXPECTED_ACTIVE=0` for the demo so a quiet room never texts anyone.
Rehearse the demo motion on the real boards first: no falls were recorded, so the trigger is only validated on
recorded movement -> stillness transitions.

### Photon (2.0 Beta REST API, verified live 2026-11-14)
1. Dashboard: copy Project id, create an API key -> `.env` `PHOTON_PROJECT_ID`, `PHOTON_PROJECT_API_KEY`.
2. iMessage > Add someone to the line: both test phones. Each phone texts its assigned line once (shared-line rule).
3. Webhooks > Add a destination: `<tunnel>/webhooks/photon`, all events -> `.env` `PHOTON_WEBHOOK_SECRET` (whsec_...).
4. `.env` `PHOTON_MODE=api`, restart uvicorn. `mock` logs messages without sending.
Sends: `photon/client.py` (users -> assigned line -> space -> messages). Replies: Standard Webhooks signature,
event `message.received` with the message in `data.object`. The tunnel URL changes when cloudflared restarts.

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
Upstream: WiFall (RS2002/WiFall), Photon, RuView firmware.

## Live CSI (RuView boards, nodes 2 and 3, room recording S20261114_1541)
- Per packet `raw[0:128]` = L-LTF, native order 0..31, -32..-1, [imag, real]. `sensors/csi_adapter.py` maps to
  52 subcarriers [-26..-1, +1..+26] (`SORTED`), confirmed on 26/26 node-recordings with `order_check_lag2`.
  `first_word_sanitized` is set on every packet; subcarrier +1 is filled from its neighbour.
- Measured: ~29 packets/s per node, ~34% lost (seq gaps), gaps up to 0.8 s. Windows use `ts_unix` + `seq`
  (`window(..., expected_hz=29, max_gap_s=0.3, min_coverage=0.6)`), resampled to a 50 Hz grid.
- WiFall activity model does not transfer to this room (`python -m ml.eval_auracare <dataset>`): in the empty room
  "fall" is top in 23% of windows. It is not used for alerts.
- Room movement model (`python -m ml.room_motion train <dataset>`), held out train _1 / test _2 and swap
  (`python -m ml.eval_room <dataset>`): moving vs still/empty balanced acc 0.84, fired after 28/28 prompts;
  occupied 0.81; people count 0.64 (context only, never suppresses alerts). One session only, so optimistic.
