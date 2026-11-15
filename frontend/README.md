# WiCare room view

`index.html?room=1` is the 3D room the nurse sees after replying 1: a hospital bed, a pixel-style patient in a gown
(`observatory/js/pixel-patient.js`), a fall out of bed and a FALL DETECTED banner. `python run.py` (one folder up)
serves it at `http://localhost:8000/room`. Timing: in bed 0-7 s, falls 7-8 s, on the floor until 15 s, then repeats
(`&t=6` jumps to just before the fall).

The rest of this file describes the earlier single-room page (the same files without `?room=1`).

---

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
