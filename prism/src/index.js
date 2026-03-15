import express from "express";
import cors from "cors";
import http from "http";
import { WebSocketServer } from "ws";

import { errorHandler } from "./utils/errors.js";
import logger from "./utils/logger.js";
import { listProviders } from "./providers/index.js";
import { setupWebSocket } from "./websocket/index.js";
import { authMiddleware } from "./middleware/AuthMiddleware.js";
import {
    PORT,
    MONGO_URI,
    MONGO_DB_NAME,
    MINIO_ENDPOINT,
    MINIO_ACCESS_KEY,
    MINIO_SECRET_KEY,
    MINIO_BUCKET_NAME,
} from "../secrets.js";
import MongoWrapper from "./wrappers/MongoWrapper.js";
import MinioWrapper from "./wrappers/MinioWrapper.js";

// Routes
import chatRouter from "./routes/chat.js";
import audioRouter from "./routes/audio.js";
import embedRouter from "./routes/embed.js";
import configRouter from "./routes/config.js";
import conversationsRouter from "./routes/conversations.js";
import filesRouter from "./routes/files.js";
import memoryRouter from "./routes/memory.js";
import MemoryService from "./services/MemoryService.js";
import adminRouter from "./routes/admin.js";
import workflowsRouter from "./routes/workflows.js";

const app = express();
const server = http.createServer(app);

// Middleware
app.use(cors());
app.use(express.json({ limit: "50mb" }));

// Endpoint registry (single source of truth for health check + startup logs)
const ENDPOINTS = {
    rest: [
        "/config",
        "/chat",
        "/text-to-audio",
        "/audio-to-text",
        "/embed",
        "/conversations",
        "/memory",
        "/files",
        "/workflows",
    ],
    websocket: ["/ws/chat", "/ws/text-to-audio"],
    admin: ["/admin", "/admin/lm-studio"],
};

// Health check (public — no auth required)
app.get("/", (_req, res) => {
    res.json({
        name: "Prism the AI Gateway",
        version: "1.0.0",
        providers: listProviders(),
        endpoints: ENDPOINTS,
    });
});

// Admin routes (own auth via x-admin-secret)
app.use("/admin", adminRouter);

// Public routes (no auth required)
app.use("/files", filesRouter);

// Auth gate — everything below requires a valid x-api-secret header
app.use(authMiddleware);

// REST routes
app.use("/config", configRouter);
app.use("/chat", chatRouter);
app.use("/text-to-audio", audioRouter);
app.use("/audio-to-text", audioRouter);
app.use("/embed", embedRouter);
app.use("/conversations", conversationsRouter);
app.use("/memory", memoryRouter);
app.use("/workflows", workflowsRouter);

// Error handler (must be last)
app.use(errorHandler);

// WebSocket server
const wss = new WebSocketServer({ server });
setupWebSocket(wss);

// Start
(async () => {
    await MongoWrapper.createClient(MONGO_DB_NAME, MONGO_URI);
    await MemoryService.ensureIndexes();

    // Initialize MinIO if all secrets are configured
    if (
        MINIO_ENDPOINT &&
        MINIO_ACCESS_KEY &&
        MINIO_SECRET_KEY &&
        MINIO_BUCKET_NAME
    ) {
        await MinioWrapper.init(
            MINIO_ENDPOINT,
            MINIO_ACCESS_KEY,
            MINIO_SECRET_KEY,
            MINIO_BUCKET_NAME,
        );
    } else {
        logger.info(
            "MinIO not configured — files will be stored inline in MongoDB",
        );
    }

    server.listen(PORT, () => {
        logger.success(`Prism the AI Gateway is running on port ${PORT}`);
        logger.info("Available providers:", listProviders().join(", "));
        ENDPOINTS.rest.forEach((ep) =>
            logger.info(`  REST  →  http://localhost:${PORT}${ep}`),
        );
        ENDPOINTS.websocket.forEach((ep) =>
            logger.info(`  WS    →  ws://localhost:${PORT}${ep}`),
        );
    });
})();
