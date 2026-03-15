import crypto from "crypto";
import MongoWrapper from "../wrappers/MongoWrapper.js";
import { getProvider } from "../providers/index.js";
import { MONGO_DB_NAME } from "../../secrets.js";
import logger from "../utils/logger.js";

const COLLECTION = "memories";
const EMBEDDING_PROVIDER = "google";
const EMBEDDING_MODEL = "gemini-embedding-2-preview";
const EXTRACTION_PROVIDER = "anthropic";
const EXTRACTION_MODEL = "claude-haiku-4-5-20251001";

/**
 * Compute cosine similarity between two vectors.
 * @param {number[]} a
 * @param {number[]} b
 * @returns {number} Similarity score between -1 and 1
 */
function cosineSimilarity(a, b) {
  if (a.length !== b.length) return 0;
  let dotProduct = 0;
  let normA = 0;
  let normB = 0;
  for (let i = 0; i < a.length; i++) {
    dotProduct += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  const denominator = Math.sqrt(normA) * Math.sqrt(normB);
  return denominator === 0 ? 0 : dotProduct / denominator;
}

/**
 * Generate an embedding for text via the existing provider system.
 * @param {string} text
 * @returns {Promise<number[]>}
 */
async function generateEmbedding(text) {
  const provider = getProvider(EMBEDDING_PROVIDER);
  const result = await provider.generateEmbedding(text, EMBEDDING_MODEL, {});
  return result.embedding;
}

/**
 * Call an AI provider to extract facts from a conversation.
 * Returns an array of { fact, aboutUserId, aboutUsername, category, confidence }.
 * @param {Array} messages - Recent conversation messages
 * @param {Array} participants - Array of { id, username, displayName }
 * @returns {Promise<Array>}
 */
async function extractFactsFromConversation(messages, participants) {
  const provider = getProvider(EXTRACTION_PROVIDER);

  const participantList = participants
    .map((p) => `- ID: ${p.id}, Username: ${p.username}, Display: ${p.displayName || p.username}`)
    .join("\n");

  const conversationText = messages
    .map((m) => `${m.name || m.role}: ${m.content}`)
    .join("\n");

  const systemPrompt = `You are a memory extraction system. Analyze the conversation and extract notable personal facts about the participants. Focus on:
- Personal information (location, occupation, hobbies, pets, family)
- Preferences (favorite things, likes, dislikes)
- Life events (moving, new job, relationships, achievements)
- Notable opinions or beliefs they express about themselves
- Information one user reveals about another user

Do NOT extract:
- Transient conversation topics (what they're currently discussing)
- Greetings, jokes, or casual banter
- Bot commands or technical requests
- Things the AI assistant says about itself
- Opinions about external topics (politics, movies, etc) unless they reveal something personal

For each fact, identify which user it's about. If a user mentions something about another user, the fact is ABOUT the other user but SOURCED from the speaker.

Respond ONLY with a JSON array. Each object must have:
- "fact": string — the personal fact in a concise sentence
- "aboutUserId": string — the Discord user ID this fact is about
- "aboutUsername": string — the username of the person this fact is about
- "sourceUserId": string — who said/revealed this (can be same as aboutUserId)
- "sourceUsername": string — username of the source
- "category": string — one of: "personal", "preference", "gaming", "work", "family", "hobby", "location", "relationship", "achievement", "other"
- "confidence": number — 0.0 to 1.0, how confident this is a real personal fact

If no facts are found, return an empty array: []

Here are the participants:
${participantList}`;

  const aiMessages = [
    { role: "system", content: systemPrompt },
    { role: "user", content: `Extract personal facts from this conversation:\n\n${conversationText}` },
  ];

  const result = await provider.generateText(aiMessages, EXTRACTION_MODEL, {
    maxTokens: 1000,
    temperature: 0.1,
  });

  const text = result.text || "";

  // Parse JSON from the response (handle markdown code blocks)
  let jsonText = text.trim();
  const jsonMatch = jsonText.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (jsonMatch) {
    jsonText = jsonMatch[1].trim();
  }

  try {
    const facts = JSON.parse(jsonText);
    if (!Array.isArray(facts)) return [];
    // Validate each fact has the required fields
    return facts.filter(
      (f) =>
        f.fact &&
        f.aboutUserId &&
        f.aboutUsername &&
        typeof f.confidence === "number" &&
        f.confidence >= 0.5,
    );
  } catch {
    logger.warn("[MemoryService] Failed to parse extraction result:", jsonText.substring(0, 200));
    return [];
  }
}

/**
 * MemoryService — manages long-term user memories in MongoDB.
 */
const MemoryService = {
  /**
   * Extract and store memories from a conversation chunk.
   *
   * @param {object} params
   * @param {string} params.guildId
   * @param {string} params.channelId
   * @param {Array} params.messages - Recent conversation messages
   * @param {Array} params.participants - Array of { id, username, displayName }
   * @param {string} [params.sourceMessageId]
   * @returns {Promise<Array>} The stored memory documents
   */
  async extractAndStore({ guildId, channelId, messages, participants, sourceMessageId }) {
    const client = MongoWrapper.getClient(MONGO_DB_NAME);
    if (!client) throw new Error("Database not available");

    const db = client.db(MONGO_DB_NAME);
    const collection = db.collection(COLLECTION);

    // Extract facts from the conversation via AI
    const facts = await extractFactsFromConversation(messages, participants);
    if (facts.length === 0) {
      logger.info("[MemoryService] No personal facts extracted from conversation.");
      return [];
    }

    logger.info(`[MemoryService] Extracted ${facts.length} fact(s), generating embeddings...`);

    const storedMemories = [];
    const now = new Date().toISOString();

    for (const fact of facts) {
      try {
        // Check for duplicate facts (same user + very similar content)
        const existingMemories = await collection
          .find({ guildId, aboutUserId: fact.aboutUserId })
          .toArray();

        if (existingMemories.length > 0) {
          // Generate embedding for the new fact
          const newEmbedding = await generateEmbedding(fact.fact);

          // Check if a very similar fact already exists
          const isDuplicate = existingMemories.some((existing) => {
            if (!existing.embedding) return false;
            const similarity = cosineSimilarity(newEmbedding, existing.embedding);
            return similarity > 0.92; // High threshold for dedup
          });

          if (isDuplicate) {
            logger.info(`[MemoryService] Skipping duplicate fact: "${fact.fact.substring(0, 60)}..."`);
            continue;
          }

          // Store with the already-computed embedding
          const memory = {
            id: crypto.randomUUID(),
            guildId,
            channelId,
            aboutUserId: fact.aboutUserId,
            aboutUsername: fact.aboutUsername,
            sourceUserId: fact.sourceUserId,
            sourceUsername: fact.sourceUsername,
            fact: fact.fact,
            category: fact.category || "other",
            embedding: newEmbedding,
            confidence: fact.confidence,
            sourceMessageId: sourceMessageId || null,
            createdAt: now,
            updatedAt: now,
          };

          await collection.insertOne(memory);
          storedMemories.push(memory);
          logger.info(`[MemoryService] Stored: "${fact.fact.substring(0, 60)}..." (about: ${fact.aboutUsername})`);
        } else {
          // No existing memories, generate embedding and store
          const embedding = await generateEmbedding(fact.fact);

          const memory = {
            id: crypto.randomUUID(),
            guildId,
            channelId,
            aboutUserId: fact.aboutUserId,
            aboutUsername: fact.aboutUsername,
            sourceUserId: fact.sourceUserId,
            sourceUsername: fact.sourceUsername,
            fact: fact.fact,
            category: fact.category || "other",
            embedding,
            confidence: fact.confidence,
            sourceMessageId: sourceMessageId || null,
            createdAt: now,
            updatedAt: now,
          };

          await collection.insertOne(memory);
          storedMemories.push(memory);
          logger.info(`[MemoryService] Stored: "${fact.fact.substring(0, 60)}..." (about: ${fact.aboutUsername})`);
        }
      } catch (err) {
        logger.error(`[MemoryService] Failed to store fact: ${err.message}`);
      }
    }

    return storedMemories;
  },

  /**
   * Search for relevant memories using cosine similarity.
   *
   * @param {object} params
   * @param {string} params.guildId
   * @param {string[]} [params.userIds] - Filter to memories about these users
   * @param {string} params.queryText - Text to search for
   * @param {number} [params.limit=10]
   * @returns {Promise<Array>} Relevant memories sorted by relevance
   */
  async search({ guildId, userIds, queryText, limit = 10 }) {
    const client = MongoWrapper.getClient(MONGO_DB_NAME);
    if (!client) throw new Error("Database not available");

    const db = client.db(MONGO_DB_NAME);
    const collection = db.collection(COLLECTION);

    // Generate embedding for the search query
    const queryEmbedding = await generateEmbedding(queryText);

    // Build the filter
    const filter = { guildId };
    if (userIds && userIds.length > 0) {
      filter.aboutUserId = { $in: userIds };
    }

    // Fetch all memories matching the filter
    const memories = await collection
      .find(filter, { projection: { embedding: 1, fact: 1, aboutUserId: 1, aboutUsername: 1, category: 1, confidence: 1, createdAt: 1 } })
      .toArray();

    if (memories.length === 0) return [];

    // Compute cosine similarity and sort
    const scored = memories
      .filter((m) => m.embedding && m.embedding.length > 0)
      .map((m) => ({
        id: m._id,
        fact: m.fact,
        aboutUserId: m.aboutUserId,
        aboutUsername: m.aboutUsername,
        category: m.category,
        confidence: m.confidence,
        createdAt: m.createdAt,
        score: cosineSimilarity(queryEmbedding, m.embedding),
      }))
      .filter((m) => m.score > 0.3) // Minimum relevance threshold
      .sort((a, b) => b.score - a.score)
      .slice(0, limit);

    logger.info(`[MemoryService] Search found ${scored.length} relevant memories (from ${memories.length} total)`);

    return scored;
  },

  /**
   * List all memories for a user in a guild.
   *
   * @param {string} guildId
   * @param {string} userId
   * @param {number} [limit=50]
   * @param {number} [skip=0]
   * @returns {Promise<{ memories: Array, total: number }>}
   */
  async list(guildId, userId, limit = 50, skip = 0) {
    const client = MongoWrapper.getClient(MONGO_DB_NAME);
    if (!client) throw new Error("Database not available");

    const db = client.db(MONGO_DB_NAME);
    const collection = db.collection(COLLECTION);

    const filter = { guildId, aboutUserId: userId };

    const [memories, total] = await Promise.all([
      collection
        .find(filter, { projection: { embedding: 0 } })
        .sort({ createdAt: -1 })
        .skip(skip)
        .limit(limit)
        .toArray(),
      collection.countDocuments(filter),
    ]);

    return { memories, total };
  },

  /**
   * Delete a specific memory by its id field.
   *
   * @param {string} memoryId
   * @returns {Promise<boolean>} Whether a document was deleted
   */
  async delete(memoryId) {
    const client = MongoWrapper.getClient(MONGO_DB_NAME);
    if (!client) throw new Error("Database not available");

    const db = client.db(MONGO_DB_NAME);
    const collection = db.collection(COLLECTION);

    const result = await collection.deleteOne({ id: memoryId });
    return result.deletedCount > 0;
  },

  /**
   * Ensure indexes exist on the memories collection.
   */
  async ensureIndexes() {
    const client = MongoWrapper.getClient(MONGO_DB_NAME);
    if (!client) return;

    const db = client.db(MONGO_DB_NAME);
    const collection = db.collection(COLLECTION);

    await collection.createIndex({ guildId: 1, aboutUserId: 1 });
    await collection.createIndex({ guildId: 1 });
    await collection.createIndex({ id: 1 }, { unique: true });
    logger.info("[MemoryService] Indexes ensured on memories collection.");
  },
};

export default MemoryService;
