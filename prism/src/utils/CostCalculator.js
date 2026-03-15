// ============================================================
// CostCalculator — Pure cost estimation utilities
// ============================================================

/**
 * Calculate the estimated cost for a text-to-text request.
 *
 * @param {{ inputTokens: number, outputTokens: number }} usage
 * @param {{ inputPerMillion: number, outputPerMillion: number }} pricing
 * @returns {number|null} Cost in USD, or null if pricing is unavailable.
 */
export function calculateTextCost(usage, pricing) {
  if (!pricing || !usage) return null;
  return parseFloat(
    (
      (usage.inputTokens / 1_000_000) * (pricing.inputPerMillion || 0) +
      (usage.outputTokens / 1_000_000) * (pricing.outputPerMillion || 0)
    ).toFixed(8),
  );
}

/**
 * Calculate the estimated cost for an audio-to-text request.
 * Supports two strategies — per-minute pricing takes priority.
 *
 * @param {{ durationSeconds?: number, inputTokens?: number, outputTokens?: number }} usage
 * @param {{ perMinute?: number, audioInputPerMillion?: number, outputPerMillion?: number }} pricing
 * @returns {number|null} Cost in USD, or null if pricing is unavailable.
 */
export function calculateAudioCost(usage, pricing) {
  if (!pricing || !usage) return null;

  // Strategy 1: per-minute pricing
  if (pricing.perMinute && usage.durationSeconds) {
    return parseFloat(
      ((usage.durationSeconds / 60) * pricing.perMinute).toFixed(8),
    );
  }

  // Strategy 2: token-based pricing
  if (pricing.audioInputPerMillion && usage.inputTokens) {
    return parseFloat(
      (
        (usage.inputTokens / 1_000_000) * pricing.audioInputPerMillion +
        ((usage.outputTokens || 0) / 1_000_000) *
          (pricing.outputPerMillion || 0)
      ).toFixed(8),
    );
  }

  return null;
}

/**
 * Calculate the estimated cost for a text-to-image request.
 * Estimates input tokens from prompt length (~4 chars per token).
 * Output image tokens vary by provider and resolution:
 *   - Google 1K images  ≈ 258 tokens
 *   - OpenAI 1024×1024 high-quality ≈ 1056 tokens
 *
 * @param {string} prompt - The text prompt used for generation
 * @param {{ inputPerMillion?: number, outputPerMillion?: number, imageOutputPerMillion?: number, imageInputPerMillion?: number }} pricing
 * @param {number} [inputImages=0] - Number of input images (for edit requests)
 * @param {number} [outputImageTokens=258] - Estimated output image tokens (provider-specific)
 * @returns {number|null} Cost in USD, or null if pricing is unavailable.
 */
export function calculateImageCost(
  prompt,
  pricing,
  inputImages = 0,
  outputImageTokens = 258,
) {
  if (!pricing || !prompt) return null;

  const estimatedInputTokens = Math.ceil(prompt.length / 4);

  let cost = 0;

  // Input text cost
  if (pricing.inputPerMillion) {
    cost += (estimatedInputTokens / 1_000_000) * pricing.inputPerMillion;
  }

  // Input image cost (for edit requests)
  if (inputImages > 0 && pricing.imageInputPerMillion) {
    cost += ((inputImages * 258) / 1_000_000) * pricing.imageInputPerMillion;
  }

  // Output image cost
  if (pricing.imageOutputPerMillion) {
    cost += (outputImageTokens / 1_000_000) * pricing.imageOutputPerMillion;
  } else if (pricing.outputPerMillion) {
    cost += (outputImageTokens / 1_000_000) * pricing.outputPerMillion;
  }

  return cost > 0 ? parseFloat(cost.toFixed(8)) : null;
}
