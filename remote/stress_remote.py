"""Stress monitor remote: a phone-controlled stand-in for a WiCare (ESPectre) board.

WiCare's app reads each board as an SSE stream at <board>/events (event: motion, data: {timestamp_ms, state, score}).
This server speaks exactly that, so WiCare runs unchanged: point it here with
    FALLGUARD_BOARDS=b1=http://127.0.0.1:8100/espectre/v1      (in wicare/fallguard/.env)
and control the score from a phone at http://<laptop-ip>:8100/

Run:  python -m remote.stress_remote          (from the auracare folder)
"""
import asyncio, json, math, random, time

from fastapi import FastAPI
from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

RATE_S = 0.25                       # ESPectre streams about 4 samples a second
THRESHOLD = 0.962                   # WiCare's alert line
FALL = [(2.0, 1.4), (12.0, 0.06)]   # WiCare's rule: short burst (<= 3 s) then 8 s still -> possible fall

app = FastAPI(title='Stress monitor remote')
state = dict(level=0.30, script=[], started=time.time())


def current_score(now):
    """The level set from the phone, or the running script step, with a little sensor noise."""
    while state['script'] and now >= state['script'][0][0]:
        _, level = state['script'].pop(0)
        state['level'] = level
    level = state['level']
    noise = 0.0 if level < 0.1 else 0.03 * math.sin(now * 2.3) + random.uniform(-0.02, 0.02)
    return round(max(0.0, level + noise), 3)


@app.get('/espectre/v1/events')
async def events():
    async def stream():
        while True:
            now = time.time()
            score = current_score(now)
            data = dict(timestamp_ms=int((now - state['started']) * 1000),
                        state='motion' if score >= THRESHOLD else 'idle', score=score)
            yield f'event: motion\ndata: {json.dumps(data)}\n\n'
            await asyncio.sleep(RATE_S)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-store'})


@app.get('/espectre/v1/diagnostics')
def diagnostics(fields: str = ''):
    return dict(csi_admitted_pps=50, csi_occupancy=1.0)


@app.get('/espectre/v1/sensing')
def sensing():
    return dict(ready=True)


class Level(BaseModel):
    score: float


@app.post('/set')
def set_level(body: Level):
    state['script'] = []
    state['level'] = max(0.0, min(2.0, body.score))
    return dict(level=state['level'])


@app.post('/fall')
def fall():
    """Play a fall the way WiCare's rule sees one: burst, then stillness."""
    now, t, script = time.time(), 0.0, []
    for dur, level in FALL:
        script.append((now + t, level)); t += dur
    state['script'] = script
    return dict(playing=True, seconds=t)


@app.get('/score')
def score():
    return dict(score=current_score(time.time()), threshold=THRESHOLD, script=bool(state['script']))


WICARE_CSS = Path(__file__).resolve().parents[1] / 'wicare' / 'fallguard' / 'fallguard' / 'static' / 'style.css'


@app.get('/', response_class=HTMLResponse)
def page():
    return PAGE


@app.get('/static/style.css')
def wicare_css():
    """WiCare's own stylesheet, so this page looks like the rest of WiCare."""
    return FileResponse(WICARE_CSS, media_type='text/css', headers={'Cache-Control': 'no-cache'})


PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WiCare · Monitor control</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/style.css">
  <style>
    /* layout only; colours, type and buttons come from WiCare's style.css */
    .controls { display: flex; flex-wrap: wrap; gap: .75rem; margin-top: 1rem; }
    .controls .fall { border-color: var(--fall); color: var(--fall); font-weight: 700; }
    .level { width: 100%; height: 2.5rem; accent-color: var(--trace); margin-top: .5rem; }
    .status-word.hi { color: var(--fall); }
  </style>
</head>
<body>
  <header class="top">
    <h1>WiCare <span class="room">Bed 1 · monitor control</span></h1>
  </header>

  <main>
    <section class="status" aria-live="polite">
      <p id="v" class="status-word">Connecting…</p>
      <p id="note" class="status-note"></p>
    </section>

    <section class="trace" aria-label="Motion score control">
      <label for="s" class="status-note">Motion score (alert threshold 0.962)</label>
      <input id="s" class="level" type="range" min="0" max="2" step="0.01" value="0.3">
      <div class="controls">
        <button id="calm" type="button">Calm</button>
        <button id="still" type="button">Still</button>
        <button id="move" type="button">Moving</button>
        <button id="fall" type="button" class="fall">Fall (sudden movement, then stillness)</button>
      </div>
      <p class="axis"><span>0</span><span>2</span></p>
    </section>
  </main>

  <script>
    const $ = (i) => document.getElementById(i);
    const post = (p, b) => fetch(p, { method: 'POST', headers: { 'content-type': 'application/json' },
      body: b ? JSON.stringify(b) : undefined }).catch(() => { $('note').textContent = 'Not connected'; });
    const set = (x) => { $('s').value = x; post('/set', { score: +x }); };
    $('s').oninput = () => set($('s').value);
    $('calm').onclick = () => set(0.3);
    $('still').onclick = () => set(0.05);
    $('move').onclick = () => set(1.3);
    $('fall').onclick = () => post('/fall');
    async function poll() {
      try {
        const r = await (await fetch('/score')).json();
        const hi = r.score >= r.threshold;
        $('v').textContent = r.score.toFixed(2);
        $('v').classList.toggle('hi', hi);
        $('note').textContent = r.script ? 'Playing a fall: sudden movement, then stillness'
          : hi ? 'Above the alert threshold' : 'Below the alert threshold';
      } catch { $('note').textContent = 'Not connected'; }
      setTimeout(poll, 300);
    }
    poll();
  </script>
</body>
</html>
"""


if __name__ == '__main__':
    import socket, uvicorn
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try: s.connect(('10.255.255.255', 1)); ip = s.getsockname()[0]
        except OSError: ip = '127.0.0.1'
    print(f'Stress monitor remote: http://{ip}:8100/  (WiCare board endpoint: http://127.0.0.1:8100/espectre/v1)', flush=True)
    uvicorn.run(app, host='0.0.0.0', port=8100, log_level='warning')
