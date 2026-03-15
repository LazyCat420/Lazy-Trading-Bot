import { Router } from "express";
import { ObjectId } from "mongodb";
import logger from "../utils/logger.js";
import MongoWrapper from "../wrappers/MongoWrapper.js";
import FileService from "../services/FileService.js";
import { assembleGraph } from "../services/WorkflowAssembler.js";
import { MONGO_DB_NAME } from "../../secrets.js";

const router = Router();
const WORKFLOWS_COL = "workflows";

/** Media fields on messages that may contain base64 data URLs. */
const MEDIA_FIELDS = ["images", "audio", "video", "pdf"];

function getDb() {
    const client = MongoWrapper.getClient(MONGO_DB_NAME);
    if (!client) return null;
    return client.db(MONGO_DB_NAME);
}

/**
 * Upload a single value if it's a base64 data URL, returning the minio:// ref.
 * Non-data-URL strings (minio://, http://, etc.) pass through unchanged.
 */
async function uploadIfDataUrl(value, category = "uploads", project = null, username = null) {
    if (typeof value === "string" && value.startsWith("data:")) {
        try {
            const { ref } = await FileService.uploadFile(value, category, project, username);
            return ref;
        } catch (err) {
            logger.error(`Workflow file upload failed: ${err.message}`);
            return value;
        }
    }
    return value;
}

/**
 * Walk all workflow nodes and upload any base64 data URLs to MinIO,
 * replacing them with minio:// refs.  Mirrors the extractFiles pattern
 * used by ConversationService for chat messages.
 */
async function extractWorkflowFiles(nodes, project = null, username = null) {
    if (!Array.isArray(nodes) || !FileService.isExternalStorage()) return nodes;

    const processed = [];
    for (const node of nodes) {
        const updated = { ...node };

        // 1. Node-level content (asset input nodes store content as a data URL)
        if (typeof updated.content === "string" && updated.content.startsWith("data:")) {
            updated.content = await uploadIfDataUrl(updated.content, "uploads", project, username);
        }

        // 2. Messages array (conversation / model nodes)
        if (Array.isArray(updated.messages)) {
            const newMessages = [];
            for (const msg of updated.messages) {
                const m = { ...msg };
                for (const field of MEDIA_FIELDS) {
                    const val = m[field];
                    if (Array.isArray(val)) {
                        const arr = [];
                        for (const item of val) {
                            arr.push(await uploadIfDataUrl(item, "uploads", project, username));
                        }
                        m[field] = arr;
                    } else if (typeof val === "string" && val.startsWith("data:")) {
                        m[field] = await uploadIfDataUrl(val, "uploads", project, username);
                    }
                }
                newMessages.push(m);
            }
            updated.messages = newMessages;
        }

        // 3. Viewer nodes store receivedOutputs — same { modality: data } shape
        if (updated.receivedOutputs && typeof updated.receivedOutputs === "object") {
            const newReceived = {};
            for (const [mod, data] of Object.entries(updated.receivedOutputs)) {
                newReceived[mod] = await uploadIfDataUrl(data, "uploads", project, username);
            }
            updated.receivedOutputs = newReceived;
        }

        processed.push(updated);
    }
    return processed;
}

/**
 * Walk nodeResults and upload any base64 data URLs to MinIO.
 * Shape: { [nodeId]: { [modality]: dataUrl | messagesArray } }
 */
async function extractNodeResultFiles(nodeResults, project = null, username = null) {
    if (!nodeResults || typeof nodeResults !== "object" || !FileService.isExternalStorage()) return nodeResults;

    const processed = {};
    for (const [nodeId, outputs] of Object.entries(nodeResults)) {
        if (!outputs || typeof outputs !== "object") {
            processed[nodeId] = outputs;
            continue;
        }
        const newOutputs = {};
        for (const [mod, data] of Object.entries(outputs)) {
            // conversation modality is an array of message objects with nested media
            if (mod === "conversation" && Array.isArray(data)) {
                const msgs = [];
                for (const msg of data) {
                    const m = { ...msg };
                    for (const field of MEDIA_FIELDS) {
                        const val = m[field];
                        if (Array.isArray(val)) {
                            const arr = [];
                            for (const item of val) {
                                arr.push(await uploadIfDataUrl(item, "uploads", project, username));
                            }
                            m[field] = arr;
                        } else if (typeof val === "string" && val.startsWith("data:")) {
                            m[field] = await uploadIfDataUrl(val, "uploads", project, username);
                        }
                    }
                    msgs.push(m);
                }
                newOutputs[mod] = msgs;
            } else {
                newOutputs[mod] = await uploadIfDataUrl(data, "uploads", project, username);
            }
        }
        processed[nodeId] = newOutputs;
    }
    return processed;
}

/**
 * Convert a minio:// ref to an HTTP /files/ URL.
 * Non-minio strings (data URLs, http URLs, etc.) pass through unchanged.
 */
function resolveMinioRef(value, baseUrl) {
    if (typeof value === "string" && value.startsWith("minio://")) {
        const key = value.replace("minio://", "");
        return `${baseUrl}/files/${key}`;
    }
    return value;
}

/**
 * Walk a workflow document and replace all minio:// refs with HTTP /files/ URLs
 * so the frontend receives browser-renderable URLs directly.
 */
function resolveWorkflowFileRefs(workflow, baseUrl) {
    // Resolve nodes
    if (Array.isArray(workflow.nodes)) {
        for (const node of workflow.nodes) {
            // Node-level content (asset input nodes)
            if (typeof node.content === "string") {
                node.content = resolveMinioRef(node.content, baseUrl);
            }

            // Messages array (conversation / model nodes)
            if (Array.isArray(node.messages)) {
                for (const msg of node.messages) {
                    for (const field of MEDIA_FIELDS) {
                        const val = msg[field];
                        if (Array.isArray(val)) {
                            msg[field] = val.map((item) => resolveMinioRef(item, baseUrl));
                        } else if (typeof val === "string") {
                            msg[field] = resolveMinioRef(val, baseUrl);
                        }
                    }
                }
            }

            // Viewer receivedOutputs
            if (node.receivedOutputs && typeof node.receivedOutputs === "object") {
                for (const [mod, data] of Object.entries(node.receivedOutputs)) {
                    node.receivedOutputs[mod] = resolveMinioRef(data, baseUrl);
                }
            }
        }
    }

    // Resolve nodeResults: { [nodeId]: { [modality]: value | messagesArray } }
    if (workflow.nodeResults && typeof workflow.nodeResults === "object") {
        for (const outputs of Object.values(workflow.nodeResults)) {
            if (!outputs || typeof outputs !== "object") continue;
            for (const [mod, data] of Object.entries(outputs)) {
                // conversation modality is an array of message objects with nested media
                if (mod === "conversation" && Array.isArray(data)) {
                    for (const msg of data) {
                        for (const field of MEDIA_FIELDS) {
                            const val = msg[field];
                            if (Array.isArray(val)) {
                                msg[field] = val.map((item) => resolveMinioRef(item, baseUrl));
                            } else if (typeof val === "string") {
                                msg[field] = resolveMinioRef(val, baseUrl);
                            }
                        }
                    }
                } else {
                    outputs[mod] = resolveMinioRef(data, baseUrl);
                }
            }
        }
    }

    return workflow;
}

/**
 * Build the external base URL from the request (handles proxies, HTTPS, etc.).
 */
function getBaseUrl(req) {
    const proto = req.headers["x-forwarded-proto"] || req.protocol || "http";
    const host = req.headers["x-forwarded-host"] || req.get("host");
    return `${proto}://${host}`;
}

/**
 * GET /workflows
 * List all saved workflows (metadata only).
 */
router.get("/", async (req, res, next) => {
    try {
        const db = getDb();
        if (!db) return res.status(503).json({ error: "Database not available" });

        const source = req.query.source || "retina";
        const query = source === "all" ? {} : { source };

        const workflows = await db
            .collection(WORKFLOWS_COL)
            .find(query)
            .sort({ updatedAt: -1 })
            .project({ nodes: 0, connections: 0, nodeResults: 0, nodeStatuses: 0 })
            .toArray();

        res.json(workflows);
    } catch (error) {
        logger.error(`GET /workflows error: ${error.message}`);
        next(error);
    }
});

/**
 * GET /workflows/:id
 * Get a single workflow by ID (full document).
 */
router.get("/:id", async (req, res, next) => {
    try {
        const db = getDb();
        if (!db) return res.status(503).json({ error: "Database not available" });

        let filter;
        try {
            filter = { _id: new ObjectId(req.params.id) };
        } catch {
            filter = { workflowId: req.params.id };
        }

        const workflow = await db.collection(WORKFLOWS_COL).findOne(filter);
        if (!workflow) return res.status(404).json({ error: "Workflow not found" });

        const baseUrl = getBaseUrl(req);
        resolveWorkflowFileRefs(workflow, baseUrl);

        res.json(workflow);
    } catch (error) {
        logger.error(`GET /workflows/:id error: ${error.message}`);
        next(error);
    }
});

/**
 * POST /workflows
 * Save a new workflow document.
 *
 * Accepts two payload formats:
 * 1. Raw steps (from Lupos/bots): { steps, messageId, ... }
 *    → Prism assembles the visual graph using WorkflowAssembler
 * 2. Pre-built graph (from Retina editor): { nodes, connections, ... }
 *    → Passes through unchanged
 */
router.post("/", async (req, res, next) => {
    try {
        const db = getDb();
        if (!db) return res.status(503).json({ error: "Database not available" });

        const project = req.headers["x-project"] || null;
        const username = req.headers["x-username"] || null;

        let { nodes, connections, nodeResults, nodeStatuses } = req.body;

        // If the payload has steps but no pre-built nodes, assemble the graph
        if (Array.isArray(req.body.steps) && req.body.steps.length > 0 && !Array.isArray(nodes)) {
            const graph = assembleGraph(req.body.steps);
            nodes = graph.nodes;
            connections = graph.connections;
            nodeResults = graph.nodeResults;
            nodeStatuses = graph.nodeStatuses;
        }

        const processedNodes = await extractWorkflowFiles(nodes, project, username);
        const processedResults = await extractNodeResultFiles(nodeResults, project, username);

        const now = new Date().toISOString();
        const workflow = {
            ...req.body,
            nodes: processedNodes || nodes,
            connections: connections || req.body.connections,
            nodeResults: processedResults || nodeResults,
            nodeStatuses: nodeStatuses || req.body.nodeStatuses,
            source: req.body.source || "retina",
            nodeCount: Array.isArray(processedNodes || nodes) ? (processedNodes || nodes).length : 0,
            connectionCount: Array.isArray(connections) ? connections.length : 0,
            createdAt: now,
            updatedAt: now,
        };

        const result = await db.collection(WORKFLOWS_COL).insertOne(workflow);
        res.json({ success: true, id: result.insertedId.toString() });
    } catch (error) {
        logger.error(`POST /workflows error: ${error.message}`);
        next(error);
    }
});

/**
 * PUT /workflows/:id
 * Update an existing workflow.
 */
router.put("/:id", async (req, res, next) => {
    try {
        const db = getDb();
        if (!db) return res.status(503).json({ error: "Database not available" });

        let filter;
        try {
            filter = { _id: new ObjectId(req.params.id) };
        } catch {
            filter = { workflowId: req.params.id };
        }

        const project = req.headers["x-project"] || null;
        const username = req.headers["x-username"] || null;
        const body = { ...req.body };
        if (Array.isArray(body.nodes)) {
            body.nodes = await extractWorkflowFiles(body.nodes, project, username);
            body.nodeCount = body.nodes.length;
        }
        if (body.nodeResults && typeof body.nodeResults === "object") {
            body.nodeResults = await extractNodeResultFiles(body.nodeResults, project, username);
        }
        if (Array.isArray(body.connections)) body.connectionCount = body.connections.length;
        const update = {
            $set: {
                ...body,
                updatedAt: new Date().toISOString(),
            },
        };
        delete update.$set._id; // prevent overwriting _id

        const result = await db.collection(WORKFLOWS_COL).updateOne(filter, update);
        if (result.matchedCount === 0) return res.status(404).json({ error: "Workflow not found" });

        res.json({ success: true });
    } catch (error) {
        logger.error(`PUT /workflows/:id error: ${error.message}`);
        next(error);
    }
});

/**
 * PATCH /workflows/:id/conversations
 * Append conversation IDs generated during workflow execution.
 * Body: { conversationIds: string[] }
 */
router.patch("/:id/conversations", async (req, res, next) => {
    try {
        const db = getDb();
        if (!db) return res.status(503).json({ error: "Database not available" });

        let filter;
        try {
            filter = { _id: new ObjectId(req.params.id) };
        } catch {
            filter = { workflowId: req.params.id };
        }

        const { conversationIds } = req.body;
        if (!Array.isArray(conversationIds) || conversationIds.length === 0) {
            return res.status(400).json({ error: "conversationIds array required" });
        }

        const result = await db.collection(WORKFLOWS_COL).updateOne(filter, {
            $push: { conversationIds: { $each: conversationIds } },
            $set: { updatedAt: new Date().toISOString() },
        });

        if (result.matchedCount === 0) return res.status(404).json({ error: "Workflow not found" });
        res.json({ success: true });
    } catch (error) {
        logger.error(`PATCH /workflows/:id/conversations error: ${error.message}`);
        next(error);
    }
});

/**
 * DELETE /workflows/:id
 * Delete a workflow by ID.
 */
router.delete("/:id", async (req, res, next) => {
    try {
        const db = getDb();
        if (!db) return res.status(503).json({ error: "Database not available" });

        let filter;
        try {
            filter = { _id: new ObjectId(req.params.id) };
        } catch {
            filter = { workflowId: req.params.id };
        }

        await db.collection(WORKFLOWS_COL).deleteOne(filter);
        res.json({ success: true });
    } catch (error) {
        logger.error(`DELETE /workflows/:id error: ${error.message}`);
        next(error);
    }
});

export default router;
