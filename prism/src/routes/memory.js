import express from "express";
import MemoryService from "../services/MemoryService.js";
import logger from "../utils/logger.js";

const router = express.Router();

/**
 * POST /memory/extract
 * Extract and store memories from a conversation chunk.
 * Body: { guildId, channelId, messages, participants, sourceMessageId? }
 */
router.post("/extract", async (req, res, next) => {
  try {
    const { guildId, channelId, messages, participants, sourceMessageId } = req.body;

    if (!guildId || !messages || !participants) {
      return res.status(400).json({
        error: "Missing required fields: guildId, messages, participants",
      });
    }

    const memories = await MemoryService.extractAndStore({
      guildId,
      channelId,
      messages,
      participants,
      sourceMessageId,
    });

    res.json({ memories, count: memories.length });
  } catch (error) {
    logger.error(`[memory/extract] ${error.message}`);
    next(error);
  }
});

/**
 * POST /memory/search
 * Search for relevant memories using vector similarity.
 * Body: { guildId, userIds?, queryText, limit? }
 */
router.post("/search", async (req, res, next) => {
  try {
    const { guildId, userIds, queryText, limit } = req.body;

    if (!guildId || !queryText) {
      return res.status(400).json({
        error: "Missing required fields: guildId, queryText",
      });
    }

    const memories = await MemoryService.search({
      guildId,
      userIds,
      queryText,
      limit: limit || 10,
    });

    res.json({ memories, count: memories.length });
  } catch (error) {
    logger.error(`[memory/search] ${error.message}`);
    next(error);
  }
});

/**
 * GET /memory/list/:guildId/:userId
 * List all memories for a user in a guild.
 */
router.get("/list/:guildId/:userId", async (req, res, next) => {
  try {
    const { guildId, userId } = req.params;
    const limit = parseInt(req.query.limit) || 50;
    const skip = parseInt(req.query.skip) || 0;

    const result = await MemoryService.list(guildId, userId, limit, skip);
    res.json(result);
  } catch (error) {
    logger.error(`[memory/list] ${error.message}`);
    next(error);
  }
});

/**
 * DELETE /memory/:id
 * Delete a specific memory.
 */
router.delete("/:id", async (req, res, next) => {
  try {
    const deleted = await MemoryService.delete(req.params.id);
    res.json({ deleted });
  } catch (error) {
    logger.error(`[memory/delete] ${error.message}`);
    next(error);
  }
});

export default router;
