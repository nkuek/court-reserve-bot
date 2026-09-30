// Holds one linked-device WhatsApp session and exposes the few calls the sidecar needs.
import makeWASocket, {
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import { rmSync } from "node:fs";
import pino from "pino";
import qrcode from "qrcode-terminal";

/** Session keys live here. Deleting the folder forces a fresh link. */
export const authDir = () =>
  process.env.WHATSAPP_AUTH_DIR ?? new URL("./auth/", import.meta.url).pathname;

/** Delay before reopening the socket after a non-fatal disconnect. */
const RECONNECT_MS = 5000;

export function createClient({ phone, onLinked, log }) {
  const logger = pino({ level: "silent" });
  let sock = null;
  let connected = false;
  let pairingRequested = false;

  async function start() {
    const { state, saveCreds } = await useMultiFileAuthState(authDir());
    const { version } = await fetchLatestBaileysVersion();

    sock = makeWASocket({
      version,
      logger,
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      // The library default. Other identities have been rejected at pairing time.
      browser: Browsers.macOS("Chrome"),
      // An "online" linked device silences push notifications on the phone.
      markOnlineOnConnect: false,
      syncFullHistory: false,
    });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", async (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr && !state.creds.registered) {
        if (phone) {
          if (!pairingRequested) {
            pairingRequested = true;
            const code = await sock.requestPairingCode(phone);
            log(`Pairing code: ${code}`);
            log("WhatsApp > Linked devices > Link a device > Link with phone number instead");
          }
        } else {
          qrcode.generate(qr, { small: true });
          log("Scan with WhatsApp > Linked devices > Link a device");
        }
      }

      if (connection === "open") {
        connected = true;
        log(`Connected as ${sock.user?.id ?? "unknown"}`);
        onLinked?.();
      }

      if (connection === "close") {
        connected = false;
        pairingRequested = false;
        const code = lastDisconnect?.error?.output?.statusCode;
        const reason = lastDisconnect?.error?.message ?? "";
        if (code === DisconnectReason.loggedOut) {
          if (state.creds.registered) {
            log(`Logged out by WhatsApp. Delete ${authDir()} and link again.`);
            process.exit(1);
          }
          // A pairing attempt that expired leaves half-made creds the server now rejects.
          log("Pairing attempt expired, starting over with fresh keys");
          rmSync(authDir(), { recursive: true, force: true });
        } else {
          log(`Connection closed (status ${code ?? "unknown"} ${reason}), reconnecting`);
        }
        setTimeout(start, RECONNECT_MS);
      }
    });
  }

  async function listGroups() {
    const groups = await sock.groupFetchAllParticipating();
    return Object.values(groups).map((g) => ({ id: g.id, subject: g.subject }));
  }

  async function sendPoll(jid, { question, options, selectableCount = 1 }) {
    const sent = await sock.sendMessage(jid, {
      poll: { name: question, values: options, selectableCount },
    });
    return sent?.key?.id ?? null;
  }

  return {
    start,
    listGroups,
    sendPoll,
    isConnected: () => connected,
    me: () => sock?.user?.id ?? null,
  };
}
