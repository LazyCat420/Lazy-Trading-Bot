import { GoogleGenAI } from "@google/genai";
import { Readable } from "stream";
import { ProviderError } from "../utils/errors.js";
import logger from "../utils/logger.js";
import { GOOGLE_API_KEY } from "../../secrets.js";
import { TYPES, MODELS, DEFAULT_VOICES, getDefaultModels } from "../config.js";

let client = null;

function getClient() {
    if (!client) {
        if (!GOOGLE_API_KEY) {
            throw new ProviderError("google", "GOOGLE_API_KEY is not set", 401);
        }
        client = new GoogleGenAI({ apiKey: GOOGLE_API_KEY });
    }
    return client;
}

/**
 * Add a WAV header to raw PCM audio data.
 */
function addWavHeader(buffer, sampleRate = 24000, numChannels = 1) {
    const headerLength = 44;
    const dataLength = buffer.length;
    const fileSize = dataLength + headerLength - 8;
    const header = Buffer.alloc(headerLength);

    header.write("RIFF", 0);
    header.writeUInt32LE(fileSize, 4);
    header.write("WAVE", 8);
    header.write("fmt ", 12);
    header.writeUInt32LE(16, 16);
    header.writeUInt16LE(1, 20);
    header.writeUInt16LE(numChannels, 22);
    header.writeUInt32LE(sampleRate, 24);
    header.writeUInt32LE(sampleRate * numChannels * 2, 28);
    header.writeUInt16LE(numChannels * 2, 32);
    header.writeUInt16LE(16, 34);
    header.write("data", 36);
    header.writeUInt32LE(dataLength, 40);

    return Buffer.concat([header, buffer]);
}

/**
 * Convert OpenAI-style messages to Google GenAI content format.
 * Handles image content from base64 data URLs.
 * Note: Images on assistant/model messages are stripped to avoid
 * Gemini's thought_signature requirement for model-generated images.
 */
function convertMessages(messages) {
    return messages.map((item) => {
        const parts = [];
        // Only include media for user messages — model-generated media
        // require a thought_signature when sent back, so we skip them.
        if (item.role !== "assistant") {
            // All media fields are arrays of data URLs
            for (const field of ["images", "audio", "video", "pdf"]) {
                const arr = item[field];
                if (arr && Array.isArray(arr)) {
                    for (const dataUrl of arr) {
                        const match = dataUrl.match(/^data:([\w-]+\/[\w.+-]+);base64,(.+)$/);
                        if (match) {
                            parts.push({
                                inlineData: { mimeType: match[1], data: match[2] },
                            });
                        }
                    }
                }
            }
        }
        if (item.content) {
            parts.push({ text: item.content });
        }
        return {
            role: item.role === "assistant" ? "model" : "user",
            parts,
        };
    });
}

const googleProvider = {
    name: "google",

    async generateText(
        messages,
        model = getDefaultModels(TYPES.TEXT, TYPES.TEXT).google,
        options = {},
    ) {
        logger.provider("Google", `generateText model=${model}`);
        try {
            const contents = convertMessages(messages);
            const config = {};
            if (options.temperature !== undefined) {
                config.temperature = options.temperature;
            }
            if (options.topP !== undefined) {
                config.topP = options.topP;
            }
            if (options.topK !== undefined) {
                config.topK = options.topK;
            }
            if (options.presencePenalty !== undefined) {
                config.presencePenalty = options.presencePenalty;
            }
            if (options.frequencyPenalty !== undefined) {
                config.frequencyPenalty = options.frequencyPenalty;
            }
            if (options.stopSequences !== undefined) {
                config.stopSequences = options.stopSequences;
            }
            if (options.maxTokens !== undefined) {
                config.maxOutputTokens = options.maxTokens;
            }
            if (options.thinkingLevel || options.thinkingBudget !== undefined) {
                config.thinkingConfig = {
                    includeThoughts: true,
                };
                if (options.thinkingLevel) {
                    config.thinkingConfig.thinkingLevel = options.thinkingLevel;
                }
                if (
                    options.thinkingBudget !== undefined &&
                    options.thinkingBudget !== ""
                ) {
                    config.thinkingConfig.thinkingBudgetTokens = parseInt(
                        options.thinkingBudget,
                    );
                }
            }
            if (options.webSearch) {
                config.tools = [{ googleSearch: {} }];
            }

            const response = await getClient().models.generateContent({
                model,
                contents,
                config,
            });
            return {
                text: response.text,
                usage: {
                    inputTokens: response.usageMetadata?.promptTokenCount ?? 0,
                    outputTokens: response.usageMetadata?.candidatesTokenCount ?? 0,
                },
            };
        } catch (error) {
            throw new ProviderError("google", error.message, 500, error);
        }
    },

    async *generateTextStream(
        messages,
        model = getDefaultModels(TYPES.TEXT, TYPES.TEXT).google,
        options = {},
    ) {
        logger.provider("Google", `generateTextStream model=${model}`);
        try {
            const contents = convertMessages(messages);
            const config = {};
            if (options.temperature !== undefined) {
                config.temperature = options.temperature;
            }
            if (options.topP !== undefined) {
                config.topP = options.topP;
            }
            if (options.topK !== undefined) {
                config.topK = options.topK;
            }
            if (options.presencePenalty !== undefined) {
                config.presencePenalty = options.presencePenalty;
            }
            if (options.frequencyPenalty !== undefined) {
                config.frequencyPenalty = options.frequencyPenalty;
            }
            if (options.stopSequences !== undefined) {
                config.stopSequences = options.stopSequences;
            }
            if (options.maxTokens !== undefined) {
                config.maxOutputTokens = options.maxTokens;
            }
            if (options.thinkingLevel || options.thinkingBudget !== undefined) {
                config.thinkingConfig = {
                    includeThoughts: true,
                };
                if (options.thinkingLevel) {
                    config.thinkingConfig.thinkingLevel = options.thinkingLevel;
                }
                if (
                    options.thinkingBudget !== undefined &&
                    options.thinkingBudget !== ""
                ) {
                    config.thinkingConfig.thinkingBudgetTokens = parseInt(
                        options.thinkingBudget,
                    );
                }
            }
            // Build tools array based on enabled options
            const tools = [];
            if (options.webSearch) tools.push({ googleSearch: {} });
            if (options.codeExecution) tools.push({ codeExecution: {} });
            if (options.urlContext) tools.push({ urlContext: {} });
            if (tools.length > 0) config.tools = tools;

            // For models that output images, enable multimodal response
            const modelDef = Object.values(MODELS).find((m) => m.name === model);
            if (modelDef?.outputTypes?.includes(TYPES.IMAGE)) {
                config.responseModalities = ["TEXT", "IMAGE"];
            }

            const responseStream = await getClient().models.generateContentStream({
                model,
                contents,
                config,
            });
            let usage = null;
            for await (const chunk of responseStream) {
                // Process all parts in the chunk
                if (chunk.candidates?.[0]?.content?.parts) {
                    for (const part of chunk.candidates[0].content.parts) {
                        if (part.thought && part.text) {
                            yield { type: "thinking", content: part.text };
                        } else if (part.text) {
                            yield part.text;
                        } else if (part.inlineData) {
                            yield {
                                type: "image",
                                data: part.inlineData.data,
                                mimeType: part.inlineData.mimeType || "image/png",
                            };
                        } else if (part.executableCode?.code) {
                            yield {
                                type: "executableCode",
                                code: part.executableCode.code,
                                language: part.executableCode.language || "python",
                            };
                        } else if (part.codeExecutionResult) {
                            yield {
                                type: "codeExecutionResult",
                                output: part.codeExecutionResult.output || "",
                                outcome: part.codeExecutionResult.outcome || "OK",
                            };
                        }
                    }
                } else if (chunk.text) {
                    yield chunk.text;
                }
                if (chunk.usageMetadata) {
                    usage = {
                        inputTokens: chunk.usageMetadata.promptTokenCount ?? 0,
                        outputTokens: chunk.usageMetadata.candidatesTokenCount ?? 0,
                    };
                }
            }
            if (usage) {
                yield { type: "usage", usage };
            }
        } catch (error) {
            throw new ProviderError("google", error.message, 500, error);
        }
    },

    async captionImage(
        images,
        prompt = "Describe this image.",
        model = getDefaultModels(TYPES.IMAGE, TYPES.TEXT).google,
        systemPrompt,
    ) {
        logger.provider("Google", `captionImage model=${model}`);
        try {
            // Process each image into inline data parts
            const imageParts = [];
            for (const imageUrlOrBase64 of images) {
                let imageData = imageUrlOrBase64;
                let mimeType = "image/jpeg";

                if (imageUrlOrBase64.startsWith("http")) {
                    const response = await fetch(imageUrlOrBase64);
                    if (!response.ok) {
                        throw new Error(
                            `Failed to fetch image from URL: ${imageUrlOrBase64}`,
                        );
                    }
                    const arrayBuffer = await response.arrayBuffer();
                    imageData = Buffer.from(arrayBuffer).toString("base64");
                    mimeType = response.headers.get("content-type") || "image/jpeg";
                } else if (imageUrlOrBase64.includes(";base64,")) {
                    const parts = imageUrlOrBase64.split(";base64,");
                    mimeType = parts[0].split(":")[1];
                    imageData = parts[1];
                }

                imageParts.push({ inlineData: { data: imageData, mimeType } });
            }

            const contents = [
                {
                    role: "user",
                    parts: [
                        ...imageParts,
                        { text: prompt },
                    ],
                },
            ];

            const config = {};
            if (systemPrompt) {
                config.systemInstruction = systemPrompt;
            }

            const response = await getClient().models.generateContent({
                model,
                contents,
                config: Object.keys(config).length > 0 ? config : undefined,
            });
            const usage = {
                inputTokens: response.usageMetadata?.promptTokenCount || 0,
                outputTokens: response.usageMetadata?.candidatesTokenCount || 0,
            };
            return { text: response.text, usage };
        } catch (error) {
            throw new ProviderError("google", error.message, 500, error);
        }
    },

    async generateImage(
        prompt,
        images = [],
        model = MODELS.GEMINI_3_PRO_IMAGE.name,
        systemPrompt,
    ) {
        logger.provider("Google", `generateImage model=${model}`);
        try {
            const config = {
                responseModalities: ["IMAGE"],
                imageConfig: { imageSize: "1K" },
            };

            if (systemPrompt) {
                config.systemInstruction = systemPrompt;
            }

            const parts = [{ text: prompt }];
            if (images.length) {
                for (const image of images) {
                    parts.push({
                        inlineData: {
                            data: image.imageData,
                            mimeType: image.mimeType || "image/jpeg",
                        },
                    });
                }
            }

            const contents = [{ role: "user", parts }];
            const response = await getClient().models.generateContentStream({
                model,
                config,
                contents,
            });

            let combinedText = "";
            for await (const chunk of response) {
                if (!chunk.candidates?.[0]?.content?.parts) continue;
                if (chunk.candidates?.[0]?.finishReason === "PROHIBITED_CONTENT") {
                    throw new Error("Content was flagged as prohibited by Google AI");
                }
                const part = chunk.candidates[0].content.parts[0];
                if (part.inlineData) {
                    return {
                        imageData: part.inlineData.data,
                        mimeType: part.inlineData.mimeType || "image/png",
                        text: combinedText,
                    };
                } else if (chunk.text) {
                    combinedText += chunk.text;
                }
            }
            throw new Error("No image data received from Google AI");
        } catch (error) {
            if (error instanceof ProviderError) throw error;
            throw new ProviderError("google", error.message, 500, error);
        }
    },

    async generateSpeech(text, voice = DEFAULT_VOICES.google, options = {}) {
        logger.provider("Google", `generateSpeech voice=${voice}`);
        try {
            const config = {
                temperature: 1,
                responseModalities: ["audio"],
                speechConfig: {
                    voiceConfig: {
                        prebuiltVoiceConfig: {
                            voiceName: voice,
                        },
                    },
                },
            };

            const response = await getClient().models.generateContent({
                model:
                    options.model || getDefaultModels(TYPES.TEXT, TYPES.AUDIO).google,
                contents: [
                    {
                        role: "user",
                        parts: [
                            { text: options.prompt ? `${options.prompt}\n\n${text}` : text },
                        ],
                    },
                ],
                config,
            });

            const candidates = response.candidates;
            if (candidates?.[0]?.content?.parts?.[0]?.inlineData) {
                const inlineData = candidates[0].content.parts[0].inlineData;
                const audioBuffer = Buffer.from(inlineData.data || "", "base64");

                if (
                    inlineData.mimeType === "audio/mpeg" ||
                    inlineData.mimeType === "audio/mp3"
                ) {
                    return {
                        stream: Readable.from(audioBuffer),
                        contentType: "audio/mpeg",
                    };
                } else {
                    const wavBuffer = addWavHeader(audioBuffer);
                    return { stream: Readable.from(wavBuffer), contentType: "audio/wav" };
                }
            } else {
                throw new Error("No audio content received from Google GenAI");
            }
        } catch (error) {
            if (error instanceof ProviderError) throw error;
            throw new ProviderError("google", error.message, 500, error);
        }
    },

    async transcribeAudio(
        audioBuffer,
        mimeType,
        model = "gemini-3-flash-preview",
        options = {},
    ) {
        logger.provider("Google", `transcribeAudio model=${model}`);
        try {
            const audioBase64 = audioBuffer.toString("base64");
            const prompt =
                options.prompt ||
                "Transcribe the following audio accurately. Return only the transcription text, nothing else.";

            const contents = [
                {
                    role: "user",
                    parts: [
                        { inlineData: { mimeType, data: audioBase64 } },
                        { text: prompt },
                    ],
                },
            ];

            const config = {};
            if (options.language) {
                config.systemInstruction = `Transcribe in ${options.language}.`;
            }

            const response = await getClient().models.generateContent({
                model,
                contents,
                config,
            });

            return {
                text: response.text || "",
                usage: {
                    inputTokens: response.usageMetadata?.promptTokenCount ?? 0,
                    outputTokens: response.usageMetadata?.candidatesTokenCount ?? 0,
                },
            };
        } catch (error) {
            throw new ProviderError("google", error.message, 500, error);
        }
    },

    async generateEmbedding(content, model, options = {}) {
        model = model || getDefaultModels(TYPES.TEXT, TYPES.EMBEDDING)?.google || "gemini-embedding-2-preview";
        logger.provider("Google", `generateEmbedding model=${model}`);
        try {
            const params = { model };
            const config = {};

            // Build the contents for the embedding request
            if (typeof content === "string") {
                // Simple text-only input
                params.contents = content;
            } else if (Array.isArray(content)) {
                // Multimodal: wrap all parts in a single Content object.
                // The SDK maps each top-level array item to a separate batch request,
                // so we must bundle parts into one Content to get a single embedding.
                params.contents = { role: "user", parts: content };
            } else {
                params.contents = content;
            }

            if (options.taskType) {
                config.taskType = options.taskType;
            }
            if (options.dimensions) {
                config.outputDimensionality = options.dimensions;
            }

            if (Object.keys(config).length > 0) {
                params.config = config;
            }

            const response = await getClient().models.embedContent(params);

            // embedContent returns { embeddings: [{ values: [...] }] } for batch/multimodal,
            // or { embedding: { values: [...] } } for single text
            let values;
            if (response.embedding?.values) {
                values = response.embedding.values;
            } else if (response.embeddings?.[0]?.values) {
                values = response.embeddings[0].values;
            } else {
                throw new Error("No embedding data in response");
            }

            return {
                embedding: values,
                dimensions: values.length,
            };
        } catch (error) {
            throw new ProviderError("google", error.message, error.status || 500, error);
        }
    },
};

export default googleProvider;
