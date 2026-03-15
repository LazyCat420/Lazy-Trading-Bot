import OpenAI, { toFile } from "openai";
import { ProviderError } from "../utils/errors.js";
import logger from "../utils/logger.js";
import { OPENAI_API_KEY } from "../../secrets.js";
import {
    TYPES,
    DEFAULT_VOICES,
    getDefaultModels,
    getModelByName,
} from "../config.js";

/**
 * Check if a model should use the Responses API.
 */
function useResponsesAPI(model) {
    const modelDef = getModelByName(model);
    return modelDef?.responsesAPI === true;
}

let client = null;

function getClient() {
    if (!client) {
        if (!OPENAI_API_KEY) {
            throw new ProviderError("openai", "OPENAI_API_KEY is not set", 401);
        }
        client = new OpenAI({ apiKey: OPENAI_API_KEY });
    }
    return client;
}
/**
 * Detect MIME category from a base64 data URL.
 */
function getDataUrlMimeType(dataUrl) {
    const match = dataUrl.match(/^data:([^;]+);base64,/);
    return match ? match[1] : null;
}

/**
 * Convert messages with media to OpenAI multimodal content format (Chat Completions).
 */
function prepareOpenAIMessages(messages) {
    return messages.map((m) => {
        const base = { role: m.role };
        if (m.name) base.name = m.name;
        if (m.images && m.images.length > 0) {
            const content = [];
            for (const dataUrl of m.images) {
                const mime = getDataUrlMimeType(dataUrl);
                if (mime && mime.startsWith("image/")) {
                    content.push({ type: "image_url", image_url: { url: dataUrl } });
                } else if (mime === "application/pdf") {
                    content.push({
                        type: "file",
                        file: { file_data: dataUrl, filename: "document.pdf" },
                    });
                } else if (
                    mime &&
                    (mime.startsWith("text/") || mime === "application/json")
                ) {
                    // Decode text files and inline as text
                    try {
                        const base64 = dataUrl.split(";base64,")[1];
                        const decoded = Buffer.from(base64, "base64").toString("utf-8");
                        content.push({
                            type: "text",
                            text: `[Attached file (${mime})]:\n${decoded}`,
                        });
                    } catch {
                        content.push({
                            type: "text",
                            text: `[Attached file (${mime}): unable to decode]`,
                        });
                    }
                } else {
                    // Other file types — try sending as file
                    content.push({
                        type: "file",
                        file: { file_data: dataUrl, filename: "attachment" },
                    });
                }
            }
            if (m.content) {
                content.push({ type: "text", text: m.content });
            }
            return { ...base, content };
        }
        return { ...base, content: m.content };
    });
}

/**
 * Convert messages to Responses API input format.
 * System messages become developer messages; images use input_image, PDFs use input_file.
 */
function prepareResponsesInput(messages) {
    return messages.map((m) => {
        const role = m.role === "system" ? "developer" : m.role;
        const base = { role };
        if (m.name) base.name = m.name;
        if (m.images && m.images.length > 0) {
            const content = [];
            for (const dataUrl of m.images) {
                const mime = getDataUrlMimeType(dataUrl);
                if (mime && mime.startsWith("image/")) {
                    content.push({ type: "input_image", image_url: dataUrl });
                } else if (
                    mime === "application/pdf" ||
                    mime ===
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ) {
                    content.push({ type: "input_file", file_data: dataUrl });
                } else if (
                    mime &&
                    (mime.startsWith("text/") || mime === "application/json")
                ) {
                    // Decode text files and inline as text
                    try {
                        const base64 = dataUrl.split(";base64,")[1];
                        const decoded = Buffer.from(base64, "base64").toString("utf-8");
                        content.push({
                            type: "input_text",
                            text: `[Attached file (${mime})]:\n${decoded}`,
                        });
                    } catch {
                        content.push({
                            type: "input_text",
                            text: `[Attached file (${mime}): unable to decode]`,
                        });
                    }
                } else {
                    // Other file types — try sending as file
                    content.push({ type: "input_file", file_data: dataUrl });
                }
            }
            if (m.content) {
                content.push({ type: "input_text", text: m.content });
            }
            return { ...base, content };
        }
        return { ...base, content: m.content };
    });
}

const openaiProvider = {
    name: "openai",

    async generateText(
        messages,
        model = getDefaultModels(TYPES.TEXT, TYPES.TEXT).openai,
        options = {},
    ) {
        logger.provider("OpenAI", `generateText model=${model}`);
        try {
            if (useResponsesAPI(model)) {
                return await this._generateTextResponses(messages, model, options);
            }
            return await this._generateTextChatCompletions(messages, model, options);
        } catch (error) {
            throw new ProviderError(
                "openai",
                error.message,
                error.status || 500,
                error,
            );
        }
    },

    /**
     * Responses API path for GPT-5.2/5.4 models.
     */
    async _generateTextResponses(messages, model, options) {
        const input = prepareResponsesInput(messages);
        const payload = { model, input };

        // Reasoning
        const reasoning = {};
        if (options.reasoningEffort) reasoning.effort = options.reasoningEffort;
        if (options.reasoningSummary) reasoning.summary = options.reasoningSummary;
        if (Object.keys(reasoning).length > 0) payload.reasoning = reasoning;

        // Text / verbosity
        const text = {};
        if (options.verbosity) text.format = { type: "text" };
        if (options.verbosity) text.verbosity = options.verbosity;
        if (Object.keys(text).length > 0) payload.text = text;

        if (options.maxTokens) payload.max_output_tokens = options.maxTokens;

        // Temperature/topP only work with reasoning.effort=none
        if (options.reasoningEffort === "none") {
            if (options.temperature !== undefined)
                payload.temperature = options.temperature;
            if (options.topP !== undefined) payload.top_p = options.topP;
            if (options.frequencyPenalty !== undefined)
                payload.frequency_penalty = options.frequencyPenalty;
            if (options.presencePenalty !== undefined)
                payload.presence_penalty = options.presencePenalty;
            if (options.stopSequences !== undefined)
                payload.stop = options.stopSequences;
        }

        // Web search tool
        if (options.webSearch) {
            payload.tools = [{ type: "web_search_preview" }];
        }

        const response = await getClient().responses.create(payload);

        // Collect any generated images from output items
        const images = [];
        if (response.output) {
            for (const item of response.output) {
                if (item.type === "image_generation_call" && item.result) {
                    images.push({
                        type: "image",
                        data: item.result,
                        mimeType: "image/png",
                    });
                }
            }
        }

        return {
            text: response.output_text || "",
            images,
            usage: {
                inputTokens: response.usage?.input_tokens ?? 0,
                outputTokens: response.usage?.output_tokens ?? 0,
            },
        };
    },

    /**
     * Chat Completions fallback for older models.
     */
    async _generateTextChatCompletions(messages, model, options) {
        const modelDef = getModelByName(model);
        const isReasoning =
            modelDef?.thinking || model.includes("o1") || model.includes("o3");
        const prepared = prepareOpenAIMessages(messages);
        const payload = {
            model,
            messages: prepared,
        };
        if (isReasoning) {
            if (options.maxTokens) payload.max_completion_tokens = options.maxTokens;
            if (options.reasoningEffort)
                payload.reasoning_effort = options.reasoningEffort;
        } else {
            if (options.temperature !== undefined)
                payload.temperature = options.temperature;
            if (options.topP !== undefined) payload.top_p = options.topP;
            if (options.frequencyPenalty !== undefined)
                payload.frequency_penalty = options.frequencyPenalty;
            if (options.presencePenalty !== undefined)
                payload.presence_penalty = options.presencePenalty;
            if (options.stopSequences !== undefined)
                payload.stop = options.stopSequences;
            if (options.maxTokens) payload.max_completion_tokens = options.maxTokens;
        }
        if (options.webSearch) {
            payload.tools = [{ type: "web_search_preview" }];
        }

        try {
            const response = await getClient().chat.completions.create(payload);
            return {
                text: response.choices[0].message.content,
                usage: {
                    inputTokens: response.usage?.prompt_tokens ?? 0,
                    outputTokens: response.usage?.completion_tokens ?? 0,
                },
            };
        } catch (error) {
            // Retry once after stripping unsupported parameters (e.g. gpt-5-nano rejects temperature)
            if (error.status === 400 && error.message?.includes("Unsupported")) {
                const unsupportedParams = [
                    "temperature",
                    "top_p",
                    "frequency_penalty",
                    "presence_penalty",
                    "max_completion_tokens",
                ];
                let stripped = false;
                for (const param of unsupportedParams) {
                    if (
                        error.message.includes(`'${param}'`) &&
                        payload[param] !== undefined
                    ) {
                        logger.provider(
                            "OpenAI",
                            `Stripping unsupported param '${param}' for ${model} and retrying`,
                        );
                        delete payload[param];
                        stripped = true;
                    }
                }
                if (stripped) {
                    const response = await getClient().chat.completions.create(payload);
                    return {
                        text: response.choices[0].message.content,
                        usage: {
                            inputTokens: response.usage?.prompt_tokens ?? 0,
                            outputTokens: response.usage?.completion_tokens ?? 0,
                        },
                    };
                }
            }
            throw error;
        }
    },

    async *generateTextStream(
        messages,
        model = getDefaultModels(TYPES.TEXT, TYPES.TEXT).openai,
        options = {},
    ) {
        logger.provider("OpenAI", `generateTextStream model=${model}`);
        try {
            if (useResponsesAPI(model)) {
                yield* this._streamResponses(messages, model, options);
            } else {
                yield* this._streamChatCompletions(messages, model, options);
            }
        } catch (error) {
            throw new ProviderError(
                "openai",
                error.message,
                error.status || 500,
                error,
            );
        }
    },

    /**
     * Streaming via the Responses API.
     */
    async *_streamResponses(messages, model, options) {
        const input = prepareResponsesInput(messages);
        const payload = { model, input, stream: true };

        // Reasoning
        const reasoning = {};
        if (options.reasoningEffort) reasoning.effort = options.reasoningEffort;
        if (options.reasoningSummary) reasoning.summary = options.reasoningSummary;
        if (Object.keys(reasoning).length > 0) payload.reasoning = reasoning;

        // Text / verbosity
        const text = {};
        if (options.verbosity) text.format = { type: "text" };
        if (options.verbosity) text.verbosity = options.verbosity;
        if (Object.keys(text).length > 0) payload.text = text;

        if (options.maxTokens) payload.max_output_tokens = options.maxTokens;

        // Temperature/topP only work with reasoning.effort=none
        if (options.reasoningEffort === "none") {
            if (options.temperature !== undefined)
                payload.temperature = options.temperature;
            if (options.topP !== undefined) payload.top_p = options.topP;
            if (options.frequencyPenalty !== undefined)
                payload.frequency_penalty = options.frequencyPenalty;
            if (options.presencePenalty !== undefined)
                payload.presence_penalty = options.presencePenalty;
            if (options.stopSequences !== undefined)
                payload.stop = options.stopSequences;
        }

        // Web search tool
        if (options.webSearch) {
            payload.tools = [{ type: "web_search_preview" }];
        }

        const stream = await getClient().responses.create(payload);
        let usage = null;
        for await (const event of stream) {
            // Text delta from output_text
            if (event.type === "response.output_text.delta") {
                yield event.delta || "";
            }
            // Reasoning / thinking summary delta
            if (event.type === "response.reasoning_summary_text.delta") {
                yield { type: "thinking", content: event.delta || "" };
            }
            // Image generation completed
            if (
                event.type === "response.image_generation_call.completed" &&
                event.result
            ) {
                yield {
                    type: "image",
                    data: event.result,
                    mimeType: "image/png",
                };
            }
            // Completed response — extract usage
            if (event.type === "response.completed" && event.response?.usage) {
                usage = {
                    inputTokens: event.response.usage.input_tokens ?? 0,
                    outputTokens: event.response.usage.output_tokens ?? 0,
                };
            }
        }
        if (usage) {
            yield { type: "usage", usage };
        }
    },

    /**
     * Streaming via Chat Completions (fallback for older models).
     */
    async *_streamChatCompletions(messages, model, options) {
        const modelDef = getModelByName(model);
        const isReasoning =
            modelDef?.thinking || model.includes("o1") || model.includes("o3");
        const prepared = prepareOpenAIMessages(messages);
        const payload = {
            model,
            messages: prepared,
            stream: true,
            stream_options: { include_usage: true },
        };
        if (isReasoning) {
            if (options.maxTokens) payload.max_completion_tokens = options.maxTokens;
            if (options.reasoningEffort)
                payload.reasoning_effort = options.reasoningEffort;
        } else {
            if (options.temperature !== undefined)
                payload.temperature = options.temperature;
            if (options.topP !== undefined) payload.top_p = options.topP;
            if (options.frequencyPenalty !== undefined)
                payload.frequency_penalty = options.frequencyPenalty;
            if (options.presencePenalty !== undefined)
                payload.presence_penalty = options.presencePenalty;
            if (options.stopSequences !== undefined)
                payload.stop = options.stopSequences;
            if (options.maxTokens) payload.max_completion_tokens = options.maxTokens;
        }
        if (options.webSearch) {
            payload.tools = [{ type: "web_search_preview" }];
        }

        let stream;
        try {
            stream = await getClient().chat.completions.create(payload);
        } catch (error) {
            // Retry once after stripping unsupported parameters (e.g. gpt-5-nano rejects temperature)
            if (error.status === 400 && error.message?.includes("Unsupported")) {
                const unsupportedParams = [
                    "temperature",
                    "top_p",
                    "frequency_penalty",
                    "presence_penalty",
                    "max_completion_tokens",
                ];
                let stripped = false;
                for (const param of unsupportedParams) {
                    if (
                        error.message.includes(`'${param}'`) &&
                        payload[param] !== undefined
                    ) {
                        logger.provider(
                            "OpenAI",
                            `Stripping unsupported param '${param}' for ${model} and retrying (stream)`,
                        );
                        delete payload[param];
                        stripped = true;
                    }
                }
                if (stripped) {
                    stream = await getClient().chat.completions.create(payload);
                } else {
                    throw error;
                }
            } else {
                throw error;
            }
        }

        let usage = null;
        for await (const chunk of stream) {
            if (chunk.usage) {
                usage = {
                    inputTokens: chunk.usage.prompt_tokens ?? 0,
                    outputTokens: chunk.usage.completion_tokens ?? 0,
                };
            }
            const content = chunk.choices[0]?.delta?.content || "";
            if (content) {
                yield content;
            }
        }
        if (usage) {
            yield { type: "usage", usage };
        }
    },

    async generateSpeech(text, voice = DEFAULT_VOICES.openai, options = {}) {
        logger.provider("OpenAI", `generateSpeech voice=${voice}`);
        try {
            const response = await getClient().audio.speech.create({
                model:
                    options.model || getDefaultModels(TYPES.TEXT, TYPES.AUDIO).openai,
                voice,
                input: text,
                instructions: options.instructions || undefined,
                response_format: options.format || "mp3",
            });
            return { stream: response.body, contentType: "audio/mpeg" };
        } catch (error) {
            throw new ProviderError(
                "openai",
                error.message,
                error.status || 500,
                error,
            );
        }
    },

    async generateImage(prompt, images = [], model = "gpt-image-1.5") {
        logger.provider(
            "OpenAI",
            `generateImage model=${model} images=${images.length}`,
        );
        try {
            let response;

            if (images.length > 0) {
                // Use the edit endpoint when input images are provided
                // Take the last image in conversation as the one to edit
                const lastImage = images[images.length - 1];
                let imageBuffer, mimeType;

                if (typeof lastImage === "object" && lastImage.imageData) {
                    // Object format: { imageData: base64, mimeType }
                    imageBuffer = Buffer.from(lastImage.imageData, "base64");
                    mimeType = lastImage.mimeType || "image/png";
                } else {
                    // Legacy data URL format: data:image/png;base64,...
                    const base64Match = lastImage.match(/^data:([^;]+);base64,(.+)$/);
                    if (!base64Match) {
                        throw new Error("Invalid image data format");
                    }
                    imageBuffer = Buffer.from(base64Match[2], "base64");
                    mimeType = base64Match[1];
                }
                const ext = mimeType.split("/")[1] || "png";
                const imageFile = await toFile(imageBuffer, `input.${ext}`, {
                    type: mimeType,
                });

                response = await getClient().images.edit({
                    model,
                    prompt,
                    image: imageFile,
                    size: "1024x1024",
                });
            } else {
                // Generate new image
                response = await getClient().images.generate({
                    model,
                    prompt,
                    output_format: "png",
                    size: "1024x1024",
                    quality: "high",
                });
            }

            const imageData =
                response.data?.[0]?.b64_json || response.data?.[0]?.b64 || response.b64;
            if (!imageData) {
                throw new Error("No image data received from OpenAI");
            }
            return {
                imageData,
                mimeType: "image/png",
                text: response.data?.[0]?.revised_prompt || "",
            };
        } catch (error) {
            throw new ProviderError(
                "openai",
                error.message,
                error.status || 500,
                error,
            );
        }
    },

    async captionImage(
        images,
        prompt = "What's in this image?",
        model = getDefaultModels(TYPES.TEXT, TYPES.TEXT).openai,
        systemPrompt,
    ) {
        logger.provider("OpenAI", `captionImage model=${model}`);
        try {
            const content = [
                { type: "text", text: prompt },
                ...images.map((img) => ({ type: "image_url", image_url: { url: img } })),
            ];
            const messages = [];
            if (systemPrompt) {
                messages.push({ role: "system", content: systemPrompt });
            }
            messages.push({ role: "user", content });
            const response = await getClient().chat.completions.create({
                model,
                messages,
                max_completion_tokens: 1000,
            });
            const usage = {
                inputTokens: response.usage?.prompt_tokens || 0,
                outputTokens: response.usage?.completion_tokens || 0,
            };
            return { text: response.choices[0].message.content, usage };
        } catch (error) {
            throw new ProviderError(
                "openai",
                error.message,
                error.status || 500,
                error,
            );
        }
    },

    async generateEmbedding(
        text,
        model = getDefaultModels(TYPES.TEXT, TYPES.EMBEDDING).openai,
    ) {
        logger.provider("OpenAI", `generateEmbedding model=${model}`);
        try {
            const response = await getClient().embeddings.create({
                model,
                input: text,
            });
            return { embedding: response.data[0].embedding };
        } catch (error) {
            throw new ProviderError(
                "openai",
                error.message,
                error.status || 500,
                error,
            );
        }
    },

    async transcribeAudio(
        audioBuffer,
        mimeType,
        model = "gpt-4o-transcribe",
        options = {},
    ) {
        logger.provider("OpenAI", `transcribeAudio model=${model}`);
        try {
            const ext = mimeType.split("/")[1] || "wav";
            const file = await toFile(audioBuffer, `audio.${ext}`, {
                type: mimeType,
            });
            const payload = {
                file,
                model,
            };
            if (options.language) payload.language = options.language;
            if (options.prompt) payload.prompt = options.prompt;

            const response = await getClient().audio.transcriptions.create(payload);
            const usage = {};
            if (response.usage) {
                if (response.usage.type === "tokens") {
                    usage.inputTokens = response.usage.input_tokens ?? 0;
                    usage.outputTokens = response.usage.output_tokens ?? 0;
                } else if (response.usage.type === "duration") {
                    usage.durationSeconds = response.usage.seconds ?? 0;
                }
            }
            return {
                text: response.text,
                usage,
            };
        } catch (error) {
            throw new ProviderError(
                "openai",
                error.message,
                error.status || 500,
                error,
            );
        }
    },
};

export default openaiProvider;
