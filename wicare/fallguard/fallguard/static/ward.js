"use strict";

// Ward overview. Bed 1 is live from /ws; Beds 2–6 are simulated so the demo shows a full ward.

const WINDOW_S = 30;
const $ = (id) => document.getElementById(id);
const css = getComputedStyle(document.documentElement);
const color = (name) => css.getPropertyValue(name).trim();
const localNow = () => performance.now() / 1000;

// ---------- simulated beds ----------
const STEP_S = 0.1;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function makeSim(profile) {
  const sim = { profile, v: 0.06, bump: 0, phase: Math.random() * 10, points: [] };
  const start = localNow() - WINDOW_S;
  for (let t = start; t <= localNow(); t += STEP_S) sim.points.push({ t, score: nextScore(sim, t) });
  return sim;
}

function nextScore(sim, t) {
  const noise = Math.random() - 0.5;
  let target;
  if (sim.profile === "moving") {
    target = 0.5 + 0.28 * Math.sin(t * 1.1 + sim.phase) + 0.18 * Math.sin(t * 3.7) + 0.3 * noise;
    sim.v += (target - sim.v) * 0.35;
    return clamp(sim.v, 0.12, 0.9);
  }
  if (sim.profile === "paused") return clamp(0.025 + 0.015 * noise, 0, 1);
  // calm: low breathing-like wobble with the occasional small shift in bed
  if (Math.random() < 0.004) sim.bump = 0.18 + Math.random() * 0.2;
  sim.bump *= 0.93;
  target = 0.07 + 0.03 * Math.sin(t * 0.9 + sim.phase) + 0.04 * noise + sim.bump;
  sim.v += (target - sim.v) * 0.5;
  return clamp(sim.v, 0, 1);
}

const sims = [...document.querySelectorAll("canvas[data-profile]")].map((canvas) => ({
  canvas, sim: makeSim(canvas.dataset.profile),
}));

setInterval(() => {
  const t = localNow();
  for (const { sim } of sims) sim.points.push({ t, score: nextScore(sim, t) });
}, STEP_S * 1000);

// ---------- live bed ----------
const live = {
  connected: false,
  state: null, // last state message
  samples: new Map(), // board -> [{t, score}] in local seconds
  boards: new Map(),
};

const LIVE_TEXT = {
  MONITORING: ["Monitoring", "Watching for unusual movements"],
  BURST: ["Sudden movement", "Checking whether it stops suddenly"],
  STILLNESS: ["Stillness", "Still after a sudden movement"],
  FALL: ["Possible fall", "Open the dashboard to respond"],
  MOVEMENT: ["Moving", "Ongoing movement. Not the fall pattern."],
  NO_SIGNAL: ["No signal", "No data from the Wi-Fi sensor"],
};

function liveView() {
  if (!live.connected) return { key: "CONNECTING", word: "Not connected", note: "Reconnecting to the WiCare app…" };
  const s = live.state;
  if (!s) return { key: "CONNECTING", word: "Connecting…", note: "Waiting for the WiCare app" };
  if (s.state === "MONITORING" && !s.armed) return { key: "PAUSED", word: "Alerts paused", note: "Patient not in bed" };
  const [word, note] = LIVE_TEXT[s.state] || [s.state, ""];
  return { key: s.state, word, note };
}

const calmTitle = document.title;
function renderLive() {
  const view = liveView();
  const tile = $("bed-live");
  tile.dataset.state = view.key;
  $("live-word").textContent = view.word;
  $("live-note").textContent = view.note;

  const boards = [...live.boards.values()];
  const online = boards.filter((b) => b.connected).length;
  const sensors = $("live-sensors");
  const dot = document.createElement("span");
  dot.className = "dot" + (online > 0 ? " on" : "");
  const label = boards.length === 0 ? "No sensors yet"
    : `${online} of ${boards.length} sensor${boards.length === 1 ? "" : "s"} online`;
  sensors.replaceChildren(dot, document.createTextNode(label));

  const fall = view.key === "FALL";
  $("ward-summary").textContent = fall ? "Possible fall in Bed 1"
    : live.connected ? "6 beds · no alerts" : "6 beds · Bed 1 not connected";
  $("ward-summary").dataset.alert = fall ? "true" : "false";
  $("favicon").href = fall ? "/static/favicon-alarm.svg" : "/static/favicon.svg";
  document.title = fall ? "Possible fall · " + calmTitle : calmTitle;
}

function onMessage(msg) {
  switch (msg.type) {
    case "config":
      $("replay-banner").hidden = msg.mode !== "replay";
      break;
    case "state":
      live.state = msg;
      renderLive();
      break;
    case "sample": {
      if (!live.samples.has(msg.board)) live.samples.set(msg.board, []);
      live.samples.get(msg.board).push({ t: localNow(), score: msg.score });
      break;
    }
    case "board":
      live.boards.set(msg.board, { ...(live.boards.get(msg.board) || {}), ...msg });
      renderLive();
      break;
  }
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { live.connected = true; renderLive(); };
  ws.onmessage = (event) => onMessage(JSON.parse(event.data));
  ws.onclose = () => {
    live.connected = false;
    live.state = null;
    renderLive();
    setTimeout(connect, 2000);
  };
}

// ---------- sparklines ----------
function drawSpark(canvas, series, stroke) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const t0 = localNow() - WINDOW_S;
  const x = (t) => ((t - t0) / WINDOW_S) * w;
  const y = (v) => h - 3 - clamp(v, 0, 1) * (h - 6);

  ctx.strokeStyle = color("--line");
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, Math.round(y(0)) + 0.5);
  ctx.lineTo(w, Math.round(y(0)) + 0.5);
  ctx.stroke();

  ctx.strokeStyle = stroke;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  for (const points of series) {
    while (points.length && points[0].t < t0 - 1) points.shift();
    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(x(p.t), y(p.score)) : ctx.moveTo(x(p.t), y(p.score))));
    ctx.stroke();
  }
}

function strokeFor(key) {
  if (key === "FALL") return "#FFFFFF";
  if (key === "STILLNESS") return color("--fall");
  if (key === "BURST") return color("--burst");
  if (key === "PAUSED" || key === "CONNECTING" || key === "NO_SIGNAL") return color("--quiet");
  return color("--trace");
}

function frame() {
  for (const { canvas, sim } of sims) drawSpark(canvas, [sim.points], strokeFor(canvas.closest(".bed").dataset.state));
  drawSpark($("live-spark"), [...live.samples.values()], strokeFor($("bed-live").dataset.state));
  requestAnimationFrame(frame);
}

renderLive();
connect();
requestAnimationFrame(frame);
