/**
 * AuraCare ward: 5 hospital bays in 3D, one stress monitor per room.
 *
 * Data: polls GET /ward/state on the AuraCare backend (?api=http://host:8000, default same host :8000).
 *   The other rooms drift at calm levels (simulated). The live room (backend WARD_LIVE_ROOM, default 3) is set from
 *   /ward/control (a teammate's phone). Live room high for 3 s -> backend texts the nurse "check on them? 1 YES / 2 NO".
 * Privacy: the overview shows closed rooms only (roofs, walls, doors), and nobody is drawn inside. Only after the nurse
 *   replies 1 (or presses "Open room") does that one room open: the roof lifts, the front wall lowers, the camera goes
 *   in and the wake-up script plays: lying in bed 0-5 s, sits up at 5 s, gets up at 11 s, walks from 15 s.
 * No backend: runs a local simulation (keys: H = live room high, C = calm, O = open the room, B = back).
 */
import * as THREE from 'three';
import { PoseSystem, WAKE_BED } from '../observatory/js/pose-system.js';

const Q = new URLSearchParams(location.search);
const API = Q.get('api') || `http://${location.hostname || 'localhost'}:8000`;
const ROOMS = Math.max(1, Math.min(5, parseInt(Q.get('rooms'), 10) || 1)), HIGH = 70, LOW = 50, H = 2.6, FRONT = 0.35;   // ?rooms=5 for the ward
let LIVE = Math.min(ROOMS, parseInt(Q.get('room'), 10) || 1);   // replaced by the backend's live_room on the first poll
const BAY = 3.6, BED_Z = -2.4;
const bayX = (r) => (r - (ROOMS + 1) / 2) * BAY;
const HALL = ROOMS * BAY + 2.4;            // corridor width
const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------- renderer + camera
const canvas = $('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xdfe8ec);
scene.fog = new THREE.Fog(0xdfe8ec, 22, 40);

const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
const cam = { pos: new THREE.Vector3(), target: new THREE.Vector3(), goalPos: new THREE.Vector3(), goalTarget: new THREE.Vector3() };

function overviewGoal() {
  const narrow = innerWidth / innerHeight < 1.2;
  const k = ROOMS === 1 ? 0.48 : 1;                        // one room: come closer
  cam.goalPos.set(0, (narrow ? 15 : 9.6) * k + (ROOMS === 1 ? 1.2 : 0), (narrow ? 24 : 15.5) * k + (ROOMS === 1 ? 2 : 0));
  cam.goalTarget.set(0, narrow ? 0.6 : ROOMS === 1 ? 1.0 : 0.2, -1.2);
}
function roomGoal(r) {
  const x = bayX(r), narrow = innerWidth < 900;
  // subject sits left of centre on wide screens (the detail panel is on the right)
  cam.goalPos.set(x + (narrow ? -0.6 : 0.5), 3.9, BED_Z + 7.4);
  cam.goalTarget.set(x + (narrow ? 0 : 1.75), narrow ? 0.2 : 0.55, BED_Z + 0.7);
}
overviewGoal();
cam.pos.copy(cam.goalPos); cam.target.copy(cam.goalTarget);

function resize() {
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  if (!viewing) overviewGoal(); else roomGoal(LIVE);
}

// ---------------------------------------------------------------- lights
scene.add(new THREE.HemisphereLight(0xffffff, 0xb8c7cc, 1.25));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(-6, 12, 8);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -14, right: 14, top: 8, bottom: -8, near: 1, far: 40 });
sun.shadow.bias = -0.0004;
scene.add(sun);

// ---------------------------------------------------------------- materials + helpers
const M = (color, o = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.6, metalness: 0, ...o });
const mat = {
  floor: M(0xcdd8dc, { roughness: 0.85 }), wall: M(0xeaf1f0, { roughness: 0.95 }), stripe: M(0xb7d3cf, { roughness: 0.9 }),
  rail: M(0x9fb6b5, { metalness: 0.3 }), frame: M(0xf1f4f5, { metalness: 0.35, roughness: 0.35 }),
  steel: M(0xaab4b8, { metalness: 0.8, roughness: 0.3 }), dark: M(0x2a3438, { roughness: 0.5 }),
  mattress: M(0xfafcfc, { roughness: 0.9 }), sheet: M(0x7fb2cf, { roughness: 0.95 }), pillow: M(0xffffff, { roughness: 1 }),
  head: M(0xd6e4e7, { roughness: 0.7 }), bay: M(0xc4d2d6, { roughness: 0.9 }),
  curtain: new THREE.MeshStandardMaterial({ color: 0x8fcac4, roughness: 0.9, transparent: true, opacity: 0.6, side: THREE.DoubleSide }),
  iv: new THREE.MeshStandardMaterial({ color: 0xe8f6ff, roughness: 0.2, transparent: true, opacity: 0.7 }),
  wood: M(0xc9ad8a, { roughness: 0.7 }),
  partition: M(0xe3ecec, { roughness: 0.9 }), front: M(0xf2f6f6, { roughness: 0.85 }), door: M(0x9cc9c4, { roughness: 0.6 }),
  handle: M(0x7d8a8e, { metalness: 0.8, roughness: 0.3 }),
};
function box(w, h, d, m, x, y, z, shadow = true, parent = scene) {
  const o = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
  o.position.set(x, y, z); o.castShadow = shadow; o.receiveShadow = true; parent.add(o); return o;
}
function cyl(r, h, m, x, y, z) {
  const o = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 12), m);
  o.position.set(x, y, z); o.castShadow = true; scene.add(o); return o;
}
function canvasPlane(w, h, cw, ch, parent = scene) {
  const c = document.createElement('canvas'); c.width = cw; c.height = ch;
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
  parent.add(mesh); return { c, ctx: c.getContext('2d'), tex, mesh };
}

// ---------------------------------------------------------------- the ward
{
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 20), mat.floor);
  floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
  const grid = new THREE.GridHelper(40, 66, 0xb9c7cc, 0xc1ced2);
  grid.position.y = 0.002; grid.material.transparent = true; grid.material.opacity = 0.5; scene.add(grid);
  box(HALL, 3.2, 0.2, mat.wall, 0, 1.6, -4.2, false);                    // back wall
  box(HALL, 0.9, 0.04, mat.stripe, 0, 0.45, -4.08, false);              // wainscot
  box(HALL, 0.06, 0.1, mat.rail, 0, 0.95, -4.03, false);                // handrail
  box(0.2, 3.2, 7, mat.wall, -HALL / 2, 1.6, -0.9, false);              // side walls
  box(0.2, 3.2, 7, mat.wall, HALL / 2, 1.6, -0.9, false);
  box(HALL, 0.04, 0.6, mat.stripe, 0, 0.002, 1.6, false);              // aisle line
}

// solid walls between rooms (private rooms, not curtained bays)
for (let k = 0; k <= ROOMS; k++) box(0.14, H, FRONT + 4.2, mat.partition, bayX(k + 0.5), H / 2, (FRONT - 4.2) / 2, true);

// per-room furniture; returns the bedside monitor's canvas
function buildBay(r) {
  const x = bayX(r), z = BED_Z;
  box(3.3, 0.01, 3.4, mat.bay, x, 0.006, z + 0.5, false);
  // bed
  box(1.02, 0.1, 2.12, mat.frame, x, 0.4, z);
  for (const [dx, dz] of [[-0.45, -0.95], [0.45, -0.95], [-0.45, 0.95], [0.45, 0.95]]) {
    cyl(0.025, 0.32, mat.steel, x + dx, 0.2, z + dz);
    const w = new THREE.Mesh(new THREE.SphereGeometry(0.05, 10, 8), mat.dark); w.position.set(x + dx, 0.05, z + dz); scene.add(w);
  }
  box(0.94, 0.12, 2.02, mat.mattress, x, 0.51, z);
  box(0.98, 0.05, 1.2, mat.sheet, x, 0.585, z + 0.42);
  box(0.62, 0.1, 0.34, mat.pillow, x, 0.62, z - 0.8);
  box(1.04, 0.62, 0.06, mat.head, x, 0.72, z - 1.09);
  box(1.04, 0.4, 0.06, mat.head, x, 0.62, z + 1.09);
  box(0.03, 0.12, 0.9, mat.steel, x + 0.52, 0.68, z - 0.35);           // side rail (far side)
  // bedside cabinet + monitor on a pole
  box(0.42, 0.62, 0.42, mat.wood, x + 0.95, 0.31, z - 0.55);
  cyl(0.02, 1.35, mat.steel, x + 0.95, 1.05, z - 0.95);
  box(0.56, 0.4, 0.08, mat.dark, x + 0.95, 1.72, z - 0.95);
  const screen = canvasPlane(0.5, 0.34, 256, 174);
  screen.mesh.position.set(x + 0.95, 1.72, z - 0.905);
  // IV pole
  cyl(0.015, 1.9, mat.steel, x - 0.8, 0.95, z - 1.0);
  box(0.4, 0.015, 0.015, mat.steel, x - 0.8, 1.9, z - 1.0);
  box(0.14, 0.22, 0.05, mat.iv, x - 0.68, 1.74, z - 1.0);
  cyl(0.18, 0.03, mat.steel, x - 0.8, 0.02, z - 1.0);
  return screen;
}
const screens = {};
for (let r = 1; r <= ROOMS; r++) screens[r] = buildBay(r);

// closed-room shell: a roof and a front wall with a door. Nobody can see inside until the room is opened.
const W = BAY - 0.14, DEPTH = FRONT + 4.2, ZC = (FRONT - 4.2) / 2;
function buildShell(r) {
  const x = bayX(r);
  const roof = new THREE.Group(), front = new THREE.Group();
  const roofMat = M(0xd9e4e6, { roughness: 0.8, emissive: 0xd64545, emissiveIntensity: 0 });
  box(W + 0.14, 0.12, DEPTH + 0.1, roofMat, x, H + 0.06, ZC, true, roof);
  const label = canvasPlane(2.6, 1.3, 512, 256, roof);
  label.mesh.rotation.x = -Math.PI / 2; label.mesh.position.set(x, H + 0.125, ZC + 0.4);
  // front wall: door on the left, sign + outside stress screen on the right
  const dx0 = x - 1.4, dx1 = x - 0.4, L = x - W / 2, R = x + W / 2, dh = 2.05;
  box(dx0 - L, H, 0.12, mat.front, (L + dx0) / 2, H / 2, FRONT, true, front);
  box(R - dx1, H, 0.12, mat.front, (dx1 + R) / 2, H / 2, FRONT, true, front);
  box(dx1 - dx0, H - dh, 0.12, mat.front, (dx0 + dx1) / 2, dh + (H - dh) / 2, FRONT, true, front);
  box(dx1 - dx0 - 0.04, dh - 0.02, 0.06, mat.door, (dx0 + dx1) / 2, dh / 2, FRONT + 0.02, true, front);
  box(0.04, 0.16, 0.05, mat.handle, dx1 - 0.14, 1.0, FRONT + 0.08, false, front);
  box(0.03, 0.03, 0.12, mat.handle, dx1 - 0.14, 1.07, FRONT + 0.1, false, front);
  const sign = canvasPlane(0.9, 0.36, 256, 102, front);
  sign.mesh.position.set(x + 0.55, 2.2, FRONT + 0.065);
  box(0.58, 0.42, 0.04, mat.dark, x + 0.55, 1.5, FRONT + 0.075, false, front);
  const outside = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.34), screens[r].mesh.material);
  outside.position.set(x + 0.55, 1.5, FRONT + 0.1); front.add(outside);
  scene.add(roof, front);
  return { roof, front, roofMat, label, sign, lift: 0, drop: 0, key: '' };
}
const shells = {};
for (let r = 1; r <= ROOMS; r++) shells[r] = buildShell(r);

function paintShell(r, level, mode) {
  const sh = shells[r], key = `${level}|${mode}|${r === LIVE}`;
  if (sh.key === key) return; sh.key = key;
  const hot = level === 'high', col = hot ? '#d64545' : r === LIVE ? '#0f8b8d' : '#2f4a52';
  const g = sh.label.ctx; g.clearRect(0, 0, 512, 256);
  g.fillStyle = 'rgba(255,255,255,.55)'; g.beginPath(); g.roundRect(8, 8, 496, 240, 28); g.fill();
  g.fillStyle = col; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.font = '700 92px "Space Grotesk", sans-serif'; g.fillText(`ROOM ${r}`, 256, 110);
  g.font = '600 38px "Space Grotesk", sans-serif';
  g.fillText(mode === 'asked' ? 'NURSE ASKED' : hot ? 'STRESS HIGH' : 'PRIVATE', 256, 196);
  sh.label.tex.needsUpdate = true;
  const s = sh.sign.ctx; s.clearRect(0, 0, 256, 102);
  s.fillStyle = col; s.beginPath(); s.roundRect(0, 0, 256, 102, 18); s.fill();
  s.fillStyle = '#fff'; s.font = '600 54px "Space Grotesk", sans-serif'; s.textAlign = 'center'; s.textBaseline = 'middle';
  s.fillText(`Room ${r}`, 128, 54); sh.sign.tex.needsUpdate = true;
}
for (let r = 1; r <= ROOMS; r++) paintShell(r, 'calm', '');

// ---------------------------------------------------------------- stick-figure patients
const BONES = [[5, 6], [5, 7], [7, 9], [6, 8], [8, 10], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]];
const UP = new THREE.Vector3(0, 1, 0);
class Figure {
  constructor(color) {
    const m = M(color, { roughness: 0.45, emissive: color, emissiveIntensity: 0.15 });
    this.joints = Array.from({ length: 17 }, (_, i) => {
      const s = new THREE.Mesh(new THREE.SphereGeometry(i === 0 ? 0.11 : 0.04, 14, 10), m);
      s.castShadow = true; scene.add(s); if (i > 0 && i < 5) s.visible = false; return s;
    });
    this.bones = BONES.map(() => {
      const c = new THREE.Mesh(new THREE.CylinderGeometry(0.032, 0.032, 1, 8), m); c.castShadow = true; scene.add(c); return c;
    });
    this.neck = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 1, 8), m); this.neck.castShadow = true; scene.add(this.neck);
  }
  setVisible(v) { for (const o of [...this.joints, ...this.bones, this.neck]) o.visible = v && !(this.joints.indexOf(o) > 0 && this.joints.indexOf(o) < 5); }
  set(kps) {
    kps.forEach((p, i) => this.joints[i].position.set(p[0], p[1], p[2]));
    BONES.forEach(([a, b], i) => this._bone(this.bones[i], kps[a], kps[b]));
    const sh = [(kps[5][0] + kps[6][0]) / 2, (kps[5][1] + kps[6][1]) / 2, (kps[5][2] + kps[6][2]) / 2];
    this._bone(this.neck, sh, kps[0]);
  }
  _bone(mesh, a, b) {
    const va = new THREE.Vector3(...a), vb = new THREE.Vector3(...b), d = vb.clone().sub(va), len = d.length() || 1e-3;
    mesh.position.copy(va).addScaledVector(d, 0.5);
    mesh.quaternion.setFromUnitVectors(UP, d.normalize());
    mesh.scale.set(1, len, 1);
  }
}

PoseSystem.frail = true;
const poses = new PoseSystem();
// pose-system's wake-up keypoints are built around its own bed; map that bed onto ours (head toward the wall)
const CX = 3.5 + WAKE_BED.dx, CZ = -3.5 + WAKE_BED.dz;
function toBay(kps, r) {
  const bx = bayX(r);
  return kps.map(([x, y, z]) => [bx - (z - CZ), y, BED_Z + (x - CX)]);
}

// ---------------------------------------------------------------- room 5 script
const SCRIPT = { sit: 5, stand: 11, walk: 15, fly: 1.6 };
const WALK_SPEED = 0.45;                                   // m/s, older adult
// local bed frame: +x = toward the foot, +z = the side the patient gets out on
const START = [0.05, 1.0], LOOP = [[1.45, 1.0], [2.0, 0.35], [2.0, -0.6], [1.45, -1.1], [1.3, -0.05], [1.45, 1.0]];
const ss = (a, b, t) => { const x = Math.min(1, Math.max(0, (t - a) / (b - a))); return x * x * (3 - 2 * x); };

function along(path, d) {
  for (let i = 1; i < path.length; i++) {
    const [ax, az] = path[i - 1], [bx, bz] = path[i], L = Math.hypot(bx - ax, bz - az);
    if (d <= L || i === path.length - 1) { const f = Math.min(1, d / L); return [ax + (bx - ax) * f, az + (bz - az) * f, Math.atan2(bx - ax, -(bz - az))]; }
    d -= L;
  }
}
const plen = (p) => p.slice(1).reduce((s, q, i) => s + Math.hypot(q[0] - p[i][0], q[1] - p[i][1]), 0);
const FIRST = [START, LOOP[0]], L0 = plen(FIRST), LC = plen(LOOP);

let facingNow = 0;
function scriptFrame(s, elapsed, bp, dt) {
  if (s < SCRIPT.walk) {
    let wake = 0, act = 'Lying in bed', step = 0;
    if (s >= SCRIPT.sit && s < SCRIPT.stand) { wake = ss(SCRIPT.sit, SCRIPT.sit + 1.8, s); act = 'Sitting up in bed'; step = 1; }
    else if (s >= SCRIPT.stand) {
      const e = s - SCRIPT.stand; wake = 1 + ss(0, 1.4, e) + ss(1.6, 3.0, e);
      act = wake < 2 ? 'Getting out of bed' : wake < 3 ? 'Standing up' : 'Standing beside the bed'; step = 2;
    }
    facingNow = 0;
    return { kps: poses.generateKeypoints({ pose: 'waking', wake }, elapsed, bp), act, step };
  }
  const d = (s - SCRIPT.walk) * WALK_SPEED;
  const [lx, lz, face] = d < L0 ? along(FIRST, d) : along(LOOP, (d - L0) % LC);
  // turn smoothly instead of snapping at corners
  let diff = face - facingNow; diff = Math.atan2(Math.sin(diff), Math.cos(diff));
  facingNow += diff * Math.min(1, dt * 5);
  const kps = poses.generateKeypoints({ pose: 'walking', position: [CX + lx, 0, CZ + lz], facing: facingNow, motion_score: 70 }, elapsed, bp);
  return { kps, act: 'Walking around the room', step: 3 };
}

// one patient figure, drawn only inside an opened room (privacy: nobody is rendered in the overview)
const patient = new Figure(0x0f8b8d);
patient.setVisible(false);

// ---------------------------------------------------------------- data: backend or local simulation
let state = null, lastOk = 0, offline = false, viewing = false, viewT0 = 0;
const local = { target: 30, value: 30, series: {}, alert: null, armed: true, highSince: null, seed: [11, 47, 83, 29, 64] };
const calm = (r, t) => Math.max(5, Math.min(60, 32 + 7 * Math.sin(t / 9 + local.seed[r - 1]) + 4 * Math.sin(t / 3.7 + local.seed[r - 1] * 2) + 2 * Math.sin(t * 1.3 + local.seed[r - 1])));

function localTick() {
  const t = Date.now() / 1000;
  local.value += (local.target - local.value) * 0.5;
  const rooms = [];
  for (let r = 1; r <= ROOMS; r++) {
    const v = r === LIVE ? Math.max(0, Math.min(100, local.value + 1.5 * Math.sin(t * 1.7))) : calm(r, t);
    (local.series[r] ||= []).push(v); local.series[r] = local.series[r].slice(-90);
    rooms.push({ room: r, value: v, series: local.series[r], source: r === LIVE ? 'control' : 'simulated', level: v >= HIGH ? 'high' : v >= LOW ? 'elevated' : 'calm' });
  }
  const v5 = rooms[LIVE - 1].value;
  if (v5 < LOW) { local.armed = true; local.highSince = null; if (local.alert?.state === 'dismissed') local.alert = null; }
  else if (v5 >= HIGH && local.armed && !local.alert) {
    local.highSince ||= t;
    if (t - local.highSince >= 3) { local.alert = { room: LIVE, state: 'asked', value: Math.round(v5) }; local.armed = false; }
  }
  state = { rooms, alert: local.alert, messages: [], high: HIGH, low: LOW, offline: true };
  render();
}

async function poll() {
  try {
    const r = await fetch(`${API}/ward/state`, { signal: AbortSignal.timeout(2000) });
    if (r.ok) { state = await r.json(); lastOk = Date.now(); offline = false; render(); }
  } catch { /* backend down */ }
  if (Date.now() - lastOk > 4000) { offline = true; }
  setTimeout(poll, 600);
}
setInterval(() => { if (offline) localTick(); }, 1000);

async function post(path) {
  if (offline) {
    if (path === '/ward/view') local.alert = { room: LIVE, state: 'viewing', value: Math.round(local.value) };
    if (path === '/ward/close') { local.alert = null; local.armed = local.value < LOW; }
    return localTick();
  }
  try { await fetch(`${API}${path}`, { method: 'POST' }); } catch { /* ignore */ }
  lastOk = 0; poll();
}

// ---------------------------------------------------------------- 2D overlay
const mons = $('monitors');
mons.style.gridTemplateColumns = `repeat(${ROOMS}, minmax(0, ${ROOMS === 1 ? '360px' : '1fr'}))`;
if (ROOMS === 1) mons.style.justifyContent = 'center';
$('ward-name').textContent = ROOMS === 1 ? 'Room 1 · private room' : `Ward 3B · ${ROOMS} rooms`;
for (let r = 1; r <= ROOMS; r++) {
  const el = document.createElement('div');
  el.className = 'mon'; el.id = `mon-${r}`;
  el.innerHTML = `<div class="mon-top"><span class="mon-room">Room ${r}</span><span>STRESS</span></div>
    <div class="mon-val">--</div><canvas width="300" height="70"></canvas>
    <div class="mon-foot"><span class="lvl">--</span>${r === LIVE ? '<span class="tag">REMOTE</span>' : '<span>simulated</span>'}</div>`;
  mons.appendChild(el);
}

function chart(ctx, w, h, series, opts = {}) {
  ctx.clearRect(0, 0, w, h);
  const y = (v) => h - 4 - (v / 100) * (h - 8);
  ctx.setLineDash([4, 4]); ctx.strokeStyle = 'rgba(255,107,107,.45)'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, y(HIGH)); ctx.lineTo(w, y(HIGH)); ctx.stroke(); ctx.setLineDash([]);
  if (!series || series.length < 2) return;
  const n = opts.n || 60, s = series.slice(-n), step = w / (n - 1), x0 = w - (s.length - 1) * step;
  const last = s[s.length - 1];
  const col = last >= HIGH ? '#ff6b6b' : last >= LOW ? '#f2c14e' : '#4fe0b6';
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, col + '55'); grad.addColorStop(1, col + '00');
  ctx.beginPath(); s.forEach((v, i) => (i ? ctx.lineTo : ctx.moveTo).call(ctx, x0 + i * step, y(v)));
  if (opts.fill !== false) { ctx.lineTo(x0 + (s.length - 1) * step, h); ctx.lineTo(x0, h); ctx.closePath(); ctx.fillStyle = grad; ctx.fill(); }
  ctx.beginPath(); s.forEach((v, i) => (i ? ctx.lineTo : ctx.moveTo).call(ctx, x0 + i * step, y(v)));
  ctx.strokeStyle = col; ctx.lineWidth = opts.lw || 2; ctx.lineJoin = 'round'; ctx.stroke();
  ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x0 + (s.length - 1) * step, y(last), (opts.lw || 2) + 1.5, 0, 7); ctx.fill();
}

function drawScreen(r, room) {
  const { ctx, c, tex } = screens[r];
  ctx.fillStyle = '#071216'; ctx.fillRect(0, 0, c.width, c.height);
  ctx.fillStyle = '#8fb3b5'; ctx.font = '600 20px "Space Grotesk", sans-serif'; ctx.textBaseline = 'top';
  ctx.fillText(`RM ${r}  STRESS`, 12, 10);
  const v = room.value, col = v >= HIGH ? '#ff6b6b' : v >= LOW ? '#f2c14e' : '#4fe0b6';
  ctx.fillStyle = col; ctx.font = '700 46px "JetBrains Mono", monospace'; ctx.textAlign = 'right';
  ctx.fillText(String(Math.round(v)), c.width - 12, 4); ctx.textAlign = 'left';
  ctx.save(); ctx.translate(8, 66); chart(ctx, c.width - 16, c.height - 74, room.series, { n: 40, lw: 4, fill: false }); ctx.restore();
  tex.needsUpdate = true;
}

const LEVEL = { calm: 'Calm', elevated: 'Elevated', high: 'High' };
const hhmmss = (d) => d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
let chatKey = '';

function render() {
  if (!state) return;
  for (const room of state.rooms) {
    const el = $(`mon-${room.room}`);
    el.dataset.l = room.level;
    el.querySelector('.mon-val').textContent = Math.round(room.value);
    el.querySelector('.lvl').textContent = LEVEL[room.level];
    const cv = el.querySelector('canvas');
    chart(cv.getContext('2d'), cv.width, cv.height, room.series);
    drawScreen(room.room, room);
  }
  if (state.rooms.length < ROOMS) return;               // page shows more rooms than the backend has
  if (state.live_room && state.live_room <= ROOMS && state.live_room !== LIVE && !viewing) {
    LIVE = state.live_room;
    for (let r = 1; r <= ROOMS; r++) shells[r].key = '';
  }
  for (let r = 1; r <= ROOMS; r++) {
    const tag = $(`mon-${r}`).querySelector('.mon-foot span:last-child');
    const src = state.rooms[r - 1].source;
    tag.className = r === LIVE ? 'tag' : ''; tag.textContent = r !== LIVE ? 'simulated' : src === 'board' ? 'WICARE BOARD' : 'REMOTE';
    paintShell(r, state.rooms[r - 1].level, state.alert && state.alert.room === r ? state.alert.state : '');
  }
  $('open-now').textContent = `Open room ${LIVE}`;
  $('room-kicker').textContent = `Room ${LIVE} · opened by the nurse`;
  $('note').textContent = `Rooms stay closed for privacy until the nurse replies 1 · Room ${LIVE} set live from the control page · demo index, not a clinical measure`;
  const a = state.alert, r5 = state.rooms[LIVE - 1];
  const pill = $('pill');
  if (a?.state === 'viewing') { pill.dataset.s = 'view'; $('pill-text').textContent = `Viewing Room ${a.room}`; }
  else if (a?.state === 'asked') { pill.dataset.s = 'high'; $('pill-text').textContent = `Room ${a.room} high · nurse asked`; }
  else if (a?.state === 'dismissed') { pill.dataset.s = 'high'; $('pill-text').textContent = `Room ${a.room} high · nurse said no`; }
  else if (r5.level === 'high') { pill.dataset.s = 'high'; $('pill-text').textContent = `Room ${LIVE} stress high`; }
  else { pill.dataset.s = 'ok'; $('pill-text').textContent = ROOMS === 1 ? 'Calm' : 'All rooms calm'; }

  $('toast').hidden = a?.state !== 'asked';
  if (a?.state === 'asked') {
    $('toast-title').textContent = `Room ${a.room} stress is high (${a.value})`;
    $('toast-sub').textContent = state.offline ? 'Backend offline: open the room from the screen.'
      : 'Nurse asked on iMessage: check on them? Reply 1 opens the room, 2 dismisses.';
  }

  const want = a?.state === 'viewing';
  if (want && !viewing) enterRoom(); else if (!want && viewing) leaveRoom();

  if (viewing) {
    const bv = $('bm-val'); bv.textContent = Math.round(r5.value); bv.classList.toggle('hi', r5.value >= HIGH);
    $('bm-level').textContent = LEVEL[r5.level];
    const bc = $('bm-chart'); chart(bc.getContext('2d'), bc.width, bc.height, r5.series, { n: 90, lw: 3 });
    renderChat(state.messages || []);
  }
}

function renderChat(msgs) {
  const key = msgs.map((m) => m.ts_utc + m.status).join('|');
  if (key === chatKey) return; chatKey = key;
  const body = $('chat'); body.textContent = '';
  if (!msgs.length) {
    const e = document.createElement('div'); e.className = 'empty';
    e.textContent = state.offline ? 'Backend offline: no messages.' : 'Opened from the screen, no iMessage this time.';
    return body.appendChild(e);
  }
  for (const m of msgs) {
    const d = document.createElement('div'); d.className = `msg ${m.direction}`; d.textContent = m.text;
    const t = document.createElement('small');
    t.textContent = `${m.direction === 'in' ? 'Nurse' : 'AuraCare'} · ${hhmmss(new Date(m.ts_utc))}${m.status === 'mock_sent' ? ' · mock' : ''}`;
    d.appendChild(t); body.appendChild(d);
  }
  body.scrollTop = body.scrollHeight;
}

function enterRoom() {
  viewing = true; viewT0 = performance.now() / 1000 - (parseFloat(Q.get('t')) || 0); chatKey = ''; facingNow = 0;
  document.body.dataset.mode = 'room'; $('detail').hidden = false; roomGoal(LIVE);
}
function leaveRoom() {
  viewing = false; document.body.dataset.mode = 'ward'; $('detail').hidden = true; overviewGoal();
}

$('open-now').onclick = () => post('/ward/view');
$('back').onclick = () => post('/ward/close');
addEventListener('keydown', (e) => {
  const k = e.key.toLowerCase();
  if (k === 'o') post('/ward/view');
  if (k === 'b' || k === 'escape') post('/ward/close');
  if (offline && k === 'h') local.target = 85;
  if (offline && k === 'c') local.target = 30;
});

// ---------------------------------------------------------------- loop
const clock = new THREE.Clock();
let lastAct = '';
function frame() {
  const dt = Math.min(clock.getDelta(), 0.1), elapsed = clock.elapsedTime;
  const bp = Math.sin(elapsed * 2 * Math.PI * 0.25) * 0.012;           // ~15 breaths / min
  const s = viewing ? Math.max(0, performance.now() / 1000 - viewT0 - SCRIPT.fly) : 0;
  const f = scriptFrame(s, elapsed, bp, dt);
  patient.set(toBay(f.kps, LIVE));
  patient.setVisible(viewing && shells[LIVE].lift > 1.5);           // only once the room is open
  // rooms: the alerting one pops up and glows red; the opened one lifts its roof and lowers its front wall
  const a = state && state.alert;
  for (let r = 1; r <= ROOMS; r++) {
    const sh = shells[r], open = viewing && r === LIVE, asked = a && a.state === 'asked' && a.room === r;
    const hot = state && state.rooms[r - 1] && state.rooms[r - 1].level === 'high';
    const liftGoal = open ? 6 : asked ? 0.35 + Math.abs(Math.sin(elapsed * 3)) * 0.25 : 0;
    sh.lift += (liftGoal - sh.lift) * Math.min(1, dt * (open ? 1.6 : 6));
    sh.drop += ((open ? H : 0) - sh.drop) * Math.min(1, dt * 2.2);
    sh.roof.position.y = sh.lift; sh.roof.visible = sh.lift < 5.5;
    sh.front.position.y = -sh.drop; sh.front.visible = sh.drop < H * 0.45;
    sh.roofMat.emissiveIntensity = asked || hot ? 0.35 + 0.35 * Math.abs(Math.sin(elapsed * 3)) : 0;
  }
  if (viewing && f.act !== lastAct) {
    lastAct = f.act; $('activity').textContent = f.act;
    document.querySelectorAll('#timeline div').forEach((el) => {
      const k = +el.dataset.k; el.dataset.on = k < f.step ? 'done' : k === f.step ? 'now' : '';
    });
  }
  const k = 1 - Math.exp(-dt * 3);
  cam.pos.lerp(cam.goalPos, k); cam.target.lerp(cam.goalTarget, k);
  camera.position.copy(cam.pos); camera.lookAt(cam.target);
  $('clock').textContent = hhmmss(new Date());
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

addEventListener('resize', resize);
resize();
poll();
frame();
