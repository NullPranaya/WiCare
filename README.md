<div align="center">

# WiCare

### Wi-Fi fall detection that texts the nurse, and shows the room when they ask.

*No cameras. No wearables. A Wi-Fi signal, a text message, and a reply.*

**HackPrinceton Fall 2026 · Photon track**

</div>

---

## What it does

A patient is alone in a room. If they fall, somebody has to find out, and nobody wants a camera pointed at a
hospital bed.

WiCare watches the room's **Wi-Fi signal** instead. Movement in a room disturbs the signal, and a fall has a
recognisable shape: **a sudden burst of movement, then stillness.** When WiCare sees that shape, it does not
sound a siren. It **texts the nurse over iMessage** (through [Photon](https://photon.codes)) and asks what to do:

> WiCare: possible fall detected at 21:04:12. Patient has been still for 8 s after a sudden movement.
> Reply 1 to acknowledge, 2 to ignore.

- The nurse replies **1**: the screen opens the **room view**, a 3D picture of the bed and the patient on the floor.
- The nurse replies **2**: the alert is dismissed as a false alarm.

The room stays private until the nurse asks to see it. Before that, the screen shows only a line on a chart.

## How it works

```
 Wi-Fi motion score ──> WiCare monitor ──> fall rule ──> Photon iMessage ──> nurse's phone
   (boards, or the        (live chart,       (burst,        "possible fall,        │
    control page)          Bed 1)             then           reply 1 or 2")        │ replies 1
                                              stillness)                           ▼
                                                              3D room view opens on the monitor screen
```

1. **The monitor.** WiCare's dashboard shows Bed 1's motion score live, four readings a second, with the alert
   line drawn across the chart. A second page shows the whole ward.
2. **The rule.** Score above the line for at most 3 seconds, then below it for 8 seconds: possible fall.
   Longer movement is treated as someone walking around, and never alerts.
3. **The text.** WiCare sends the alert through Photon's iMessage API and keeps checking Photon for the nurse's reply.
4. **The room view.** On reply **1**, the dashboard switches to a 3D room: a hospital bed, a patient in a gown,
   and the fall played out, with **FALL DETECTED** across the top.

## Run it

Everything runs from one laptop with one command.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in the Photon values below
python run.py
```

`run.py` prints the addresses to open:

| What | Where |
|---|---|
| Monitor (show this screen) | `http://localhost:8000/` |
| All rooms | `http://localhost:8000/ward` |
| Room view (opens by itself on reply 1) | `http://localhost:8000/room` |
| Control page | the link `run.py` prints, for a teammate on any network |

In `.env`: `CAREGIVER_ADDRESS` (the nurse's phone, like `+15551234567`), `PHOTON_PROJECT_ID` and
`PHOTON_PROJECT_API_KEY`. The nurse's phone must have texted the Photon line once, so Photon is allowed to
message it. Node.js is needed for the sender (`wicare/fallguard/notify/send.mjs`).

**Where the motion score comes from.** WiCare reads ESP32 boards running ESPectre firmware
(`python run.py --boards`, addresses in `wicare/fallguard/.env`). Without boards, the **control page** supplies
the score in the same format, so the rest of the system runs exactly the same: "Send alert" plays a sudden
movement and then stillness.

**Tips**
- After an alert, WiCare holds the alarm until the nurse replies or someone presses Acknowledge
  (or "Reset alarm" on the control page). It will not alert again until then.
- Options: `--no-tunnel` (no public control link), `--port`, `--cooldown` (seconds between alerts, default 5).

## What is in this repository

```
run.py           starts everything: WiCare's app, the room view, the control page, the public control link
wicare/          the team's WiCare app, exactly as in github.com/duna-akin/WiCare:
                 monitor dashboard, ward page, fall rule, Photon sender, reply reader
frontend/        the 3D room view (index.html?room=1): bed, patient, fall, built on three.js
remote/          the control page, which stands in for a board
backend/ ml/ monitoring/ photon/ sensors/ investigation/ sim/ evaluation/ models/ tests/
                 our earlier research code (Wi-Fi signal models and a first messaging pipeline); not used by run.py
```

## Honest limits

- This is a hackathon prototype, tested with teammates only. It is not a medical device.
- The fall rule is simple on purpose: one threshold and two timers, tuned in one room.
- The 3D room view is an animation that plays when the nurse opens the room. It is not reconstructed from the
  Wi-Fi signal.
- Beds 2 to 6 on the ward page are simulated by the dashboard; Bed 1 is the live one.

## Credits

- [WiCare](https://github.com/duna-akin/WiCare): monitor dashboard, ward page, fall rule and Photon messaging
- [Photon](https://photon.codes): iMessage API
- ESPectre: ESP32 Wi-Fi motion sensing firmware
- [RuView](https://github.com/ruvnet/RuView) (MIT): the 3D scene the room view started from
- [three.js](https://threejs.org), FastAPI, Uvicorn
