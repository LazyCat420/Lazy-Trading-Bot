import MongoWrapper from "../wrappers/MongoWrapper.js";
import { MONGO_DB_NAME } from "../../secrets.js";
import logger from "../utils/logger.js";

const COLLECTION = "requests";

const RequestLogger = {
  /**
   * Log a text-to-text request to MongoDB (fire-and-forget).
   */
  async log({
    requestId,
    endpoint,
    project,
    username,
    provider,
    model,
    success,
    errorMessage = null,
    inputTokens = 0,
    outputTokens = 0,
    estimatedCost = null,
    tokensPerSec = null,
    temperature = null,
    maxTokens = null,
    topP = null,
    topK = null,
    frequencyPenalty = null,
    presencePenalty = null,
    stopSequences = null,
    messageCount = 0,
    inputCharacters = 0,
    outputCharacters = 0,
    timeToGeneration = null,
    generationTime = null,
    totalTime = null,
  }) {
    try {
      const client = MongoWrapper.getClient(MONGO_DB_NAME);
      if (!client) {
        logger.error("RequestLogger: MongoDB client not available");
        return;
      }

      const doc = {
        requestId,
        timestamp: new Date().toISOString(),
        endpoint,
        project,
        username,
        provider,
        model,
        success,
        errorMessage,
        inputTokens,
        outputTokens,
        estimatedCost,
        tokensPerSec,
        temperature,
        maxTokens,
        topP,
        topK,
        frequencyPenalty,
        presencePenalty,
        stopSequences,
        messageCount,
        inputCharacters,
        outputCharacters,
        timeToGeneration,
        generationTime,
        totalTime,
      };

      await client.db(MONGO_DB_NAME).collection(COLLECTION).insertOne(doc);
    } catch (error) {
      logger.error("RequestLogger: failed to save request", error.message);
    }
  },
};

export default RequestLogger;
