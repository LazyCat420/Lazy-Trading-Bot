// ============================================================
// Prism the AI Gateway — Configuration & Reference Catalog
// ============================================================

// PROVIDERS
const PROVIDERS = {
    OPENAI: "openai",
    ANTHROPIC: "anthropic",
    GOOGLE: "google",
    ELEVENLABS: "elevenlabs",
    INWORLD: "inworld",
    LM_STUDIO: "lm-studio",
    OLLAMA: "ollama",
};

const PROVIDER_LIST = Object.values(PROVIDERS);

// ============================================================
// TYPES — Input / Output modality constants
// ============================================================

const TYPES = {
    TEXT: "text",
    IMAGE: "image",
    AUDIO: "audio",
    VIDEO: "video",
    PDF: "pdf",
    EMBEDDING: "embedding",
};

// ============================================================
// MODEL TYPES — Endpoint-based model category
// ============================================================

const MODEL_TYPES = {
    CONVERSATION: "conversation",
    AUDIO: "audio",
    EMBED: "embed",
};

// ============================================================
// UNIFIED MODEL CATALOG
// ============================================================
// Every model lives here with all its metadata.
// Helper functions below derive defaults, options, and pricing.

const MODELS = {
    // ----- OpenAI — Text Generation -----
    GPT_5_2: {
        name: "gpt-5.2",
        label: "GPT 5.2",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        arena: {
            text: 1479,
            code: 1472,
            vision: 1271,
            document: 1412,
            search: 1219,
        },
        pricing: {
            inputPerMillion: 0.875,
            cachedInputPerMillion: 0.0875,
            outputPerMillion: 7.0,
            webSearchPer1kCalls: 10.0,
        },
        maxInputTokens: 400_000,
        maxOutputTokens: 128_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 16, maxSizeMB: 20 } },
        streaming: true,
        thinking: true,
        verbosity: true,
        reasoningSummary: true,
        responsesAPI: true,
        webSearch: true,
        tools: ["Thinking", "Web Search", "Function Calling", "File Search"],
    },
    GPT_5_MINI: {
        name: "gpt-5-mini",
        label: "GPT 5 Mini",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        pricing: {
            inputPerMillion: 0.125,
            cachedInputPerMillion: 0.0125,
            outputPerMillion: 1.0,
            webSearchPer1kCalls: 10.0,
        },
        maxInputTokens: 400_000,
        maxOutputTokens: 128_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 16, maxSizeMB: 20 } },
        streaming: true,
        thinking: true,
        webSearch: true,
        tools: ["Thinking", "Web Search", "Function Calling", "File Search"],
    },
    GPT_5_NANO: {
        name: "gpt-5-nano",
        label: "GPT 5 Nano",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        pricing: {
            inputPerMillion: 0.025,
            cachedInputPerMillion: 0.0025,
            outputPerMillion: 0.2,
            webSearchPer1kCalls: 25.0,
        },
        maxInputTokens: 400_000,
        maxOutputTokens: 128_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 16, maxSizeMB: 20 } },
        streaming: true,
        thinking: false,
        webSearch: true,
        tools: ["Web Search", "Function Calling", "File Search"],
    },
    // ----- Legacy OpenAI Models (unlisted, kept for cost tracking) -----
    GPT_41_MINI: {
        name: "gpt-4.1-mini",
        label: "GPT 4.1 Mini",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        listed: false,
        pricing: {
            inputPerMillion: 0.4,
            cachedInputPerMillion: 0.1,
            outputPerMillion: 1.6,
        },
        maxInputTokens: 1_047_576,
        maxOutputTokens: 32_768,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 16, maxSizeMB: 20 } },
        streaming: true,
    },
    GPT_41_NANO: {
        name: "gpt-4.1-nano",
        label: "GPT 4.1 Nano",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        listed: false,
        pricing: {
            inputPerMillion: 0.1,
            cachedInputPerMillion: 0.025,
            outputPerMillion: 0.4,
        },
        maxInputTokens: 1_047_576,
        maxOutputTokens: 32_768,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 16, maxSizeMB: 20 } },
        streaming: true,
    },
    GPT_4O: {
        name: "gpt-4o",
        label: "GPT 4o",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        listed: false,
        pricing: {
            inputPerMillion: 2.5,
            cachedInputPerMillion: 1.25,
            outputPerMillion: 10.0,
        },
        maxInputTokens: 128_000,
        maxOutputTokens: 16_384,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 16, maxSizeMB: 20 } },
        streaming: true,
    },
    GPT_4: {
        name: "gpt-4",
        label: "GPT 4",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        listed: false,
        pricing: { inputPerMillion: 30.0, outputPerMillion: 60.0 },
        maxInputTokens: 8_192,
        maxOutputTokens: 8_192,
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.TEXT],
        streaming: true,
    },
    GPT_53_CHAT: {
        name: "gpt-5.3-chat-latest",
        label: "GPT 5.3 Chat",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        pricing: {
            inputPerMillion: 0.875,
            cachedInputPerMillion: 0.0875,
            outputPerMillion: 7.0,
            webSearchPer1kCalls: 10.0,
        },
        maxInputTokens: 128_000,
        maxOutputTokens: 128_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 16, maxSizeMB: 20 } },
        streaming: true,
        thinking: true,
        webSearch: true,
        tools: ["Thinking", "Web Search", "Function Calling", "File Search"],
    },
    GPT_53_CODEX: {
        name: "gpt-5.3-codex",
        label: "GPT 5.3 Codex",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        pricing: {
            inputPerMillion: 0.875,
            cachedInputPerMillion: 0.0875,
            outputPerMillion: 7.0,
            webSearchPer1kCalls: 10.0,
        },
        maxInputTokens: 272_000,
        maxOutputTokens: 128_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 16, maxSizeMB: 20 } },
        streaming: true,
        thinking: true,
        webSearch: true,
        tools: ["Thinking", "Web Search", "Function Calling", "File Search"],
    },
    GPT_54: {
        name: "gpt-5.4",
        label: "GPT 5.4",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        default: true,
        defaultTemperature: 1.0,
        arena: { text: 1479 },
        pricing: {
            inputPerMillion: 1.25,
            cachedInputPerMillion: 0.13,
            outputPerMillion: 7.5,
            inputOver272kPerMillion: 2.5,
            outputOver272kPerMillion: 11.25,
            webSearchPer1kCalls: 10.0,
        },
        maxInputTokens: 1_050_000,
        maxOutputTokens: 128_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 16, maxSizeMB: 20 } },
        streaming: true,
        thinking: true,
        verbosity: true,
        reasoningSummary: true,
        responsesAPI: true,
        webSearch: true,
        tools: [
            "Thinking",
            "Web Search",
            "Function Calling",
            "File Search",
            "Computer Use",
        ],
    },
    GPT_54_PRO: {
        name: "gpt-5.4-pro",
        label: "GPT 5.4 Pro",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        pricing: {
            inputPerMillion: 15.0,
            outputPerMillion: 90.0,
            inputOver272kPerMillion: 30.0,
            outputOver272kPerMillion: 135.0,
            webSearchPer1kCalls: 10.0,
        },
        maxInputTokens: 1_050_000,
        maxOutputTokens: 128_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 16, maxSizeMB: 20 } },
        streaming: true,
        thinking: true,
        verbosity: true,
        reasoningSummary: true,
        responsesAPI: true,
        webSearch: true,
        tools: [
            "Thinking",
            "Web Search",
            "Function Calling",
            "File Search",
            "Computer Use",
        ],
    },
    // ----- Anthropic — Text Generation -----
    HAIKU_45: {
        name: "claude-haiku-4-5-20251001",
        label: "Haiku 4.5",
        provider: PROVIDERS.ANTHROPIC,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        arena: { document: 1426 },
        pricing: { inputPerMillion: 1.0, outputPerMillion: 5.0 },
        maxInputTokens: 200_000,
        maxOutputTokens: 64_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 100, maxSizeMB: 32 }, pdf: { maxCount: 5, maxSizeMB: 32 } },
        streaming: true,
        thinking: true,
        assistantImages: false,
        webSearch: true,
        codeExecution: true,
        tools: ["Thinking", "Web Search", "Function Calling", "Code Execution"],
    },
    SONNET_45: {
        name: "claude-sonnet-4-5-20250929",
        label: "Sonnet 4.5",
        provider: PROVIDERS.ANTHROPIC,
        modelType: MODEL_TYPES.CONVERSATION,
        default: true,
        defaultTemperature: 1.0,
        arena: { document: 1450 },
        pricing: { inputPerMillion: 3.0, outputPerMillion: 15.0 },
        maxInputTokens: 200_000,
        maxOutputTokens: 64_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 100, maxSizeMB: 32 }, pdf: { maxCount: 5, maxSizeMB: 32 } },
        streaming: true,
        thinking: true,
        assistantImages: false,
        webSearch: true,
        webFetch: true,
        codeExecution: true,
        tools: [
            "Thinking",
            "Web Search",
            "Function Calling",
            "Computer Use",
            "Code Execution",
        ],
    },
    SONNET_46: {
        name: "claude-sonnet-4-6",
        label: "Sonnet 4.6",
        provider: PROVIDERS.ANTHROPIC,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        arena: { code: 1523, search: 1203 },
        pricing: { inputPerMillion: 3.0, outputPerMillion: 15.0 },
        maxInputTokens: 200_000,
        maxOutputTokens: 64_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 100, maxSizeMB: 32 }, pdf: { maxCount: 5, maxSizeMB: 32 } },
        streaming: true,
        thinking: true,
        assistantImages: false,
        webSearch: true,
        webFetch: true,
        codeExecution: true,
        tools: [
            "Thinking",
            "Web Search",
            "Function Calling",
            "Computer Use",
            "Code Execution",
        ],
    },
    OPUS_45: {
        name: "claude-opus-4-5-20251101",
        label: "Opus 4.5",
        provider: PROVIDERS.ANTHROPIC,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        arena: { text: 1470, code: 1475, document: 1474 },
        pricing: { inputPerMillion: 5.0, outputPerMillion: 25.0 },
        maxInputTokens: 200_000,
        maxOutputTokens: 64_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 100, maxSizeMB: 32 }, pdf: { maxCount: 5, maxSizeMB: 32 } },
        streaming: true,
        thinking: true,
        assistantImages: false,
        webSearch: true,
        webFetch: true,
        codeExecution: true,
        tools: [
            "Thinking",
            "Web Search",
            "Function Calling",
            "Computer Use",
            "Code Execution",
        ],
    },
    OPUS_46: {
        name: "claude-opus-4-6",
        label: "Opus 4.6",
        provider: PROVIDERS.ANTHROPIC,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        arena: { text: 1504, code: 1555, document: 1525, search: 1255 },
        pricing: { inputPerMillion: 5.0, outputPerMillion: 25.0 },
        maxInputTokens: 200_000,
        maxOutputTokens: 64_000,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        mediaLimits: { image: { maxCount: 100, maxSizeMB: 32 }, pdf: { maxCount: 5, maxSizeMB: 32 } },
        streaming: true,
        thinking: true,
        assistantImages: false,
        webSearch: true,
        webFetch: true,
        codeExecution: true,
        tools: [
            "Thinking",
            "Web Search",
            "Function Calling",
            "Computer Use",
            "Code Execution",
        ],
    },

    // ----- Google — Text Generation -----
    GEMINI_3_FLASH: {
        name: "gemini-3-flash-preview",
        label: "Gemini 3 Flash",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.CONVERSATION,
        default: true,
        defaultTemperature: 1.0,
        arena: {
            text: 1473,
            code: 1442,
            vision: 1276,
            document: 1422,
            search: 1218,
        },
        pricing: {
            inputPerMillion: 0.5,
            audioInputPerMillion: 1.0,
            outputPerMillion: 3.0,
        },
        maxInputTokens: 1_048_576,
        maxOutputTokens: 65_536,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE, TYPES.AUDIO, TYPES.VIDEO, TYPES.PDF],
        outputTypes: [TYPES.TEXT],
        mediaLimits: {
            image: { maxCount: 3000, maxSizeMB: 100 },
            audio: { maxCount: 50, maxSizeMB: 100 },
            video: { maxCount: 10, maxSizeMB: 100 },
            pdf: { maxCount: 50, maxSizeMB: 100 },
        },
        streaming: true,
        thinking: true,
        webSearch: "Google Search",
        codeExecution: true,
        urlContext: true,
        tools: [
            "Thinking",
            "Google Search",
            "Function Calling",
            "Code Execution",
            "URL Context",
        ],
    },
    GEMINI_3_PRO: {
        name: "gemini-3-pro-preview",
        label: "Gemini 3 Pro",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        arena: {
            text: 1485,
            code: 1442,
            vision: 1288,
            document: 1444,
            search: 1214,
        },
        pricing: {
            inputPerMillion: 2.0,
            audioInputPerMillion: 4.0,
            outputPerMillion: 12.0,
        },
        maxInputTokens: 1_048_576,
        maxOutputTokens: 65_536,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE, TYPES.AUDIO, TYPES.VIDEO, TYPES.PDF],
        outputTypes: [TYPES.TEXT],
        mediaLimits: {
            image: { maxCount: 3000, maxSizeMB: 100 },
            audio: { maxCount: 50, maxSizeMB: 100 },
            video: { maxCount: 10, maxSizeMB: 100 },
            pdf: { maxCount: 50, maxSizeMB: 100 },
        },
        streaming: true,
        thinking: true,
        webSearch: "Google Search",
        codeExecution: true,
        urlContext: true,
        tools: [
            "Thinking",
            "Google Search",
            "Function Calling",
            "Code Execution",
            "URL Context",
        ],
    },
    GEMINI_31_PRO: {
        name: "gemini-3.1-pro-preview",
        label: "Gemini 3.1 Pro",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        arena: { text: 1500, code: 1461, vision: 1278, document: 1462 },
        pricing: {
            inputPerMillion: 2.0,
            audioInputPerMillion: 4.0,
            outputPerMillion: 12.0,
        },
        maxInputTokens: 1_048_576,
        maxOutputTokens: 65_536,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE, TYPES.AUDIO, TYPES.VIDEO, TYPES.PDF],
        outputTypes: [TYPES.TEXT],
        mediaLimits: {
            image: { maxCount: 3000, maxSizeMB: 100 },
            audio: { maxCount: 50, maxSizeMB: 100 },
            video: { maxCount: 10, maxSizeMB: 100 },
            pdf: { maxCount: 50, maxSizeMB: 100 },
        },
        streaming: true,
        thinking: true,
        webSearch: "Google Search",
        codeExecution: true,
        urlContext: true,
        tools: [
            "Thinking",
            "Google Search",
            "Function Calling",
            "Code Execution",
            "URL Context",
        ],
    },

    // ----- LM Studio — Local Text Generation -----
    QWEN_VL_8B: {
        name: "qwen/qwen3-vl-8b",
        label: "Qwen3 VL 8B",
        provider: PROVIDERS.LM_STUDIO,
        modelType: MODEL_TYPES.CONVERSATION,
        default: true,
        defaultTemperature: 0.7,
        size: "5.6 GB",
        maxInputTokens: 131_072,
        maxOutputTokens: 8_192,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT],
        streaming: true,
        thinking: false,
        pricing: { inputPerMillion: 0, outputPerMillion: 0 },
    },
    JOSIEFIED_QWEN: {
        name: "josiefied-qwen3-8b-abliterated-v1",
        label: "Josiefied Qwen3 8B",
        provider: PROVIDERS.LM_STUDIO,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 0.7,
        size: "5.5 GB",
        maxInputTokens: 32_768,
        maxOutputTokens: 8_192,
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.TEXT],
        streaming: true,
        thinking: false,
        pricing: { inputPerMillion: 0, outputPerMillion: 0 },
    },

    // ----- Text-to-Speech -----
    GPT_4O_MINI_TTS: {
        name: "gpt-4o-mini-tts",
        label: "GPT 4o Mini TTS",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.AUDIO,
        default: true,
        pricing: {
            inputPerMillion: 0.6,
            audioOutputPerMillion: 12.0,
            perMinute: 0.015,
        },
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.AUDIO],
        streaming: true,
    },
    GEMINI_2_FLASH_LITE_PREVIEW_TTS: {
        name: "gemini-2.0-flash-lite-preview-tts",
        label: "Gemini 2.0 Flash Lite TTS",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.AUDIO,
        pricing: { inputPerMillion: 0.075, audioOutputPerMillion: 0.3 },
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.AUDIO],
        streaming: true,
    },
    GEMINI_25_FLASH_LITE_TTS: {
        name: "gemini-2.5-flash-lite-preview-tts",
        label: "Gemini 2.5 Flash Lite TTS",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.AUDIO,
        pricing: { inputPerMillion: 0.3, audioOutputPerMillion: 2.5 },
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.AUDIO],
        streaming: true,
    },
    GEMINI_25_FLASH_TTS: {
        name: "gemini-2.5-flash-preview-tts",
        label: "Gemini 2.5 Flash TTS",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.AUDIO,
        pricing: { inputPerMillion: 0.5, audioOutputPerMillion: 10.0 },
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.AUDIO],
        streaming: true,
    },
    GEMINI_25_PRO_TTS: {
        name: "gemini-2.5-pro-preview-tts",
        label: "Gemini 2.5 Pro TTS",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.AUDIO,
        default: true,
        pricing: { inputPerMillion: 1.0, audioOutputPerMillion: 20.0 },
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.AUDIO],
        streaming: true,
    },
    ESPEAKNG: {
        name: "espeak-ng",
        label: "eSpeak NG",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.AUDIO,
        listed: false,
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.AUDIO],
        streaming: false,
    },
    ELEVEN_TURBO_V2: {
        name: "eleven_turbo_v2",
        label: "Eleven Turbo v2",
        provider: PROVIDERS.ELEVENLABS,
        modelType: MODEL_TYPES.AUDIO,
        default: true,
        pricing: { perCharacter: 0.00005 },
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.AUDIO],
        streaming: true,
    },
    INWORLD_TTS_1_5_MAX: {
        name: "inworld-tts-1.5-max",
        label: "Inworld TTS 1.5 Max",
        provider: PROVIDERS.INWORLD,
        modelType: MODEL_TYPES.AUDIO,
        default: true,
        pricing: { perCharacter: 0.00001, perMinute: 0.01 },
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.AUDIO],
        streaming: true,
    },
    INWORLD_TTS_1_5_MINI: {
        name: "inworld-tts-1.5-mini",
        label: "Inworld TTS 1.5 Mini",
        provider: PROVIDERS.INWORLD,
        modelType: MODEL_TYPES.AUDIO,
        pricing: { perCharacter: 0.000005, perMinute: 0.005 },
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.AUDIO],
        streaming: true,
    },

    // ----- Text-to-Image -----
    GPT_IMAGE_15: {
        name: "gpt-image-1.5",
        label: "GPT Image 1.5",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        arena: { image: 1307, imageEdit: 1348 },
        pricing: {
            inputPerMillion: 2.5,
            cachedInputPerMillion: 0.63,
            outputPerMillion: 5.0,
            imageInputPerMillion: 4.0,
            cachedImageInputPerMillion: 1.0,
            imageOutputPerMillion: 16.0,
        },
        maxInputTokens: 32_768,
        maxOutputTokens: 32_768,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT, TYPES.IMAGE],
        imageAPI: true,
        supportsSystemPrompt: false,
        tools: ["Image Generation"],
    },
    GEMINI_3_PRO_IMAGE: {
        name: "gemini-3-pro-image-preview",
        label: "Gemini 3 Pro Image",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.CONVERSATION,
        default: true,
        defaultTemperature: 1.0,
        arena: { image: 1233, imageEdit: 1391 },
        pricing: {
            inputPerMillion: 2.0,
            imageInputPerMillion: 2.0,
            outputPerMillion: 12.0,
            imageOutputPerMillion: 120.0,
        },
        maxInputTokens: 1_048_576,
        maxOutputTokens: 32_768,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE],
        outputTypes: [TYPES.TEXT, TYPES.IMAGE],
        streaming: false,
        thinking: true,
        webSearch: true,
        tools: ["Thinking", "Image Generation", "Web Search"],
    },
    GEMINI_31_FLASH_IMAGE: {
        name: "gemini-3.1-flash-image-preview",
        label: "Gemini 3.1 Flash Image",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.CONVERSATION,
        defaultTemperature: 1.0,
        arena: { image: 1268, imageEdit: 1388 },
        pricing: {
            inputPerMillion: 0.25,
            imageInputPerMillion: 0.25,
            outputPerMillion: 1.5,
            imageOutputPerMillion: 60.0,
        },
        maxInputTokens: 131_072,
        maxOutputTokens: 32_768,
        inputTypes: [TYPES.TEXT, TYPES.IMAGE, TYPES.PDF],
        outputTypes: [TYPES.TEXT, TYPES.IMAGE],
        streaming: false,
        thinking: true,
        webSearch: true,
        tools: ["Thinking", "Image Generation", "Web Search"],
    },

    // ----- Embeddings -----
    TEXT_EMBEDDING_3_SMALL: {
        name: "text-embedding-3-small",
        label: "Embedding 3 Small",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.EMBED,
        default: true,
        pricing: { inputPerMillion: 0.02 },
        maxInputTokens: 8_191,
        dimensions: 1536,
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.EMBEDDING],
    },
    TEXT_EMBEDDING_3_LARGE: {
        name: "text-embedding-3-large",
        label: "Embedding 3 Large",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.EMBED,
        pricing: { inputPerMillion: 0.13 },
        maxInputTokens: 8_191,
        dimensions: 3072,
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.EMBEDDING],
    },
    TEXT_EMBEDDING_ADA_002: {
        name: "text-embedding-ada-002",
        label: "Ada 002 (Legacy)",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.EMBED,
        pricing: { inputPerMillion: 0.1 },
        maxInputTokens: 8_191,
        dimensions: 1536,
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.EMBEDDING],
    },
    GEMINI_EMBEDDING_2: {
        name: "gemini-embedding-2-preview",
        label: "Gemini Embedding 2",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.EMBED,
        default: true,
        pricing: { inputPerMillion: 0.2 },
        maxInputTokens: 8_192,
        dimensions: 3072,
        inputTypes: [
            TYPES.TEXT,
            TYPES.IMAGE,
            TYPES.AUDIO,
            TYPES.VIDEO,
            TYPES.PDF,
        ],
        outputTypes: [TYPES.EMBEDDING],
    },
    GEMINI_EMBEDDING_001: {
        name: "gemini-embedding-001",
        label: "Gemini Embedding",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.EMBED,
        pricing: { inputPerMillion: 0.2 },
        maxInputTokens: 2_048,
        dimensions: 3072,
        inputTypes: [TYPES.TEXT],
        outputTypes: [TYPES.EMBEDDING],
    },

    // ----- Speech-to-Text (Audio → Text) -----
    GPT_4O_TRANSCRIBE: {
        name: "gpt-4o-transcribe",
        label: "GPT-4o Transcribe",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        default: true,
        pricing: {
            audioInputPerMillion: 2.5,
            outputPerMillion: 10.0,
            perMinute: 0.006,
        },
        inputTypes: [TYPES.AUDIO],
        outputTypes: [TYPES.TEXT],
    },
    GPT_4O_MINI_TRANSCRIBE: {
        name: "gpt-4o-mini-transcribe",
        label: "GPT-4o Mini Transcribe",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        pricing: {
            audioInputPerMillion: 1.25,
            outputPerMillion: 5.0,
            perMinute: 0.003,
        },
        inputTypes: [TYPES.AUDIO],
        outputTypes: [TYPES.TEXT],
    },
    WHISPER_1: {
        name: "whisper-1",
        label: "Whisper V2",
        provider: PROVIDERS.OPENAI,
        modelType: MODEL_TYPES.CONVERSATION,
        pricing: { perMinute: 0.006 },
        inputTypes: [TYPES.AUDIO],
        outputTypes: [TYPES.TEXT],
    },
    GEMINI_3_FLASH_STT: {
        name: "gemini-3-flash-preview",
        label: "Gemini 3 Flash",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.CONVERSATION,
        default: true,
        pricing: { audioInputPerMillion: 1.0, outputPerMillion: 3.0 },
        inputTypes: [TYPES.AUDIO],
        outputTypes: [TYPES.TEXT],
    },
    GEMINI_3_PRO_STT: {
        name: "gemini-3-pro-preview",
        label: "Gemini 3 Pro",
        provider: PROVIDERS.GOOGLE,
        modelType: MODEL_TYPES.CONVERSATION,
        pricing: { audioInputPerMillion: 4.0, outputPerMillion: 12.0 },
        inputTypes: [TYPES.AUDIO],
        outputTypes: [TYPES.TEXT],
    },
};

// ============================================================
// HELPER FUNCTIONS — derive defaults, options, pricing from MODELS
// ============================================================

/**
 * Get all models whose inputTypes includes `inputType`
 * and whose outputTypes includes `outputType`.
 */
function getModels(inputType, outputType) {
    return Object.values(MODELS).filter(
        (m) =>
            m.inputTypes.includes(inputType) && m.outputTypes.includes(outputType),
    );
}

/**
 * Get listed model options grouped by provider
 * for a given input→output type combination.
 * Returns: { [provider]: [{ name, label }, ...] }
 */
function getModelOptions(inputType, outputType) {
    const opts = {};
    for (const m of getModels(inputType, outputType)) {
        if (m.listed !== false) {
            const entry = { name: m.name, label: m.label };
            if (m.thinking) entry.thinking = true;
            if (m.inputTypes?.includes(TYPES.IMAGE)) entry.vision = true;
            if (m.webSearch) entry.webSearch = m.webSearch;
            if (m.inputTypes) entry.inputTypes = m.inputTypes;
            if (m.outputTypes) entry.outputTypes = m.outputTypes;
            if (m.tools) entry.tools = m.tools;
            if (m.pricing) entry.pricing = m.pricing;
            if (m.arena) entry.arena = m.arena;
            if (m.maxInputTokens) entry.contextLength = m.maxInputTokens;
            if (m.assistantImages === false) entry.assistantImages = false;
            if (m.codeExecution) entry.codeExecution = true;
            if (m.webFetch) entry.webFetch = true;
            if (m.urlContext) entry.urlContext = true;
            if (m.defaultTemperature !== undefined)
                entry.defaultTemperature = m.defaultTemperature;
            if (m.verbosity) entry.verbosity = true;
            if (m.reasoningSummary) entry.reasoningSummary = true;
            if (m.responsesAPI) entry.responsesAPI = true;
            if (m.size) entry.size = m.size;
            if (m.modelType) entry.modelType = m.modelType;
            if (m.mediaLimits) entry.mediaLimits = m.mediaLimits;
            // System prompt support: true for chat models, false for image-only/TTS/embedding APIs
            entry.supportsSystemPrompt = m.supportsSystemPrompt !== undefined
                ? m.supportsSystemPrompt
                : m.outputTypes.includes(TYPES.TEXT);
            (opts[m.provider] ??= []).push(entry);
        }
    }
    return opts;
}

/**
 * Get the default model name per provider
 * for a given input→output type combination.
 * Returns: { [provider]: modelName }
 */
function getDefaultModels(inputType, outputType) {
    const defaults = {};
    for (const m of getModels(inputType, outputType)) {
        if (m.default) {
            defaults[m.provider] = m.name;
        }
    }
    return defaults;
}

/**
 * Get pricing map for a given input→output type combination.
 * Returns: { [modelName]: { inputPerMillion, outputPerMillion } }
 */
function getPricing(inputType, outputType) {
    const pricing = {};
    for (const m of getModels(inputType, outputType)) {
        if (m.pricing) {
            pricing[m.name] = m.pricing;
        }
    }
    return pricing;
}

/**
 * Find a single model object by its API name.
 * Returns the model object or null.
 */
function getModelByName(name) {
    return Object.values(MODELS).find((m) => m.name === name) || null;
}

// ============================================================
// VOICES (per provider — applies to TEXT → AUDIO models)
// ============================================================

const OPENAI_VOICES = [
    { name: "alloy", gender: "Neutral" },
    { name: "ash", gender: "Male" },
    { name: "ballad", gender: "Male" },
    { name: "coral", gender: "Female" },
    { name: "echo", gender: "Male" },
    { name: "fable", gender: "Male" },
    { name: "nova", gender: "Female" },
    { name: "onyx", gender: "Male" },
    { name: "sage", gender: "Female" },
    { name: "shimmer", gender: "Female" },
    { name: "verse", gender: "Male" },
    { name: "marin", gender: "Female" },
    { name: "cedar", gender: "Male" },
];

const GOOGLE_VOICES = [
    { name: "Achernar", gender: "Female" },
    { name: "Achird", gender: "Male" },
    { name: "Algenib", gender: "Male" },
    { name: "Algieba", gender: "Male" },
    { name: "Alnilam", gender: "Male" },
    { name: "Aoede", gender: "Female" },
    { name: "Autonoe", gender: "Female" },
    { name: "Callirrhoe", gender: "Female" },
    { name: "Charon", gender: "Male" },
    { name: "Despina", gender: "Female" },
    { name: "Enceladus", gender: "Male" },
    { name: "Erinome", gender: "Female" },
    { name: "Fenrir", gender: "Male" },
    { name: "Gacrux", gender: "Female" },
    { name: "Iapetus", gender: "Male" },
    { name: "Kore", gender: "Female" },
    { name: "Laomedeia", gender: "Female" },
    { name: "Leda", gender: "Female" },
    { name: "Orus", gender: "Male" },
    { name: "Pulcherrima", gender: "Female" },
    { name: "Puck", gender: "Male" },
    { name: "Rasalgethi", gender: "Male" },
    { name: "Sadachbia", gender: "Male" },
    { name: "Sadaltager", gender: "Male" },
    { name: "Schedar", gender: "Male" },
    { name: "Sulafat", gender: "Female" },
    { name: "Umbriel", gender: "Male" },
    { name: "Vindemiatrix", gender: "Female" },
    { name: "Zephyr", gender: "Female" },
    { name: "Zubenelgenubi", gender: "Male" },
];

const ELEVENLABS_VOICES = [
    { name: "21m00Tcm4TlvDq8ikWAM", label: "Rachel", gender: "Female" },
    { name: "EXAVITQu4vr4xnSDxMaL", label: "Bella", gender: "Female" },
    { name: "ErXwobaYiN019PkySvjV", label: "Antoni", gender: "Male" },
    { name: "MF3mGyEYCl7XYWbV9V6O", label: "Elli", gender: "Female" },
    { name: "TxGEqnHWrfWFTfGW9XjX", label: "Josh", gender: "Male" },
    { name: "VR6AewLTigWG4xSOukaG", label: "Arnold", gender: "Male" },
    { name: "pNInz6obpgDQGcFmaJgB", label: "Adam", gender: "Male" },
    { name: "yoZ06aMxZJJ28mfd3POQ", label: "Sam", gender: "Male" },
];

const INWORLD_VOICES = [
    {
        name: "default-wf7_kdeq9hcrw0dojoklzq__bender",
        gender: "Male",
        description: "Bender",
    },
    {
        name: "Alex",
        gender: "Male",
        description:
            "Energetic and expressive mid-range male voice, with a mildly nasal quality",
    },
    {
        name: "Ashley",
        gender: "Female",
        description: "A warm, natural female voice",
    },
    {
        name: "Craig",
        gender: "Male",
        description: "Older British male with a refined and articulate voice",
    },
    {
        name: "Deborah",
        gender: "Female",
        description: "Gentle and elegant female voice",
    },
    {
        name: "Dennis",
        gender: "Male",
        description: "Middle-aged man with a smooth, calm and friendly voice",
    },
    {
        name: "Edward",
        gender: "Male",
        description: "Male with a fast-talking, emphatic and streetwise tone",
    },
    {
        name: "Hades",
        gender: "Male",
        description:
            "Commanding and gruff male voice, think an omniscient narrator or castle guard",
    },
    {
        name: "Pixie",
        gender: "Female",
        description:
            "High-pitched, childlike female voice with a squeaky quality - great for a cartoon",
    },
    {
        name: "Mark",
        gender: "Male",
        description: "Energetic, expressive man with a rapid-fire delivery",
    },
    {
        name: "Olivia",
        gender: "Female",
        description: "Young, British female with an upbeat, friendly tone",
    },
    {
        name: "Ronald",
        gender: "Male",
        description: "Confident, British man with a deep, gravelly voice",
    },
    {
        name: "Sarah",
        gender: "Female",
        description:
            "Fast-talking young adult woman, with a questioning and curious tone",
    },
    {
        name: "Theodore",
        gender: "Male",
        description: "Gravelly male voice, with a time-worn quality",
    },
    {
        name: "Timothy",
        gender: "Male",
        description: "Lively, upbeat American male voice",
    },
    {
        name: "Wendy",
        gender: "Female",
        description: "Posh, middle-aged British female voice",
    },
    {
        name: "Dominus",
        gender: "Male",
        description:
            "Robotic, deep male voice with a menacing quality. Perfect for villains",
    },
    {
        name: "Hana",
        gender: "Female",
        description:
            "Bright, expressive young female voice, perfect for storytelling, gaming, and playing",
    },
    {
        name: "Clive",
        gender: "Male",
        description:
            "British-accented English-language male voice with a calm, cordial quality",
    },
    {
        name: "Carter",
        gender: "Male",
        description:
            "Energetic, mature radio announcer-style male voice, great for storytelling",
    },
    {
        name: "Blake",
        gender: "Male",
        description:
            "Rich, intimate male voice, perfect for audiobooks, romantic content, and reassuring",
    },
    {
        name: "Luna",
        gender: "Female",
        description:
            "Calm, relaxing female voice, perfect for meditations, sleep stories, and mindful",
    },
];

const VOICES = {
    [PROVIDERS.OPENAI]: OPENAI_VOICES,
    [PROVIDERS.GOOGLE]: GOOGLE_VOICES,
    [PROVIDERS.ELEVENLABS]: ELEVENLABS_VOICES,
    [PROVIDERS.INWORLD]: INWORLD_VOICES,
};

const DEFAULT_VOICES = {
    [PROVIDERS.OPENAI]: "echo",
    [PROVIDERS.GOOGLE]: "Kore",
    [PROVIDERS.ELEVENLABS]: "21m00Tcm4TlvDq8ikWAM",
    [PROVIDERS.INWORLD]: "Dennis",
};

// ============================================================
// EXPORTS
// ============================================================

export {
    // Providers
    PROVIDERS,
    PROVIDER_LIST,

    // Types
    TYPES,
    MODEL_TYPES,

    // Models
    MODELS,

    // Helpers
    getModels,
    getModelOptions,
    getDefaultModels,
    getPricing,
    getModelByName,

    // Voices
    VOICES,
    DEFAULT_VOICES,
};
