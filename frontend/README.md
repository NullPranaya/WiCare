# WiCare front end (page 1: live room)

Based on RuView's Observatory UI (MIT, (c) 2024 rUv, see RUVIEW_LICENSE): same 3D scene,
WiCare branding, two small cards (Breathing, Room), a care status pill and an event strip.

```bash
cd frontend && python3 -m http.server 3000
# open http://localhost:3000            demo story: waking up, everything OK (loops every 36 s)
#                                       0 s lying in bed, 5 s sits up, 11 s gets up, 15 s walks around the room
# open http://localhost:3000/?t=14      jump into the story (seconds), handy for rehearsal
# open http://localhost:3000/?scene=fall the older story: calm activity, then a fall pattern
```
- **With the WiCare backend (main mode):** the page polls `GET /care/state` on port 8000 every second
  (`?api=http://host:8000` to point elsewhere). The care pill, the event strip (Detected, Check-in sent,
  Reply with countdown, Caregiver), a live movement-score chart with the alert thresholds, and the real
  iMessage conversation all come from the backend. RuView's simulated Breathing / Room cards are hidden and
  the badge reads "3D VIEW SIMULATED": the figure is a visualisation, the care data is real. Replayed events
  are labelled "room replay" in the strip.
- No backend: runs RuView's simulated data, badge says DEMO.
- With a sensing server: it auto-connects to `/ws/sensing` (badge LIVE) and reads `data.auracare`
  `{status: ok|watch|help|off, label, title, step 0-4, since_utc}` for the pill and strip.
  Nothing care-related is invented in LIVE mode.
- Changed from RuView: `index.html` (branding, hidden panels), `observatory/css/auracare.css`,
  `observatory/js/auracare.js` (new), `main.js` (2 lines), `pose-system.js` (older-adult gait/stoop),
  `services/ws-ticket.js` (inlined one constant). three.js 0.160 vendored for offline demos.

## Private room page: `frontend/ward/` (one room by default, `?rooms=5` for a 5-room ward)

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000     # 0.0.0.0 so a phone on the same Wi-Fi can open the control page
cd frontend && python3 -m http.server 3000
# laptop:   http://localhost:3000/ward/              Room 1 in 3D, closed (roof, walls, door): nobody visible inside
# teammate: http://<laptop-ip>:8000/ward/control     slider + Calm / Stressed buttons for the room's stress monitor
```
- Privacy: the room stays closed. Stress is a 0-100 demo index, not a clinical measure.
- Stress 70+ for 3 s: the room pops up red and the nurse's phone (CAREGIVER_ADDRESS, Photon) gets "Room 1's stress level is
  high. Do you want to check on them? Reply 1: YES / 2: NO".
- Reply 1: the roof lifts, the front wall lowers, the camera goes in and the patient script plays (lying 0-5 s, sits up at
  5 s, gets up at 11 s, walks from 15 s) beside the stress chart and the iMessage thread. Reply 2: dismissed; it asks again
  only after stress drops below 50.
- Fallbacks: "Open room 1" button (or key O) opens it without the phone; "Close room" (B / Esc) closes it.
  `?t=12` starts the script 12 s in. No backend: local simulation (keys H = high, C = calm).
- Backend: `WARD_ROOMS` (default 1), `WARD_LIVE_ROOM` (default 1). API: `GET /ward/state`, `POST /ward/stress {value}`,
  `POST /ward/view`, `POST /ward/close` (`backend/ward.py`).
