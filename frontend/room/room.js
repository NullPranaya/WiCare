/**
 * WiCare room view: a pixel-style patient on a plain floor. WiCare's dashboard sends the nurse here after they
 * reply 1 (fallguard/room.py).
 *
 * Story (seconds, loops every 15 s; ?t=6 starts just before the fall):
 *   0 standing · 7 falls · 8 on the floor, not moving (until 15)
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = (id) => document.getElementById(id);
const Q = new URLSearchParams(location.search);

// ------------------------------------------------------------------ renderer, camera, light, floor
const canvas = $('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;

const BG = 0x101a2c;                                   // the earlier room view's background
const scene = new THREE.Scene();
scene.background = new THREE.Color(BG);
scene.fog = new THREE.Fog(BG, 14, 30);

const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.maxPolarAngle = 1.4;
controls.minDistance = 2.5;
controls.maxDistance = 16;
camera.position.set(3.2, 2.9, 4.4);
controls.target.set(0, 0.55, 0.3);
controls.update();
function resize() {
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);
resize();

scene.add(new THREE.HemisphereLight(0xdfe8ff, 0x1a2230, 1.3));
const key = new THREE.DirectionalLight(0xffffff, 2.2);
key.position.set(3, 7, 4);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.radius = 5;
key.shadow.bias = -0.0005;
Object.assign(key.shadow.camera, { left: -4, right: 4, top: 4, bottom: -4, near: 1, far: 20 });
scene.add(key);
const rim = new THREE.DirectionalLight(0x9fb8ff, 0.6);
rim.position.set(-4, 3, -4);
scene.add(rim);

// plain floor: dark slate with a soft grey grid (no green)
const floor = new THREE.Mesh(new THREE.PlaneGeometry(14, 12), new THREE.MeshStandardMaterial({ color: 0x1e2836, roughness: 0.85 }));
floor.rotation.x = -Math.PI / 2; floor.receiveShadow = true; scene.add(floor);
const grid = new THREE.GridHelper(14, 28, 0x46566b, 0x2b3747);
grid.position.y = 0.002; grid.material.transparent = true; grid.material.opacity = 0.6; scene.add(grid);

function canvasTex(w, h, draw, { nearest = false } = {}) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  if (nearest) { t.magFilter = THREE.NearestFilter; t.minFilter = THREE.NearestFilter; t.generateMipmaps = false; }
  return t;
}

// ------------------------------------------------------------------ pixel-style patient
const SKIN = '#e8a67c', SKIN_D = '#c97e58', HAIR = '#6b3f22', HAIR_D = '#4a2a16';
function pixelTex(w, h, paint) { return canvasTex(w, h, paint, { nearest: true }); }
const gownTex = pixelTex(8, 8, (g) => {
  const cols = ['#bfeee0', '#9fdccc', '#a9cdec', '#d4f3ea', '#8fd0c1'];
  for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) { g.fillStyle = cols[(x * 7 + y * 3 + ((x * y) % 5)) % cols.length]; g.fillRect(x, y, 1, 1); }
});
const face = pixelTex(8, 8, (g) => {
  g.fillStyle = SKIN; g.fillRect(0, 0, 8, 8);
  g.fillStyle = HAIR; g.fillRect(0, 0, 8, 2); g.fillRect(0, 2, 1, 2); g.fillRect(7, 2, 1, 2);
  g.fillStyle = HAIR_D; g.fillRect(1, 3, 2, 1); g.fillRect(5, 3, 2, 1);            // brows
  g.fillStyle = '#3a2416'; g.fillRect(1, 4, 2, 2); g.fillRect(5, 4, 2, 2);          // eyes
  g.fillStyle = '#7a4a2c'; g.fillRect(2, 4, 1, 2); g.fillRect(6, 4, 1, 2);
  g.fillStyle = SKIN_D; g.fillRect(3, 5, 2, 1);                                      // nose
  g.fillStyle = '#9c4a3a'; g.fillRect(3, 6, 2, 1);                                   // mouth
});
const hairSide = pixelTex(8, 8, (g) => { g.fillStyle = SKIN; g.fillRect(0, 0, 8, 8); g.fillStyle = HAIR; g.fillRect(0, 0, 8, 3); g.fillRect(0, 3, 3, 2); g.fillStyle = SKIN_D; g.fillRect(4, 4, 2, 2); });
const hairBack = pixelTex(8, 8, (g) => { g.fillStyle = HAIR; g.fillRect(0, 0, 8, 6); g.fillStyle = HAIR_D; g.fillRect(1, 1, 2, 1); g.fillRect(5, 3, 2, 1); g.fillStyle = SKIN; g.fillRect(0, 6, 8, 2); });
const hairTop = pixelTex(8, 8, (g) => { g.fillStyle = HAIR; g.fillRect(0, 0, 8, 8); g.fillStyle = '#7a4a29'; g.fillRect(1, 1, 3, 2); g.fillRect(5, 4, 2, 3); });
const pm = (map) => new THREE.MeshStandardMaterial({ map, roughness: 0.9 });
const pc = (hex) => new THREE.MeshStandardMaterial({ color: hex, roughness: 0.9 });
const gown = pm(gownTex), skin = pc(SKIN), sock = pc('#8d969b');
// BoxGeometry face order: +x, -x, +y, -y, +z (front), -z
const headMats = [pm(hairSide), pm(hairSide), pm(hairTop), skin, pm(face), pm(hairBack)];

function block(w, h, d, m, x, y, z, parent) {
  const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
  b.position.set(x, y, z); b.castShadow = true; b.receiveShadow = true; parent.add(b); return b;
}
const S = 0.9;                       // overall scale; hip height standing = 0.86 * S
const HIP_STAND = 0.86 * S, HALF_DEPTH = 0.13 * S;
class Patient {
  constructor() {
    this.root = new THREE.Group(); this.root.rotation.order = 'YXZ';
    const body = new THREE.Group(); body.scale.setScalar(S); this.root.add(body);
    this.torso = new THREE.Group(); body.add(this.torso);
    block(0.46, 0.6, 0.26, gown, 0, 0.3, 0, this.torso);
    this.skirt = block(0.5, 0.24, 0.29, gown, 0, -0.06, 0, this.torso);
    this.head = new THREE.Group(); this.head.position.y = 0.6; this.torso.add(this.head);
    block(0.12, 0.06, 0.12, skin, 0, 0.03, 0, this.head);
    block(0.42, 0.42, 0.42, headMats, 0, 0.27, 0, this.head);
    block(0.46, 0.1, 0.46, pm(hairTop), 0, 0.5, -0.01, this.head);
    this.arms = [-1, 1].map((s) => {
      const a = new THREE.Group(); a.position.set(s * 0.31, 0.56, 0); this.torso.add(a);
      block(0.16, 0.2, 0.18, gown, 0, -0.08, 0, a);
      block(0.14, 0.42, 0.16, skin, 0, -0.38, 0, a);
      return a;
    });
    this.legs = [-1, 1].map((s) => {
      const thigh = new THREE.Group(); thigh.position.set(s * 0.12, 0, 0); body.add(thigh);
      block(0.17, 0.4, 0.19, skin, 0, -0.2, 0, thigh);
      const shin = new THREE.Group(); shin.position.y = -0.4; thigh.add(shin);
      block(0.16, 0.38, 0.18, skin, 0, -0.19, 0, shin);
      block(0.17, 0.08, 0.25, sock, 0, -0.41, 0.03, shin);
      return { thigh, shin };
    });
    scene.add(this.root);
  }
  /** p: { x, y, z, ry, rx, torso, head, headYaw, armL, armR, thighL, thighR, shinL, shinR } */
  set(p) {
    this.root.position.set(p.x, p.y, p.z);
    this.root.rotation.set(p.rx || 0, p.ry || 0, p.rz || 0);
    this.torso.rotation.x = p.torso || 0;
    this.head.rotation.set(p.head || 0, p.headYaw || 0, 0);
    this.arms[0].rotation.set(p.armL || 0, 0, p.armSpread ? -p.armSpread : 0);
    this.arms[1].rotation.set(p.armR || 0, 0, p.armSpread ? p.armSpread : 0);
    this.legs[0].thigh.rotation.x = p.thighL || 0; this.legs[1].thigh.rotation.x = p.thighR || 0;
    this.legs[0].shin.rotation.x = p.shinL || 0; this.legs[1].shin.rotation.x = p.shinR || 0;
  }
}
const patient = new Patient();

// ------------------------------------------------------------------ the story: standing, then a fall
const T_FALL = 7, T_FLOOR = 8, LOOP = 15;
const ss = (a, b, t) => { const x = Math.min(1, Math.max(0, (t - a) / (b - a))); return x * x * (3 - 2 * x); };
const mix = (a, b, k) => a + (b - a) * k;
const FACE = 0.6;                                       // turned a little toward the camera

function pose(t, breath) {
  if (t < T_FALL) {                                     // standing: breathing, small weight shift, looks around
    const sway = Math.sin(t * 1.3) * 0.03;
    return { k: 'standing', label: 'Standing', x: sway * 0.3, y: HIP_STAND + breath * 0.004, z: 0, ry: FACE,
      torso: breath * 0.01, headYaw: Math.sin(t * 0.7) * 0.35, armL: sway, armR: -sway, armSpread: 0.06 };
  }
  // knees buckle, then he tips over onto his side (lengthwise to the camera) and stays there
  const k = ss(T_FALL, T_FLOOR, t), k2 = k * k, buckle = Math.sin(Math.min(1, k * 1.6) * Math.PI / 2);
  const side = [-Math.cos(FACE), Math.sin(FACE)];      // his right-hand side, across the screen
  const twitch = t > T_FLOOR + 2 && t < T_FLOOR + 2.6 ? Math.sin((t - T_FLOOR - 2) * 10) * 0.25 : 0;
  const lieY = 0.23 * S + 0.01;                        // hip height lying on one side (half the shoulder width)
  return { k: t < T_FLOOR ? 'fall' : 'floor', label: t < T_FLOOR ? 'Falling' : 'On the floor, not moving',
    x: side[0] * 0.35 * k, y: mix(HIP_STAND - 0.12 * buckle, lieY, k2), z: side[1] * 0.35 * k, ry: FACE, rz: -(Math.PI / 2) * k2,
    torso: 0.25 * buckle, head: -0.2 * k,
    armL: mix(0, -0.6, k) + twitch, armR: mix(0, -2.6, k), armSpread: 0.06 + 0.25 * k,
    thighL: -0.55 * buckle, thighR: -0.35 * buckle, shinL: 0.8 * buckle, shinR: 0.6 * buckle };
}

// ------------------------------------------------------------------ HUD + loop
const fade = document.createElement('div');
Object.assign(fade.style, { position: 'fixed', inset: '0', background: '#101a2c', opacity: '0', pointerEvents: 'none', zIndex: 1, transition: 'opacity .4s' });
document.body.appendChild(fade);
let lastKey = '', lastLabel = '';
const start = performance.now() / 1000 - (parseFloat(Q.get('t')) || 0);
function frame() {
  const now = performance.now() / 1000, t = (now - start) % LOOP;
  const breath = Math.sin(now * Math.PI * 0.5);
  const p = pose(t, breath);
  patient.set(p);
  fade.style.opacity = t > LOOP - 0.5 || t < 0.3 ? '1' : '0';
  if (p.label !== lastLabel) {
    lastLabel = p.label;
    const el = $('now'); el.textContent = p.label;
    el.dataset.tone = p.k === 'fall' || p.k === 'floor' ? 'fall' : '';
  }
  if (p.k !== lastKey) {
    lastKey = p.k;
    document.querySelectorAll('.steps li').forEach((li) => li.classList.toggle('active', li.dataset.k === p.k));
  }
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}
frame();
