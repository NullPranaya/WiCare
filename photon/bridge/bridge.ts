// Photon bridge: spectrum-ts holds the iMessage connection.
//  - POST /send {to, text, key}  (local only, shared secret, allowlisted recipients, idempotent by key)
//  - forwards every inbound text message to the Python backend /webhooks/photon
// Run: bun bridge.ts   (or: npx tsx bridge.ts)
import { createServer } from "node:http";
import { Spectrum, text } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";

const env = process.env;
const PORT = Number(env.BRIDGE_PORT ?? 8787);
const SECRET = env.BRIDGE_SECRET ?? "";
const WEBHOOK = env.AURACARE_WEBHOOK ?? "http://127.0.0.1:8000/webhooks/photon";
const ALLOWED = new Set((env.ALLOWED_TO ?? "").split(",").map((s) => s.trim()).filter(Boolean));
if (!SECRET || ALLOWED.size === 0) throw new Error("set BRIDGE_SECRET and ALLOWED_TO (comma-separated E.164 test numbers)");

const app = await Spectrum({
  projectId: env.PROJECT_ID!,
  projectSecret: env.PROJECT_SECRET!,
  providers: [imessage.config()],
});
const im = imessage(app);
const done = new Map<string, unknown>(); // idempotency: key -> result

async function send(to: string, body: string) {
  const user = await im.user(to);
  const space = await im.space.create(user);
  const msg = await space.send(text(body));
  return { spaceId: space.id, messageId: msg?.id ?? null };
}

createServer((req, res) => {
  const reply = (code: number, obj: unknown) => {
    res.writeHead(code, { "content-type": "application/json" });
    res.end(JSON.stringify(obj));
  };
  if (req.method !== "POST" || req.url !== "/send") return reply(404, { error: "not found" });
  if (req.headers["x-bridge-secret"] !== SECRET) return reply(401, { error: "unauthorized" });
  let raw = "";
  req.on("data", (c) => (raw += c));
  req.on("end", async () => {
    try {
      const { to, text: body, key } = JSON.parse(raw);
      if (!ALLOWED.has(to)) return reply(403, { error: "recipient not allowlisted" });
      if (!key || typeof body !== "string") return reply(400, { error: "key and text required" });
      if (done.has(key)) return reply(200, { duplicate: true, ...(done.get(key) as object) });
      const result = await send(to, body);
      done.set(key, result);
      reply(200, { accepted: true, ...result });
    } catch (e) {
      reply(502, { error: String(e) });
    }
  });
}).listen(PORT, "127.0.0.1", () => console.log(`bridge on 127.0.0.1:${PORT}`));

for await (const [space, message] of app.messages) {
  if (message.content.type !== "text" || !message.sender) continue;
  const payload = {
    event: "messages",
    space: { id: space.id },
    message: {
      id: message.id,
      sender: { id: message.sender.id },
      content: { type: "text", text: message.content.text },
      timestamp: new Date().toISOString(),
    },
  };
  try {
    await fetch(WEBHOOK, {
      method: "POST",
      headers: { "content-type": "application/json", "x-bridge-secret": SECRET },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    console.error("forward failed", e);
  }
}
