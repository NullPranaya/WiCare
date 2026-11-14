# AuraCare front end (page 1: live room)

Based on RuView's Observatory UI (MIT, (c) 2024 rUv, see RUVIEW_LICENSE): same 3D scene,
AuraCare branding, two small cards (Breathing, Room), a care status pill and an event strip.

```bash
cd frontend && python3 -m http.server 3000
# open http://localhost:3000            demo story: calm activity, then a fall pattern
# open http://localhost:3000/?scene=fall start at the fall
```
- **With the AuraCare backend (main mode):** the page polls `GET /care/state` on port 8000 every second
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
