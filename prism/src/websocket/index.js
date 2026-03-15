import { GATEWAY_SECRET } from "../../secrets.js";
import { handleChat } from "../routes/chat.js";
import { handleVoice } from "../routes/audio.js";
import logger from "../utils/logger.js";

/**
 * Set up WebSocket handlers on the HTTP server.
 * Routes:
 *   /ws/chat   — Streaming chat (text, images, code, thinking, etc.)
 *   /ws/text-to-audio  — Streaming TTS (binary audio frames)
 */
export function setupWebSocket(wss) {
  wss.on("connection", (ws, req) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const pathname = url.pathname;

    // Auth — accept header or query param
    const secret =
      req.headers["x-api-secret"] || url.searchParams.get("secret");
    if (!secret || secret !== GATEWAY_SECRET) {
      logger.error(`WebSocket auth failed — ${pathname}`);
      ws.send(
        JSON.stringify({
          type: "error",
          message: "Unauthorized — missing or invalid secret",
        }),
      );
      ws.close();
      return;
    }

    const project =
      req.headers["x-project"] || url.searchParams.get("project") || "unknown";
    const username =
      req.headers["x-username"] ||
      url.searchParams.get("username") ||
      "unknown";
    logger.info(
      `WebSocket connection on ${pathname} (project: ${project}, user: ${username})`,
    );

    if (pathname === "/ws/chat") {
      handleWsChat(ws, project, username);
    } else if (pathname === "/ws/text-to-audio") {
      handleWsVoice(ws, project, username);
    } else {
      ws.send(
        JSON.stringify({
          type: "error",
          message: `Unknown WebSocket path: ${pathname}`,
        }),
      );
      ws.close();
    }
  });
}

/**
 * WebSocket chat handler — delegates to shared handleChat() from chat.js.
 */
function handleWsChat(ws, project, username) {
  ws.on("message", async (rawData) => {
    let data;
    try {
      data = JSON.parse(rawData.toString());
    } catch {
      ws.send(JSON.stringify({ type: "error", message: "Invalid JSON" }));
      return;
    }

    await handleChat(
      { ...data, project, username },
      (event) => {
        if (ws.readyState === ws.OPEN) {
          ws.send(JSON.stringify(event));
        }
      },
    );
  });
}

/**
 * WebSocket voice handler — delegates to shared handleVoice() from voice.js.
 * Sends binary audio frames for audio data, JSON for control events.
 */
function handleWsVoice(ws, project, username) {
  ws.on("message", async (rawData) => {
    let data;
    try {
      data = JSON.parse(rawData.toString());
    } catch {
      ws.send(JSON.stringify({ type: "error", message: "Invalid JSON" }));
      return;
    }

    try {
      await handleVoice(
        { ...data, project, username },
        (chunk) => {
          if (ws.readyState === ws.OPEN) {
            ws.send(chunk); // Binary audio frame
          }
        },
        (event) => {
          if (ws.readyState === ws.OPEN) {
            ws.send(JSON.stringify(event));
          }
        },
      );
    } catch {
      // Error already emitted via emitJSON in handleVoice
    }
  });
}
