"""Monitor control: a stand-in for a WiCare (ESPectre) board, driven from a web page.

WiCare reads each board as an SSE stream at <board>/events (event: motion, data: {timestamp_ms, state, score}).
This server speaks exactly that, so WiCare runs unchanged with
    FALLGUARD_BOARDS=b1=http://127.0.0.1:8100/espectre/v1
`python run.py` (one folder up) starts WiCare, this control and a public link for the person holding the control.

How WiCare decides to alert (its rule, not ours): a short burst of strong movement (score >= 0.962 for at most 3 s)
followed by 8 s of stillness. So "push it high" here always plays that shape: high for 2 s, then still. Holding the
score high would read as ordinary movement and never alert.
"""
import asyncio, json, math, os, random, secrets, time, urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

RATE_S = 0.25                       # ESPectre streams about 4 samples a second
THRESHOLD = 0.962                   # WiCare's alert line
CALM, STILL, SPIKE = 0.08, 0.04, 1.35
SPIKE_S, STILL_S, LOCK_S = 2.0, 8.0, 14.0    # spike, WiCare's stillness wait, then the control unlocks
KEY = os.getenv('REMOTE_KEY') or secrets.token_urlsafe(6)        # in the control link, so only its holder can drive it
WICARE = os.getenv('WICARE_URL', 'http://127.0.0.1:8000')

app = FastAPI(title='WiCare monitor control')
state = dict(level=CALM, started=time.time(), seq_at=None)


def _check(k):
    if k != KEY:
        raise HTTPException(403, 'wrong or missing key: open the full control link')


def phase(now):
    """None when idle; otherwise ('spike' | 'still' | 'sent', seconds left in that step)."""
    if state['seq_at'] is None:
        return None
    e = now - state['seq_at']
    if e < SPIKE_S: return 'spike', SPIKE_S - e
    if e < SPIKE_S + STILL_S: return 'still', SPIKE_S + STILL_S - e
    if e < LOCK_S: return 'sent', LOCK_S - e
    state['seq_at'], state['level'] = None, CALM
    return None


def current_score(now):
    ph = phase(now)
    if ph and ph[0] == 'spike':
        return round(SPIKE + 0.08 * math.sin(now * 9) + random.uniform(-0.04, 0.04), 3)
    if ph:
        return round(max(0.0, STILL + random.uniform(-0.012, 0.012)), 3)
    level = state['level']
    wobble = 0.03 * math.sin(now * 0.9) + 0.02 * math.sin(now * 2.7) + random.uniform(-0.015, 0.015)
    return round(max(0.0, min(THRESHOLD - 0.05, level + wobble * (1 + level * 3))), 3)   # idle never crosses the line


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


def _start_sequence():
    if phase(time.time()) is None:
        state['seq_at'] = time.time()
    return dict(sequence=True)


@app.post('/set')
def set_level(body: Level, k: str = ''):
    _check(k)
    if phase(time.time()):
        return dict(locked=True)
    if body.score >= THRESHOLD:                 # pushed over the line: play the shape WiCare alerts on
        return _start_sequence()
    state['level'] = max(0.0, body.score)
    return dict(level=state['level'])


@app.post('/fall')
def fall(k: str = ''):
    _check(k)
    return _start_sequence()


@app.post('/reset')
def reset(k: str = ''):
    """Clear WiCare's alarm (same as pressing Acknowledge on its dashboard) and go back to calm."""
    _check(k)
    state['seq_at'], state['level'] = None, CALM
    try:
        urllib.request.urlopen(urllib.request.Request(WICARE + '/api/ack', data=b'', method='POST'), timeout=3)
        return dict(reset=True)
    except OSError as e:
        return dict(reset=False, detail=str(e))


@app.get('/score')
def score():
    now = time.time()
    ph = phase(now)
    return dict(score=current_score(now), threshold=THRESHOLD, level=state['level'],
                phase=ph[0] if ph else None, left=round(ph[1], 1) if ph else 0)


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
  <title>WiCare · Bed 1 control</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/style.css">
  <style>
    /* layout only; colours, type and buttons come from WiCare's style.css */
    .controls { display: flex; flex-wrap: wrap; gap: .75rem; margin-top: 1rem; }
    .controls .go { border-color: var(--fall); background: var(--fall); color: #fff; font-weight: 700; font-size: 1.25rem; padding: .9rem 1.6rem; }
    .controls .go:disabled { opacity: .5; cursor: default; }
    .level { width: 100%; height: 2.5rem; accent-color: var(--trace); margin-top: .5rem; }
    .status-word.hi { color: var(--fall); }
    .how { margin-top: 1.25rem; color: var(--quiet); font-size: 1rem; line-height: 1.5; max-width: 60ch; }
  </style>
</head>
<body>
  <div id="link-banner" class="link-banner" hidden>This link is missing its key. Open the full control link.</div>
  <header class="top">
    <h1>WiCare <span class="room">Bed 1 · control</span></h1>
  </header>

  <main>
    <section class="status" aria-live="polite">
      <p id="v" class="status-word">Connecting…</p>
      <p id="note" class="status-note"></p>
    </section>

    <section class="trace" aria-label="Motion score control">
      <label for="s" class="status-note">Movement level (alert line at 0.96)</label>
      <input id="s" class="level" type="range" min="0" max="1.2" step="0.01" value="0.08">
      <p class="axis"><span>still</span><span>over the line = alert</span></p>
      <div class="controls">
        <button id="go" type="button" class="go">Send alert</button>
        <button id="calm" type="button">Calm</button>
        <button id="move" type="button">Moving a little</button>
        <button id="reset" type="button">Reset alarm</button>
      </div>
    </section>
    <p class="how">Send alert plays a sudden movement for 2 s, then stillness. About 10 s after you press it the
      dashboard shows Possible fall and the nurse's phone gets the text. Dragging the slider over the line does the same.
      Press Reset alarm before sending another one, unless the nurse already replied.</p>
  </main>

  <script>
    const $ = (i) => document.getElementById(i);
    const k = new URLSearchParams(location.search).get('k') || '';
    if (!k) $('link-banner').hidden = false;
    let dragging = false;
    const post = (p, b) => fetch(p + '?k=' + encodeURIComponent(k), { method: 'POST', headers: { 'content-type': 'application/json' },
      body: b ? JSON.stringify(b) : undefined }).then((r) => { if (r.status === 403) $('link-banner').hidden = false; })
      .catch(() => { $('note').textContent = 'Not connected'; });
    const set = (x) => post('/set', { score: +x });
    $('s').oninput = () => { dragging = true; set($('s').value); };
    $('s').onchange = () => { dragging = false; };
    $('calm').onclick = () => set(0.08);
    $('move').onclick = () => set(0.5);
    $('go').onclick = () => post('/fall');
    $('reset').onclick = () => post('/reset');
    async function poll() {
      try {
        const r = await (await fetch('/score')).json();
        $('v').textContent = r.score.toFixed(2);
        $('v').classList.toggle('hi', r.score >= r.threshold);
        $('go').disabled = !!r.phase; $('s').disabled = !!r.phase;
        if (!dragging || r.phase) $('s').value = r.phase ? (r.phase === 'spike' ? 1.2 : 0.04) : r.level;
        $('note').textContent = r.phase === 'spike' ? 'Sudden movement…'
          : r.phase === 'still' ? 'Stillness. Text goes out in ' + Math.ceil(r.left) + ' s'
          : r.phase === 'sent' ? 'Alert sent to the phone (if the alarm was clear). Ready again in ' + Math.ceil(r.left) + ' s'
          : 'Ready';
      } catch { $('note').textContent = 'Not connected'; }
      setTimeout(poll, 300);
    }
    poll();
  </script>
</body>
</html>
"""


if __name__ == '__main__':
    import uvicorn
    print(f'Control page: http://127.0.0.1:8100/?k={KEY}', flush=True)
    uvicorn.run(app, host='0.0.0.0', port=8100, log_level='warning')
