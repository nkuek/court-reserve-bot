// Local HTTP front for the WhatsApp session. Binds to loopback only.
//
//   node server.js                 run the sidecar
//   node server.js --link          link this machine, exit once connected
//   node server.js --phone +1555…  link with a pairing code instead of a QR
//
//   GET  /health      {connected, me, group}
//   GET  /groups      [{id, subject}]
//   POST /send-poll   {question, options?, selectableCount?, to?} -> {id}
import http from "node:http";
import { createClient } from "./client.js";

// libsignal dumps every closed session, private keys included, through console.info.
const nativeInfo = console.info;
console.info = (...args) => {
  if (typeof args[0] === "string" && args[0].startsWith("Closing session")) return;
  nativeInfo(...args);
};

try {
  process.loadEnvFile(new URL("../.env", import.meta.url).pathname);
} catch {
  // No repo .env; rely on the process environment.
}

const PORT = Number(process.env.WHATSAPP_PORT ?? 8765);
const GROUP_JID = process.env.WHATSAPP_GROUP_JID ?? null;
const DEFAULT_OPTIONS = ["Yes", "No"];

const args = process.argv.slice(2);
const linkOnly = args.includes("--link");
const phoneIdx = args.indexOf("--phone");
const phone = phoneIdx === -1 ? null : args[phoneIdx + 1]?.replace(/\D/g, "");

const log = (msg) => console.log(`[${new Date().toISOString()}] ${msg}`);

const client = createClient({
  phone,
  log,
  onLinked: () => {
    if (linkOnly) {
      log("Linked. Session saved; start the sidecar normally now.");
      // Give creds.update a moment to flush before exiting.
      setTimeout(() => process.exit(0), 1500);
    }
  },
});

function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (chunk) => {
      data += chunk;
      if (data.length > 64 * 1024) reject(new Error("body too large"));
    });
    req.on("end", () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch (e) {
        reject(e);
      }
    });
    req.on("error", reject);
  });
}

async function handle(req, res) {
  if (req.method === "GET" && req.url === "/health") {
    return json(res, 200, { connected: client.isConnected(), me: client.me(), group: GROUP_JID });
  }

  if (!client.isConnected()) {
    return json(res, 503, { error: "whatsapp not connected" });
  }

  if (req.method === "GET" && req.url === "/groups") {
    return json(res, 200, await client.listGroups());
  }

  if (req.method === "POST" && req.url === "/send-poll") {
    const body = await readBody(req);
    const to = body.to ?? GROUP_JID;
    if (!to) return json(res, 400, { error: "no target: set WHATSAPP_GROUP_JID or pass `to`" });
    if (!body.question) return json(res, 400, { error: "question is required" });
    const id = await client.sendPoll(to, {
      question: body.question,
      options: body.options ?? DEFAULT_OPTIONS,
      selectableCount: body.selectableCount ?? 1,
    });
    log(`Sent poll "${body.question}" to ${to}`);
    return json(res, 200, { id });
  }

  return json(res, 404, { error: "not found" });
}

if (!linkOnly) {
  http
    .createServer((req, res) => {
      handle(req, res).catch((e) => {
        log(`Request failed: ${e.message}`);
        json(res, 500, { error: e.message });
      });
    })
    .listen(PORT, "127.0.0.1", () => log(`Listening on http://127.0.0.1:${PORT}`));
}

client.start().catch((e) => {
  log(`Failed to start: ${e.stack ?? e}`);
  process.exit(1);
});
