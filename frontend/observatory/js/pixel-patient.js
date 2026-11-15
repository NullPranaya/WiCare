/**
 * WiCare room view (?room=1): the patient as a pixel-style (blocky) man in a hospital gown, replacing the line figure.
 * Follows the room story from demo-data.js 'stand_fall': standing beside the bed, falls forward, face down on the floor.
 *   data.persons[0].fallProgress: 0 standing .. 1 on the floor.
 * Bed geometry matches scenario-props.js _buildHospital (centre -1.5, -2.5; front edge z -1.9).
 */
import * as THREE from 'three';
import { FALL_SPOT } from './demo-data.js';

const S = 0.9, HALF_DEPTH = 0.13 * S;
const HIP = 0.85 * S;                                  // hip height when standing (feet on the floor)

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

export class PixelPatient {
  constructor(scene) {
    this.rig = new THREE.Group(); this.rig.rotation.order = 'YXZ';   // pivot at the feet: the body tips forward from here
    this.root = new THREE.Group(); this.root.position.y = HIP;
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

  /** k: 0 standing .. 1 face down on the floor. */
  set(k, breath, twitch = 0) {
    const e = k * k, flail = Math.sin(k * Math.PI), sway = (1 - Math.min(1, k * 4)) * breath;
    this.rig.position.set(FALL_SPOT.x, (HALF_DEPTH + 0.02) * e, FALL_SPOT.z);
    this.rig.rotation.set(Math.PI / 2 * e, FALL_SPOT.facing, sway * 0.02);
    this.torso.rotation.x = sway * 0.015 + 0.25 * flail;                 // folds at the waist on the way down
    this.head.rotation.set(-0.3 * flail, 0.9 * e, 0);                    // ends with the face turned to one side
    // arms come forward to break the fall, then lie out to the sides
    this.arms[0].rotation.set(-1.3 * flail, 0, -(1.25 * e + twitch));
    this.arms[1].rotation.set(-1.0 * flail, 0, 0.9 * e);
    this.legs[0].thigh.rotation.set(-0.55 * flail, 0, -0.12 * e); this.legs[1].thigh.rotation.set(-0.3 * flail, 0, 0.1 * e);
    this.legs[0].shin.rotation.x = 0.9 * flail; this.legs[1].shin.rotation.x = 0.5 * flail;   // knees buckle
  }

  update(data, elapsed) {
    const p = data && data.persons && data.persons[0];
    const k = p ? Math.max(0, Math.min(1, p.fallProgress || 0)) : 0;
    const onFloorFor = k >= 1 ? (this._downAt ??= elapsed, elapsed - this._downAt) : (this._downAt = null, 0);
    const twitch = onFloorFor > 2 && onFloorFor < 2.6 ? Math.sin((onFloorFor - 2) * 10) * 0.12 : 0;
    this.set(k, Math.sin(elapsed * Math.PI * 0.5), twitch);
  }
}
