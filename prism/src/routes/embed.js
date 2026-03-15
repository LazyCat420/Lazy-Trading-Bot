import express from "express";
import crypto from "crypto";
import { getProvider } from "../providers/index.js";
import { ProviderError } from "../utils/errors.js";
import { TYPES, getDefaultModels, getPricing } from "../config.js";
import RequestLogger from "../services/RequestLogger.js";
import logger from "../utils/logger.js";

const router = express.Router();

/**
 * POST /embed
 * Body: {
 *   provider,          // required
 *   model?,            // optional, falls back to provider default
 *   text?,             // optional — text content
 *   images?,           // optional — array of base64 / data URL strings
 *   audio?,            // optional — base64 / data URL string
 *   video?,            // optional — base64 / data URL string
 *   pdf?,              // optional — base64 / data URL string
 *   taskType?,         // optional — e.g. SEMANTIC_SIMILARITY, RETRIEVAL_DOCUMENT
 *   dimensions?,       // optional — output dimensionality (128–3072)
 * }
 * Response: { embedding, dimensions, provider, model }
 */
router.post("/", async (req, res, next) => {
  const requestId = crypto.randomUUID();
  const requestStart = performance.now();
  let providerName = null;
  let resolvedModel = null;

  try {
    const {
      provider: pName,
      model,
      text,
      images,
      audio,
      video,
      pdf,
      taskType,
      dimensions,
    } = req.body;
    providerName = pName;

    if (!providerName) {
      throw new ProviderError(
        "server",
        "Missing required field: provider",
        400,
      );
    }

    // At least one content input is required
    const hasContent = text || (images && images.length > 0) || audio || video || pdf;
    if (!hasContent) {
      throw new ProviderError(
        "server",
        "At least one content input is required (text, images, audio, video, or pdf)",
        400,
      );
    }

    const provider = getProvider(providerName);
    if (!provider.generateEmbedding) {
      throw new ProviderError(
        providerName,
        `Provider "${providerName}" does not support embeddings`,
        400,
      );
    }

    resolvedModel =
      model || getDefaultModels(TYPES.TEXT, TYPES.EMBEDDING)[providerName] || null;

    // Build content for provider — text-only vs multimodal
    let content;
    const isMultimodal = (images && images.length > 0) || audio || video || pdf;

    if (!isMultimodal && text) {
      content = text;
    } else {
      const parts = [];

      if (text) {
        parts.push({ text });
      }

      const parseDataUrl = (data, fallbackMime) => {
        if (typeof data === "string" && data.includes(";base64,")) {
          const segments = data.split(";base64,");
          return {
            data: segments[1],
            mimeType: segments[0].replace("data:", ""),
          };
        }
        return { data, mimeType: fallbackMime };
      };

      if (images && images.length > 0) {
        for (const img of images) {
          const { data, mimeType } = parseDataUrl(img, "image/jpeg");
          parts.push({ inlineData: { data, mimeType } });
        }
      }

      if (audio) {
        const { data, mimeType } = parseDataUrl(audio, "audio/mpeg");
        parts.push({ inlineData: { data, mimeType } });
      }

      if (video) {
        const { data, mimeType } = parseDataUrl(video, "video/mp4");
        parts.push({ inlineData: { data, mimeType } });
      }

      if (pdf) {
        const { data, mimeType } = parseDataUrl(pdf, "application/pdf");
        parts.push({ inlineData: { data, mimeType } });
      }

      content = parts;
    }

    const options = {};
    if (taskType) options.taskType = taskType;
    if (dimensions) options.dimensions = dimensions;

    const result = await provider.generateEmbedding(content, resolvedModel, options);
    const totalSec = (performance.now() - requestStart) / 1000;

    // Cost estimation
    const pricing = getPricing(TYPES.TEXT, TYPES.EMBEDDING)[resolvedModel];
    let estimatedCost = null;
    if (pricing?.inputPerMillion) {
      const approxTokens = text ? Math.ceil(text.length / 4) : 100;
      estimatedCost = (approxTokens / 1_000_000) * pricing.inputPerMillion;
    }

    logger.info(
      `[embed] ${providerName} model=${resolvedModel} — ` +
      `dims: ${result.dimensions}, total: ${totalSec.toFixed(2)}s` +
      (estimatedCost !== null ? `, cost: $${estimatedCost.toFixed(6)}` : ""),
    );

    RequestLogger.log({
      requestId,
      endpoint: "embed",
      project: req.project,
      username: req.username,
      provider: providerName,
      model: resolvedModel,
      success: true,
      estimatedCost,
      totalTime: parseFloat(totalSec.toFixed(3)),
    });

    res.json({
      embedding: result.embedding,
      dimensions: result.dimensions,
      provider: providerName,
      model: resolvedModel,
    });
  } catch (error) {
    const totalSec = (performance.now() - requestStart) / 1000;
    RequestLogger.log({
      requestId,
      endpoint: "embed",
      project: req.project,
      username: req.username,
      provider: providerName,
      model: resolvedModel,
      success: false,
      errorMessage: error.message,
      totalTime: parseFloat(totalSec.toFixed(3)),
    });
    next(error);
  }
});

export default router;
