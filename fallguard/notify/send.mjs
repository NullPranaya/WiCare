// Send one iMessage through Photon Spectrum (cloud).
// Usage: node send.mjs <E.164 recipient> <text>
// Credentials: SPECTRUM_PROJECT_ID and SPECTRUM_PROJECT_SECRET in the environment.
import { Spectrum } from "spectrum-ts";
import { imessage } from "spectrum-ts/providers/imessage";

const [recipient, text] = process.argv.slice(2);
const { SPECTRUM_PROJECT_ID: projectId, SPECTRUM_PROJECT_SECRET: projectSecret } = process.env;

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

if (!recipient || !/^\+[1-9]\d{6,14}$/.test(recipient)) fail("recipient must be an E.164 number like +15551234567");
if (!text) fail("message text is required");
if (!projectId || !projectSecret) fail("SPECTRUM_PROJECT_ID and SPECTRUM_PROJECT_SECRET must be set");

let app;
try {
  app = await Spectrum({ projectId, projectSecret, providers: [imessage.config()] });
  const im = imessage(app);
  const user = await im.user(recipient);
  const dm = await im.space.create(user);
  await dm.send(text);
} catch (error) {
  fail(`iMessage send failed: ${error?.message ?? error}`);
} finally {
  try {
    await app?.stop();
  } catch {
    // ignore shutdown errors after a send attempt
  }
}
process.exit(0);
