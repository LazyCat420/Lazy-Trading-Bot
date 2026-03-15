import express from "express";
import {
    PROVIDERS,
    PROVIDER_LIST,
    TYPES,
    VOICES,
    DEFAULT_VOICES,
    getModelOptions,
    getDefaultModels,
} from "../config.js";
import { getProvider } from "../providers/index.js";
import { ARENA_SCORES } from "../arrays.js";
import logger from "../utils/logger.js";
import {
    OPENAI_API_KEY,
    ANTHROPIC_API_KEY,
    GOOGLE_API_KEY,
    ELEVENLABS_API_KEY,
    INWORLD_BASIC,
    LM_STUDIO_BASE_URL,
    OLLAMA_BASE_URL,
} from "../../secrets.js";

const router = express.Router();

// Map each provider to its secret — provider is "available" when secret is truthy
const PROVIDER_SECRETS = {
    [PROVIDERS.OPENAI]: OPENAI_API_KEY,
    [PROVIDERS.ANTHROPIC]: ANTHROPIC_API_KEY,
    [PROVIDERS.GOOGLE]: GOOGLE_API_KEY,
    [PROVIDERS.ELEVENLABS]: ELEVENLABS_API_KEY,
    [PROVIDERS.INWORLD]: INWORLD_BASIC,
    [PROVIDERS.LM_STUDIO]: LM_STUDIO_BASE_URL,
    [PROVIDERS.OLLAMA]: OLLAMA_BASE_URL,
};

const AVAILABLE_PROVIDERS = new Set(
    Object.entries(PROVIDER_SECRETS)
        .filter(([, secret]) => !!secret)
        .map(([provider]) => provider),
);

/** Keep only available provider keys in a models map. */
function filterByAvailableProviders(modelsMap) {
    const filtered = {};
    for (const [provider, models] of Object.entries(modelsMap)) {
        if (AVAILABLE_PROVIDERS.has(provider)) {
            filtered[provider] = models;
        }
    }
    return filtered;
}

/** Filter defaults to only include available providers. */
function filterDefaults(defaults) {
    const filtered = {};
    for (const [provider, model] of Object.entries(defaults)) {
        if (AVAILABLE_PROVIDERS.has(provider)) {
            filtered[provider] = model;
        }
    }
    return filtered;
}

/**
 * Look up arena scores for a model name from ARENA_SCORES.
 * Tries exact match first, then checks if an arena entry name
 * is contained within the model name (for versioned names like
 * "claude-haiku-4-5-20251001" matching "claude-haiku-4-5-20251001").
 *
 * Returns an arena object like { text: 1406, code: 1310, ... } or null.
 */
function lookupArenaScores(modelName) {
    const arena = {};
    const key = modelName.toLowerCase();

    // Strip path prefix (e.g. "google/gemma-3-12b" → "gemma-3-12b")
    // and quantization suffix (e.g. "qwen3-32b@q4_k_m" → "qwen3-32b")
    const stripped = key.includes("/") ? key.split("/").pop() : key;
    const cleaned = stripped.includes("@") ? stripped.split("@")[0] : stripped;

    for (const [category, scores] of Object.entries(ARENA_SCORES)) {
        if (!scores || typeof scores !== "object") continue;

        let bestMatch = null;
        let bestLen = 0;

        for (const [arenaName, score] of Object.entries(scores)) {
            const an = arenaName.toLowerCase();

            // Exact match on raw key or cleaned key
            if (key === an || cleaned === an) {
                bestMatch = score;
                break;
            }

            // Check both directions of startsWith/includes using cleaned key
            const matched =
                cleaned.startsWith(an) ||
                an.startsWith(cleaned) ||
                key.includes(an) ||
                an.includes(cleaned);

            if (matched && an.length > bestLen) {
                bestMatch = score;
                bestLen = an.length;
            }
        }

        if (bestMatch !== null) {
            arena[category] = bestMatch;
        }
    }

    return Object.keys(arena).length > 0 ? arena : null;
}

/**
 * Enrich all models in a provider map with arena scores from ARENA_SCORES.
 * Merges with any existing arena data on the model (existing takes priority).
 */
function enrichModelsWithArenaScores(modelsMap) {
    for (const provider of Object.keys(modelsMap)) {
        for (const model of modelsMap[provider]) {
            const scores = lookupArenaScores(model.name);
            if (scores) {
                // Merge: existing hardcoded arena data takes priority
                model.arena = { ...scores, ...(model.arena || {}) };
            }
        }
    }
    return modelsMap;
}

/**
 * Format a byte count into a human-readable size string.
 */
function formatBytes(bytes) {
    if (!bytes) return null;
    if (bytes >= 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)} GB`;
    if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
    return `${(bytes / 1024).toFixed(0)} KB`;
}

/**
 * Fetch LM Studio models and convert them to the config model format.
 * Returns an array of model option objects for the 'lm-studio' provider.
 */
async function getLmStudioModelOptions() {
    try {
        const provider = getProvider("lm-studio");
        const { models } = await provider.listModels();
        if (!models || !Array.isArray(models)) return [];

        return models
            .filter((m) => m.type === "llm")
            .map((m) => {
                // Build label with quantization suffix to disambiguate
                let label = m.display_name || m.key;
                if (m.quantization?.name) {
                    label += ` (${m.quantization.name})`;
                }

                const entry = {
                    name: m.key,
                    label,
                    inputTypes: [TYPES.TEXT],
                    outputTypes: [TYPES.TEXT],
                    streaming: true,
                    defaultTemperature: 0.7,
                    pricing: { inputPerMillion: 0, outputPerMillion: 0 },
                };
                if (m.capabilities?.vision) {
                    entry.vision = true;
                    entry.inputTypes = [TYPES.TEXT, TYPES.IMAGE];
                }
                if (m.max_context_length) {
                    entry.contextLength = m.max_context_length;
                }
                if (m.size_bytes) {
                    entry.size = formatBytes(m.size_bytes);
                }
                if (m.params_string) {
                    entry.params = m.params_string;
                }
                if (m.quantization?.name) {
                    entry.quantization = m.quantization.name;
                }
                if (m.architecture) {
                    entry.architecture = m.architecture;
                }
                if (m.loaded_instances?.length > 0) {
                    entry.loaded = true;
                }
                return entry;
            });
    } catch (err) {
        logger.warn(`Could not fetch LM Studio models for config: ${err.message}`);
        return [];
    }
}

/**
 * Fetch Ollama models and convert them to the config model format.
 * Returns an array of model option objects for the 'ollama' provider.
 */
async function getOllamaModelOptions() {
    try {
        const provider = getProvider("ollama");
        const { models } = await provider.listModels();
        if (!models || !Array.isArray(models)) return [];

        return models.map((m) => {
            // Ollama returns { name, model, size, details: { family, parameter_size, ... } }
            const name = m.model || m.name;
            const label = m.name || name;
            const details = m.details || {};
            const nameLower = name.toLowerCase();

            // Detect thinking-capable models by name/family
            const THINKING_PATTERNS = ["qwen3", "deepseek-r1", "deepseek-v3", "gpt-oss"];
            const supportsThinking = THINKING_PATTERNS.some(
                (p) => nameLower.includes(p),
            );

            const entry = {
                name,
                label,
                inputTypes: [TYPES.TEXT],
                outputTypes: [TYPES.TEXT],
                streaming: true,
                defaultTemperature: 0.7,
                pricing: { inputPerMillion: 0, outputPerMillion: 0 },
            };
            if (supportsThinking) {
                entry.thinking = true;
                entry.tools = ["Thinking"];
            }
            if (details.parameter_size) {
                entry.params = details.parameter_size;
            }
            if (details.family) {
                entry.architecture = details.family;
            }
            if (m.size) {
                entry.size = formatBytes(m.size);
            }
            return entry;
        });
    } catch (err) {
        logger.warn(`Could not fetch Ollama models for config: ${err.message}`);
        return [];
    }
}
/**
 * GET /config
 * Returns the full catalog of providers, models, voices, and capabilities.
 */
router.get("/", async (_req, res) => {
    // Get static model options
    let textToTextModels = getModelOptions(TYPES.TEXT, TYPES.TEXT);
    let textToImageModels = getModelOptions(TYPES.TEXT, TYPES.IMAGE);

    // Merge dynamic LM Studio models into lm-studio provider (only if configured)
    if (AVAILABLE_PROVIDERS.has(PROVIDERS.LM_STUDIO)) {
        try {
            const lmModels = await getLmStudioModelOptions();
            if (lmModels.length > 0) {
                const staticLmModels = textToTextModels["lm-studio"] || [];
                const staticKeys = new Set(staticLmModels.map((m) => m.name));
                for (const m of lmModels) {
                    if (!staticKeys.has(m.name)) {
                        staticLmModels.push(m);
                    }
                }
                textToTextModels["lm-studio"] = staticLmModels;
            }
        } catch {
            // Ignore — use static models only
        }
    }

    // Merge dynamic Ollama models into ollama provider (only if configured)
    if (AVAILABLE_PROVIDERS.has(PROVIDERS.OLLAMA)) {
        try {
            const ollamaModels = await getOllamaModelOptions();
            if (ollamaModels.length > 0) {
                const staticOllamaModels = textToTextModels["ollama"] || [];
                const staticKeys = new Set(staticOllamaModels.map((m) => m.name));
                for (const m of ollamaModels) {
                    if (!staticKeys.has(m.name)) {
                        staticOllamaModels.push(m);
                    }
                }
                textToTextModels["ollama"] = staticOllamaModels;
            }
        } catch {
            // Ignore — use static models only
        }
    }

    // Enrich ALL model lists with arena scores from the scraped leaderboard data
    enrichModelsWithArenaScores(textToTextModels);
    enrichModelsWithArenaScores(textToImageModels);

    // Filter to only available providers
    textToTextModels = filterByAvailableProviders(textToTextModels);
    textToImageModels = filterByAvailableProviders(textToImageModels);

    const availableProviderList = PROVIDER_LIST.filter((p) => AVAILABLE_PROVIDERS.has(p));
    const availableProviderMap = {};
    for (const [key, val] of Object.entries(PROVIDERS)) {
        if (AVAILABLE_PROVIDERS.has(val)) availableProviderMap[key] = val;
    }

    res.json({
        providers: availableProviderMap,
        providerList: availableProviderList,
        availableProviders: availableProviderList,
        textToText: {
            models: textToTextModels,
            defaults: filterDefaults(getDefaultModels(TYPES.TEXT, TYPES.TEXT)),
        },
        textToSpeech: {
            models: filterByAvailableProviders(getModelOptions(TYPES.TEXT, TYPES.AUDIO)),
            defaults: filterDefaults(getDefaultModels(TYPES.TEXT, TYPES.AUDIO)),
            voices: VOICES,
            defaultVoices: DEFAULT_VOICES,
        },
        textToImage: {
            models: textToImageModels,
            defaults: filterDefaults(getDefaultModels(TYPES.TEXT, TYPES.IMAGE)),
        },
        imageToText: {
            models: filterByAvailableProviders(getModelOptions(TYPES.IMAGE, TYPES.TEXT)),
            defaults: filterDefaults(getDefaultModels(TYPES.IMAGE, TYPES.TEXT)),
        },
        embedding: {
            models: filterByAvailableProviders(getModelOptions(TYPES.TEXT, TYPES.EMBEDDING)),
            defaults: filterDefaults(getDefaultModels(TYPES.TEXT, TYPES.EMBEDDING)),
        },
        audioToText: {
            models: filterByAvailableProviders(getModelOptions(TYPES.AUDIO, TYPES.TEXT)),
            defaults: filterDefaults(getDefaultModels(TYPES.AUDIO, TYPES.TEXT)),
        },
    });
});

export default router;
