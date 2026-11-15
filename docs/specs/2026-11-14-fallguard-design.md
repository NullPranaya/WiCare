# FallGuard design: Wi-Fi fall detection with ESPectre

Date: 2026-11-14 · Event: HackPrinceton 2026 · Time budget: about 10 hours to the judge demo

## Intent

Build a judge demo that detects a patient falling out of bed using Wi-Fi sensing from ESP32
boards running ESPectre. A fall is a **sudden burst of strong movement followed by minimal
movement**. Slight movements, such as shifting, rolling over gently, or breathing, must not
alert.

When a fall is detected:

1. A laptop dashboard shows a clear **FALL** alert and explains why it fired.
2. An iMessage goes to an iPhone through Photon's cloud API.

The team wants an easy win first and ML later. The first version uses each board's own
motion score; raw-CSI recording and ML come after the end-to-end demo works.

## Goals

- One ESP32 running official ESPectre Native firmware on the iPhone hotspot (done, see
  "Current hardware state").
- A laptop app that listens to the board's motion events, runs a burst-then-stillness rule,
  and drives the dashboard and the iMessage alert.
- A local web dashboard in the "signal explainer" layout (see "Dashboard").
- One iMessage per fall event through Photon.

## Stretch goals (in order)

1. **Second ESP32.** A board pair across the bed, and a "both boards saw the burst" check
   against false alarms from passers-by. Target: start around the halfway point, once the
   single-board demo works end to end.
2. **ML.** Record labeled raw-CSI clips and train a classifier that confirms or replaces the
   rule.

## Non-goals

- Custom ESP32 firmware or changes inside `espectre-main/`.
- Home Assistant, MQTT, or cloud hosting of the app.
- Clinical-grade accuracy, multi-room coverage, locating the person, or identifying who fell.
- Detecting "getting out of bed" as a separate event.
- More than two boards.

## Anti-goals (failure even if it technically works)

- Alerting on every roll-over or on people walking near the setup.
- Sending a stream of iMessages for one fall.
- A demo that needs a long quiet-room calibration at the booth.
- Committing the Photon credentials or the recipient's phone number.

## Current hardware state (verified 2026-11-14)

- Board 1: classic ESP32-D0WD-V3, CP2102 on `/dev/ttyUSB0`, MAC `8c:aa:b5:37:6b:ec`.
  Previous firmware was an unrelated `esp32-csi-node` 0.8.18 build; replaced by the official
  `espectre-native-3.2.0-esp32.bin` from the GitHub 3.2.0 release (SHA-256 matches the
  release digest), installed with an erase and a verified write.
- Wi-Fi: provisioned with Improv Serial to the hotspot `Tuna’s iPhone` (curly apostrophe),
  2.4 GHz channel 6, subnet `172.20.10.0/28`. Board at `172.20.10.3`, Direct endpoint
  `http://172.20.10.3:62587/espectre/v1`. The laptop is `172.20.10.2`.
- `direct get device` reports `frontend: native`, `firmware: 3.2.0`, `chip: esp32`.
- Sensing: Lightweight detector, internal `ping` traffic to the iPhone at 100 pps with about
  100 replies per second, occupancy about 74% (the floor is 70%), RSSI about −50 dBm.
- Threshold: calibrated in the crowded room to **0.962**. The team tested it and likes it:
  it fires only on aggressive motion. A reboot recalibrates, so the app (or a person)
  re-applies it with `PATCH /sensing {"threshold": 0.962}`.
- Startup note: right after provisioning, traffic read 0 pps for roughly the first 100 s,
  then ran normally. Wait for `ready: true` before trusting the stream.

## Constraints and facts

### ESPectre interface the app uses

- `GET /espectre/v1/events` is a Server-Sent Events stream. Each detector evaluation (every
  250 ms by default) emits `motion` with `{"timestamp_ms", "state": "idle"|"motion",
  "score"}`. The device allows at most two stream clients. Missed events are not replayed.
  There is a heartbeat comment every 10 s.
- `GET /sensing` gives `threshold`, `ready`, `calibrating`, and `detector`.
  `GET /diagnostics` gives traffic and occupancy (for the "sensor alive" indicator).
- `PATCH /sensing` sets `threshold` and `motion_on_hits` / `motion_off_hits` (currently 4 and 3).
- The repo's Python client lives in `espectre-main/src/python/espectre_cli/device_transport.py`
  (`DirectClient`). The CLI sends `Origin: https://test.espectre.dev`, which the firmware
  accepts by default.
- Opening the raw CSI stream (`/csi`) pauses motion events. Do not open it in v1.
- Never enable Wi-Fi promiscuous mode (repo rule).

### Network

- The iPhone hotspot is the router and one end of the sensing link. It must lie still on a
  table during the demo. A judge picking it up disturbs the link.
- Use IP addresses, not mDNS. A hotspot reconnect may change the board's IP; the app takes
  the endpoint from config.

### Photon (iMessage)

- The team has a Photon cloud (Stable / Spectrum) account.
- Sending uses the TypeScript SDK `spectrum-ts` with `SPECTRUM_PROJECT_ID` and
  `SPECTRUM_PROJECT_SECRET` (project Settings on app.photon.codes). The REST API has no send
  endpoint, so the Python app calls a small Node script. Node 26 and npm are installed.
- Minimal send, from the Photon docs:

  ```ts
  import { Spectrum } from "spectrum-ts";
  import { imessage } from "spectrum-ts/providers/imessage";
  const app = await Spectrum({ providers: [imessage.config()] }); // reads env credentials
  const im = imessage(app);
  const dm = await im.space.create(await im.user("+1XXXXXXXXXX"));
  await dm.send("...");
  await app.stop();
  ```

- The free tier uses a shared line pool, so the sender number can vary.

## Approaches considered

1. **Board's motion score (chosen for v1).** The laptop listens to the board's motion events
   and runs the rule on them. This is the fastest path and keeps the detector the team has
   already tested and liked.
2. **Raw CSI on the laptop.** The laptop computes its own movement measure from the raw
   signal. It is better for ML, but it disables the on-device detector while running and
   means tuning from scratch. The team first picked this one, then switched to 1 after
   testing showed that the calibrated on-device threshold already ignores small movements.
   It remains the path for the ML stretch goal.
3. **ML only.** Dropped: nothing works until enough falls are recorded.

## Design (decided)

### Layout

```
hackprinceton 2026/
  espectre-main/          upstream ESPectre 3.3.0-dev checkout, unmodified, git-ignored
  fallguard/              our app (Python), may import espectre-main code
    notify/               Node script for Photon (spectrum-ts)
  docs/specs/ this document
```

The project root is a git repo. `.env` holds the Photon credentials, the recipient number,
and the board endpoint. It is git-ignored; `.env.example` is committed.

### Pipeline

```
ESP32 --SSE motion events (4/s)--> laptop app --> fall rule --> dashboard (live, browser)
                                                            \-> Photon iMessage (Node)
```

### Fall rule (state machine, all numbers in config)

- `MONITORING`: score below the threshold.
- `BURST`: motion starts. Score above the threshold (board threshold, default 0.962); the
  burst length is timed.
- If the burst ends within the burst limit (default 3 s) → `STILLNESS_CHECK`, with a countdown
  (default 8 s).
- If motion returns during the countdown, or the burst runs longer than the limit → `MOVEMENT`
  (walking or getting up, no alert), then back to `MONITORING` once quiet.
- Countdown completes → `FALL`: dashboard alarm and one iMessage. A cooldown (default 60 s)
  blocks repeats. The state stays `FALL` until someone presses **Acknowledge**.
- Use the raw score crossing the threshold, not the board's debounced `state`, so the burst
  timing is not delayed by the on/off hit counters. Tune with the board's `state` as a
  fallback if the raw score is too jittery.

### Dashboard: "signal explainer" layout (picked by the team)

- A live movement graph of the last 60 s, with the threshold as a dashed line.
- The burst is shaded amber and labeled with its length ("burst 1.4 s"). The stillness
  countdown is shaded red ("still 5/8 s"). The shading resets if movement returns.
- Status chips: `Monitoring → Burst → FALL`. A rule panel shows checks as they pass
  ("burst ≤ 3 s ✓ → stillness 8/8 s ✓"). An iMessage panel shows the send status.
- Buttons: **Acknowledge**, **Mark: fall**, **Mark: false alarm**. The marks are saved with
  timestamps for later ML labeling.
- In a corner: board address, packets per second, occupancy, and a connected/disconnected
  indicator.
- Mockup: `.superpowers/brainstorm/*/content/dashboard-layouts.html`, option C.

### Second board (stretch goal 1)

- The app reads several boards from the start (a list of endpoints in config), so adding one
  is a config change.
- Placement: one board on each side of the bed. Use the ESPectre pair (one board on
  `external`, the other on `wifi_raw` with `traffic_generator_target_mac` set to the first
  board's MAC) so the link crosses the bed.
- Fusion: a burst counts only if every connected board sees it within about 1 s.
  Stillness requires every board to be below the threshold.

### ML (stretch goal 2)

Use `./espectre collect --label <name>` to record labeled raw-CSI clips (`fall`,
`roll_over`, `lying_still`, `walking_nearby`, `empty`), and train a small classifier. This
pauses the on-device detector while recording, so it runs as a separate mode, not during the
live demo. Perform falls onto a mattress or crash mat.

## Calls made during design (approved with the design)

- **Bystanders:** tape off a zone of about 2–3 m around the bed and the board↔phone link, and
  add an **Armed / patient in bed** toggle on the dashboard. Alerts fire only while armed.
- **Alert phone (decided by the team):** the iMessage goes to a second iPhone, the "nurse
  iPhone", not the hotspot phone. The hotspot phone stays still on the table.
- **Who does hardware steps:** a person does anything that needs a browser or a password.
  The agent does serial and Direct HTTP work with the team's go-ahead.

## Demo script

1. The phone lies still on the table, the board sits beside the bed or mat, and the zone is
   taped off.
2. A teammate lies on the bed and presses **Armed**. Shifting and rolling cause no alert;
   at most a short burst shows that resets.
3. The teammate rolls off onto the crash mat and stays still. The burst is shaded, the
   stillness counts to 8, the dashboard turns red, and the iPhone gets the iMessage.
4. **Acknowledge**, and repeat.

## Left to the builder

- The web stack for the dashboard and how live updates reach the browser.
- How Python calls the Node script (one-shot process or a small long-running service).
- The exact message text, and the default rule numbers within the ranges above.
- Test strategy: host-side tests that feed recorded or synthetic motion-event sequences
  into the rule.
- Order of work, as long as the single-board end-to-end demo comes first.
