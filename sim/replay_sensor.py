"""Fake ESPectre /espectre/v1/events stream from WiFall CSV files (for testing without the board,
or for the labelled replay view). Run: PLAYLIST=a.csv,b.csv uvicorn sim.replay_sensor:app --port 62587
Always ingest with --source wifall_replay."""
import os, json, time, asyncio
import numpy as np, pandas as pd
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()
FILES = [f for f in os.environ.get('PLAYLIST', '').split(',') if f]
WIN = 40                                   # ~0.5 s at ~83 Hz
state = {'online': True}

def amps(s):
    a = np.array(s.strip('[]').split(','), dtype=np.float32)
    return np.hypot(a[0::2], a[1::2]) if a.size == 104 else None

def packets():
    while True:
        for f in FILES:
            df = pd.read_csv(f, usecols=['local_timestamp', 'data'])
            for ts, d in zip(df.local_timestamp.values, df.data.values):
                a = amps(d)
                if a is not None:
                    yield int(ts), a, f

async def stream():
    turb, prev, n = [], None, 0
    for ts, a, f in packets():
        while not state['online']:
            await asyncio.sleep(0.2)
        dt = 0 if prev is None else min(((ts - prev) % 2**32) / 1e6, 0.1)
        prev = ts
        await asyncio.sleep(dt)
        turb = (turb + [float(a.std())])[-WIN:]
        n += 1
        if n % 8 == 0 and len(turb) == WIN:
            p = {'score': float(np.std(turb)), 'source': 'wifall_replay', 'recording': os.path.basename(f)}
            yield f'event: motion\ndata: {json.dumps(p)}\n\n'

@app.get('/espectre/v1/events')
async def events():
    return StreamingResponse(stream(), media_type='text/event-stream')

@app.post('/sim/outage/{on}')
def outage(on: int):
    state['online'] = not on
    return state
