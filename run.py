"""Run the whole WiCare demo from this laptop:   python run.py

Starts, in one process:
  1. WiCare's own app (wicare/fallguard, unchanged): the Bed 1 dashboard, the fall rule, the Photon text to the nurse
     and the reader for the nurse's reply.                                       http://localhost:8000/
  2. The room view: when the nurse replies 1, WiCare sends its dashboard to /room. That address is pointed at our 3D
     room (frontend/, ?room=1) from here, so no WiCare file is edited.
  3. The monitor control (remote/stress_remote.py), which feeds WiCare's monitor in place of a board.
  4. A public link to the control (cloudflared), for the teammate on another laptop or network.

Settings come from .env here (CAREGIVER_ADDRESS, PHOTON_PROJECT_ID, PHOTON_PROJECT_API_KEY) or from
wicare/fallguard/.env (NURSE_PHONE, SPECTRUM_PROJECT_ID, SPECTRUM_PROJECT_SECRET).
Options: --boards (use real ESPectre boards instead of the control), --no-tunnel, --port, --cooldown.
"""
import argparse, asyncio, os, re, shutil, subprocess, sys, threading
from pathlib import Path

import uvicorn
from dotenv import dotenv_values
from starlette.responses import RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
WICARE_DIR = ROOT / 'wicare' / 'fallguard'
FRONTEND = ROOT / 'frontend'
CONTROL_PORT = 8100


def settings(args):
    """WiCare reads its settings from the environment; fill them from our .env when its own .env has none."""
    ours = dotenv_values(ROOT / '.env')
    theirs = dotenv_values(WICARE_DIR / '.env')
    for name, fallback in (('NURSE_PHONE', 'CAREGIVER_ADDRESS'), ('SPECTRUM_PROJECT_ID', 'PHOTON_PROJECT_ID'),
                           ('SPECTRUM_PROJECT_SECRET', 'PHOTON_PROJECT_API_KEY')):
        value = os.environ.get(name) or theirs.get(name) or ours.get(fallback)
        if value: os.environ[name] = value
    if not args.boards:                                        # the control stands in for the board
        os.environ['FALLGUARD_BOARDS'] = f'b1=http://127.0.0.1:{CONTROL_PORT}/espectre/v1'
    os.environ['FALLGUARD_COOLDOWN_S'] = str(args.cooldown)    # WiCare's default (60 s) silently skips repeat alerts
    os.environ['FALLGUARD_PORT'] = str(args.port)
    os.environ['WICARE_URL'] = f'http://127.0.0.1:{args.port}'


def attach_room_view(app):
    """Point WiCare's /room at our 3D room. Our routes go first, so WiCare's placeholder is never reached."""
    app.router.routes.insert(0, Mount('/room3d', app=StaticFiles(directory=FRONTEND, html=True), name='room3d'))
    app.router.routes.insert(0, Route('/room', lambda request: RedirectResponse('/room3d/?room=1')))


def open_tunnel(port, on_url):
    """cloudflared quick tunnel -> https://<random>.trycloudflare.com. Returns the process (or None)."""
    exe = shutil.which('cloudflared')
    if not exe:
        return None
    proc = subprocess.Popen([exe, 'tunnel', '--no-autoupdate', '--url', f'http://127.0.0.1:{port}'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def watch():
        for line in proc.stderr:
            m = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
            if m:
                on_url(m.group(0)); break
        for _ in proc.stderr: pass                             # keep draining so cloudflared never blocks
    threading.Thread(target=watch, daemon=True).start()
    return proc


async def serve(*servers):
    await asyncio.gather(*(s.serve() for s in servers))


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--boards', action='store_true', help='read real ESPectre boards (FALLGUARD_BOARDS) instead of the control')
    p.add_argument('--no-tunnel', action='store_true', help="don't open a public link to the control")
    p.add_argument('--port', type=int, default=8000)
    p.add_argument('--cooldown', type=float, default=5, help='seconds between alerts')
    args = p.parse_args()

    settings(args)
    sys.path.insert(0, str(WICARE_DIR))
    sys.path.insert(0, str(ROOT))
    from fallguard.__main__ import build_app, lan_address      # WiCare, as written by the team
    config, app = build_app()
    attach_room_view(app)
    servers = [uvicorn.Server(uvicorn.Config(app, host=config.host, port=args.port, log_level='warning'))]

    print(f'\nWiCare dashboard (this laptop):  http://localhost:{args.port}/')
    print(f'All rooms:                       http://localhost:{args.port}/ward')
    print(f'Room view (opens on reply 1):    http://localhost:{args.port}/room')
    print(f"Nurse phone: {'set' if config.nurse_phone else 'NOT SET (no text will be sent)'}")
    tunnel = None
    if not args.boards:
        from remote import stress_remote
        servers.append(uvicorn.Server(uvicorn.Config(stress_remote.app, host='0.0.0.0', port=CONTROL_PORT, log_level='warning')))
        key = stress_remote.KEY
        print(f'\nControl (this laptop):           http://localhost:{CONTROL_PORT}/?k={key}')
        print(f'Control (same Wi-Fi):            http://{lan_address()}:{CONTROL_PORT}/?k={key}')
        if not args.no_tunnel:
            tunnel = open_tunnel(CONTROL_PORT, lambda url: print(f'\n>>> Send this link to your teammate:  {url}/?k={key}\n', flush=True))
            if tunnel is None: print('cloudflared not found: no public link (brew install cloudflared)')
            else: print('Opening a public link for your teammate...', flush=True)
    print(flush=True)
    try:
        asyncio.run(serve(*servers))
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel: tunnel.terminate()


if __name__ == '__main__':
    main()
