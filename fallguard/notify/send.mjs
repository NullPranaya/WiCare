// Send one iMessage through the Photon Beta (2.0) REST API, https://api.photon.codes/v1.
// Usage: node send.mjs <E.164 recipient> <text>
// Credentials: SPECTRUM_PROJECT_ID (pho_prj_...) and SPECTRUM_PROJECT_SECRET (pho_sk_ project key).
// The project uses a shared iMessage line, so the recipient must have an active assignment on
// that line and must have texted it first (opt-in received).
import { randomUUID } from "node:crypto";

const API = process.env.PHOTON_API_URL ?? "https://api.photon.codes";
const REQUEST_TIMEOUT_MS = 15_000;

const [recipient, text] = process.argv.slice(2);
const { SPECTRUM_PROJECT_ID: projectId, SPECTRUM_PROJECT_SECRET: apiKey } = process.env;

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

if (!recipient || !/^\+[1-9]\d{6,14}$/.test(recipient)) fail("recipient must be an E.164 number like +15551234567");
if (!text) fail("message text is required");
if (!projectId || !apiKey) fail("SPECTRUM_PROJECT_ID and SPECTRUM_PROJECT_SECRET must be set");
if (!projectId.startsWith("pho_prj_")) fail("SPECTRUM_PROJECT_ID must be a Photon Beta project ID (pho_prj_...)");

async function api(method, path, { body, idempotencyKey } = {}) {
  const headers = { Authorization: `Bearer ${apiKey}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const response = await fetch(`${API}/v1/projects/${projectId}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  const raw = await response.text();
  let data;
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    data = { detail: raw };
  }
  if (!response.ok) {
    const reason = data.detail ?? data.title ?? data.message ?? raw;
    throw new Error(`${method} ${path} -> HTTP ${response.status}: ${String(reason).slice(0, 200)}`);
  }
  return data;
}

// Create-or-get, so it returns the existing user for a known handle.
const user = (handle) => api("POST", "/users", { body: { handle, platform: "imessage" } });

async function lineFor(handle) {
  const { assignments = [] } = await api("GET", "/platforms/imessage/assignments?pageSize=200");
  const assignment = assignments.find((a) => a.handle === handle && a.optIn?.state === "received");
  if (!assignment) {
    throw new Error(`${handle} has no opted-in assignment on the shared iMessage line; text the line from that phone first`);
  }
  return assignment.assignedPhoneNumber;
}

try {
  // Check the assignment first so an unknown number never creates a stray user.
  const line = await lineFor(recipient);
  const [nurse, agent] = await Promise.all([user(recipient), user(line)]);
  const space = await api("POST", "/spaces", {
    body: { participantIds: [agent.id, nurse.id], platform: "imessage" },
    idempotencyKey: `fallguard-space-${agent.id}-${nurse.id}`,
  });
  await api("POST", "/messages", {
    body: { senderId: agent.id, spaceId: space.id, content: { type: "text", format: "plain", text } },
    idempotencyKey: `fallguard-msg-${randomUUID()}`,
  });
} catch (error) {
  fail(`iMessage send failed: ${error?.message ?? error}`);
}
