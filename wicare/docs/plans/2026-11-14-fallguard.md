# FallGuard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A laptop app that turns an ESPectre board's live motion score into fall alerts, shown on a "signal explainer" dashboard and sent as an iMessage to the nurse iPhone.

**Architecture:** An async Python service (FastAPI) reads each board's Server-Sent Events motion stream. It feeds a pure, clock-injected fall state machine and pushes samples, state, and alert status to the browser over a WebSocket. iMessage goes out through a one-shot Node script that uses Photon's `spectrum-ts`.

**Tech Stack:** Python 3.14, FastAPI, uvicorn, httpx, python-dotenv, pytest, and pytest-asyncio. Node 26 with `spectrum-ts`. Plain HTML/CSS/JS with a `<canvas>` graph, no build step.

**Spec:** `docs/specs/2026-11-14-fallguard-design.md`

## Global Constraints

- All new code lives in `fallguard/` at the project root. Never modify `espectre-main/` (it is git-ignored upstream code).
- Every request to a board sends the header `Origin: https://test.espectre.dev` (the board answers `Origin required` without it).
- Board endpoint format: `http://<ip>:<port>/espectre/v1`. Board 1 is `http://172.20.10.3:62587/espectre/v1`.
- The app judges motion with **its own threshold, default `0.962`**, applied to the raw `score`. It never uses the board's `state` label or the board's threshold. The board's threshold drifted from 0.962 to 0.773 at uptime 1889 s with no reboot.
- Rule defaults: burst ≤ `3.0` s, stillness `8.0` s, iMessage cooldown `60` s.
- Secrets (`SPECTRUM_PROJECT_ID`, `SPECTRUM_PROJECT_SECRET`, `NURSE_PHONE`) live only in `fallguard/.env`, which is git-ignored. `fallguard/.env.example` is committed with placeholder values.
- iMessage goes to the nurse iPhone (`NURSE_PHONE`, E.164), never to the hotspot phone.
- A board accepts at most **two** event-stream clients. The espectre.dev Monitor tab counts as one.
- Never open `/espectre/v1/csi` in this plan (it pauses motion events). Never enable promiscuous mode.
- Commits: `git commit -m "type: short description"`, a Conventional Commits subject only, with no body or trailers.
- Run all Python commands from `fallguard/` with `.venv/bin/python` / `.venv/bin/pytest`.

## Review Focus

1. **The stream drops during the stillness countdown.** Silence must never count as stillness. After `max_gap_s` (1.5 s) without samples, the state becomes `NO_SIGNAL` and no fall fires. *Test: Task 2, `test_gap_cancels_stillness`.*
2. **The board refuses the stream** (a third client, a reboot, or an IP change). The app must show the board as disconnected and keep retrying without crashing. *Test: Task 3, `test_reconnects_after_refusal`.*
3. **Photon is slow, fails, or has bad credentials.** The dashboard alarm must appear immediately anyway, and the iMessage panel shows `failed`. *Tests: Task 4, `test_send_failure_reports_failed_and_no_cooldown`; Task 5, `test_alarm_broadcast_before_send_finishes`.*
4. **The board reports `state: "motion"` with a score below the app threshold** (board threshold drift). This must not start a burst. *Test: Task 5, `test_board_state_label_is_ignored`.*
5. **A browser reload or a second tab during `FALL`.** It must show the current state at once, not `MONITORING`. *Test: Task 5, `test_new_socket_gets_current_status`.*

---

## File Structure

```
fallguard/
  pyproject.toml            deps + pytest config (asyncio_mode = "auto")
  .env.example              config keys with placeholders
  fallguard/__init__.py
  fallguard/__main__.py     CLI entry: python -m fallguard [--replay PATH] [--port N]
  fallguard/config.py       env -> AppConfig / RuleConfig
  fallguard/detector.py     pure fall state machine (no I/O)
  fallguard/board.py        SSE parsing, BoardClient (stream + health)
  fallguard/alerts.py       AlertManager (cooldown, message text), NodeSender
  fallguard/server.py       FastAPI app wiring sources -> detector -> WebSocket, session log, marks
  fallguard/replay.py       ReplaySource for recorded sessions (Task 6)
  fallguard/fusion.py       multi-board fusion (Task 8, stretch)
  fallguard/static/index.html, app.js, style.css   dashboard
  notify/package.json, notify/send.mjs             Photon sender
  tests/test_config.py, test_detector.py, test_board.py, test_alerts.py, test_server.py, test_replay.py, test_fusion.py
  data/                     runtime output (git-ignored): sessions/*.jsonl, marks.jsonl
```

---

### Task 1: Scaffold and config

**Files:**
- Create: `fallguard/pyproject.toml`, `fallguard/.env.example`, `fallguard/fallguard/__init__.py`, `fallguard/fallguard/config.py`, `fallguard/tests/test_config.py`
- Modify: `.gitignore` (add `fallguard/data/`)

**Interfaces:**
- Produces:
  - `RuleConfig` (frozen dataclass): `threshold: float = 0.962`, `burst_max_s: float = 3.0`, `still_s: float = 8.0`, `burst_merge_s: float = 0.75`, `quiet_reset_s: float = 1.0`, `max_gap_s: float = 1.5`.
  - `AppConfig` (frozen dataclass): `boards: tuple[tuple[str, str], ...]` (id, endpoint), `rule: RuleConfig`, `cooldown_s: float = 60.0`, `nurse_phone: str | None`, `port: int = 8000`, `data_dir: Path`.
  - `load_config(env: Mapping[str, str], base_dir: Path) -> AppConfig`.
- Env keys:
  - `FALLGUARD_BOARDS`: comma-separated `id=endpoint`, default `b1=http://172.20.10.3:62587/espectre/v1`.
  - `FALLGUARD_THRESHOLD`, `FALLGUARD_BURST_MAX_S`, `FALLGUARD_STILL_S`, `FALLGUARD_COOLDOWN_S`, `FALLGUARD_PORT`, `NURSE_PHONE`.
  - `data_dir` is always `base_dir / "data"`.

- [ ] **Step 1: Create the venv and install the deps**

`pyproject.toml` declares `fastapi`, `uvicorn`, `httpx`, and `python-dotenv`, with dev deps `pytest` and `pytest-asyncio`. Set `[tool.pytest.ini_options] asyncio_mode = "auto"`.
Run: `cd fallguard && python3.14 -m venv .venv && .venv/bin/pip install -q -e '.[dev]'`
Expected: exit 0. If a package has no 3.14 wheel, pin the newest version that installs, and note it in the commit.

- [ ] **Step 2: Write the failing tests**

```python
def test_defaults():
    c = load_config({}, Path("/x"))
    assert c.boards == (("b1", "http://172.20.10.3:62587/espectre/v1"),)
    assert c.rule.threshold == 0.962 and c.rule.burst_max_s == 3.0 and c.rule.still_s == 8.0
    assert c.cooldown_s == 60.0 and c.nurse_phone is None and c.data_dir == Path("/x/data")

def test_parses_two_boards_and_overrides():
    c = load_config({"FALLGUARD_BOARDS": "a=http://1.2.3.4/espectre/v1, b=http://1.2.3.5:81/espectre/v1",
                     "FALLGUARD_THRESHOLD": "0.9", "NURSE_PHONE": "+15550001111"}, Path("/x"))
    assert c.boards == (("a", "http://1.2.3.4/espectre/v1"), ("b", "http://1.2.3.5:81/espectre/v1"))
    assert c.rule.threshold == 0.9 and c.nurse_phone == "+15550001111"

def test_rejects_bad_board_entry():
    with pytest.raises(ValueError):
        load_config({"FALLGUARD_BOARDS": "no-equals-sign"}, Path("/x"))
```

- [ ] **Step 3: Run the tests and confirm they fail.** Run: `.venv/bin/pytest tests/test_config.py -q`. Expected: ImportError.
- [ ] **Step 4: Implement `config.py`.** Add `.env.example` with every key from above, the two `SPECTRUM_*` keys, and placeholder values.
- [ ] **Step 5: Run the tests and confirm they pass.** Run: `.venv/bin/pytest tests/test_config.py -q`. Expected: 3 passed.
- [ ] **Step 6: Commit.** `git add .gitignore fallguard && git commit -m "feat: scaffold fallguard config"`

---

### Task 2: Fall state machine

**Files:**
- Create: `fallguard/fallguard/detector.py`, `fallguard/tests/test_detector.py`

**Interfaces:**
- Consumes: `RuleConfig` (Task 1).
- Produces:
  - `Level = Literal["motion", "still", "mixed"]`
  - `level_for(score: float, threshold: float) -> Level`: `"motion"` if `score >= threshold`, else `"still"`.
  - `class State(StrEnum)`: `MONITORING`, `BURST`, `STILLNESS`, `MOVEMENT`, `FALL`, `NO_SIGNAL`.
  - `Status` (frozen dataclass): `state: State`, `since: float`, `burst_s: float | None`, `still_s: float | None`, `armed: bool`, `new_fall: bool`.
  - `class FallDetector(config: RuleConfig, armed: bool = True)`, with these methods, each returning `Status`:
    - `update(t: float, level: Level)`
    - `check_gap(t: float)`
    - `acknowledge(t: float)`
    - `set_armed(armed: bool, t: float)`
    - property `status`

Transition rules. `t` is host monotonic seconds:

- `MONITORING`: `motion` → `BURST` (`burst_start = t`). `still` and `mixed` stay.
- `BURST`: `motion` or `mixed` sets `last_motion = t`. If `t - burst_start > burst_max_s` → `MOVEMENT`. `still` → `STILLNESS` (`still_start = t`).
- `STILLNESS`:
  - `motion` with `t - still_start <= burst_merge_s` → back to `BURST`, keeping `burst_start` (a brief dip).
  - `motion` after that, or `mixed` → `MOVEMENT`.
  - `still` with `t - still_start >= still_s`: armed → `FALL` with `new_fall=True` on this one update only; disarmed → `MONITORING`.
  - `burst_s = last_motion - burst_start`.
- `MOVEMENT`: `still` held for `quiet_reset_s` → `MONITORING`. Any `motion` or `mixed` restarts the quiet timer.
- `FALL`: latches. Ignores all samples and gaps until `acknowledge` → `MONITORING`.
- `check_gap(t)`: if the state is not `FALL` and `t - last_sample_t > max_gap_s` → `NO_SIGNAL`. The next `update` in `NO_SIGNAL` goes to `MONITORING`, then applies the `MONITORING` rule for that same sample.
- `set_armed(False)` during `STILLNESS` or `BURST` keeps tracking, but the stillness completion goes to `MONITORING`.

- [ ] **Step 1: Write the failing tests.** Feed samples every 0.25 s with a helper `feed(det, start, end, level)` that returns the last Status and any Status with `new_fall`.

```python
def test_fall_fires_after_short_burst_then_stillness():
    d = FallDetector(RuleConfig())
    feed(d, 0.0, 2.0, "still"); feed(d, 2.0, 3.0, "motion")      # motion samples 2.0..2.75
    falls = feed(d, 3.0, 12.0, "still").falls
    assert [round(s.since, 2) for s in falls] == [11.0]          # 3.0 + 8.0
    assert falls[0].state is State.FALL and abs(falls[0].burst_s - 0.75) < 1e-9

def test_long_burst_is_movement_not_fall():
    d = FallDetector(RuleConfig()); feed(d, 0.0, 4.0, "motion")
    assert d.status.state is State.MOVEMENT
    assert feed(d, 4.0, 14.0, "still").falls == []

def test_motion_during_stillness_is_movement():
    d = FallDetector(RuleConfig()); feed(d, 0, 1, "motion"); feed(d, 1, 4, "still"); feed(d, 4, 4.5, "motion")
    assert d.status.state is State.MOVEMENT
    assert feed(d, 4.5, 14, "still").falls == []

def test_brief_dip_merges_into_burst():
    d = FallDetector(RuleConfig()); feed(d, 0, 1, "motion"); feed(d, 1, 1.5, "still"); feed(d, 1.5, 2, "motion")
    assert d.status.state is State.BURST

def test_disarmed_never_falls():
    d = FallDetector(RuleConfig(), armed=False); feed(d, 0, 1, "motion")
    assert feed(d, 1, 12, "still").falls == [] and d.status.state is State.MONITORING

def test_gap_cancels_stillness():
    d = FallDetector(RuleConfig()); feed(d, 0, 1, "motion"); feed(d, 1, 4, "still")
    assert d.check_gap(5.6).state is State.NO_SIGNAL
    assert feed(d, 5.75, 15, "still").falls == []

def test_fall_latches_until_acknowledge():
    d = FallDetector(RuleConfig()); feed(d, 0, 1, "motion"); feed(d, 1, 10, "still")
    feed(d, 10, 12, "motion"); assert d.check_gap(30).state is State.FALL
    assert d.acknowledge(31).state is State.MONITORING

def test_new_fall_only_once():
    d = FallDetector(RuleConfig()); feed(d, 0, 1, "motion")
    assert len(feed(d, 1, 20, "still").falls) == 1
```

- [ ] **Step 2: Run the tests and confirm they fail.** Run: `.venv/bin/pytest tests/test_detector.py -q`. Expected: ImportError.
- [ ] **Step 3: Implement `detector.py`** following the transition rules above. Keep it free of I/O and clocks.
- [ ] **Step 4: Run the tests and confirm they pass.** Expected: 8 passed.
- [ ] **Step 5: Commit.** `git commit -m "feat: add fall state machine"` (after `git add fallguard`)

---

### Task 3: Board event stream

**Files:**
- Create: `fallguard/fallguard/board.py`, `fallguard/tests/test_board.py`

**Interfaces:**
- Produces:
  - `ORIGIN = "https://test.espectre.dev"`
  - `MotionSample` (frozen dataclass): `board: str`, `t: float` (host monotonic), `score: float`, `device_ms: int`
  - `BoardStatus` (frozen dataclass): `board: str`, `connected: bool`, `detail: str`
  - `BoardHealth` (frozen dataclass): `board: str`, `pps: float | None`, `occupancy: float | None`, `ready: bool | None`
  - `parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict]]`: yields `(event_name, data)` per blank-line-terminated block. Ignores `:` comments and `retry:`. Skips blocks with invalid JSON.
  - `class BoardClient(board_id: str, endpoint: str, *, http: httpx.AsyncClient, clock: Callable[[], float] = time.monotonic, backoff_s: tuple[float, ...] = (1, 2, 5))`
    - `async def stream(self) -> AsyncIterator[MotionSample | BoardStatus]`: runs forever. It yields `BoardStatus(connected=True)` after a 200 response, then a `MotionSample` for each `motion` event. On a non-200 response, an error, or the stream closing, it yields `BoardStatus(connected=False, detail=...)`, sleeps for the next backoff (staying at the last value), and reconnects.
    - `async def health(self) -> BoardHealth`: calls `GET diagnostics` (fields `csi_admitted_pps`, `csi_occupancy`) and `GET sensing` (field `ready`). Returns `None` fields on any error.
- Wire format (captured from board 1):
  ```
  retry: 3000
  : connected

  event: motion
  data: {"timestamp_ms":1889616,"state":"idle","score":0.0148352}
  ```

- [ ] **Step 1: Write the failing tests.** Use `httpx.MockTransport` and `backoff_s=(0,)`.
  - `test_parse_sse_motion_blocks`: the captured text above plus a second block → `[("motion", {...score 0.0148352...}), ...]`, with the comment and `retry` ignored.
  - `test_stream_yields_connected_then_samples`: a mock serves two motion blocks. The first three items are `BoardStatus(connected=True)`, then `MotionSample(score=0.0148352, device_ms=1889616)`, then a second sample. Also assert the request carried `Origin: https://test.espectre.dev` and `Accept: text/event-stream`.
  - `test_reconnects_after_refusal`: the mock returns 503 on the first call and a valid stream on the second. The items are `BoardStatus(connected=False)`, then `BoardStatus(connected=True)`, then a sample.
  - `test_health_reads_fields`: mock diagnostics `{"csi_admitted_pps": 73.9, "csi_occupancy": 0.74}` and sensing `{"ready": true}` → `BoardHealth("b1", 73.9, 0.74, True)`. A 500 response → all `None`.
- [ ] **Step 2: Run the tests and confirm they fail.** Run: `.venv/bin/pytest tests/test_board.py -q`. Expected: ImportError.
- [ ] **Step 3: Implement `board.py`.** Use `http.stream("GET", endpoint + "/events", headers=..., timeout=httpx.Timeout(5.0, read=15.0))` and `aiter_lines()`. The read timeout is 15 s because the board sends a heartbeat every 10 s.
- [ ] **Step 4: Run the tests and confirm they pass.** Expected: 4 passed.
- [ ] **Step 5: Live smoke check.** Run: `.venv/bin/python -c "import asyncio,httpx;from fallguard.board import *;
async def m():
  async with httpx.AsyncClient() as h:
    n=0
    async for x in BoardClient('b1','http://172.20.10.3:62587/espectre/v1',http=h).stream():
      print(x); n+=1
      if n>5: break
asyncio.run(m())"`
  Expected: one `BoardStatus(connected=True)` followed by five `MotionSample` lines. Skip this step if the board is offline, and say so in the report.
- [ ] **Step 6: Commit.** `git commit -m "feat: add board event stream client"`

---

### Task 4: iMessage alerts

**Files:**
- Create: `fallguard/fallguard/alerts.py`, `fallguard/tests/test_alerts.py`, `fallguard/notify/package.json`, `fallguard/notify/send.mjs`
- Modify: `.gitignore` (`node_modules/` is already present)

**Interfaces:**
- Produces:
  - `AlertResult` (frozen dataclass): `status: Literal["sent", "failed", "skipped_cooldown", "no_recipient"]`, `detail: str`
  - `fall_message(at: datetime, still_s: float) -> str` returns exactly `f"FallGuard: possible fall detected at {at:%H:%M:%S}. Patient has been still for {still_s:.0f} s after a sudden movement."`
  - `class NodeSender(script: Path, recipient: str, *, command: Sequence[str] = ("node",), timeout_s: float = 20.0)`: `async def __call__(self, text: str) -> None`. Runs `[*command, script, recipient, text]` with `asyncio.create_subprocess_exec` and inherits the environment. Raises `RuntimeError(stderr tail)` on a non-zero exit or a timeout, killing the process on timeout.
  - `class AlertManager(sender: Callable[[str], Awaitable[None]] | None, cooldown_s: float, clock: Callable[[], float] = time.monotonic)`: `async def on_fall(self, at: datetime, still_s: float) -> AlertResult`.
    - The cooldown starts only after a successful send.
    - With `sender=None` it returns `no_recipient`.
- `send.mjs` contract: `node send.mjs <E.164 recipient> <text>`. It reads `SPECTRUM_PROJECT_ID` and `SPECTRUM_PROJECT_SECRET` from the environment, sends one iMessage using the snippet in the spec, calls `app.stop()`, and exits 0. On error it prints the message to stderr and exits 1.

- [ ] **Step 1: Write the failing tests.**
  - `test_fall_message_text`: `fall_message(datetime(2026,11,14,14,32,5), 8.0)` equals `"FallGuard: possible fall detected at 14:32:05. Patient has been still for 8 s after a sudden movement."`
  - `test_sends_then_cooldown_blocks`: with a fake async sender and a fake clock, the first call returns `sent` (sender called once). A call at +30 s returns `skipped_cooldown`, and one at +61 s returns `sent`.
  - `test_send_failure_reports_failed_and_no_cooldown`: the sender raises, so the result is `failed` with the error text in `detail`. An immediate retry calls the sender again.
  - `test_no_recipient`: `AlertManager(None, 60).on_fall(...)` returns `no_recipient`.
  - `test_node_sender_timeout`: `NodeSender(Path("x"), "+1", command=(sys.executable, "-c", "import time; time.sleep(5)"), timeout_s=0.2)` raises `RuntimeError` in under 2 s.
  - `test_node_sender_passes_args`: the command is a Python one-liner that writes `sys.argv[1:]` to a temp file. Assert it contains `["x", "+1555", "hi"]`.
- [ ] **Step 2: Run the tests and confirm they fail.** Run: `.venv/bin/pytest tests/test_alerts.py -q`. Expected: ImportError.
- [ ] **Step 3: Implement `alerts.py`.**
- [ ] **Step 4: Run the tests and confirm they pass.** Expected: 6 passed.
- [ ] **Step 5: Write `notify/send.mjs` and `package.json`** (`"type": "module"`, with dependency `spectrum-ts`). Run: `cd notify && npm install`. Expected: exit 0.
- [ ] **Step 6: Live send (needs a person).** Ask the user to put the real `SPECTRUM_*` values and `NURSE_PHONE` in `fallguard/.env`. Then run: `set -a; . ./.env; set +a; node notify/send.mjs "$NURSE_PHONE" "FallGuard test message"`.
  Expected: exit 0 and the nurse iPhone receives the message. The user confirms it arrived. Never print or commit the `.env` values.
- [ ] **Step 7: Commit.** `git commit -m "feat: add imessage alerts via photon"`

---

### Task 5: Server: wiring, WebSocket, logs

**Files:**
- Create: `fallguard/fallguard/server.py`, `fallguard/fallguard/__main__.py`, `fallguard/tests/test_server.py`

**Interfaces:**
- Consumes: `AppConfig`, `FallDetector`/`level_for`/`Status`, `MotionSample`/`BoardStatus`/`BoardHealth`, and `AlertManager`/`AlertResult` from Tasks 1–4.
- Produces:
  - `SampleSource = Callable[[], AsyncIterator[MotionSample | BoardStatus]]`
  - `HealthSource = Callable[[], Awaitable[list[BoardHealth]]]`
  - `create_app(config: AppConfig, sources: list[SampleSource], health: HealthSource | None, alerts: AlertManager, *, mode: Literal["live", "replay"] = "live", clock=time.monotonic, wall=datetime.now) -> FastAPI`
- Behavior:
  - On startup, start one task per source. Single-board path: `level = level_for(sample.score, config.rule.threshold)` (this ignores the board `state`) → `detector.update`.
  - Run a 0.25 s ticker that calls `detector.check_gap`, and a 2 s health poll.
  - When a Status has `new_fall`: broadcast `state` first, then broadcast `alert {"imessage": "sending"}`, then run `alerts.on_fall` as a background task and broadcast its result.
  - Every sample is appended to `data/sessions/<YYYYmmdd-HHMMSS>.jsonl` as `{"t","board","score","device_ms"}`.
- WebSocket `/ws` messages (JSON). On connect, send `config` and then the current `state`:
  - `{"type":"config","threshold":f,"burst_max_s":f,"still_s":f,"mode":"live"|"replay","boards":[ids]}`
  - `{"type":"sample","board":id,"t":f,"score":f}`
  - `{"type":"state","state":"FALL",...,"since":f,"burst_s":f|null,"still_s":f|null,"armed":bool}`
  - `{"type":"alert","imessage":"sending"|"sent"|"failed"|"skipped_cooldown"|"no_recipient","detail":str,"at":"HH:MM:SS"}`
  - `{"type":"board","board":id,"connected":bool,"pps":f|null,"occupancy":f|null,"ready":bool|null}`
- HTTP routes:
  - `POST /api/ack` → `detector.acknowledge`, then broadcast state.
  - `POST /api/arm {"armed":bool}`.
  - `POST /api/mark {"label":"fall"|"false_alarm"}` appends `{"wall":iso,"t":f,"label":..,"state":..}` to `data/marks.jsonl`. Any other label returns 422.
  - `GET /` serves `static/index.html`, and `/static/*` serves the static files.
- `__main__.py`: `python -m fallguard [--port N] [--replay PATH]`. It loads `.env` from the `fallguard/` dir with python-dotenv and builds `BoardClient`s from `config.boards`. It builds a `NodeSender(notify/send.mjs, NURSE_PHONE)`, or `None` when `NURSE_PHONE` is unset. It runs uvicorn on `127.0.0.1`.

- [ ] **Step 1: Write the failing tests.** Use `fastapi.testclient.TestClient` with scripted fake sources (an async generator that yields samples and sleeps 0), a fake clock, and `tmp_path` as `data_dir`.
  - `test_fall_flow_broadcasts_state_and_alert_once`: script 1 s of motion (score 0.99), then 9 s of still (0.01). Over the socket you receive `state` FALL exactly once, `alert` `sending`, then `alert` `sent`. The fake sender is called once.
  - `test_board_state_label_is_ignored`: samples with score 0.80 (below 0.962) never produce `state` BURST.
  - `test_new_socket_gets_current_status`: after FALL, a second socket's first two messages are `config` and `state` with `state == "FALL"`.
  - `test_alarm_broadcast_before_send_finishes`: a sender that awaits a never-set event. The `state` FALL message still arrives, and `alert` `sending` follows.
  - `test_ack_and_mark`: `POST /api/ack` → the state is MONITORING. `POST /api/mark {"label":"fall"}` → `marks.jsonl` has one line with `label == "fall"`. `{"label":"x"}` → 422.
  - `test_session_log_written`: after N samples, the session file has N JSON lines.
  - `test_disconnect_status_forwarded`: a `BoardStatus(connected=False)` from a source → a `board` message with `connected == false`.
- [ ] **Step 2: Run the tests and confirm they fail.** Run: `.venv/bin/pytest tests/test_server.py -q`. Expected: ImportError.
- [ ] **Step 3: Implement `server.py` and `__main__.py`.** Use a placeholder `static/index.html` (Task 7 replaces it).
- [ ] **Step 4: Run the full suite.** Run: `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Live check.** Run `.venv/bin/python -m fallguard` in the background, then `curl -s localhost:8000/ | head -3`. Session lines should appear in `data/sessions/`. Stop the server afterwards.
- [ ] **Step 6: Commit.** `git commit -m "feat: add fallguard server"`

---

### Task 6: Replay source (demo backup and tuning)

**Files:**
- Create: `fallguard/fallguard/replay.py`, `fallguard/tests/test_replay.py`
- Modify: `fallguard/fallguard/__main__.py` (`--replay` builds this source with `mode="replay"`)

**Interfaces:**
- Produces: `class ReplaySource(path: Path, *, speed: float = 1.0, sleep=asyncio.sleep, clock=time.monotonic)` and `def __call__(self) -> AsyncIterator[MotionSample | BoardStatus]`. It yields `BoardStatus(connected=True)` first. Then it yields each line of a session file as a `MotionSample`, keeping the original gaps divided by `speed` and re-stamping `t` with the current clock. It ends after the last line.

- [ ] **Step 1: Write the failing tests.**
  - `test_replay_preserves_order_and_gaps`: a 3-line file with `t` = 10.0, 10.25, 11.25, and a fake sleep that records its arguments. With `speed=1` the sleeps are `[0.25, 1.0]`; with `speed=2` they are `[0.125, 0.5]`. The scores come out in file order.
  - `test_replay_skips_bad_lines`: a malformed line is skipped, not fatal.
- [ ] **Step 2: Run the tests and confirm they fail, then implement, then confirm they pass.** Run: `.venv/bin/pytest tests/test_replay.py -q`. Expected: 2 passed.
- [ ] **Step 3: Commit.** `git commit -m "feat: add session replay"`

---

### Task 7: Dashboard ("signal explainer")

**Files:**
- Create: `fallguard/fallguard/static/index.html`, `app.js`, `style.css`

**Interfaces:**
- Consumes: the `/ws` messages and `/api/*` routes from Task 5. No new server code.
- Layout: option C from `.superpowers/brainstorm/*/content/dashboard-layouts.html`, plus option A's full-screen red overlay while the state is `FALL`.
  - The header shows "FallGuard", the status chips `Monitoring → Burst → FALL` (`MOVEMENT` shows amber "Movement", `NO_SIGNAL` shows grey "No signal"), and an **Armed** toggle.
  - The main area is a `<canvas>` graph of the last 60 s of score from 0 to 1, with a dashed threshold line from `config.threshold`. While the state is `BURST`, shade amber and label `burst {x.x} s`. While the state is `STILLNESS`, shade red and label `still {n}/{still_s} s`, with the countdown driven client-side from `since`. On `MOVEMENT`, stop shading.
  - The rule panel shows `burst ≤ 3 s ✓/✗ → stillness n/8 s`.
  - The iMessage panel shows the last `alert` status and time.
  - Buttons: **Acknowledge** (`POST /api/ack`), **Mark: fall**, **Mark: false alarm**.
  - A corner shows each board's id, connected dot, pps, and occupancy %.
  - When `config.mode == "replay"`, show a permanent "REPLAY" banner.
- The page reconnects the WebSocket every 2 s after it drops and shows "Disconnected from app" meanwhile.
- The page is readable from 3 m away: the status text is at least 32 px, and the FALL overlay text is at least 72 px.

- [ ] **Step 1: Build the page.**
- [ ] **Step 2: Verify with a replay.** Use the `run` skill and Chrome to drive it. Create a scripted session file: 2 s still (0.01), 1 s motion (0.99), 9 s still (0.01), 4 Hz. Run `.venv/bin/python -m fallguard --replay <file>` and open `http://127.0.0.1:8000`.
  Expected: the amber burst shading appears, then the red countdown reaches 8/8, then the full-screen FALL overlay and the iMessage panel show `no_recipient` (the replay is run without `NURSE_PHONE`). **Acknowledge** clears it. Take a screenshot of each stage.
- [ ] **Step 3: Commit.** `git commit -m "feat: add signal explainer dashboard"`

---

### Task 8 (stretch 1): Two-board fusion

**Files:**
- Create: `fallguard/fallguard/fusion.py`, `fallguard/tests/test_fusion.py`
- Modify: `fallguard/fallguard/server.py` (use `Fusion` when `len(config.boards) > 1`)

**Interfaces:**
- Produces: `class Fusion(boards: Sequence[str], threshold: float, window_s: float = 1.0)` with `update(sample: MotionSample) -> Level | None`. It returns `None` until every board has reported at least once. After that:
  - `"motion"` if every board had a score ≥ threshold within the last `window_s`;
  - `"still"` if every board's latest score is below the threshold;
  - `"mixed"` otherwise.

- [ ] **Step 1: Write the failing tests.**
  - `test_motion_needs_all_boards`: a above, b below → `"mixed"`. b above 0.5 s later → `"motion"`.
  - `test_still_needs_all_boards`.
  - `test_window_expires`: a above at t=0, b above at t=1.2 → `"mixed"`.
  - `test_none_until_all_reported`.
  - In `test_server.py`, `test_two_boards_one_noisy_no_fall`: board a scripts burst-then-still, b stays still → no FALL.
- [ ] **Step 2: Implement, then run the full suite.** Run: `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 3: Hardware pairing (needs a person).**
  1. Flash board 2 with the same release image, following the steps in the spec's "Current hardware state", and provision it.
  2. Set board A to `external` and board B to `wifi_raw` with `traffic_generator_target_mac` = A's MAC, using `./espectre direct patch sensing` from `espectre-main/`.
  3. Add `b2=` to `FALLGUARD_BOARDS`.
  Expected: both board dots are green and both graphs are live.
- [ ] **Step 4: Commit.** `git commit -m "feat: fuse two boards"`

---

### Task 9: Tuning and rehearsal (needs a person)

**Files:**
- Modify: `fallguard/.env` (values only; it is never committed)

- [ ] **Step 1:** Run live with the phone fixed on the table and the zone taped. Do 5 roll-overs and 5 falls onto the mat, pressing **Mark** after each.
- [ ] **Step 2:** Check the session file and `marks.jsonl`.
  Pass criteria: all 5 falls alert, and at most 1 of the 5 roll-overs alerts. If a criterion fails, adjust `FALLGUARD_THRESHOLD`, `FALLGUARD_BURST_MAX_S`, or `FALLGUARD_STILL_S` one at a time and repeat.
- [ ] **Step 3:** Keep one good session file as the replay backup for the demo.
- [ ] **Step 4:** `git commit -m "docs: record tuned defaults"`, after updating the defaults table in the spec with the tuned values (not `.env`).

ML (stretch 2) gets its own plan once Task 9 passes.
