import { ProviderError } from "../utils/errors.js";
import logger from "../utils/logger.js";
import { OLLAMA_BASE_URL } from "../../secrets.js";
import { TYPES, getDefaultModels } from "../config.js";

function getBaseUrl() {
    return OLLAMA_BASE_URL;
}

/**
 * Convert messages with images to Ollama's native format.
 * Ollama expects images as base64 strings (without the data URL prefix).
 */
function prepareOllamaMessages(messages) {
    return messages.map((m) => {
        const msg = { role: m.role, content: m.content || "" };
        if (m.images && m.images.length > 0) {
            // Ollama's native API expects images as raw base64 strings
            msg.images = m.images.map((dataUrl) => {
                if (dataUrl.startsWith("data:")) {
                    return dataUrl.split(",")[1]; // strip data:image/...;base64, prefix
                }
                return dataUrl;
            });
        }
        return msg;
    });
}

const ollamaProvider = {
    name: "ollama",

    // ── Non-Streaming Text Generation ──────────────────────

    async generateText(
        messages,
        model = getDefaultModels(TYPES.TEXT, TYPES.TEXT)["ollama"],
        options = {},
    ) {
        const baseUrl = getBaseUrl();
        logger.provider("Ollama", `generateText model=${model} baseUrl=${baseUrl}`);
        try {
            const prepared = prepareOllamaMessages(messages);

            const body = {
                model,
                messages: prepared,
                stream: false,
                ...(options.thinkingEnabled ? { think: true } : {}),
            };

            const response = await fetch(`${baseUrl}/api/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`API error: ${response.status} ${errorText}`);
            }

            const data = await response.json();
            return {
                text: data.message?.content || "",
                thinking: data.message?.thinking || null,
                usage: {
                    inputTokens: data.prompt_eval_count ?? 0,
                    outputTokens: data.eval_count ?? 0,
                },
            };
        } catch (error) {
            if (error instanceof ProviderError) throw error;
            throw new ProviderError("ollama", error.message, 500, error);
        }
    },

    // ── Streaming Text Generation ──────────────────────

    async *generateTextStream(
        messages,
        model = getDefaultModels(TYPES.TEXT, TYPES.TEXT)["ollama"],
        options = {},
    ) {
        const baseUrl = getBaseUrl();
        logger.provider("Ollama", `generateTextStream model=${model} baseUrl=${baseUrl}`);
        try {
            // Single-model enforcement: unload any other loaded models
            try {
                const psRes = await fetch(`${baseUrl}/api/ps`);
                if (psRes.ok) {
                    const psData = await psRes.json();
                    const running = psData.models || [];
                    for (const m of running) {
                        const runningName = m.model || m.name;
                        if (runningName && runningName !== model) {
                            yield { type: "status", message: `Unloading ${runningName}…` };
                            logger.info(`Ollama: unloading ${runningName} before loading ${model}`);
                            await fetch(`${baseUrl}/api/generate`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ model: runningName, keep_alive: 0 }),
                            });
                        }
                    }
                }
            } catch (unloadErr) {
                logger.warn(`Ollama: could not check/unload models: ${unloadErr.message}`);
            }

            const prepared = prepareOllamaMessages(messages);

            const body = {
                model,
                messages: prepared,
                stream: true,
                ...(options.thinkingEnabled ? { think: true } : {}),
            };

            const response = await fetch(`${baseUrl}/api/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`API error: ${response.status} ${errorText}`);
            }

            // Ollama streams NDJSON (one JSON object per line)
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            let usage = null;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split("\n");
                buffer = lines.pop(); // keep incomplete line in buffer

                for (const line of lines) {
                    const trimmed = line.trim();
                    if (!trimmed) continue;

                    try {
                        const json = JSON.parse(trimmed);

                        // Thinking content comes in message.thinking
                        if (json.message?.thinking) {
                            yield { type: "thinking", content: json.message.thinking };
                        }

                        // Text content comes in message.content
                        if (json.message?.content) {
                            yield json.message.content;
                        }

                        // Final chunk has done: true with usage stats
                        if (json.done) {
                            const evalDurationSec = json.eval_duration
                                ? json.eval_duration / 1_000_000_000
                                : null;
                            usage = {
                                inputTokens: json.prompt_eval_count ?? 0,
                                outputTokens: json.eval_count ?? 0,
                            };
                            // Ollama reports precise eval_duration — use it for tok/s
                            if (evalDurationSec && evalDurationSec > 0 && usage.outputTokens > 0) {
                                usage.tokensPerSec = parseFloat(
                                    (usage.outputTokens / evalDurationSec).toFixed(1),
                                );
                            }
                        }
                    } catch {
                        // skip malformed JSON lines
                    }
                }
            }

            if (usage) {
                yield { type: "usage", usage };
            }
        } catch (error) {
            if (error instanceof ProviderError) throw error;
            throw new ProviderError("ollama", error.message, 500, error);
        }
    },

    // ── Image Captioning ──────────────────────

    async captionImage(
        images,
        prompt = "Describe this image.",
        model = getDefaultModels(TYPES.IMAGE, TYPES.TEXT)["ollama"],
        systemPrompt,
    ) {
        const baseUrl = getBaseUrl();
        logger.provider("Ollama", `captionImage model=${model} baseUrl=${baseUrl}`);
        try {
            // Extract raw base64 from data URLs
            const imageBase64List = images.map((img) => {
                if (img.startsWith("data:")) {
                    return img.split(",")[1];
                }
                return img;
            });

            const messages = [];
            if (systemPrompt) {
                messages.push({ role: "system", content: systemPrompt });
            }
            messages.push({ role: "user", content: prompt, images: imageBase64List });

            const response = await fetch(`${baseUrl}/api/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    model,
                    messages,
                    stream: false,
                }),
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`API error: ${response.status} ${errorText}`);
            }

            const data = await response.json();
            const text = data.message?.content || "";
            const usage = {
                inputTokens: data.prompt_eval_count || 0,
                outputTokens: data.eval_count || 0,
            };
            return { text, usage };
        } catch (error) {
            if (error instanceof ProviderError) throw error;
            throw new ProviderError("ollama", error.message, 500, error);
        }
    },

    // ── Ollama Model Listing ─────────────────────

    /**
     * List all models available in Ollama.
     * GET /api/tags
     */
    async listModels() {
        const baseUrl = getBaseUrl();
        logger.provider("Ollama", "listModels");
        try {
            const response = await fetch(`${baseUrl}/api/tags`, {
                method: "GET",
                headers: { "Content-Type": "application/json" },
            });

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`API error: ${response.status} ${errorText}`);
            }

            const data = await response.json();
            // Ollama returns { models: [{ name, model, size, ... }] }
            return { models: data.models || [] };
        } catch (error) {
            if (error instanceof ProviderError) throw error;
            throw new ProviderError("ollama", error.message, 500, error);
        }
    },
};

export default ollamaProvider;
