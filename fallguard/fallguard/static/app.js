"use strict";

const WINDOW_S = 60;
const $ = (id) => document.getElementById(id);
const css = getComputedStyle(document.documentElement);
const color = (name) => css.getPropertyValue(name).trim();

const app = {
  config: { threshold: 0.962, burst_max_s: 3, still_s: 8, mode: "live", boards: [] },
  state: { state: "MONITORING", since: 0, armed: true },
  samples: new Map(), // board -> [{t, score}]
  bands: [], // {kind: burst|still|fall|moved, start, end}
  boards: new Map(),
  offset: null, // local seconds minus server monotonic seconds
  alert: null,
};

const localNow = () => performance.now() / 1000;
const serverNow = () => (app.offset === null ? 0 : localNow() - app.offset);
const fmt = (s) => (s == null ? "–" : s.toFixed(1));

// ---------- state handling ----------
// bands: {phase: "burst"|"still", outcome: null|"fall"|"moved", start, end}
function openBand() {
  const last = app.bands[app.bands.length - 1];
  return last && last.end === null ? last : null;
}

function settle(at, outcome) {
  const band = openBand();
  if (band) band.end = at;
  // the latest burst and its stillness share one outcome
  for (let i = app.bands.length - 1; i >= 0 && app.bands[i].outcome === null; i--) {
    app.bands[i].outcome = outcome;
    if (app.bands[i].phase === "burst") break;
  }
}

function onState(msg) {
  const prev = app.state.state;
  const at = msg.since;
  if (msg.state !== prev) {
    if (msg.state === "BURST") {
      const band = openBand();
      if (band && band.phase === "still") {
        app.bands.pop(); // brief dip merged back into the same burst
        const burst = app.bands[app.bands.length - 1];
        if (burst) burst.end = null;
      } else {
        settle(at, "moved");
        app.bands.push({ phase: "burst", outcome: null, start: at, end: null });
      }
    } else if (msg.state === "STILLNESS") {
      const band = openBand();
      if (band) band.end = at;
      app.bands.push({ phase: "still", outcome: null, start: at, end: null });
    } else if (msg.state === "FALL") {
      settle(at, "fall");
    } else {
      settle(at, "moved"); // MOVEMENT, MONITORING, NO_SIGNAL: no fall
    }
  }
  app.state = msg;
  if (app.offset === null && typeof msg.now === "number") app.offset = localNow() - msg.now;
  $("armed").checked = msg.armed;
  render();
}

function burstStart() {
  for (let i = app.bands.length - 1; i >= 0; i--) {
    if (app.bands[i].phase === "burst") return app.bands[i].start;
  }
  return null;
}

// ---------- rendering ----------
function setRule(el, text, mark) {
  el.textContent = text;
  el.className = mark || "";
}

function render() {
  const s = app.state;
  const now = serverNow();
  const word = $("status-word");
  const note = $("status-note");
  word.dataset.state = s.state;

  document.querySelectorAll(".steps li").forEach((li) => {
    const active = li.dataset.step === s.state;
    li.classList.toggle("active", active);
    if (active) li.setAttribute("aria-current", "step");
    else li.removeAttribute("aria-current");
  });

  const limit = app.config.burst_max_s;
  const need = app.config.still_s;
  const burstRule = $("rule-burst");
  const stillRule = $("rule-still");
  const elapsed = Math.max(0, now - s.since);

  switch (s.state) {
    case "BURST": {
      const so = Math.max(0, now - (burstStart() ?? s.since));
      word.textContent = "Sudden movement";
      note.textContent = `${fmt(so)} s so far. A fall pattern needs it to stop within ${limit} s.`;
      setRule(burstRule, `Sudden movement: ${fmt(so)} s so far (limit ${limit} s)`);
      setRule(stillRule, "Stillness after it: waiting");
      break;
    }
    case "STILLNESS": {
      const n = Math.min(need, Math.floor(elapsed));
      word.textContent = `Stillness ${n} of ${need} s`;
      note.textContent = `After a ${fmt(s.burst_s)} s sudden movement. Any movement now cancels the alert.`;
      setRule(burstRule, `Sudden movement: ${fmt(s.burst_s)} s (limit ${limit} s)`, "ok");
      setRule(stillRule, `Stillness after it: ${n} of ${need} s`);
      break;
    }
    case "FALL":
      word.textContent = "Possible fall";
      note.textContent = "Waiting for someone to acknowledge.";
      setRule(burstRule, `Sudden movement: ${fmt(s.burst_s)} s (limit ${limit} s)`, "ok");
      setRule(stillRule, `Stillness after it: ${need} of ${need} s`, "ok");
      break;
    case "MOVEMENT":
      word.textContent = "Moving";
      note.textContent = "Ongoing movement. This is not the fall pattern.";
      setRule(burstRule, "Sudden movement: lasted too long or kept going", "bad");
      setRule(stillRule, "Stillness after it: waiting");
      break;
    case "NO_SIGNAL":
      word.textContent = "No signal";
      note.textContent = "No data from the Wi-Fi sensor for over 1.5 s. Check the board and the hotspot.";
      setRule(burstRule, "Sudden movement: waiting");
      setRule(stillRule, "Stillness after it: waiting");
      break;
    default:
      word.textContent = s.armed ? "Monitoring" : "Alerts paused";
      note.textContent = s.armed
        ? "Watching for a sudden movement followed by stillness."
        : "Turn on “Patient in bed” to resume fall alerts.";
      setRule(burstRule, "Sudden movement: waiting");
      setRule(stillRule, "Stillness after it: waiting");
  }

  const alarm = $("alarm");
  const showAlarm = s.state === "FALL";
  if (showAlarm && alarm.hidden) {
    alarm.hidden = false;
    $("ack").focus();
    alarmSound.start();
  } else if (!showAlarm && !alarm.hidden) {
    alarm.hidden = true;
    alarmSound.stop();
  }
  if (showAlarm) {
    $("alarm-detail").textContent =
      `Sudden movement for ${fmt(s.burst_s)} s, then still for ${need} s.`;
    $("alarm-message").textContent = alertText();
    $("alarm-sound").hidden = !alarmSound.blocked();
  }
}

// ---------- alarm sound ----------
// Synthesized with Web Audio, so there is no file to load. Browsers only allow sound after someone
// has clicked or pressed a key on the page; until then the alarm shows a hint instead.
const alarmSound = (() => {
  const PATTERN_S = 1.6; // three rising beeps, then a pause
  const NOTES = [660, 880, 1100];
  let ctx = null;
  let timer = null;

  function context() {
    if (!ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      ctx = new Ctx();
    }
    return ctx;
  }

  function beep(c, at, freq) {
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.type = "square";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(0.18, at + 0.02);
    gain.gain.setValueAtTime(0.18, at + 0.15);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.2);
    osc.connect(gain).connect(c.destination);
    osc.start(at);
    osc.stop(at + 0.22);
  }

  function pattern() {
    const c = context();
    if (!c || c.state !== "running") return; // never queue beeps that would all fire at once later
    const t = c.currentTime + 0.05;
    NOTES.forEach((freq, i) => beep(c, t + i * 0.25, freq));
  }

  function unlock() {
    const c = context();
    if (c && c.state !== "running") c.resume().then(() => { if (timer) pattern(); }).catch(() => {});
  }

  return {
    unlock,
    start() {
      if (timer) return;
      unlock();
      pattern();
      timer = setInterval(pattern, PATTERN_S * 1000);
    },
    stop() {
      clearInterval(timer);
      timer = null;
    },
    blocked: () => !ctx || ctx.state !== "running",
  };
})();

for (const kind of ["pointerdown", "keydown"]) {
  document.addEventListener(kind, () => alarmSound.unlock(), { capture: true });
}

function alertText() {
  const a = app.alert;
  if (!a) return "No alerts sent yet";
  switch (a.imessage) {
    case "sending": return "Sending to the nurse iPhone…";
    case "sent": return `Nurse alerted at ${a.at}. Waiting for their reply (1 acknowledge, 2 ignore)`;
    case "acknowledged": return `Nurse acknowledged at ${a.at}`;
    case "ignored": return `Nurse ignored the alert at ${a.at}. Saved as a false alarm`;
    case "failed": return `Not sent at ${a.at}: ${a.detail}`;
    case "skipped_cooldown": return "Not resent: the nurse was alerted under a minute ago";
    case "no_recipient": return "Not sent: no nurse number is set (NURSE_PHONE)";
    default: return a.detail || a.imessage;
  }
}

function renderBoards() {
  const list = $("boards");
  list.replaceChildren();
  for (const b of app.boards.values()) {
    const li = document.createElement("li");
    const dot = document.createElement("span");
    dot.className = "dot" + (b.connected ? " on" : "");
    li.append(dot, document.createTextNode(`${b.board} `));
    const meta = document.createElement("span");
    meta.className = "meta";
    if (b.connected) {
      const parts = [];
      if (b.pps != null) parts.push(`${Math.round(b.pps)} packets/s`);
      if (b.occupancy != null) parts.push(`${Math.round(b.occupancy * 100)}% of slots filled`);
      meta.textContent = parts.join(", ") || "connected";
    } else {
      meta.textContent = `offline (${b.detail || "no connection"})`;
    }
    li.append(meta);
    list.append(li);
  }
}

// ---------- chart ----------
const canvas = $("chart");
const ctx = canvas.getContext("2d");

function draw() {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const now = serverNow();
  const t0 = now - WINDOW_S;
  const x = (t) => ((t - t0) / WINDOW_S) * w;
  const pad = 14;
  const y = (v) => h - pad - v * (h - 2 * pad);

  // bands
  for (const band of app.bands) {
    const end = band.end ?? now;
    if (end < t0) continue;
    const left = x(Math.max(band.start, t0));
    const fill = band.outcome === "moved" ? color("--quiet") : band.phase === "burst" ? color("--burst") : color("--fall");
    ctx.globalAlpha = band.outcome === "moved" ? 0.1 : band.phase === "still" ? 0.14 : 0.24;
    ctx.fillStyle = fill;
    ctx.fillRect(left, 0, x(end) - left, h);
    ctx.globalAlpha = 1;
    if (band.outcome !== "moved" && x(end) - left > 70) {
      ctx.font = "700 15px 'Atkinson Hyperlegible', sans-serif";
      ctx.fillText(band.phase === "burst" ? `movement ${fmt(end - band.start)} s` : `still ${Math.floor(end - band.start)} s`, left + 6, 44);
    }
  }

  // threshold
  ctx.strokeStyle = color("--fall");
  ctx.setLineDash([6, 5]);
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(0, y(app.config.threshold));
  ctx.lineTo(w, y(app.config.threshold));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = color("--fall");
  ctx.font = "15px 'Atkinson Hyperlegible', sans-serif";
  ctx.textAlign = "right";
  ctx.fillText("alert threshold", w - 6, y(app.config.threshold) - 6);
  ctx.textAlign = "left";

  // traces
  const traceColors = [color("--trace"), "#3E5C9A"];
  let i = 0;
  for (const [, points] of app.samples) {
    while (points.length && points[0].t < t0 - 1) points.shift();
    ctx.strokeStyle = traceColors[i++ % traceColors.length];
    ctx.lineWidth = 2.5;
    ctx.lineJoin = "round";
    ctx.beginPath();
    let started = false;
    for (const p of points) {
      const px = x(p.t);
      const py = y(Math.max(0, Math.min(1, p.score)));
      if (started) ctx.lineTo(px, py);
      else { ctx.moveTo(px, py); started = true; }
    }
    ctx.stroke();
  }

  render();
  requestAnimationFrame(draw);
}

// ---------- socket ----------
function onMessage(msg) {
  switch (msg.type) {
    case "config":
      app.config = msg;
      $("replay-banner").hidden = msg.mode !== "replay";
      break;
    case "state":
      onState(msg);
      break;
    case "sample": {
      app.offset = localNow() - msg.t;
      if (!app.samples.has(msg.board)) app.samples.set(msg.board, []);
      app.samples.get(msg.board).push({ t: msg.t, score: msg.score });
      break;
    }
    case "alert":
      app.alert = msg;
      $("alert-text").textContent = alertText();
      render();
      break;
    case "route":
      if (typeof msg.to === "string" && msg.to.startsWith("/")) location.assign(msg.to);
      break;
    case "board":
      app.boards.set(msg.board, { ...(app.boards.get(msg.board) || {}), ...msg });
      renderBoards();
      break;
  }
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { $("link-banner").hidden = true; };
  ws.onmessage = (event) => onMessage(JSON.parse(event.data));
  ws.onclose = () => {
    $("link-banner").hidden = false;
    setTimeout(connect, 2000);
  };
}

// ---------- actions ----------
async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json();
}

$("ack").addEventListener("click", () => post("/api/ack").then(onState).catch(console.error));
$("armed").addEventListener("change", (e) => post("/api/arm", { armed: e.target.checked }).then(onState).catch(console.error));

function mark(label, text) {
  post("/api/mark", { label })
    .then((r) => { $("mark-note").textContent = `Saved “${text}” at ${r.wall.slice(11)}`; })
    .catch(() => { $("mark-note").textContent = "Could not save the label. Is the app running?"; });
}
$("mark-fall").addEventListener("click", () => mark("fall", "fall"));
$("mark-false").addEventListener("click", () => mark("false_alarm", "false alarm"));

connect();
requestAnimationFrame(draw);
