/**
 * WiCare room view (?room=1): the patient as a pixel-style (blocky) man in a hospital gown, replacing the line figure.
 * Follows the room story from demo-data.js 'stand_fall': lying in bed, rolls off the near edge, face down on the floor.
 *   data.persons[0].pose: 'waking' (in bed) or 'bedfall'; data.persons[0].fallProgress: 0 in bed .. 1 on the floor.
 * Bed geometry matches scenario-props.js _buildHospital (centre -1.5, -2.5; mattress top 0.53; head toward -x).
 */
import * as THREE from 'three';
import { WAKE_BED } from './pose-system.js';

const BX = 3.5 + WAKE_BED.dx, BZ = -3.5 + WAKE_BED.dz, MATTRESS_TOP = 0.53;
const S = 0.9, HALF_DEPTH = 0.13 * S;
const HIP_BED = MATTRESS_TOP + HALF_DEPTH + 0.01;
const EDGE_Y = MATTRESS_TOP, EDGE_Z = BZ + 0.46;

const SKIN = '#e8c4ab', SKIN_D = '#c99f86', HAIR = '#5a3a24', HAIR_D = '#3f2717';
const GOWN = '#93b4cf', GOWN_D = '#5a84ad', GOWN_L = '#a9c5dc', SOCK = '#6d8fcf', SOCK_D = '#5675b3', CAP = '#86b0d8';

function pixelTex(w, h, paint) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  paint(c.getContext('2d'));
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.magFilter = THREE.NearestFilter; t.minFilter = THREE.NearestFilter; t.generateMipmaps = false;
  return t;
}
const px = (g, c, x, y, w = 1, h = 1) => { g.fillStyle = c; g.fillRect(x, y, w, h); };

// gown: light blue with a small darker diamond-dot print, like a standard hospital gown
const gownTex = pixelTex(16, 16, (g) => {
  px(g, GOWN, 0, 0, 16, 16);
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    if ((x + y) % 4 === 0 && y % 2 === 0) px(g, GOWN_D, x, y);
    else if ((x * 3 + y * 5) % 11 === 0) px(g, GOWN_L, x, y);
  }
});
const sockTex = pixelTex(8, 8, (g) => { px(g, SOCK, 0, 0, 8, 8); px(g, SOCK_D, 0, 0, 8, 1); px(g, SOCK_D, 2, 4, 1, 1); px(g, SOCK_D, 5, 6, 1, 1); });
const face = pixelTex(8, 8, (g) => {
  px(g, SKIN, 0, 0, 8, 8);
  px(g, CAP, 0, 0, 8, 2);                              // cap brim line across the forehead
  px(g, HAIR, 0, 2, 1, 2); px(g, HAIR, 7, 2, 1, 2);    // sideburns
  px(g, HAIR_D, 1, 3, 2, 1); px(g, HAIR_D, 5, 3, 2, 1);// brows
  px(g, '#ffffff', 1, 4, 1, 1); px(g, '#ffffff', 5, 4, 1, 1);
  px(g, '#2c1a10', 2, 4, 1, 1); px(g, '#2c1a10', 6, 4, 1, 1);
  px(g, SKIN_D, 3, 5, 2, 1);                           // nose
  px(g, '#9a4f3c', 3, 6, 2, 1);                        // mouth
});
const headSide = pixelTex(8, 8, (g) => { px(g, SKIN, 0, 0, 8, 8); px(g, CAP, 0, 0, 8, 2); px(g, HAIR, 0, 2, 4, 2); px(g, SKIN_D, 4, 4, 2, 2); });
const headBack = pixelTex(8, 8, (g) => { px(g, CAP, 0, 0, 8, 3); px(g, HAIR, 0, 3, 8, 3); px(g, HAIR_D, 2, 4, 3, 1); px(g, SKIN, 0, 6, 8, 2); });
const capTop = pixelTex(8, 8, (g) => { px(g, CAP, 0, 0, 8, 8); px(g, '#4a88c4', 1, 1, 6, 1); px(g, '#4a88c4', 3, 3, 2, 3); });

const mat = (map) => new THREE.MeshStandardMaterial({ map, roughness: 0.9 });
const col = (hex) => new THREE.MeshStandardMaterial({ color: hex, roughness: 0.9 });
const gown = mat(gownTex), skin = col(SKIN), sock = mat(sockTex), white = col('#f4f6f7');
// BoxGeometry face order: +x, -x, +y, -y, +z (front), -z
const headMats = [mat(headSide), mat(headSide), mat(capTop), skin, mat(face), mat(headBack)];

function block(w, h, d, m, x, y, z, parent) {
  const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), m);
  b.position.set(x, y, z); b.castShadow = true; b.receiveShadow = true; parent.add(b); return b;
}
const ss = (a, b, t) => { const x = Math.min(1, Math.max(0, (t - a) / (b - a))); return x * x * (3 - 2 * x); };
const mix = (a, b, k) => a + (b - a) * k;

export class PixelPatient {
  constructor(scene) {
    this.rig = new THREE.Group();                      // pivot on the mattress edge for the roll off the bed
    this.root = new THREE.Group(); this.root.rotation.order = 'YXZ';
    this.rig.add(this.root); scene.add(this.rig);
    const body = new THREE.Group(); body.scale.setScalar(S); this.root.add(body);

    // torso + gown (knee length) + white collar trim
    this.torso = new THREE.Group(); body.add(this.torso);
    block(0.46, 0.6, 0.26, gown, 0, 0.3, 0, this.torso);
    block(0.5, 0.42, 0.29, gown, 0, -0.19, 0, this.torso);
    block(0.26, 0.04, 0.27, white, 0, 0.6, 0.005, this.torso);
    // head with a surgical cap
    this.head = new THREE.Group(); this.head.position.y = 0.6; this.torso.add(this.head);
    block(0.13, 0.06, 0.13, skin, 0, 0.03, 0, this.head);
    block(0.42, 0.42, 0.42, headMats, 0, 0.27, 0, this.head);
    block(0.45, 0.09, 0.45, mat(capTop), 0, 0.5, -0.005, this.head);
    // arms: short gown sleeve, bare forearm
    this.arms = [-1, 1].map((s) => {
      const a = new THREE.Group(); a.position.set(s * 0.31, 0.56, 0); this.torso.add(a);
      block(0.18, 0.24, 0.2, gown, 0, -0.09, 0, a);
      block(0.14, 0.42, 0.15, skin, 0, -0.39, 0, a);
      return a;
    });
    // legs: bare shins, blue socks
    this.legs = [-1, 1].map((s) => {
      const thigh = new THREE.Group(); thigh.position.set(s * 0.12, 0, 0); body.add(thigh);
      block(0.17, 0.4, 0.19, skin, 0, -0.2, 0, thigh);
      const shin = new THREE.Group(); shin.position.y = -0.4; thigh.add(shin);
      block(0.16, 0.16, 0.18, skin, 0, -0.08, 0, shin);
      block(0.165, 0.22, 0.185, sock, 0, -0.27, 0, shin);
      block(0.17, 0.08, 0.26, sock, 0, -0.41, 0.035, shin);
      return { thigh, shin };
    });
  }

  /** k: 0 lying in bed .. 1 face down on the floor beside it. */
  set(k, breath, twitch = 0) {
    const e = k * k * (3 - 2 * k), flail = Math.sin(k * Math.PI);
    this.rig.position.set(0, mix(EDGE_Y, HALF_DEPTH + 0.02 + (HIP_BED - EDGE_Y), e), EDGE_Z + 0.15 * e);
    this.rig.rotation.x = Math.PI * e;
    this.root.position.set(BX - 0.05, HIP_BED - EDGE_Y, BZ - EDGE_Z);
    this.root.rotation.set(-Math.PI / 2, Math.PI / 2, 0);   // on the back, head toward the headboard
    this.torso.rotation.x = k === 0 ? breath * 0.015 : 0;
    this.head.rotation.x = -0.3 * flail;
    this.arms[0].rotation.set(-1.2 * flail + twitch, 0, -(0.5 * flail + 0.12 * k));
    this.arms[1].rotation.set(-0.9 * flail, 0, 0.5 * flail + 0.12 * k);
    this.legs[0].thigh.rotation.x = -0.5 * flail; this.legs[1].thigh.rotation.x = -0.2 * flail;
    this.legs[0].shin.rotation.x = 0.6 * flail; this.legs[1].shin.rotation.x = 0;
  }

  update(data, elapsed) {
    const p = data && data.persons && data.persons[0];
    const k = p && p.pose === 'bedfall' ? Math.max(0, Math.min(1, p.fallProgress || 0)) : 0;
    const onFloorFor = k >= 1 ? (this._downAt ??= elapsed, elapsed - this._downAt) : (this._downAt = null, 0);
    const twitch = onFloorFor > 2 && onFloorFor < 2.6 ? Math.sin((onFloorFor - 2) * 10) * 0.25 : 0;
    this.set(k, Math.sin(elapsed * Math.PI * 0.5), twitch);
  }
}
