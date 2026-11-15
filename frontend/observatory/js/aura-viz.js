/**
 * WiCare instruments, drawn as dot matrices to match the scene's floor.
 *  - BreathRing: a ring of dots that expands and contracts at the breathing rate (demo / RuView data only)
 *  - RoomTrail:  top-down dot map; the person leaves a fading trail (demo / RuView data only)
 *  - dotChart:   last-60-s movement as columns of dots (WiCare backend data)
 * Nothing here invents numbers: each takes the value it is given and shows "--"/dim when there is none.
 */
const GREEN = [0, 216, 120], AMBER = [255, 176, 32], ORANGE = [255, 138, 61], BLUE = [32, 144, 255];
const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const TAU = Math.PI * 2;

function fit(cv) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  const g = cv.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  return [g, w, h];
}

export class BreathRing {
  constructor(cv) { this.cv = cv; this.phase = 0; this.bpm = 0; }

  /** bpm <= 0 or null means "no reading": the ring rests, dim. */
  draw(dt, bpm, tone = GREEN) {
    const live = bpm > 0;
    this.bpm += ((live ? bpm : 0) - this.bpm) * Math.min(1, dt * 2);
    if (live) this.phase += dt * (this.bpm / 60) * TAU;
    const [g, w, h] = fit(this.cv), cx = w / 2, cy = h / 2;
    const breath = live ? (1 - Math.cos(this.phase)) / 2 : 0;          // 0 = empty lungs, 1 = full
    const rMin = w * 0.30, rMax = w * 0.46, r = rMin + (rMax - rMin) * breath;
    const N = 44;
    for (let i = 0; i < N; i++) {                                      // resting capacity, faint
      const a = (i / N) * TAU;
      g.fillStyle = rgba(tone, 0.10);
      g.beginPath(); g.arc(cx + Math.cos(a) * rMax, cy + Math.sin(a) * rMax, 1, 0, TAU); g.fill();
    }
    for (let i = 0; i < N; i++) {                                      // the breath itself
      const a = (i / N) * TAU - Math.PI / 2;
      const ripple = live ? 0.5 + 0.5 * Math.sin(this.phase * 2 - i * 0.45) : 0;
      g.fillStyle = rgba(tone, live ? 0.35 + 0.65 * breath : 0.18);
      g.beginPath(); g.arc(cx + Math.cos(a) * r, cy + Math.sin(a) * r, 1.3 + 1.1 * ripple * breath, 0, TAU); g.fill();
    }
    return live ? (Math.sin(this.phase) >= 0 ? 'in' : 'out') : '';
  }
}

export class RoomTrail {
  constructor(cv, cols = 17, rows = 11) {
    this.cv = cv; this.cols = cols; this.rows = rows;
    this.heat = new Float32Array(cols * rows);
  }

  /** persons: [{position:[x,y,z]}] in scene metres (x,z within about ±4). */
  draw(dt, persons, tone = GREEN) {
    const { cols, rows, heat } = this, decay = Math.exp(-dt / 7);
    const pts = (persons || []).map((p) => [((p.position[0] + 4) / 8) * (cols - 1), ((p.position[2] + 4) / 8) * (rows - 1)]);
    for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
      let v = heat[j * cols + i] * decay;
      for (const [px, pz] of pts) v = Math.max(v, Math.exp(-((i - px) ** 2 + (j - pz) ** 2) / 1.6));
      heat[j * cols + i] = v;
    }
    const [g, w, h] = fit(this.cv), sx = w / cols, sy = h / rows;
    for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
      const v = heat[j * cols + i];
      g.fillStyle = v > 0.03 ? rgba(tone, 0.14 + 0.86 * v) : 'rgba(232,236,224,0.10)';
      g.beginPath(); g.arc((i + 0.5) * sx, (j + 0.5) * sy, 1 + 2.2 * v, 0, TAU); g.fill();
    }
    for (const [px, pz] of pts) {                                      // where they are now
      g.strokeStyle = rgba(tone, 0.9); g.lineWidth = 1;
      g.beginPath(); g.arc((px + 0.5) * sx, (pz + 0.5) * sy, 7, 0, TAU); g.stroke();
    }
  }
}

/** series: [[t_seconds_ago_negative, value 0..1], ...] over the last 60 s; high/low: calibrated thresholds. */
export function dotChart(cv, series, high, low) {
  const [g, w, h] = fit(cv), COLS = 60, ROWS = 9, sx = w / COLS, sy = h / ROWS;
  const col = new Array(COLS).fill(null);
  for (const [t, v] of series || []) {
    const i = Math.round(COLS - 1 + t);
    if (i >= 0 && i < COLS) col[i] = col[i] == null ? v : Math.max(col[i], v);
  }
  const rowOf = (v) => (v == null ? -1 : Math.min(ROWS - 1, Math.round(v * (ROWS - 1))));
  const hi = rowOf(high), lo = rowOf(low);
  for (let i = 0; i < COLS; i++) {
    const v = col[i], top = v == null ? -1 : Math.max(0, rowOf(v));
    const tone = v != null && high != null && v >= high ? AMBER : GREEN;
    for (let r = 0; r < ROWS; r++) {
      const lit = r <= top, x = (i + 0.5) * sx, y = h - (r + 0.5) * sy;
      if (lit) g.fillStyle = rgba(tone, 0.35 + 0.65 * (r / Math.max(1, top)) * (0.4 + 0.6 * (i / COLS)));
      else if (r === hi) g.fillStyle = rgba(AMBER, 0.30);
      else if (r === lo) g.fillStyle = rgba(BLUE, 0.30);
      else g.fillStyle = v == null ? 'rgba(232,236,224,0.05)' : 'rgba(232,236,224,0.14)';
      g.beginPath(); g.arc(x, y, lit ? 1.7 : 1, 0, TAU); g.fill();
    }
  }
}

export const TONES = { ok: GREEN, watch: AMBER, help: ORANGE, off: [150, 160, 170] };
